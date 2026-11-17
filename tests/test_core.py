"""Tests für Kern: Gedächtnis, Aufgaben, Freigaben, Missionen, Automatisierungen, Routing und die Command-Center-API."""

import json
import os
import time
from datetime import date, datetime, timedelta
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from jarvis import server
from jarvis.agents import route
from jarvis.automations import next_run
from jarvis.config import load_config
from jarvis.core import JarvisCore
from jarvis.db import DB
from jarvis.missions import check_files
from jarvis.planner import norm_when
from jarvis.providers import Registry, classify
from jarvis.tools import ToolContext, execute


def text_chunk(t):
    return NS(choices=[NS(delta=NS(content=t, tool_calls=None))])


def tool_chunk(index, id=None, name=None, args=None):
    tc = NS(index=index, id=id, function=NS(name=name, arguments=args))
    return NS(choices=[NS(delta=NS(content=None, tool_calls=[tc]))])


class ScriptLLM:
    """Fake-KI, die je nach Anfrage passend antwortet (Planung, Gedächtnis, Agenten-Schritt mit Werkzeug)."""

    def __init__(self):
        self.calls = []
        self.chat = NS(completions=self)

    def create(self, **kw):
        self.calls.append(kw)
        msgs = kw["messages"]
        last = msgs[-1]
        text = str(last.get("content") or "")
        if "Zerlege das Ziel" in text:
            plan = {"steps": [{"title": "Webseite bauen", "agent": "coding", "instructions": "Baue index.html"},
                              {"title": "Bericht", "agent": "research", "instructions": "Schreibe Bericht"}]}
            return iter([text_chunk(json.dumps(plan))])
        if "Memory Agent von JARVIS" in text:
            return iter([text_chunk('{"memories": [{"kind": "preference", "title": "Kaffee", '
                                    '"content": "Der Nutzer trinkt gerne schwarzen Kaffee.", "tags": ["essen"]}]}')])
        if "Prüfe die Ergebnisse dieser Mission" in text:
            return iter([text_chunk("Alles erledigt.")])
        if last["role"] == "tool":
            return iter([text_chunk("Schritt erledigt.")])
        if kw.get("tools") and "Baue index.html" in text:
            args = json.dumps({"filename": "site/index.html", "content": "<html><body><link href='style.css'></body></html>"})
            return iter([tool_chunk(0, id="t1", name="create_file", args=args)])
        return iter([text_chunk("OK: " + text[:40])])


@pytest.fixture
def jc(tmp_path):
    cfg = load_config()
    cfg.output_dir = tmp_path / "out"
    cfg.output_dir.mkdir()
    cfg.groq_api_key, cfg.groq_model = "gsk_fake", None
    pg = os.getenv("JARVIS_TEST_PG")   # optional: dieselben Tests gegen echtes Postgres (wie Neon)
    if pg:
        from jarvis.db import md

        db = DB(url=pg)
        md.drop_all(db.engine)
        md.create_all(db.engine)
    else:
        db = DB(fallback_dir=tmp_path / "db")
    llm = ScriptLLM()
    c = JarvisCore(cfg, db, Registry(cfg, db, {"groq": llm}))
    c.llm = llm
    return c


def wait_for(fn, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.05)
    return False


# ---------------------------------------------------------------- Gedächtnis
def test_memory_dedupe_search_graph(jc):
    mid, state = jc.memory.add("Ich arbeite an dem Projekt Solar-App mit Flutter.", "project", tags=["flutter"], project="Solar-App")
    assert state == "new"
    mid2, state2 = jc.memory.add("Ich arbeite an dem Projekt Solar-App mit Flutter!", "project", project="Solar-App")
    assert mid2 == mid and state2 in ("duplicate", "updated")
    jc.memory.add("Lieblingsfarbe ist Blau", "preference")
    assert jc.memory.search("Flutter")[0]["id"] == mid
    assert "Blau" in jc.memory.context_for("egal")
    g = jc.memory.graph()
    assert any(n["group"] == "projectnode" for n in g["nodes"]) and g["edges"]
    assert jc.memory.update(mid, importance=3) and jc.memory.get(mid)["importance"] == 3
    assert jc.memory.delete(mid) and jc.memory.get(mid) is None


def test_memory_extract_with_llm(jc):
    saved = jc.memory.extract(jc.registry, "Ich trinke gerne schwarzen Kaffee", "Notiert.")
    assert saved and "Kaffee" in jc.memory.list()[0]["content"]


