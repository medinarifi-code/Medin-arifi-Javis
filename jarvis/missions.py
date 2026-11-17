"""Autonomer Modus: Ziel → Plan → Schritte durch spezialisierte Agenten → Prüfung → Ergebnis.

Live verfolgbar (Ereignisse mit mission_id), jederzeit pausierbar/abbrechbar. Sensible Aktionen
laufen auch hier über Freigaben (Security Agent); E-Mail-Versand ist in Missionen nicht möglich.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path

from sqlalchemy import desc, select, update

from .agents import AGENTS
from .db import events, loads, missions, now
from .memory import parse_json

log = logging.getLogger("jarvis.missions")
MAX_PARALLEL = 2
STEP_AGENTS = [a for a in AGENTS if a not in ("master", "security")]


class MissionRunner:
    def __init__(self, core):
        self.core = core
        self.db = core.db
        self.threads: dict[int, threading.Thread] = {}

    # ------------------------------------------------------------------ Steuerung
    def start(self, goal: str) -> int:
        goal = (goal or "").strip()
        if len(goal) < 5:
            raise ValueError("Bitte beschreibe das Ziel genauer.")
        running = [m for m in self.list(20) if m["status"] in ("planning", "running", "waiting")]
        if len(running) >= MAX_PARALLEL:
            raise ValueError("Es laufen schon zwei Missionen. Bitte warte, bis eine fertig ist.")
        mid = self.db.add(missions, goal=goal[:4000], status="planning", plan="[]", step=0, created=now(), updated=now())
        self.db.event(f"Mission #{mid} gestartet: {goal[:120]}", kind="agent", agent="Master Agent", mission_id=mid)
        t = threading.Thread(target=self._run_safe, args=(mid,), daemon=True, name=f"mission-{mid}")
        self.threads[mid] = t
        t.start()
        return mid

    def get(self, mid: int) -> dict | None:
        m = self.db.one(select(missions).where(missions.c.id == mid))
        if m:
            m["plan"] = loads(m["plan"], [])
            m["files"] = loads(m["files"], [])
            m["events"] = self.db.q(select(events).where(events.c.mission_id == mid).order_by(events.c.id).limit(300))
        return m

    def list(self, limit: int = 50) -> list[dict]:
        rows = self.db.q(select(missions).order_by(desc(missions.c.id)).limit(limit))
        for r in rows:
            r["plan"] = loads(r["plan"], [])
            r["files"] = loads(r["files"], [])
        return rows

    def set_status(self, mid: int, status: str) -> None:
        self.db.run(update(missions).where(missions.c.id == mid).values(status=status, updated=now()))

    def control(self, mid: int, action: str) -> dict:
        m = self.get(mid)
        if not m:
            raise ValueError("Mission nicht gefunden.")
        if action == "pause" and m["status"] in ("running", "planning"):
            self.set_status(mid, "paused")
        elif action == "resume" and m["status"] == "paused":
            self.set_status(mid, "running")
        elif action == "cancel" and m["status"] not in ("done", "failed", "cancelled"):
            self.set_status(mid, "cancelled")
        else:
            raise ValueError("Diese Aktion ist im aktuellen Zustand nicht möglich.")
        self.db.event(f"Mission #{mid}: {({'pause': 'pausiert', 'resume': 'fortgesetzt', 'cancel': 'abgebrochen'})[action]}",
                      kind="warn" if action == "cancel" else "info", agent="Master Agent", mission_id=mid)
        return self.get(mid)

    def _status(self, mid: int) -> str:
        row = self.db.one(select(missions.c.status).where(missions.c.id == mid))
        return row["status"] if row else "cancelled"

    def _wait_if_paused(self, mid: int) -> bool:
        """True = weiterarbeiten, False = abgebrochen."""
        while True:
            st = self._status(mid)
            if st == "cancelled":
                return False
            if st != "paused":
                return True
            time.sleep(1.5)

    def _save(self, mid: int, **values) -> None:
        if "plan" in values:
            values["plan"] = json.dumps(values["plan"], ensure_ascii=False)
        if "files" in values:
            values["files"] = json.dumps(values["files"], ensure_ascii=False)
        values["updated"] = now()
        self.db.run(update(missions).where(missions.c.id == mid).values(**values))

    # ------------------------------------------------------------------ Ablauf
    def _run_safe(self, mid: int) -> None:
        try:
            self._run(mid)
        except Exception as e:  # noqa: BLE001
            log.exception("Mission %s fehlgeschlagen", mid)
            if self._status(mid) != "cancelled":
                self._save(mid, status="failed", result=f"Fehler: {e}")
                self.db.event(f"Mission #{mid} fehlgeschlagen", kind="err", agent="Master Agent", detail=str(e), mission_id=mid)
                self.db.notify(f"Mission #{mid} fehlgeschlagen", str(e)[:300], kind="err")

    def plan(self, goal: str) -> list[dict]:
        agents = "\n".join(f"- {a}: {AGENTS[a]['desc']}" for a in STEP_AGENTS)
        prompt = (
            "Du bist der Master Agent von JARVIS. Zerlege das Ziel in 2 bis 7 konkrete, nacheinander ausführbare Schritte. "
            "Jeder Schritt wird von genau einem Agenten mit seinen Werkzeugen ausgeführt und soll ein greifbares Ergebnis "
            "liefern (z. B. eine Datei). Verfügbare Agenten:\n" + agents +
            '\nAntworte NUR mit JSON: {"steps": [{"title": "...", "agent": "coding", "instructions": "genaue Anweisung"}]}'
            f"\n\nZiel: {goal}")
        raw = self.core.registry.quick([{"role": "user", "content": prompt}], tier="smart")
        data = parse_json(raw) or {}
        steps = []
        for s in (data.get("steps") or [])[:7]:
            if not isinstance(s, dict) or not s.get("title"):
                continue
            agent = s.get("agent") if s.get("agent") in STEP_AGENTS else "research"
            steps.append({"title": str(s["title"])[:200], "agent": agent, "instructions": str(s.get("instructions") or s["title"]),
                          "status": "pending", "result": ""})
        if not steps:
            steps = [{"title": "Ziel umsetzen", "agent": "coding" if re.search(r"web|code|app|seite", goal, re.I) else "research",
                      "instructions": goal, "status": "pending", "result": ""}]
        return steps

    def _run(self, mid: int) -> None:
        m = self.get(mid)
        goal = m["goal"]
        self.db.event("Analysiere Ziel und plane Schritte …", kind="agent", agent="Master Agent", mission_id=mid)
        steps = self.plan(goal)
        if not self._wait_if_paused(mid):
            return
        self._save(mid, plan=steps, status="running")
        self.db.event(f"Plan mit {len(steps)} Schritten erstellt", kind="ok", agent="Master Agent",
                      detail="\n".join(f"{i + 1}. {s['title']} ({AGENTS[s['agent']]['name']})" for i, s in enumerate(steps)),
                      mission_id=mid)
        created: list[dict] = []
        for i, step in enumerate(steps):
            if not self._wait_if_paused(mid):
                return
            result, files = self._run_step(mid, goal, steps, i)
            created += files
            steps[i].update(status="done" if result is not None else "failed", result=(result or "")[:2000])
            self._save(mid, plan=steps, step=i + 1, files=created)
            if result is None:
                return  # abgebrochen
        if not self._wait_if_paused(mid):
            return
        summary = self._review(mid, goal, steps, created)
        self._save(mid, status="done", result=summary, files=created)
        self.db.event(f"Mission #{mid} abgeschlossen", kind="ok", agent="Master Agent", detail=summary[:2000], mission_id=mid)
        self.db.notify(f"Mission fertig: {goal[:80]}", summary[:400], kind="ok")

    def _run_step(self, mid: int, goal: str, steps: list[dict], i: int) -> tuple[str | None, list[dict]]:
        from .ai import Cancelled, Jarvis

        step = steps[i]
        agent = AGENTS[step["agent"]]
        self.db.event(f"Schritt {i + 1}/{len(steps)}: {step['title']}", kind="agent", agent=agent["name"], mission_id=mid)
        steps[i]["status"] = "running"
        self._save(mid, plan=steps)
        previous = "\n".join(f"Schritt {j + 1} ({s['title']}): {s['result'][:600]}" for j, s in enumerate(steps[:i]) if s["result"])
        worker = Jarvis(self.core.cfg, registry=self.core.registry, core=self.core, agent=step["agent"])
        worker.extra_system = (
            f"Du arbeitest an Mission #{mid}. Gesamtziel: {goal}\n"
            + (f"Ergebnisse bisheriger Schritte:\n{previous}\n" if previous else "")
            + "Arbeite selbstständig, ohne Rückfragen. Erstelle echte Dateien mit deinen Werkzeugen. "
              "Antworte am Ende kurz, was du erledigt hast und welche Dateien entstanden sind. "
              "Im Missionsmodus kannst du keine E-Mails senden.")
        worker.should_stop = lambda: self._status(mid) == "cancelled"
        out, files = [], []

        def on_event(ev: dict) -> None:
            if ev.get("type") == "file":
                files.append({"name": ev["name"], "url": ev["url"], "step": i + 1})

        def on_status(s: str) -> None:
            self.db.event(s, kind="info", agent=agent["name"], mission_id=mid)

        try:
            worker.ask(step["instructions"], on_text=out.append, on_status=on_status, on_event=on_event, agent=step["agent"])
        except Cancelled:
            return None, files
        result = "".join(out).strip() or "(keine Antwort)"
        self.db.event(f"Schritt {i + 1} erledigt", kind="ok", agent=agent["name"], detail=result[:1500], mission_id=mid)
        return result, files

    # ------------------------------------------------------------------ Prüfung (Tests)
    def _review(self, mid: int, goal: str, steps: list[dict], created: list[dict]) -> str:
        checks = check_files(self.core.cfg.output_dir, created)
        if checks:
            self.db.event("Automatische Prüfung: " + ("; ".join(checks)[:300]), kind="warn", agent="Coding Agent",
                          detail="\n".join(checks), mission_id=mid)
        else:
            self.db.event("Automatische Prüfung bestanden", kind="ok", agent="Coding Agent", mission_id=mid)
        prompt = (
            "Du bist der Master Agent. Prüfe die Ergebnisse dieser Mission kritisch und fasse sie für den Nutzer zusammen "
            "(was wurde erreicht, welche Dateien, was ist offen, nächste Schritte). Maximal 10 Sätze, Deutsch, kein Markdown.\n"
            f"Ziel: {goal}\n" + "\n".join(f"{j + 1}. {s['title']} – {s['status']}: {s['result'][:700]}" for j, s in enumerate(steps))
            + f"\nDateien: {', '.join(f['name'] for f in created) or 'keine'}"
            + (f"\nAutomatische Prüfung fand Probleme: {'; '.join(checks)}" if checks else "\nAutomatische Prüfung: ok"))
        try:
            return self.core.registry.quick([{"role": "user", "content": prompt}], tier="smart").strip()
        except Exception as e:  # noqa: BLE001
            return f"Mission abgeschlossen ({len(steps)} Schritte, {len(created)} Dateien). Zusammenfassung nicht möglich: {e}"


def check_files(root: Path, created: list[dict]) -> list[str]:
    """Echte, statische Prüfungen erzeugter Dateien (z. B. fehlende CSS/JS-Dateien einer Webseite)."""
    problems = []
    root = root.resolve()
    for f in created:
        rel = f["url"].replace("/files/", "", 1)
        p = (root / rel)
        if not p.exists():
            problems.append(f"{f['name']} fehlt")
            continue
        if p.suffix.lower() in (".html", ".htm"):
            text = p.read_text(encoding="utf-8", errors="ignore")
            if "<html" not in text.lower() and "<body" not in text.lower():
                problems.append(f"{f['name']}: kein vollständiges HTML")
            for ref in re.findall(r'(?:src|href)=["\']([^"\'#?]+)["\']', text):
                if re.match(r"^(https?:|mailto:|tel:|data:|//)", ref) or ref.startswith("#"):
                    continue
                if not (p.parent / ref).exists():
                    problems.append(f"{f['name']} verweist auf fehlende Datei {ref}")
        if p.suffix.lower() == ".py":
            try:
                compile(p.read_text(encoding="utf-8", errors="ignore"), str(p), "exec")
            except SyntaxError as e:
                problems.append(f"{f['name']}: Syntaxfehler in Zeile {e.lineno}")
        if p.suffix.lower() == ".json":
            try:
                json.loads(p.read_text(encoding="utf-8", errors="ignore"))
            except ValueError:
                problems.append(f"{f['name']}: ungültiges JSON")
    return problems[:20]
