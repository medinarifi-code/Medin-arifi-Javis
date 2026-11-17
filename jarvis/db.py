"""Dauerhafter Speicher von JARVIS.

DATABASE_URL gesetzt (z. B. kostenlose Neon-Postgres-Datenbank) → alles bleibt dauerhaft.
Sonst: SQLite-Datei neben dem Datei-Ordner (geht auf Render bei einem Neustart verloren).

Tabellen: Gedächtnis, Aufgaben, Ereignisse (Live-Feed/Audit), Missionen, Freigaben,
Automatisierungen + Läufe, Dateien (Spiegel), Einstellungen, Modell-Nutzung, Lektionen,
Benachrichtigungen, Gesprächsverlauf.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from sqlalchemy import (Boolean, Column, Float, Integer, LargeBinary, MetaData, String, Table, Text, create_engine,
                        delete, func, insert, or_, select, update)

log = logging.getLogger("jarvis.db")
md = MetaData()


def now() -> float:
    return time.time()


memories = Table("memories", md,
                 Column("id", Integer, primary_key=True, autoincrement=True),
                 Column("kind", String(20), nullable=False),          # fact|preference|project|goal|decision|person|note
                 Column("title", String(200), nullable=False),
                 Column("content", Text, nullable=False),
                 Column("tags", Text, default="[]"),                   # JSON-Liste
                 Column("project", String(120), default=""),
                 Column("importance", Integer, default=2),             # 1..3
                 Column("source", String(20), default="chat"),        # chat|user|agent
                 Column("created", Float, default=now), Column("updated", Float, default=now))

tasks = Table("tasks", md,
              Column("id", Integer, primary_key=True, autoincrement=True),
              Column("title", String(300), nullable=False),
              Column("notes", Text, default=""),
              Column("project", String(120), default=""),
              Column("priority", Integer, default=2),                  # 1 hoch, 2 normal, 3 niedrig
              Column("status", String(12), default="open"),             # open|doing|done
              Column("due", String(32), default=""),                    # ISO-Datum/Zeit
              Column("start", String(32), default=""),                  # geplante Startzeit (Tagesplan)
              Column("duration", Integer, default=0),                   # Minuten
              Column("remind_at", Float, nullable=True),
              Column("reminded", Boolean, default=False),
              Column("created", Float, default=now), Column("updated", Float, default=now))

events = Table("events", md,
               Column("id", Integer, primary_key=True, autoincrement=True),
               Column("ts", Float, default=now),
               Column("kind", String(12), default="info"),             # info|ok|warn|err|agent|security
               Column("agent", String(40), default=""),
               Column("title", String(300), nullable=False),
               Column("detail", Text, default=""),
               Column("mission_id", Integer, nullable=True))

missions = Table("missions", md,
                 Column("id", Integer, primary_key=True, autoincrement=True),
                 Column("goal", Text, nullable=False),
                 Column("status", String(12), default="planning"),     # planning|running|paused|waiting|done|failed|cancelled
                 Column("plan", Text, default="[]"),                    # JSON: [{title, agent, instructions, status, result}]
                 Column("step", Integer, default=0),
                 Column("result", Text, default=""),
                 Column("files", Text, default="[]"),
                 Column("created", Float, default=now), Column("updated", Float, default=now))

approvals = Table("approvals", md,
                  Column("id", Integer, primary_key=True, autoincrement=True),
                  Column("ts", Float, default=now),
                  Column("action", String(60), nullable=False),
                  Column("args", Text, default="{}"),
                  Column("summary", Text, default=""),
                  Column("agent", String(40), default=""),
                  Column("mission_id", Integer, nullable=True),
                  Column("status", String(12), default="pending"),      # pending|approved|denied|done|failed
                  Column("result", Text, default=""))

automations = Table("automations", md,
                    Column("id", Integer, primary_key=True, autoincrement=True),
                    Column("name", String(200), nullable=False),
                    Column("enabled", Boolean, default=True),
                    Column("trigger", Text, default="{}"),
                    Column("condition", Text, default="{}"),
                    Column("action", Text, default="{}"),
                    Column("notify", Text, default="{}"),
                    Column("last_run", Float, nullable=True),
                    Column("next_run", Float, nullable=True),
                    Column("state", Text, default="{}"),
                    Column("created", Float, default=now))

runs = Table("automation_runs", md,
             Column("id", Integer, primary_key=True, autoincrement=True),
             Column("automation_id", Integer, nullable=False),
             Column("ts", Float, default=now),
             Column("status", String(12), default="ok"),
             Column("output", Text, default=""),
             Column("duration", Float, default=0))

files = Table("files", md,
              Column("path", String(400), primary_key=True),
              Column("data", LargeBinary, nullable=False),
              Column("size", Integer, default=0),
              Column("updated", Float, default=now))

settings = Table("settings", md,
                 Column("key", String(80), primary_key=True),
                 Column("value", Text, default="null"))

usage = Table("usage", md,
              Column("id", Integer, primary_key=True, autoincrement=True),
              Column("ts", Float, default=now),
              Column("provider", String(30)), Column("model", String(120)),
              Column("ok", Boolean, default=True),
              Column("latency", Float, default=0),
              Column("tokens_in", Integer, default=0), Column("tokens_out", Integer, default=0),
              Column("error", String(300), default=""))

lessons = Table("lessons", md,
                Column("id", Integer, primary_key=True, autoincrement=True),
                Column("ts", Float, default=now),
                Column("text", Text, nullable=False),
                Column("reason", Text, default=""),
                Column("status", String(12), default="proposed"))       # proposed|active|rejected

notifications = Table("notifications", md,
                      Column("id", Integer, primary_key=True, autoincrement=True),
                      Column("ts", Float, default=now),
                      Column("title", String(300), nullable=False),
                      Column("body", Text, default=""),
                      Column("kind", String(12), default="info"),
                      Column("read", Boolean, default=False))

messages = Table("messages", md,
                 Column("id", Integer, primary_key=True, autoincrement=True),
                 Column("session", String(100), index=True),
                 Column("ts", Float, default=now),
                 Column("role", String(12)),
                 Column("content", Text))

feedback = Table("feedback", md,
                 Column("id", Integer, primary_key=True, autoincrement=True),
                 Column("ts", Float, default=now),
                 Column("rating", Integer, default=0),                  # 1 gut, -1 schlecht
                 Column("question", Text, default=""),
                 Column("answer", Text, default=""))


class DB:
    def __init__(self, url: str | None = None, fallback_dir: Path | None = None):
        url = url or os.getenv("DATABASE_URL") or ""
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        self.persistent = bool(url)
        if not url:
            base = fallback_dir or Path(os.getenv("JARVIS_DATA_DIR") or Path.home() / ".jarvis")
            base.mkdir(parents=True, exist_ok=True)
            url = f"sqlite:///{base / 'jarvis.db'}"
        self.kind = "postgres" if url.startswith("postgresql") else "sqlite"
        kwargs: dict[str, Any] = {"pool_pre_ping": True}
        if self.kind == "sqlite":
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            kwargs.update(pool_size=3, max_overflow=2, pool_recycle=280)
        self.engine = create_engine(url, **kwargs)
        self.lock = threading.RLock() if self.kind == "sqlite" else _NoLock()
        md.create_all(self.engine)
        log.info("Datenbank: %s (%s)", self.kind, "dauerhaft" if self.persistent else "nur lokal")

    # ------------------------------------------------------------------ Grundfunktionen
    def q(self, stmt) -> list[dict]:
        with self.lock, self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(stmt)]

    def one(self, stmt) -> dict | None:
        rows = self.q(stmt)
        return rows[0] if rows else None

    def run(self, stmt):
        with self.lock, self.engine.begin() as c:
            return c.execute(stmt)

    def add(self, table: Table, **values) -> int:
        with self.lock, self.engine.begin() as c:
            res = c.execute(insert(table).values(**values))
            return int(res.inserted_primary_key[0]) if res.inserted_primary_key else 0

    def ping(self) -> bool:
        try:
            self.q(select(func.count()).select_from(settings))
            return True
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------ Einstellungen
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.one(select(settings.c.value).where(settings.c.key == key))
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except ValueError:
            return default

    def set_setting(self, key: str, value: Any) -> None:
        data = json.dumps(value)
        with self.lock, self.engine.begin() as c:
            if c.execute(select(settings.c.key).where(settings.c.key == key)).first():
                c.execute(update(settings).where(settings.c.key == key).values(value=data))
            else:
                c.execute(insert(settings).values(key=key, value=data))

    # ------------------------------------------------------------------ Ereignisse / Feed
    def event(self, title: str, kind: str = "info", agent: str = "", detail: str = "", mission_id: int | None = None) -> int:
        return self.add(events, ts=now(), kind=kind, agent=agent, title=title[:300], detail=detail[:4000],
                        mission_id=mission_id)

    def notify(self, title: str, body: str = "", kind: str = "info") -> int:
        return self.add(notifications, ts=now(), title=title[:300], body=body[:4000], kind=kind, read=False)

    # ------------------------------------------------------------------ Dateien spiegeln
    def save_file(self, rel: str, data: bytes) -> None:
        with self.lock, self.engine.begin() as c:
            c.execute(delete(files).where(files.c.path == rel))
            c.execute(insert(files).values(path=rel, data=data, size=len(data), updated=now()))

    def delete_file(self, rel: str) -> None:
        self.run(delete(files).where(or_(files.c.path == rel, files.c.path.like(rel.rstrip("/") + "/%"))))

    def restore_files(self, root: Path, max_bytes: int = 300 * 1024 * 1024) -> int:
        """Schreibt gespeicherte Dateien zurück auf die Platte (nach einem Render-Neustart)."""
        count = total = 0
        with self.lock, self.engine.connect() as c:
            for row in c.execute(select(files.c.path, files.c.data).order_by(files.c.updated.desc())):
                total += len(row.data)
                if total > max_bytes:
                    break
                target = (root / row.path).resolve()
                if not target.is_relative_to(root.resolve()) or target.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(row.data)
                count += 1
        return count

    def used_bytes(self) -> int:
        row = self.one(select(func.coalesce(func.sum(files.c.size), 0).label("n")))
        return int(row["n"]) if row else 0


class _NoLock:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_db: DB | None = None


def get_db() -> DB:
    global _db
    if _db is None:
        from .config import load_config

        _db = DB(fallback_dir=load_config().output_dir.parent)
    return _db


def set_db(db: DB) -> None:
    """Für Tests: eigene Datenbank einsetzen."""
    global _db
    _db = db


def loads(text: str | None, default):
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default