# ---------------------------------------------------------------- Aufgaben & Planung
def test_norm_when_and_day_overview(jc):
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert norm_when("morgen 9:30") == f"{tomorrow} 09:30"
    assert norm_when("morgen") == tomorrow
    assert norm_when("irgendwann") == "irgendwann"
    jc.tasks.add("Meeting", start="morgen 10:00", duration=60)
    jc.tasks.add("Arzt", start="morgen 10:30", duration=30)
    jc.tasks.add("Steuer", due="morgen", priority=1)
    o = jc.tasks.day_overview("morgen")
    assert len(o["scheduled"]) == 2 and o["conflicts"]
    assert any(t["title"] == "Steuer" for t in o["unscheduled"])
    assert o["free_slots"]


def test_schedule_needs_approval(jc):
    tid = jc.tasks.add("Präsentation üben", priority=1)
    ctx = ToolContext(output_dir=jc.cfg.output_dir, core=jc, on_event=lambda e: None)
    out = execute("propose_schedule", {"day": "morgen", "items": [{"task_id": tid, "title": "Präsentation üben",
                                                                    "start": "09:00", "duration": 45}]}, ctx)
    assert "Freigeben" in out
    assert jc.tasks.get(tid)["start"] == ""          # noch nicht eingetragen
    pending = jc.security.list("pending")
    assert len(pending) == 1 and jc.counts()["approvals_pending"] == 1
    res = jc.security.decide(pending[0]["id"], True)
    assert res["status"] == "done"
    assert jc.tasks.get(tid)["start"].endswith("09:00")
    with pytest.raises(ValueError):
        jc.security.decide(pending[0]["id"], True)    # nicht doppelt ausführbar


def test_delete_file_denied_keeps_file(jc):
    (jc.cfg.output_dir / "wichtig.txt").write_text("x")
    ctx = ToolContext(output_dir=jc.cfg.output_dir, core=jc, on_event=lambda e: None)
    execute("delete_file", {"path": "wichtig.txt"}, ctx)
    aid = jc.security.list("pending")[0]["id"]
    assert jc.security.decide(aid, False)["status"] == "denied"
    assert (jc.cfg.output_dir / "wichtig.txt").exists()


def test_mail_send_permission_blocks(jc):
    from jarvis.tools import ToolError

    jc.security.set_permission("mail_send", False)
    ctx = ToolContext(output_dir=jc.cfg.output_dir, core=jc, mailbox=object(), user_message="schick die mail")
    with pytest.raises(ToolError):
        execute("send_email", {"to": "a@b.de", "subject": "x", "text": "y"}, ctx)


# ---------------------------------------------------------------- Missionen (autonom)
def test_mission_runs_plans_builds_and_reviews(jc):
    mid = jc.missions.start("Baue eine kleine Webseite für mein Café")
    assert wait_for(lambda: jc.missions.get(mid)["status"] in ("done", "failed"))
    m = jc.missions.get(mid)
    assert m["status"] == "done", m["result"]
    assert [s["status"] for s in m["plan"]] == ["done", "done"]
    assert m["files"] and m["files"][0]["name"] == "index.html"
    assert (jc.cfg.output_dir / "site" / "index.html").exists()
    titles = [e["title"] for e in m["events"]]
    assert any("Plan mit 2 Schritten" in t for t in titles)
    assert any("Prüfung" in t for t in titles)          # style.css fehlt → Warnung
    assert m["result"] == "Alles erledigt."
    # Datei wurde auch in der Datenbank gesichert
    assert jc.db.used_bytes() > 0


def test_mission_cancel(jc):
    jc.missions.plan = lambda goal: (time.sleep(0.3), [{"title": "x", "agent": "research", "instructions": "x",
                                                        "status": "pending", "result": ""}])[1]
    mid = jc.missions.start("Etwas Langes erledigen")
    jc.missions.control(mid, "cancel")
    time.sleep(0.6)
    assert jc.missions.get(mid)["status"] == "cancelled"


def test_check_files(tmp_path):
    (tmp_path / "a.py").write_text("def x(:\n")
    (tmp_path / "b.json").write_text("{nope")
    probs = check_files(tmp_path, [{"name": "a.py", "url": "/files/a.py"}, {"name": "b.json", "url": "/files/b.json"},
                                   {"name": "c.txt", "url": "/files/c.txt"}])
    assert len(probs) == 3


