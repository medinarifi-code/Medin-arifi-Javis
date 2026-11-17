"""Security Agent: Berechtigungen, Freigaben (Approvals) und Protokoll.

Sensible Aktionen (Löschen, Zeitplan eintragen, Mails in Missionen senden, Gedächtnis löschen …) werden
nicht sofort ausgeführt, sondern als Freigabe-Anfrage gespeichert. Erst wenn der Nutzer in der Oberfläche
„Freigeben“ tippt, führt JARVIS sie aus. Alles landet im Protokoll (events, kind=security).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Callable

from sqlalchemy import desc, select, update

from .db import DB, approvals, loads, now

log = logging.getLogger("jarvis.security")

# Standard-Berechtigungen (in den Einstellungen änderbar)
DEFAULT_PERMISSIONS = {
    "mail_send": True,          # Mails senden, wenn du es in deiner Nachricht verlangst
    "file_delete": True,        # Dateien löschen (immer mit Freigabe)
    "autonomous": True,         # Autonome Missionen erlauben
    "proactive": False,         # JARVIS darf von sich aus Hinweise geben
    "auto_memory": True,        # Wichtige Infos automatisch merken
    "web_fetch": True,          # Webseiten abrufen
    "notify_email": False,      # Benachrichtigungen zusätzlich per Mail an dich
}
SENSITIVE = {"delete_file": "Datei löschen", "forget": "Erinnerung löschen", "schedule": "Zeitplan eintragen",
             "send_email": "E-Mail senden", "reply_email": "E-Mail beantworten", "delete_task": "Aufgabe löschen"}


class Security:
    def __init__(self, db: DB):
        self.db = db
        self.handlers: dict[str, Callable[[dict], str]] = {}

    # ------------------------------------------------------------------ Berechtigungen
    def permissions(self) -> dict:
        saved = self.db.get_setting("permissions", {}) or {}
        return {**DEFAULT_PERMISSIONS, **{k: bool(v) for k, v in saved.items() if k in DEFAULT_PERMISSIONS}}

    def set_permission(self, key: str, value: bool) -> dict:
        if key not in DEFAULT_PERMISSIONS:
            raise KeyError(key)
        perms = self.permissions()
        perms[key] = bool(value)
        self.db.set_setting("permissions", perms)
        self.db.event(f"Berechtigung „{key}“ = {'an' if value else 'aus'}", kind="security", agent="Security")
        return perms

    def allowed(self, key: str) -> bool:
        return self.permissions().get(key, False)

    # ------------------------------------------------------------------ Freigaben
    def register(self, action: str, handler: Callable[[dict], str]) -> None:
        self.handlers[action] = handler

    def request(self, action: str, args: dict, summary: str, agent: str = "", mission_id: int | None = None) -> int:
        aid = self.db.add(approvals, ts=now(), action=action, args=json.dumps(args, ensure_ascii=False)[:20000],
                          summary=summary[:2000], agent=agent, mission_id=mission_id, status="pending")
        self.db.event(f"Freigabe nötig: {SENSITIVE.get(action, action)}", kind="security", agent=agent or "Security",
                      detail=summary, mission_id=mission_id)
        self.db.notify("Freigabe nötig", summary, kind="warn")
        return aid

    def get(self, aid: int) -> dict | None:
        r = self.db.one(select(approvals).where(approvals.c.id == aid))
        if r:
            r["args"] = loads(r["args"], {})
        return r

    def list(self, status: str | None = None, limit: int = 100) -> list[dict]:
        stmt = select(approvals)
        if status:
            stmt = stmt.where(approvals.c.status == status)
        rows = self.db.q(stmt.order_by(desc(approvals.c.id)).limit(limit))
        for r in rows:
            r["args"] = loads(r["args"], {})
        return rows

    def decide(self, aid: int, approve: bool) -> dict:
        item = self.get(aid)
        if not item or item["status"] != "pending":
            raise ValueError("Diese Freigabe ist nicht (mehr) offen.")
        if not approve:
            self.db.run(update(approvals).where(approvals.c.id == aid).values(status="denied"))
            self.db.event(f"Abgelehnt: {item['summary'][:120]}", kind="security", agent="Security", mission_id=item["mission_id"])
            return {**item, "status": "denied"}
        handler = self.handlers.get(item["action"])
        if handler is None:  # z. B. Missions-Schritt, der selbst weiterarbeitet
            self.db.run(update(approvals).where(approvals.c.id == aid).values(status="approved"))
            return {**item, "status": "approved"}
        try:
            result = handler(item["args"])
            self.db.run(update(approvals).where(approvals.c.id == aid).values(status="done", result=str(result)[:4000]))
            self.db.event(f"Freigegeben & ausgeführt: {item['summary'][:120]}", kind="ok", agent="Security",
                          detail=str(result)[:2000], mission_id=item["mission_id"])
            return {**item, "status": "done", "result": result}
        except Exception as e:  # noqa: BLE001
            self.db.run(update(approvals).where(approvals.c.id == aid).values(status="failed", result=str(e)[:4000]))
            self.db.event(f"Ausführung fehlgeschlagen: {item['summary'][:120]}", kind="err", agent="Security", detail=str(e))
            return {**item, "status": "failed", "result": str(e)}

    def wait(self, aid: int, timeout: float = 1800, should_stop: Callable[[], bool] = lambda: False) -> str:
        """Für Missionen: wartet, bis der Nutzer entschieden hat."""
        end = time.time() + timeout
        while time.time() < end:
            item = self.get(aid)
            if item and item["status"] != "pending":
                return item["status"]
            if should_stop():
                return "cancelled"
            time.sleep(1.5)
        self.db.run(update(approvals).where(approvals.c.id == aid).values(status="denied", result="Zeit abgelaufen"))
        return "timeout"
