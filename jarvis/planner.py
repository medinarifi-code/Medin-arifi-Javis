"""Aufgaben, Tagesplanung, Erinnerungen (Personal Assistant Agent)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import and_, asc, delete, or_, select, update

from .db import DB, now, tasks

PRIO = {1: "hoch", 2: "normal", 3: "niedrig"}
DAY_START, DAY_END = 8, 20  # Planungsfenster


def parse_when(value: str | None) -> datetime | None:
    if not value:
        return None
    value = str(value).strip().replace("T", " ")
    low = value.lower()
    for word, add in (("übermorgen", 2), ("uebermorgen", 2), ("morgen", 1), ("heute", 0), ("tomorrow", 1), ("today", 0)):
        if low.startswith(word):
            rest = low[len(word):].strip().removeprefix("um").strip().removesuffix("uhr").strip()
            day = date.today() + timedelta(days=add)
            if not rest:
                return datetime.combine(day, datetime.min.time())
            try:
                hh, _, mm = rest.replace(".", ":").partition(":")
                return datetime.combine(day, datetime.min.time()).replace(hour=int(hh), minute=int(mm or 0))
            except ValueError:
                return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(value[:19], fmt)
        except ValueError:
            continue
    return None


def resolve_day(value: str | None) -> date:
    v = (value or "").strip().lower()
    today = date.today()
    if v in ("", "heute", "today"):
        return today
    if v in ("morgen", "tomorrow"):
        return today + timedelta(days=1)
    if v in ("übermorgen", "uebermorgen"):
        return today + timedelta(days=2)
    dt = parse_when(value)
    return dt.date() if dt else today


def norm_when(value: str | None) -> str:
    """'morgen 9:00' → '2026-11-18 09:00'; Unverständliches bleibt wie es ist."""
    if not value:
        return ""
    dt = parse_when(value)
    if dt is None:
        return str(value)[:32]
    has_time = dt.hour or dt.minute or ":" in str(value)
    return dt.strftime("%Y-%m-%d %H:%M" if has_time else "%Y-%m-%d")


class TaskStore:
    def __init__(self, db: DB):
        self.db = db

    def add(self, title: str, due: str = "", priority: int = 2, project: str = "", notes: str = "",
            remind_at: str = "", start: str = "", duration: int = 0) -> int:
        if not title or not title.strip():
            raise ValueError("Aufgabe braucht einen Titel")
        r = parse_when(remind_at)
        return self.db.add(tasks, title=title.strip()[:300], due=norm_when(due), priority=max(1, min(3, int(priority or 2))),
                           project=(project or "")[:120], notes=notes or "", start=norm_when(start),
                           duration=int(duration or 0), remind_at=r.timestamp() if r else None, reminded=False,
                           status="open", created=now(), updated=now())

    def get(self, tid: int) -> dict | None:
        return self.db.one(select(tasks).where(tasks.c.id == tid))

    def update(self, tid: int, **fields) -> bool:
        allowed = {k: v for k, v in fields.items()
                   if k in ("title", "notes", "project", "priority", "status", "due", "start", "duration") and v is not None}
        if "remind_at" in fields and fields["remind_at"] is not None:
            r = parse_when(fields["remind_at"])
            allowed.update(remind_at=r.timestamp() if r else None, reminded=False)
        for k in ("due", "start"):
            if k in allowed:
                allowed[k] = norm_when(allowed[k])
        if not allowed:
            return False
        allowed["updated"] = now()
        return self.db.run(update(tasks).where(tasks.c.id == tid).values(**allowed)).rowcount > 0

    def delete(self, tid: int) -> bool:
        return self.db.run(delete(tasks).where(tasks.c.id == tid)).rowcount > 0

    def list(self, status: str | None = "open", project: str | None = None, day: str | None = None, limit: int = 200) -> list[dict]:
        stmt = select(tasks)
        if status == "open":
            stmt = stmt.where(tasks.c.status != "done")
        elif status:
            stmt = stmt.where(tasks.c.status == status)
        if project:
            stmt = stmt.where(tasks.c.project == project)
        if day:
            d = resolve_day(day).isoformat()
            stmt = stmt.where(or_(tasks.c.due.like(f"{d}%"), tasks.c.start.like(f"{d}%")))
        rows = self.db.q(stmt.order_by(asc(tasks.c.priority), asc(tasks.c.due), asc(tasks.c.id)).limit(limit))
        return rows

    def overdue(self) -> list[dict]:
        today = date.today().isoformat()
        return [t for t in self.list("open") if t["due"] and t["due"][:10] < today]

    def due_soon(self, hours: int = 24) -> list[dict]:
        limit = datetime.now() + timedelta(hours=hours)
        out = []
        for t in self.list("open"):
            d = parse_when(t["due"])
            if d and datetime.now() <= d <= limit:
                out.append(t)
        return out

    def due_reminders(self) -> list[dict]:
        rows = self.db.q(select(tasks).where(and_(tasks.c.remind_at != None, tasks.c.remind_at <= now(),  # noqa: E711
                                                  tasks.c.reminded == False, tasks.c.status != "done")))  # noqa: E712
        for r in rows:
            self.db.run(update(tasks).where(tasks.c.id == r["id"]).values(reminded=True))
        return rows

    # ------------------------------------------------------------------ Tagesplanung
    def day_overview(self, day: str | None = None) -> dict:
        """Echte Grundlage für einen Tagesplan: Aufgaben, Konflikte, freie Zeitfenster."""
        d = resolve_day(day)
        open_tasks = self.list("open")
        scheduled = sorted([t for t in open_tasks if t["start"][:10] == d.isoformat()], key=lambda t: t["start"])
        candidates = [t for t in open_tasks if t not in scheduled and (not t["due"] or t["due"][:10] <= d.isoformat())]
        conflicts, busy = [], []
        for t in scheduled:
            s = parse_when(t["start"])
            if not s:
                continue
            e = s + timedelta(minutes=t["duration"] or 30)
            for (bs, be, bt) in busy:
                if s < be and bs < e:
                    conflicts.append(f"„{bt['title']}“ und „{t['title']}“ überschneiden sich")
            busy.append((s, e, t))
        free, cursor = [], datetime.combine(d, datetime.min.time()).replace(hour=DAY_START)
        end_day = cursor.replace(hour=DAY_END)
        if d == date.today():
            cursor = max(cursor, datetime.now().replace(second=0, microsecond=0))
        for bs, be, _ in sorted(busy, key=lambda x: x[0]):
            if bs > cursor:
                free.append((cursor.strftime("%H:%M"), bs.strftime("%H:%M")))
            cursor = max(cursor, be)
        if cursor < end_day:
            free.append((cursor.strftime("%H:%M"), end_day.strftime("%H:%M")))
        return {"date": d.isoformat(), "scheduled": scheduled, "unscheduled": candidates, "conflicts": conflicts,
                "free_slots": free}

    def apply_schedule(self, items: list[dict]) -> list[int]:
        """Trägt einen (freigegebenen) Zeitplan ein: bestehende Aufgaben bekommen Startzeit, neue werden angelegt."""
        done = []
        for it in items:
            start = parse_when(it.get("start"))
            if not start:
                continue
            dur = int(it.get("duration") or 30)
            remind = (start - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M")
            if it.get("task_id"):
                tid = int(it["task_id"])
                if self.update(tid, start=start.strftime("%Y-%m-%d %H:%M"), duration=dur, remind_at=remind):
                    done.append(tid)
            elif it.get("title"):
                done.append(self.add(it["title"], start=start.strftime("%Y-%m-%d %H:%M"), duration=dur,
                                     priority=int(it.get("priority") or 2), remind_at=remind,
                                     due=start.strftime("%Y-%m-%d")))
        return done


def fmt_task(t: dict) -> str:
    parts = [f"#{t['id']} {t['title']}", f"Priorität {PRIO.get(t['priority'], t['priority'])}", f"Status {t['status']}"]
    if t["due"]:
        parts.append(f"fällig {t['due']}")
    if t["start"]:
        parts.append(f"geplant {t['start']} ({t['duration'] or 30} Min)")
    if t["project"]:
        parts.append(f"Projekt {t['project']}")
    return " | ".join(parts)
