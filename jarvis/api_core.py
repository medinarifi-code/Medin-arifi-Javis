"""API für das Command Center: Dashboard, Agenten, Gedächtnis, Aufgaben, Projekte, Missionen, Freigaben,
Automatisierungen, Modelle, Benachrichtigungen, Suche, Einstellungen. Alle Werte kommen aus echten Daten."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, or_, select

from . import automations as auto
from .agents import AGENTS, public as agents_public
from .db import events, lessons, memories, messages, missions, notifications, tasks, update, usage
from .memory import KINDS
from .planner import PRIO
from .security import DEFAULT_PERMISSIONS, SENSITIVE
from .server import app, cfg, core, list_files, system
from .tts import VOICES

AGENT_BY_NAME = {v["name"]: k for k, v in AGENTS.items()}
_active_chats: dict[str, dict] = {}   # wird vom Chat gefüllt (welcher Agent arbeitet gerade)


def _bad(e: Exception, code: int = 400):
    raise HTTPException(code, str(e)) from e


# --------------------------------------------------------------------------- Dashboard
def _events(limit: int = 30, after: int = 0, kind: str = "", agent: str = "", q: str = "", mission: int | None = None):
    stmt = select(events)
    if after:
        stmt = stmt.where(events.c.id > after)
    if kind:
        stmt = stmt.where(events.c.kind.in_(kind.split(",")))
    if agent:
        stmt = stmt.where(events.c.agent == agent)
    if mission:
        stmt = stmt.where(events.c.mission_id == mission)
    if q:
        stmt = stmt.where(or_(events.c.title.ilike(f"%{q}%"), events.c.detail.ilike(f"%{q}%")))
    return core().db.q(stmt.order_by(desc(events.c.id)).limit(min(limit, 500)))


def _usage_timeline(hours: int = 24) -> list[dict]:
    """Anfragen pro Stunde (erfolgreich/fehlgeschlagen) und mittlere Antwortzeit – für das Diagramm."""
    start = int(time.time() // 3600 * 3600) - (hours - 1) * 3600
    buckets = [{"t": start + i * 3600, "ok": 0, "err": 0, "lat": 0.0} for i in range(hours)]
    for r in core().db.q(select(usage.c.ts, usage.c.ok, usage.c.latency).where(usage.c.ts >= start)):
        i = int((r["ts"] - start) // 3600)
        if 0 <= i < hours:
            b = buckets[i]
            b["ok" if r["ok"] else "err"] += 1
            b["lat"] += r["latency"] or 0
    for b in buckets:
        n = b["ok"] + b["err"]
        b["lat"] = round(b["lat"] / n, 2) if n else None
    return buckets


def _agent_activity() -> list[dict]:
    c = core()
    since = time.time() - 86400
    rows = c.db.q(select(events.c.agent, func.count().label("n"), func.max(events.c.ts).label("last"))
                  .where(events.c.ts >= since).group_by(events.c.agent))
    stats = {AGENT_BY_NAME.get(r["agent"], r["agent"]): r for r in rows}
    working: dict[str, list[str]] = {}
    for sid, info in list(_active_chats.items()):
        working.setdefault(info["agent"], []).append("Chat: " + info["text"][:60])
    for m in c.missions.list(10):
        if m["status"] in ("running", "planning", "waiting") and m["plan"]:
            step = m["plan"][min(m["step"], len(m["plan"]) - 1)]
            working.setdefault(step.get("agent", "master") if m["status"] != "planning" else "master", []).append(
                f"Mission #{m['id']}: {step.get('title', '')[:60]}")
    out = []
    for a in agents_public():
        s = stats.get(a["id"]) or {}
        out.append({**a, "actions_24h": s.get("n", 0), "last_active": s.get("last"),
                    "working": working.get(a["id"], []), "status": "aktiv" if working.get(a["id"]) else "bereit"})
    return out


@app.get("/api/dashboard")
def dashboard():
    c = core()
    today = c.tasks.day_overview("heute")
    models = [p for p in c.registry.status() if p["configured"]]
    return {
        "time": time.time(), "user": cfg.user_name,
        "counts": c.counts(), "status": c.system_status(), "system": system(),
        "events": _events(25), "agents": _agent_activity(),
        "today": {"date": today["date"], "scheduled": today["scheduled"], "unscheduled": today["unscheduled"][:12],
                  "conflicts": today["conflicts"], "free": today["free_slots"]},
        "overdue": c.tasks.overdue()[:10],
        "missions": c.missions.list(6), "approvals": c.security.list("pending", 20),
        "models": models, "usage": _usage_timeline(24), "memory": c.memory.stats(),
        "automations": [{"id": a["id"], "name": a["name"], "enabled": a["enabled"], "next_run": a["next_run"],
                         "last_run": a["last_run"]} for a in c.automations.list()],
        "files": list_files(6),
    }


@app.get("/api/events")
def get_events(limit: int = 50, after: int = 0, kind: str = "", agent: str = "", q: str = "", mission: int | None = None):
    return _events(limit, after, kind, agent, q, mission)


@app.get("/api/agents")
def get_agents():
    return _agent_activity()


# --------------------------------------------------------------------------- Gedächtnis
class MemoryBody(BaseModel):
    content: str = Field(min_length=1, max_length=5000)
    kind: str = "fact"
    title: str = Field(default="", max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=20)
    project: str = Field(default="", max_length=120)
    importance: int = Field(default=2, ge=1, le=3)


@app.get("/api/memory")
def memory_list(kind: str = "", project: str = "", q: str = "", limit: int = 300):
    c = core()
    rows = c.memory.search(q, limit=50) if q else c.memory.list(kind or None, project or None, limit)
    return {"items": rows, "stats": c.memory.stats(), "kinds": KINDS, "projects": c.memory.projects()}


@app.post("/api/memory")
def memory_add(body: MemoryBody):
    mid, state = core().memory.add(body.content, body.kind, body.title, body.tags, body.project, body.importance, source="user")
    core().db.event(f"Gedächtnis manuell {('gespeichert', 'aktualisiert')[state == 'updated']}: {body.title or body.content[:60]}",
                    kind="ok", agent="Du")
    return {"id": mid, "state": state}


@app.put("/api/memory/{mid}")
def memory_update(mid: int, body: MemoryBody):
    ok = core().memory.update(mid, content=body.content, kind=body.kind, title=body.title, tags=body.tags,
                              project=body.project, importance=body.importance)
    if not ok:
        raise HTTPException(404, "Nicht gefunden")
    return {"ok": True}


@app.delete("/api/memory/{mid}")
def memory_delete(mid: int):
    """Du selbst löschst direkt (deine Kontrolle). Agenten brauchen dafür eine Freigabe."""
    m = core().memory.get(mid)
    if not m or not core().memory.delete(mid):
        raise HTTPException(404, "Nicht gefunden")
    core().db.event(f"Erinnerung gelöscht: {m['title'][:80]}", kind="security", agent="Du")
    return {"ok": True}


@app.get("/api/memory/graph")
def memory_graph():
    """Knowledge Graph: Erinnerungen, Projekte, Tags, Personen + Aufgaben und Missionen der Projekte."""
    c = core()
    g = c.memory.graph()
    ids = {n["id"] for n in g["nodes"]}
    for t in c.tasks.list(None, limit=150):
        if t["project"]:
            key = f"p:{t['project'].lower()}"
            if key not in ids:
                g["nodes"].append({"id": key, "label": t["project"][:60], "group": "projectnode"})
                ids.add(key)
            g["nodes"].append({"id": f"task{t['id']}", "label": t["title"][:60], "group": "task",
                               "content": f"{PRIO.get(t['priority'])} · {t['status']} · {t['due'] or 'ohne Frist'}"})
            g["edges"].append([f"task{t['id']}", key])
    return g


# --------------------------------------------------------------------------- Aufgaben & Tagesplanung
class TaskBody(BaseModel):
    title: str = Field(default="", max_length=300)
    due: str = Field(default="", max_length=32)
    priority: int = Field(default=2, ge=1, le=3)
    project: str = Field(default="", max_length=120)
    notes: str = Field(default="", max_length=5000)
    remind_at: str = Field(default="", max_length=32)
    start: str = Field(default="", max_length=32)
    duration: int = Field(default=0, ge=0, le=1440)
    status: str | None = None


@app.get("/api/tasks")
def task_list(status: str = "open", project: str = "", day: str = ""):
    return core().tasks.list(None if status == "all" else status, project or None, day or None)


@app.get("/api/tasks/day")
def task_day(day: str = "heute"):
    return core().tasks.day_overview(day)


@app.post("/api/tasks")
def task_add(body: TaskBody):
    try:
        tid = core().tasks.add(body.title, body.due, body.priority, body.project, body.notes, body.remind_at,
                               body.start, body.duration)
    except ValueError as e:
        _bad(e)
    core().db.event(f"Aufgabe angelegt: {body.title}", kind="ok", agent="Du")
    return {"id": tid}


@app.put("/api/tasks/{tid}")
def task_update(tid: int, body: TaskBody):
    fields = body.model_dump(exclude_unset=True)
    if not core().tasks.update(tid, **fields):
        raise HTTPException(404, "Nicht gefunden oder nichts geändert")
    if fields.get("status") == "done":
        core().db.event(f"Aufgabe erledigt: #{tid}", kind="ok", agent="Du")
    return {"ok": True}


@app.delete("/api/tasks/{tid}")
def task_delete(tid: int):
    if not core().tasks.delete(tid):
        raise HTTPException(404, "Nicht gefunden")
    return {"ok": True}


# --------------------------------------------------------------------------- Projekte (Project Brain / Digital Twin)
@app.get("/api/projects")
def project_list():
    return core().projects()


@app.get("/api/projects/{name}")
def project_detail(name: str):
    c = core()
    return {**c.project_summary(name), "tasks": c.tasks.list(None, project=name),
            "memories": c.memory.list(project=name, limit=100)}


@app.post("/api/projects/{name}/report")
def project_report(name: str):
    try:
        return {"text": core().project_report(name)}
    except RuntimeError as e:
        _bad(e, 503)


# --------------------------------------------------------------------------- Missionen (Autonomous Mode)
class GoalBody(BaseModel):
    goal: str = Field(min_length=5, max_length=4000)


@app.get("/api/missions")
def mission_list():
    return core().missions.list(50)


@app.post("/api/missions")
def mission_start(body: GoalBody):
    c = core()
    if not c.security.allowed("autonomous"):
        raise HTTPException(403, "Autonomer Modus ist im Security Center ausgeschaltet.")
    try:
        return {"id": c.missions.start(body.goal)}
    except ValueError as e:
        _bad(e)


@app.post("/api/missions/simulate")
def mission_simulate(body: GoalBody):
    """Simulation Mode: plant das Ziel, führt aber nichts aus. Zeigt Schritte, Agenten und nötige Freigaben."""
    c = core()
    try:
        steps = c.missions.plan(body.goal)
    except Exception as e:  # noqa: BLE001
        _bad(e, 503)
    sensitive_words = {"lösch": "Datei/Daten löschen", "mail": "E-Mail senden", "sende": "Nachricht senden",
                       "kauf": "Kauf (nicht möglich)", "zahl": "Zahlung (nicht möglich)", "zeitplan": "Zeitplan eintragen"}
    risks = sorted({label for s in steps for w, label in sensitive_words.items()
                    if w in (s.get("title", "") + s.get("instructions", "")).lower()})
    c.db.event(f"Simulation: {body.goal[:100]}", kind="agent", agent="Master Agent", detail=f"{len(steps)} Schritte")
    return {"steps": steps, "approvals": risks,
            "agents": sorted({s.get("agent", "master") for s in steps}),
            "note": "Nur Planung – es wurde nichts ausgeführt."}


@app.get("/api/missions/{mid}")
def mission_get(mid: int):
    m = core().missions.get(mid)
    if not m:
        raise HTTPException(404, "Nicht gefunden")
    m["approvals"] = [a for a in core().security.list(None, 200) if a["mission_id"] == mid]
    return m


@app.post("/api/missions/{mid}/{action}")
def mission_control(mid: int, action: str):
    if action not in ("pause", "resume", "cancel"):
        raise HTTPException(400, "Unbekannte Aktion")
    try:
        return core().missions.control(mid, action)
    except ValueError as e:
        _bad(e)


# --------------------------------------------------------------------------- Security Center
class DecideBody(BaseModel):
    approve: bool


@app.get("/api/approvals")
def approval_list(status: str = ""):
    return core().security.list(status or None, 100)


@app.post("/api/approvals/{aid}")
def approval_decide(aid: int, body: DecideBody):
    try:
        return core().security.decide(aid, body.approve)
    except ValueError as e:
        _bad(e)


PERMISSION_LABELS = {
    "mail_send": "E-Mails senden (nur wenn du es in deiner Nachricht verlangst)",
    "file_delete": "Dateien löschen (immer mit Freigabe)",
    "autonomous": "Autonome Missionen starten",
    "proactive": "Proaktive Hinweise (Fristen, Fehler, offene Freigaben)",
    "auto_memory": "Wichtiges aus Gesprächen automatisch merken",
    "web_fetch": "Webseiten abrufen",
    "notify_email": "Benachrichtigungen zusätzlich per E-Mail an dich",
}


@app.get("/api/permissions")
def permission_list():
    perms = core().security.permissions()
    return [{"key": k, "label": PERMISSION_LABELS.get(k, k), "value": perms[k], "default": DEFAULT_PERMISSIONS[k]}
            for k in perms]


class PermBody(BaseModel):
    key: str
    value: bool


@app.put("/api/permissions")
def permission_set(body: PermBody):
    try:
        core().security.set_permission(body.key, body.value)
    except KeyError:
        _bad(ValueError("Unbekannte Berechtigung"))
    return permission_list()


@app.get("/api/security")
def security_overview():
    c = core()
    return {"permissions": permission_list(), "approvals": c.security.list(None, 50),
            "audit": _events(80, kind="security,err"), "sensitive": SENSITIVE,
            "password": bool(cfg.web_password),
            "keys": {p["env"]: p["configured"] for p in c.registry.status()},
            "database": {"kind": c.db.kind, "persistent": c.db.persistent}}


# --------------------------------------------------------------------------- Automation Center
class AutomationBody(BaseModel):
    name: str = Field(default="", max_length=200)
    enabled: bool = True
    trigger: dict
    condition: dict = Field(default_factory=lambda: {"type": "none"})
    action: dict
    notify: dict = Field(default_factory=lambda: {"inapp": True, "email": False})


@app.get("/api/automations")
def automation_list():
    c = core()
    return {"items": c.automations.list(), "runs": c.automations.runs(None, 40), "scheduler": c.automations.alive(),
            "meta": {"triggers": auto.TRIGGERS, "conditions": auto.CONDITIONS, "actions": auto.ACTIONS,
                     "templates": auto.TEMPLATES, "agents": {k: v["name"] for k, v in AGENTS.items()}}}


@app.post("/api/automations")
def automation_add(body: AutomationBody):
    try:
        return {"id": core().automations.save(body.model_dump())}
    except ValueError as e:
        _bad(e)


@app.put("/api/automations/{aid}")
def automation_update(aid: int, body: AutomationBody):
    if not core().automations.get(aid):
        raise HTTPException(404, "Nicht gefunden")
    try:
        core().automations.save(body.model_dump(), aid)
    except ValueError as e:
        _bad(e)
    return {"ok": True}


@app.delete("/api/automations/{aid}")
def automation_delete(aid: int):
    core().automations.delete(aid)
    return {"ok": True}


@app.post("/api/automations/{aid}/run")
async def automation_run(aid: int):
    try:
        return await asyncio.to_thread(core().automations.run, aid, True)
    except ValueError as e:
        _bad(e, 404)


@app.get("/api/automations/{aid}/runs")
def automation_runs(aid: int):
    return core().automations.runs(aid, 50)


# --------------------------------------------------------------------------- Modelle
@app.get("/api/models")
def model_list():
    from . import providers as prov

    c = core()
    return {"providers": c.registry.status(), "order": {t: c.registry.order(t) for t in prov.TIERS},
            "tiers": prov.TIERS, "usage": _usage_timeline(24),
            "recent": c.db.q(select(usage).order_by(desc(usage.c.id)).limit(30))}


class ProviderBody(BaseModel):
    provider: str


@app.post("/api/models/test")
async def model_test(body: ProviderBody):
    from .providers import PROVIDERS

    if body.provider not in PROVIDERS:
        raise HTTPException(400, "Unbekannter Anbieter")
    return await asyncio.to_thread(core().registry.test, body.provider)


class OrderBody(BaseModel):
    tier: str
    order: list[str]


@app.put("/api/models/order")
def model_order(body: OrderBody):
    from .providers import PROVIDERS, TIERS

    if body.tier not in TIERS or any(p not in PROVIDERS for p in body.order):
        raise HTTPException(400, "Ungültige Reihenfolge")
    c = core()
    saved = c.db.get_setting("model_order", {}) or {}
    saved[body.tier] = body.order
    c.db.set_setting("model_order", saved)
    c.db.event(f"Modell-Reihenfolge für „{body.tier}“ geändert", kind="info", agent="Du", detail=" → ".join(body.order))
    return {"ok": True}


# --------------------------------------------------------------------------- Second Opinion & Self-Improvement
class QuestionBody(BaseModel):
    question: str = Field(min_length=3, max_length=8000)


@app.post("/api/second-opinion")
async def second_opinion(body: QuestionBody):
    try:
        return await asyncio.to_thread(core().second_opinion, body.question)
    except (RuntimeError, ValueError) as e:
        _bad(e, 503)


@app.get("/api/lessons")
def lesson_list():
    return core().db.q(select(lessons).order_by(desc(lessons.c.id)).limit(100))


class LessonBody(BaseModel):
    status: str


@app.post("/api/lessons/analyze")
async def lesson_analyze():
    try:
        return await asyncio.to_thread(core().self_improve)
    except RuntimeError as e:
        _bad(e, 503)


@app.post("/api/lessons/{lid}")
def lesson_set(lid: int, body: LessonBody):
    if body.status not in ("active", "rejected", "proposed"):
        raise HTTPException(400, "Ungültig")
    core().db.run(update(lessons).where(lessons.c.id == lid).values(status=body.status))
    core().db.event(f"Verbesserungsregel #{lid}: {body.status}", kind="info", agent="Du")
    return {"ok": True}


# --------------------------------------------------------------------------- Web-Leser (Browser)
class UrlBody(BaseModel):
    url: str = Field(min_length=4, max_length=2000)


@app.post("/api/browse")
async def browse(body: UrlBody):
    from .tools.brain import fetch_url
    from .tools.files import ToolError

    if not core().security.allowed("web_fetch"):
        raise HTTPException(403, "Webseiten abrufen ist im Security Center ausgeschaltet.")
    try:
        text = await asyncio.to_thread(fetch_url, body.url, 30000)
    except ToolError as e:
        _bad(e)
    core().db.event(f"Webseite gelesen: {body.url[:120]}", kind="info", agent="Browser Agent")
    return {"text": text}


# --------------------------------------------------------------------------- Benachrichtigungen
@app.get("/api/notifications")
def notification_list(limit: int = 50):
    c = core()
    return {"items": c.db.q(select(notifications).order_by(desc(notifications.c.id)).limit(limit)),
            "unread": c.counts()["notifications_unread"]}


class IdsBody(BaseModel):
    ids: list[int] = Field(default_factory=list)


@app.post("/api/notifications/read")
def notification_read(body: IdsBody):
    from .core import mark_notifications_read

    mark_notifications_read(core().db, body.ids or None)
    return {"ok": True}


# --------------------------------------------------------------------------- Globale Suche
@app.get("/api/search")
def search(q: str = ""):
    q = q.strip()
    if len(q) < 2:
        return {"results": []}
    c = core()
    like = f"%{q}%"
    out = []
    for m in c.memory.search(q, 8):
        out.append({"type": "memory", "id": m["id"], "title": m["title"], "text": m["content"][:160], "page": "memory"})
    for t in c.db.q(select(tasks).where(or_(tasks.c.title.ilike(like), tasks.c.notes.ilike(like))).limit(8)):
        out.append({"type": "task", "id": t["id"], "title": t["title"], "text": t["due"] or t["status"], "page": "projects"})
    for m in c.db.q(select(missions).where(missions.c.goal.ilike(like)).order_by(desc(missions.c.id)).limit(5)):
        out.append({"type": "mission", "id": m["id"], "title": f"Mission #{m['id']}", "text": m["goal"][:160], "page": "agents"})
    for f in list_files(200):
        if q.lower() in f["path"].lower():
            out.append({"type": "file", "id": f["path"], "title": f["name"], "text": f["path"], "page": "files", "url": f["url"]})
            if sum(1 for o in out if o["type"] == "file") >= 8:
                break
    for a in c.automations.list():
        if q.lower() in a["name"].lower():
            out.append({"type": "automation", "id": a["id"], "title": a["name"], "text": "Automatisierung", "page": "automations"})
    for m in c.db.q(select(messages).where(messages.c.content.ilike(like)).order_by(desc(messages.c.id)).limit(8)):
        out.append({"type": "chat", "id": m["id"], "title": ("Du" if m["role"] == "user" else "JARVIS") + " · " +
                    datetime.fromtimestamp(m["ts"]).strftime("%d.%m. %H:%M"), "text": m["content"][:160], "page": "chat"})
    for e in _events(8, q=q):
        out.append({"type": "event", "id": e["id"], "title": e["title"], "text": e["agent"], "page": "monitor"})
    return {"results": out}


# --------------------------------------------------------------------------- Einstellungen
DEFAULT_UI = {"voice": "de-DE-ConradNeural", "speak_answers": True, "wake_word": False, "continuous": False,
              "widgets": ["core", "status", "agents", "timeline", "feed", "llm", "monitor", "memory", "commands"],
              "quick_commands": ["Plane meinen Tag morgen", "Was steht heute an?", "Erstelle einen Tagesbericht",
                                 "Zeig meine offenen Aufgaben", "Recherchiere die neuesten KI-News"]}


@app.get("/api/settings")
def settings_get():
    saved = core().db.get_setting("ui", {}) or {}
    return {"ui": {**DEFAULT_UI, **saved}, "voices": VOICES, "user": cfg.user_name,
            "agents": {k: v["name"] for k, v in AGENTS.items()}}


class SettingsBody(BaseModel):
    ui: dict


@app.put("/api/settings")
def settings_put(body: SettingsBody):
    clean = {k: v for k, v in body.ui.items() if k in DEFAULT_UI}
    if "voice" in clean and clean["voice"] not in VOICES:
        raise HTTPException(400, "Unbekannte Stimme")
    c = core()
    c.db.set_setting("ui", {**(c.db.get_setting("ui", {}) or {}), **clean})
    return settings_get()


# --------------------------------------------------------------------------- Dateien hochladen (File Manager)
class UploadBody(BaseModel):
    name: str = Field(max_length=200)
    data: str = Field(max_length=12_000_000)


@app.post("/api/files/upload")
def file_upload(body: UploadBody):
    from .uploads import UploadError, save_attachment

    try:
        up = save_attachment(cfg.output_dir, body.name, body.data)
    except UploadError as e:
        _bad(e)
    core().persist_file(cfg.output_dir / up.path)
    core().db.event(f"Datei hochgeladen: {up.name}", kind="ok", agent="Du")
    return {"path": up.path}


@app.post("/api/files/sort")
async def file_sort():
    return {"text": await asyncio.to_thread(core().automations.categorize_files)}

