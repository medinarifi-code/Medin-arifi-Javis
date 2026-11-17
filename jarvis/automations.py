"""Automation Center: Trigger → Bedingung → Aktion (mit Agent) → Benachrichtigung, plus Laufprotokoll.

Der Scheduler läuft im Server (alle 60 s), solange der Server wach ist.
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from datetime import datetime, timedelta

from sqlalchemy import delete, desc, select, update

from .db import automations, loads, now, runs

log = logging.getLogger("jarvis.automations")

TRIGGERS = {"daily": "Täglich um", "weekly": "Wöchentlich", "interval": "Alle X Stunden", "new_mail": "Neue E-Mail",
            "new_file": "Neue Datei hochgeladen", "manual": "Nur manuell"}
CONDITIONS = {"none": "Keine", "contains": "Enthält Text", "weekdays": "Nur Werktage", "open_tasks": "Mehr als X offene Aufgaben"}
ACTIONS = {"daily_report": "Tagesbericht erstellen", "agent": "Agent mit Auftrag", "research": "Thema recherchieren",
           "sort_tasks": "Aufgaben nach Priorität sortieren", "categorize_files": "Dateien in Ordner sortieren",
           "project_report": "Projektbericht", "self_improve": "Self-Improvement-Analyse", "notify": "Nur benachrichtigen"}
FOLDERS = {"Dokumente": {".pdf", ".docx", ".txt", ".md", ".rtf"}, "Präsentationen": {".pptx"}, "Tabellen": {".xlsx", ".csv"},
           "Bilder": {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}, "Code": {".py", ".js", ".html", ".css", ".json"},
           "Audio": {".mp3"}, "Archive": {".zip"}}

TEMPLATES = [
    {"name": "Morgenbericht um 7:30", "trigger": {"type": "daily", "time": "07:30"}, "condition": {"type": "none"},
     "action": {"type": "daily_report"}, "notify": {"inapp": True, "email": False}},
    {"name": "Wochenrecherche KI-News (Montag 9:00)", "trigger": {"type": "weekly", "days": [0], "time": "09:00"},
     "condition": {"type": "none"}, "action": {"type": "research", "topic": "Die wichtigsten KI-Neuigkeiten dieser Woche"},
     "notify": {"inapp": True, "email": False}},
    {"name": "Wichtige Mails melden", "trigger": {"type": "new_mail"}, "condition": {"type": "contains", "value": "dringend"},
     "action": {"type": "notify", "text": "Neue dringende Mail"}, "notify": {"inapp": True, "email": False}},
    {"name": "Uploads automatisch sortieren", "trigger": {"type": "new_file"}, "condition": {"type": "none"},
     "action": {"type": "categorize_files"}, "notify": {"inapp": True, "email": False}},
    {"name": "Aufgaben täglich priorisieren (18:00)", "trigger": {"type": "daily", "time": "18:00"},
     "condition": {"type": "weekdays"}, "action": {"type": "sort_tasks"}, "notify": {"inapp": True, "email": False}},
]


def next_run(trigger: dict, after: datetime | None = None) -> float | None:
    after = after or datetime.now()
    kind = trigger.get("type")
    if kind in ("daily", "weekly"):
        try:
            hh, mm = [int(x) for x in str(trigger.get("time") or "08:00").split(":")[:2]]
        except ValueError:
            hh, mm = 8, 0
        days = trigger.get("days") if kind == "weekly" else list(range(7))
        days = [int(d) for d in (days or [0])]
        for add in range(0, 8):
            cand = (after + timedelta(days=add)).replace(hour=hh, minute=mm, second=0, microsecond=0)
            if cand > after and cand.weekday() in days:
                return cand.timestamp()
        return None
    if kind == "interval":
        hours = max(0.25, float(trigger.get("hours") or 1))
        return (after + timedelta(hours=hours)).timestamp()
    if kind in ("new_mail", "new_file"):
        return (after + timedelta(minutes=10)).timestamp()   # wird alle 10 Min geprüft
    return None


class AutomationEngine:
    def __init__(self, core):
        self.core = core
        self.db = core.db
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_proactive = 0.0
        self._busy = threading.Lock()

    # ------------------------------------------------------------------ Verwaltung
    def _row(self, r: dict) -> dict:
        for k in ("trigger", "condition", "action", "notify", "state"):
            r[k] = loads(r.get(k), {})
        return r

    def list(self) -> list[dict]:
        return [self._row(r) for r in self.db.q(select(automations).order_by(automations.c.id))]

    def get(self, aid: int) -> dict | None:
        r = self.db.one(select(automations).where(automations.c.id == aid))
        return self._row(r) if r else None

    def save(self, data: dict, aid: int | None = None) -> int:
        trig = data.get("trigger") or {"type": "manual"}
        if trig.get("type") not in TRIGGERS:
            raise ValueError("Unbekannter Auslöser")
        act = data.get("action") or {}
        if act.get("type") not in ACTIONS:
            raise ValueError("Unbekannte Aktion")
        values = {"name": (data.get("name") or ACTIONS[act["type"]])[:200], "enabled": bool(data.get("enabled", True)),
                  "trigger": json.dumps(trig), "condition": json.dumps(data.get("condition") or {"type": "none"}),
                  "action": json.dumps(act), "notify": json.dumps(data.get("notify") or {"inapp": True}),
                  "next_run": next_run(trig)}
        if aid:
            self.db.run(update(automations).where(automations.c.id == aid).values(**values))
        else:
            aid = self.db.add(automations, created=now(), state=json.dumps({"since": now()}), **values)
        self.db.event(f"Automatisierung gespeichert: {values['name']}", kind="ok", agent="Master Agent")
        return aid

    def delete(self, aid: int) -> None:
        self.db.run(delete(automations).where(automations.c.id == aid))
        self.db.run(delete(runs).where(runs.c.automation_id == aid))

    def runs(self, aid: int | None = None, limit: int = 50) -> list[dict]:
        stmt = select(runs)
        if aid:
            stmt = stmt.where(runs.c.automation_id == aid)
        return self.db.q(stmt.order_by(desc(runs.c.id)).limit(limit))

    # ------------------------------------------------------------------ Ausführung
    def run(self, aid: int, manual: bool = False) -> dict:
        a = self.get(aid)
        if not a:
            raise ValueError("Automatisierung nicht gefunden")
        t0 = time.time()
        state = a["state"] or {}
        try:
            payload = self._triggered(a, state) if not manual else {"text": "manuell gestartet"}
            if payload is None:
                self._reschedule(a, state)
                return {"status": "skipped", "output": "Kein neues Ereignis."}
            if not self._condition(a["condition"], payload):
                self._reschedule(a, state)
                self._log(aid, "skipped", "Bedingung nicht erfüllt.", t0)
                return {"status": "skipped", "output": "Bedingung nicht erfüllt."}
            output = self._action(a, payload)
            status = "ok"
        except Exception as e:  # noqa: BLE001
            log.exception("Automatisierung %s fehlgeschlagen", aid)
            output, status = f"Fehler: {e}", "error"
        self._reschedule(a, state)
        self._log(aid, status, output, t0)
        self.db.event(f"Automatisierung „{a['name']}“: {'ok' if status == 'ok' else 'Fehler'}",
                      kind="ok" if status == "ok" else "err", agent="Automation", detail=output[:1500])
        if a["notify"].get("inapp", True) or a["notify"].get("email"):
            self.core.notify(a["name"], output[:3000], "ok" if status == "ok" else "err", email=bool(a["notify"].get("email")))
        return {"status": status, "output": output}

    def _log(self, aid: int, status: str, output: str, t0: float) -> None:
        self.db.add(runs, automation_id=aid, ts=now(), status=status, output=output[:8000], duration=round(time.time() - t0, 2))
        self.db.run(update(automations).where(automations.c.id == aid).values(last_run=now()))

    def _reschedule(self, a: dict, state: dict) -> None:
        self.db.run(update(automations).where(automations.c.id == a["id"]).values(
            next_run=next_run(a["trigger"]), state=json.dumps(state)))

    def _triggered(self, a: dict, state: dict) -> dict | None:
        kind = a["trigger"].get("type")
        if kind == "new_mail":
            box = self.core.mailbox_provider()
            if box is None:
                return None
            seen = set(state.get("seen_mail", []))
            new = [m for m in box.list(unread_only=True, limit=10) if m["id"] not in seen]
            state["seen_mail"] = (list(seen) + [m["id"] for m in new])[-200:]
            if not new:
                return None
            return {"text": "\n".join(f"{m['from']} <{m['from_address']}>: {m['subject']}" for m in new), "items": new}
        if kind == "new_file":
            since = state.get("since", 0)
            up = self.core.cfg.output_dir / "uploads"
            new = [p for p in up.glob("*") if p.is_file() and p.stat().st_mtime > since] if up.exists() else []
            state["since"] = now()
            if not new:
                return None
            return {"text": "\n".join(p.name for p in new), "files": [p.name for p in new]}
        return {"text": ""}

    def _condition(self, cond: dict, payload: dict) -> bool:
        kind = (cond or {}).get("type", "none")
        if kind == "contains":
            needle = str(cond.get("value") or "").lower()
            return bool(needle) and needle in payload.get("text", "").lower()
        if kind == "weekdays":
            return datetime.now().weekday() < 5
        if kind == "open_tasks":
            return len(self.core.tasks.list("open")) > int(cond.get("value") or 0)
        return True

    def _agent(self, agent: str, prompt: str) -> str:
        from .ai import Jarvis

        out, files = [], []
        j = Jarvis(self.core.cfg, registry=self.core.registry, core=self.core, agent=agent)
        j.extra_system = "Du arbeitest automatisch im Hintergrund (Automatisierung). Keine Rückfragen, erledige die Aufgabe."
        j.ask(prompt, on_text=out.append, on_status=lambda s: None,
              on_event=lambda e: files.append(e["name"]) if e.get("type") == "file" else None, agent=agent)
        text = "".join(out).strip()
        return text + (f"\n\nDateien: {', '.join(files)}" if files else "")

    def _action(self, a: dict, payload: dict) -> str:
        act = a["action"]
        kind = act.get("type")
        if kind == "notify":
            return (act.get("text") or a["name"]) + (f"\n{payload.get('text')}" if payload.get("text") else "")
        if kind == "daily_report":
            tasks_today = self.core.tasks.day_overview("heute")
            from .planner import fmt_task

            facts = ("Offene Aufgaben heute:\n" + "\n".join(fmt_task(t) for t in tasks_today["unscheduled"] + tasks_today["scheduled"])
                     + "\nÜberfällig: " + str(len(self.core.tasks.overdue())))
            return self._agent("assistant", "Erstelle meinen kurzen Tagesbericht für heute: Datum, Wetter (search_web), "
                                            "die 3 wichtigsten Nachrichten (search_web) und meine Aufgaben mit Empfehlung, "
                                            "womit ich anfangen soll.\n" + facts)
        if kind == "research":
            return self._agent("research", f"Recherchiere: {act.get('topic') or a['name']}. Fasse die Ergebnisse "
                                           "mit Quellen kurz zusammen und erstelle ein PDF.")
        if kind == "agent":
            prompt = (act.get("prompt") or a["name"]) + (f"\n\nAuslöser-Daten:\n{payload['text']}" if payload.get("text") else "")
            return self._agent(act.get("agent") or "master", prompt)
        if kind == "sort_tasks":
            open_t = self.core.tasks.list("open")
            today = datetime.now().strftime("%Y-%m-%d")
            changed = 0
            for t in open_t:
                if t["due"] and t["due"][:10] <= today and t["priority"] > 1:
                    self.core.tasks.update(t["id"], priority=1)
                    changed += 1
            ordered = sorted(self.core.tasks.list("open"), key=lambda t: (t["priority"], t["due"] or "9999"))
            return f"{changed} fällige Aufgaben auf hohe Priorität gesetzt. Reihenfolge:\n" + \
                   "\n".join(f"{i + 1}. {t['title']}" for i, t in enumerate(ordered[:15]))
        if kind == "categorize_files":
            return self.categorize_files()
        if kind == "project_report":
            return self.core.project_report(act.get("project") or "")
        if kind == "self_improve":
            props = self.core.self_improve()
            return "Vorschläge:\n" + "\n".join(p["text"] for p in props) if props else "Keine neuen Vorschläge."
        raise ValueError("Unbekannte Aktion")

    def categorize_files(self) -> str:
        root = self.core.cfg.output_dir.resolve()
        moved = []
        for p in list(root.iterdir()) + list((root / "uploads").glob("*") if (root / "uploads").exists() else []):
            if not p.is_file() or p.name.endswith(".office.png"):
                continue
            folder = next((f for f, exts in FOLDERS.items() if p.suffix.lower() in exts), None)
            if not folder or p.parent.name == folder:
                continue
            target = root / folder / p.name
            i = 2
            while target.exists():
                target = root / folder / f"{p.stem} ({i}){p.suffix}"
                i += 1
            target.parent.mkdir(exist_ok=True)
            old_rel = p.relative_to(root).as_posix()
            shutil.move(str(p), str(target))
            self.db.delete_file(old_rel)
            self.core.persist_file(target)
            moved.append(f"{old_rel} → {target.relative_to(root).as_posix()}")
        return f"{len(moved)} Dateien sortiert." + ("\n" + "\n".join(moved[:40]) if moved else "")

    # ------------------------------------------------------------------ Scheduler
    def tick(self) -> dict:
        """Führt fällige Automatisierungen, Erinnerungen und proaktive Prüfungen aus."""
        if not self._busy.acquire(blocking=False):
            return {"ran": 0, "busy": True}
        try:
            ran = 0
            for a in self.list():
                if a["enabled"] and a["next_run"] and a["next_run"] <= now() and a["trigger"].get("type") != "manual":
                    self.run(a["id"])
                    ran += 1
            reminders = self.core.run_reminders()
            hints = []
            if time.time() - self._last_proactive > 1800:
                self._last_proactive = time.time()
                hints = self.core.proactive_check()
            return {"ran": ran, "reminders": reminders, "hints": len(hints)}
        finally:
            self._busy.release()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        def loop():
            while not self._stop.wait(60):
                try:
                    self.tick()
                except Exception:  # noqa: BLE001
                    log.exception("Scheduler-Fehler")

        self._thread = threading.Thread(target=loop, daemon=True, name="jarvis-scheduler")
        self._thread.start()

    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def stop(self) -> None:
        self._stop.set()