# ---------------------------------------------------------------- Automatisierungen
def test_next_run():
    base = datetime(2026, 11, 17, 8, 0)      # Dienstag
    assert datetime.fromtimestamp(next_run({"type": "daily", "time": "07:30"}, base)).day == 18
    assert datetime.fromtimestamp(next_run({"type": "daily", "time": "09:15"}, base)).hour == 9
    nxt = datetime.fromtimestamp(next_run({"type": "weekly", "days": [0], "time": "09:00"}, base))
    assert nxt.weekday() == 0 and nxt > base
    assert next_run({"type": "manual"}, base) is None


def test_automation_run_condition_and_tick(jc):
    a = jc.automations
    aid = a.save({"name": "Sortieren", "trigger": {"type": "interval", "hours": 1}, "action": {"type": "sort_tasks"},
                  "condition": {"type": "open_tasks", "value": 0}})
    jc.tasks.add("Überfällig", due=(date.today() - timedelta(days=1)).isoformat(), priority=3)
    res = a.run(aid, manual=True)
    assert res["status"] == "ok" and "1 fällige" in res["output"]
    assert jc.tasks.list()[0]["priority"] == 1
    assert a.runs(aid)[0]["status"] == "ok"
    assert jc.counts()["notifications_unread"] >= 1
    # fällig machen → tick führt aus
    from jarvis.db import automations
    from jarvis.db import update as upd

    jc.db.run(upd(automations).where(automations.c.id == aid).values(next_run=time.time() - 5))
    assert a.tick()["ran"] == 1
    assert len(a.runs(aid)) == 2
    with pytest.raises(ValueError):
        a.save({"trigger": {"type": "quatsch"}, "action": {"type": "notify"}})


def test_automation_condition_contains_and_agent(jc):
    a = jc.automations
    aid = a.save({"name": "Agent", "trigger": {"type": "manual"}, "action": {"type": "agent", "agent": "research",
                                                                               "prompt": "Fasse zusammen"}})
    res = a.run(aid, manual=True)
    assert res["status"] == "ok" and res["output"].startswith("OK:")
    assert a._condition({"type": "contains", "value": "dringend"}, {"text": "Bitte DRINGEND lesen"})
    assert not a._condition({"type": "contains", "value": "dringend"}, {"text": "Hallo"})


def test_categorize_files(jc):
    out = jc.cfg.output_dir
    (out / "bericht.pdf").write_bytes(b"%PDF")
    (out / "uploads").mkdir()
    (out / "uploads" / "foto.jpg").write_bytes(b"x")
    text = jc.automations.categorize_files()
    assert "2 Dateien" in text
    assert (out / "Dokumente" / "bericht.pdf").exists() and (out / "Bilder" / "foto.jpg").exists()


