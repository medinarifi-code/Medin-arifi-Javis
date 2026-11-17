"""Der JARVIS-Kern: verbindet Datenbank, Modelle, Agenten, Gedächtnis, Aufgaben, Freigaben, Missionen,
Automatisierungen sowie Second Opinion, Self-Improvement, proaktive Hinweise und Project Brain."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from sqlalchemy import desc, func, select, update

from .config import Config
from .db import DB, approvals, events, feedback, lessons, memories, missions, notifications, now, runs, tasks, usage
from .memory import MemoryStore, parse_json
from .planner import TaskStore, fmt_task
from .providers import Registry
from .security import Security

log = logging.getLogger("jarvis.core")


class JarvisCore:
    def __init__(self, cfg: Config, db: DB, registry: Registry | None = None):
        self.cfg = cfg
        self.db = db
        self.registry = registry or Registry(cfg, db)
        self.registry.build()
        self.memory = MemoryStore(db)
        self.tasks = TaskStore(db)
        self.security = Security(db)
        from .automations import AutomationEngine
        from .missions import MissionRunner

        self.missions = MissionRunner(self)
        self.automations = AutomationEngine(self)
        self.mailbox_provider = lambda: None   # wird vom Server gesetzt (für Mails an dich)
        self.started = time.time()
        self._register_handlers()

    # ------------------------------------------------------------------ Freigabe-Aktionen
    def _register_handlers(self) -> None:
        from .tools.files import existing_path

        def delete_file(args):
            p = existing_path(self.cfg.output_dir, args["path"])
            p.unlink()
            self.db.delete_file(args["path"])
            return f"Datei {args['path']} gelöscht."

        def forget(args):
            return "Gelöscht." if self.memory.delete(int(args["id"])) else "War schon gelöscht."

        def schedule(args):
            ids = self.tasks.apply_schedule(args.get("items") or [])
            return f"{len(ids)} Einträge in den Zeitplan übernommen."

        def delete_task(args):
            return "Gelöscht." if self.tasks.delete(int(args["id"])) else "Nicht gefunden."

        for name, fn in {"delete_file": delete_file, "forget": forget, "schedule": schedule, "delete_task": delete_task}.items():
            self.security.register(name, fn)

    # ------------------------------------------------------------------ Dateien dauerhaft
    def persist_file(self, path: Path) -> None:
        try:
            rel = path.resolve().relative_to(self.cfg.output_dir.resolve()).as_posix()
            if path.stat().st_size <= 15 * 1024 * 1024:
                self.db.save_file(rel, path.read_bytes())
        except Exception as e:  # noqa: BLE001
            log.warning("Datei nicht gespeichert: %s", e)

    def restore_files(self) -> int:
        n = self.db.restore_files(self.cfg.output_dir)
        if n:
            log.info("%s Dateien aus der Datenbank wiederhergestellt", n)
        return n

    # ------------------------------------------------------------------ Benachrichtigungen
    def notify(self, title: str, body: str = "", kind: str = "info", email: bool | None = None) -> int:
        nid = self.db.notify(title, body, kind)
        send = self.security.allowed("notify_email") if email is None else email
        if send:
            try:
                box = self.mailbox_provider()
                if box is not None and getattr(box, "address", ""):
                    box.send([box.address], f"JARVIS: {title}", body or title)
            except Exception as e:  # noqa: BLE001
                log.info("Benachrichtigung per Mail nicht möglich: %s", e)
        return nid

    # ------------------------------------------------------------------ Second Opinion
    def second_opinion(self, question: str, max_models: int = 3) -> dict:
        if not question.strip():
            raise ValueError("Keine Frage")
        picks, seen = [], set()
        for p, m in self.registry.candidates("smart"):
            if p not in seen:
                picks.append((p, m))
                seen.add(p)
        for p, m in self.registry.candidates("smart"):   # zu wenige Anbieter → weitere Modelle
            if len(picks) >= max_models:
                break
            if (p, m) not in picks:
                picks.append((p, m))
        picks = picks[:max_models]
        msgs = [{"role": "system", "content": "Beantworte die Frage gründlich, eigenständig und auf Deutsch."},
                {"role": "user", "content": question}]
        self.db.event(f"Second Opinion mit {len(picks)} Modellen", kind="agent", agent="Master Agent", detail=question[:300])

        def ask(pm):
            try:
                return {"provider": pm[0], "model": pm[1], "text": self.registry.complete(pm[0], pm[1], msgs, 6000)}
            except Exception as e:  # noqa: BLE001
                return {"provider": pm[0], "model": pm[1], "text": "", "error": str(e)[:200]}

        with ThreadPoolExecutor(max_workers=3) as ex:
            answers = list(ex.map(ask, picks))
        good = [a for a in answers if a["text"].strip()]
        if not good:
            raise RuntimeError("Kein Modell hat geantwortet.")
        if len(good) == 1:
            return {"answers": answers, "summary": good[0]["text"]}
        compare = "\n\n".join(f"### Antwort {i + 1} ({a['model']}):\n{a['text'][:3500]}" for i, a in enumerate(good))
        summary = self.registry.quick([{"role": "user", "content":
            "Vergleiche diese unabhängigen Antworten auf dieselbe Frage. Nenne Übereinstimmungen, Widersprüche und "
            "Fehler und erstelle daraus eine geprüfte, gemeinsame beste Antwort (Deutsch).\n\nFrage: "
            + question + "\n\n" + compare}], tier="smart")
        return {"answers": answers, "summary": summary}

    # ------------------------------------------------------------------ Self-Improvement
    def active_lessons(self) -> list[str]:
        return [r["text"] for r in self.db.q(select(lessons.c.text).where(lessons.c.status == "active").limit(12))]

    def self_improve(self) -> list[dict]:
        """Analysiert Fehler, schlechte Bewertungen und Abbrüche und schlägt Regeln vor (Nutzer gibt frei)."""
        since = time.time() - 14 * 86400
        errs = self.db.q(select(events.c.title, events.c.detail).where(events.c.kind == "err", events.c.ts >= since)
                         .order_by(desc(events.c.id)).limit(30))
        bad = self.db.q(select(feedback).where(feedback.c.rating < 0, feedback.c.ts >= since).order_by(desc(feedback.c.id)).limit(15))
        fails = self.db.q(select(usage.c.provider, usage.c.model, usage.c.error).where(usage.c.ok == False, usage.c.ts >= since)  # noqa: E712
                          .order_by(desc(usage.c.id)).limit(20))
        if not (errs or bad or fails):
            return []
        existing = [r["text"] for r in self.db.q(select(lessons.c.text).where(lessons.c.status != "rejected"))]
        prompt = (
            "Du bist die Self-Improvement Engine von JARVIS. Analysiere die Probleme der letzten zwei Wochen und schlage "
            "höchstens 3 kurze, konkrete Verhaltensregeln für JARVIS vor, die solche Probleme künftig vermeiden. "
            "Keine Regeln, die Sicherheitsregeln lockern oder Freigaben umgehen. Keine Duplikate zu: "
            + "; ".join(existing[:20]) +
            '\nAntworte NUR mit JSON: {"lessons": [{"text": "Regel", "reason": "warum"}]}\n\n'
            "Fehler:\n" + "\n".join(f"- {e['title']}: {e['detail'][:200]}" for e in errs) +
            "\nSchlecht bewertete Antworten:\n" + "\n".join(f"- Frage: {b['question'][:200]} | Antwort: {b['answer'][:200]}" for b in bad) +
            "\nModell-Fehler:\n" + "\n".join(f"- {f['provider']}/{f['model']}: {f['error'][:120]}" for f in fails))
        data = parse_json(self.registry.quick([{"role": "user", "content": prompt}], tier="smart")) or {}
        out = []
        for item in (data.get("lessons") or [])[:3]:
            text = str(item.get("text") or "").strip()
            if text and not any(word in text.lower() for word in ("ohne freigabe", "sicherheitsregel ignor", "umgeh")):
                lid = self.db.add(lessons, ts=now(), text=text[:500], reason=str(item.get("reason") or "")[:800], status="proposed")
                out.append({"id": lid, "text": text})
        self.db.event(f"Self-Improvement: {len(out)} Verbesserungsvorschläge", kind="agent", agent="Master Agent")
        if out:
            self.notify("Neue Verbesserungsvorschläge", "\n".join(o["text"] for o in out), "info", email=False)
        return out

    # ------------------------------------------------------------------ Proaktive Hinweise
    def proactive_check(self) -> list[str]:
        if not self.security.allowed("proactive"):
            return []
        seen = set(self.db.get_setting("proactive_seen", []) or [])
        hints = []
        for t in self.tasks.overdue():
            key = f"overdue:{t['id']}"
            if key not in seen:
                hints.append((key, f"Überfällig: {t['title']}", fmt_task(t)))
        for t in self.tasks.due_soon(24):
            key = f"soon:{t['id']}"
            if key not in seen:
                hints.append((key, f"Frist in < 24 h: {t['title']}", fmt_task(t)))
        for r in self.db.q(select(runs).where(runs.c.status == "error", runs.c.ts >= time.time() - 86400)):
            key = f"run:{r['id']}"
            if key not in seen:
                hints.append((key, "Automatisierung fehlgeschlagen", r["output"][:300]))
        pending = self.db.q(select(func.count().label("n")).select_from(approvals).where(approvals.c.status == "pending"))
        if pending and pending[0]["n"]:
            key = f"approvals:{pending[0]['n']}:{datetime.now():%Y%m%d}"
            if key not in seen:
                hints.append((key, f"{pending[0]['n']} Freigabe(n) warten auf dich", "Siehe Security Center."))
        for key, title, body in hints[:10]:
            self.notify(title, body, "warn")
            seen.add(key)
        self.db.set_setting("proactive_seen", list(seen)[-500:])
        return [h[1] for h in hints]

    def run_reminders(self) -> int:
        n = 0
        for t in self.tasks.due_reminders():
            self.notify(f"Erinnerung: {t['title']}", fmt_task(t), "warn")
            n += 1
        return n

    # ------------------------------------------------------------------ Project Brain / Digital Twin
    def projects(self) -> list[dict]:
        names = set(self.memory.projects())
        names |= {r["project"] for r in self.db.q(select(tasks.c.project).where(tasks.c.project != "").distinct())}
        out = []
        for name in sorted(names):
            out.append(self.project_summary(name))
        return out

    def project_summary(self, name: str) -> dict:
        all_tasks = self.tasks.list(None, project=name)
        open_t = [t for t in all_tasks if t["status"] != "done"]
        overdue = [t for t in open_t if t["due"] and t["due"][:10] < datetime.now().strftime("%Y-%m-%d")]
        mems = self.memory.list(project=name, limit=100)
        decisions = [m for m in mems if m["kind"] == "decision"]
        last = max([t["updated"] for t in all_tasks] + [m["updated"] for m in mems] + [0])
        risks = []
        if overdue:
            risks.append(f"{len(overdue)} überfällige Aufgabe(n)")
        if len([t for t in open_t if t["priority"] == 1]) >= 4:
            risks.append("viele offene Aufgaben mit hoher Priorität gleichzeitig (Engpass)")
        if open_t and not any(t["due"] for t in open_t):
            risks.append("offene Aufgaben ohne Fristen")
        if last and time.time() - last > 14 * 86400:
            risks.append("seit über 2 Wochen keine Aktivität")
        if not open_t and all_tasks:
            risks.append("keine nächsten Schritte geplant")
        done = len(all_tasks) - len(open_t)
        return {"name": name, "tasks_open": len(open_t), "tasks_done": done,
                "progress": round(100 * done / len(all_tasks)) if all_tasks else None,
                "overdue": len(overdue), "memories": len(mems), "decisions": [d["content"] for d in decisions[:10]],
                "next": [fmt_task(t) for t in sorted(open_t, key=lambda t: (t["priority"], t["due"] or "9"))[:5]],
                "risks": risks, "last_activity": last or None}

    def project_report(self, name: str) -> str:
        s = self.project_summary(name)
        notes = "\n".join(f"- [{m['kind']}] {m['content'][:300]}" for m in self.memory.list(project=name, limit=40))
        prompt = (f"Erstelle einen kurzen Projektstatus für „{name}“ (Deutsch, sachlich, ohne Markdown-Tabellen): "
                  "Stand, Entscheidungen, Probleme/Engpässe, empfohlene nächste Schritte.\n"
                  f"Kennzahlen: {s}\nWissen:\n{notes}")
        return self.registry.quick([{"role": "user", "content": prompt}], tier="smart")

    # ------------------------------------------------------------------ Systemstatus (echte Prüfungen)
    def system_status(self) -> list[dict]:
        db_ok = self.db.ping()
        out = [{"id": "database", "label": "Datenbank", "ok": db_ok,
                "detail": ("Neon/Postgres – dauerhaft" if self.db.persistent else "SQLite – geht bei Neustart verloren")
                if db_ok else "nicht erreichbar"}]
        since = time.time() - 3600
        for p in self.registry.status():
            if not p["configured"]:
                continue
            recent = self.db.q(select(usage.c.ok).where(usage.c.provider == p["id"], usage.c.ts >= since)
                               .order_by(desc(usage.c.id)).limit(5))
            ok = (not recent) or any(r["ok"] for r in recent)
            out.append({"id": p["id"], "label": p["label"], "ok": ok,
                        "detail": "bereit" if not recent else ("antwortet" if ok else "Fehler zuletzt")})
        out.append({"id": "scheduler", "label": "Automatisierungen", "ok": self.automations.alive(),
                    "detail": "läuft" if self.automations.alive() else "gestoppt"})
        box = None
        try:
            box = self.mailbox_provider()
        except Exception:  # noqa: BLE001
            box = None
        out.append({"id": "mail", "label": "E-Mail", "ok": box is not None, "detail": getattr(box, "address", "") or "nicht verbunden"})
        out.append({"id": "voice", "label": "Stimme", "ok": True, "detail": "Server-Stimme + Browser"})
        return out

    def counts(self) -> dict:
        def n(table, *where):
            stmt = select(func.count().label("n")).select_from(table)
            for w in where:
                stmt = stmt.where(w)
            r = self.db.q(stmt)
            return r[0]["n"] if r else 0

        return {"memories": n(memories), "tasks_open": n(tasks, tasks.c.status != "done"),
                "missions_running": n(missions, missions.c.status.in_(["planning", "running", "waiting", "paused"])),
                "approvals_pending": n(approvals, approvals.c.status == "pending"),
                "notifications_unread": n(notifications, notifications.c.read == False)}  # noqa: E712


_lock = threading.Lock()


def mark_notifications_read(db: DB, ids: list[int] | None = None) -> None:
    stmt = update(notifications).values(read=True)
    if ids:
        stmt = stmt.where(notifications.c.id.in_(ids))
    db.run(stmt)