def test_reminders_and_proactive(jc):
    jc.tasks.add("Anrufen", remind_at=(datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M"))
    assert jc.run_reminders() == 1
    assert jc.run_reminders() == 0
    assert jc.proactive_check() == []                 # ohne Erlaubnis nichts
    jc.security.set_permission("proactive", True)
    jc.tasks.add("Alt", due=(date.today() - timedelta(days=2)).isoformat())
    hints = jc.proactive_check()
    assert any("Überfällig" in h for h in hints)
    assert jc.proactive_check() == []                 # nicht doppelt melden


# ---------------------------------------------------------------- Routing & Second Opinion
def test_route_and_classify():
    assert route("Baue mir eine Webseite") == "coding"
    assert route("Organisiere meine Aufgaben für morgen") == "assistant"
    assert route("Wie geht es dir?") == "master"
    assert route("Erstelle eine Präsentation über meine Termine") == "master"
    assert classify("Schreib ein Python-Skript") == "code"
    assert classify("hi") == "fast"


def test_second_opinion_single_provider(jc):
    res = jc.second_opinion("Was ist 2+2?")
    assert len(res["answers"]) >= 1 and res["summary"]


# ---------------------------------------------------------------- API
@pytest.fixture
def api(jc, monkeypatch):
    monkeypatch.setattr(server, "_core", jc)
    monkeypatch.setattr(server.cfg, "web_password", "")
    monkeypatch.setattr(server.cfg, "output_dir", jc.cfg.output_dir)
    return TestClient(server.app)


def test_api_dashboard_and_pages(api, jc):
    d = api.get("/api/dashboard").json()
    for key in ("counts", "status", "system", "events", "agents", "today", "models", "usage", "memory"):
        assert key in d
    assert len(d["usage"]) == 24 and any(s["id"] == "database" for s in d["status"])
    assert {a["id"] for a in d["agents"]} >= {"master", "research", "coding", "security"}
    assert api.get("/api/models").json()["providers"]
    assert api.get("/api/security").json()["permissions"]
    assert api.get("/api/settings").json()["voices"]


def test_api_memory_tasks_search(api):
    r = api.post("/api/memory", json={"content": "Mein Hund heißt Bello", "kind": "fact", "tags": ["hund"]})
    mid = r.json()["id"]
    assert api.get("/api/memory?q=Bello").json()["items"][0]["id"] == mid
    assert api.put(f"/api/memory/{mid}", json={"content": "Mein Hund heißt Bello und ist 3", "kind": "fact"}).json()["ok"]
    tid = api.post("/api/tasks", json={"title": "Bello impfen", "due": "morgen", "project": "Hund"}).json()["id"]
    assert api.put(f"/api/tasks/{tid}", json={"status": "done"}).json()["ok"]
    assert api.get("/api/projects").json()[0]["name"] == "Hund"
    res = api.get("/api/search?q=Bello").json()["results"]
    assert {r["type"] for r in res} >= {"memory", "task"}
    g = api.get("/api/memory/graph").json()
    assert any(n["group"] == "task" for n in g["nodes"])
    assert api.delete(f"/api/memory/{mid}").json()["ok"]
    assert api.delete(f"/api/memory/{mid}").status_code == 404


def test_api_permissions_and_missions(api):
    perms = api.put("/api/permissions", json={"key": "autonomous", "value": False}).json()
    assert next(p for p in perms if p["key"] == "autonomous")["value"] is False
    assert api.post("/api/missions", json={"goal": "Mach etwas Großes"}).status_code == 403
    assert api.put("/api/permissions", json={"key": "gibtsnicht", "value": True}).status_code == 400
    sim = api.post("/api/missions/simulate", json={"goal": "Baue eine Webseite"}).json()
    assert len(sim["steps"]) == 2 and "nichts ausgeführt" in sim["note"]


def test_api_automations_models_cron(api, monkeypatch):
    body = {"name": "Test", "trigger": {"type": "manual"}, "action": {"type": "notify", "text": "Hallo"}}
    aid = api.post("/api/automations", json=body).json()["id"]
    assert api.post(f"/api/automations/{aid}/run").json()["status"] == "ok"
    data = api.get("/api/automations").json()
    assert data["items"][0]["name"] == "Test" and data["runs"] and data["meta"]["actions"]
    assert api.put("/api/models/order", json={"tier": "fast", "order": ["openrouter", "groq"]}).json()["ok"]
    assert api.get("/api/models").json()["order"]["fast"][0] == "openrouter"
    assert api.put("/api/models/order", json={"tier": "fast", "order": ["evil"]}).status_code == 400
    assert api.get("/api/cron").status_code == 404
    monkeypatch.setenv("JARVIS_CRON_TOKEN", "geheim")
    assert api.get("/api/cron?token=falsch").status_code == 401
    assert "ran" in api.get("/api/cron?token=geheim").json()
    n = api.get("/api/notifications").json()
    assert n["unread"] >= 1
    api.post("/api/notifications/read", json={"ids": []})
    assert api.get("/api/notifications").json()["unread"] == 0


def test_api_settings_and_approvals(api, jc):
    s = api.put("/api/settings", json={"ui": {"voice": "de-DE-KatjaNeural", "evil": 1}}).json()
    assert s["ui"]["voice"] == "de-DE-KatjaNeural" and "evil" not in s["ui"]
    assert api.put("/api/settings", json={"ui": {"voice": "x"}}).status_code == 400
    aid = jc.security.request("forget", {"id": 999}, "Test")
    assert api.get("/api/approvals?status=pending").json()[0]["id"] == aid
    assert api.post(f"/api/approvals/{aid}", json={"approve": True}).json()["status"] == "done"


def test_api_password_protects_core_endpoints(api, monkeypatch):
    monkeypatch.setattr(server.cfg, "web_password", "pw")
    assert api.get("/api/dashboard").status_code == 401
    assert api.get("/api/dashboard", headers={"X-Jarvis-Key": "pw"}).status_code == 200
