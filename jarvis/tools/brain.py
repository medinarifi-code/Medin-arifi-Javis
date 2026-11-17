"""Werkzeuge für Gedächtnis, Aufgaben, Missionen, Web, Second Opinion und sensible Aktionen."""

from __future__ import annotations

import html
import ipaddress
import re
import socket
from datetime import datetime
from urllib.parse import urlparse

import httpx

from .files import ToolError

S = {"type": "string"}
I = {"type": "integer"}
S_LIST = {"type": "array", "items": S}


def obj(props: dict, required: list[str] | None = None) -> dict:
    out: dict = {"type": "object", "properties": props}
    if required:
        out["required"] = required
    return out


BRAIN_TOOLS = [
    {"name": "remember", "description": "Etwas dauerhaft merken. kind: fact|preference|project|goal|decision|person|note.",
     "parameters": obj({"content": S, "kind": S, "title": S, "tags": S_LIST, "project": S, "importance": I}, ["content"])},
    {"name": "recall", "description": "Im Gedächtnis suchen (frühere Infos, Entscheidungen, Projekte).",
     "parameters": obj({"query": S}, ["query"])},
    {"name": "forget", "description": "Erinnerung löschen (Nutzer muss freigeben).", "parameters": obj({"id": I}, ["id"])},
    {"name": "add_task", "description": "Aufgabe anlegen. due/remind_at als 'JJJJ-MM-TT HH:MM'. priority 1 hoch,2,3 niedrig.",
     "parameters": obj({"title": S, "due": S, "priority": I, "project": S, "notes": S, "remind_at": S}, ["title"])},
    {"name": "list_tasks", "description": "Aufgaben auflisten. status: open|done|all; day: heute|morgen|JJJJ-MM-TT.",
     "parameters": obj({"status": S, "project": S, "day": S})},
    {"name": "update_task", "description": "Aufgabe ändern/erledigen (status done).",
     "parameters": obj({"id": I, "title": S, "status": S, "priority": I, "due": S, "notes": S, "remind_at": S}, ["id"])},
    {"name": "plan_day", "description": "Grundlage für einen Tagesplan: Aufgaben, Konflikte, freie Zeitfenster.",
     "parameters": obj({"day": S})},
    {"name": "propose_schedule",
     "description": "Zeitplan vorschlagen; wird erst nach Freigabe eingetragen. items: task_id oder title, start 'HH:MM', duration (Min).",
     "parameters": obj({"day": S, "items": {"type": "array", "items": obj(
         {"task_id": I, "title": S, "start": S, "duration": I})}}, ["items"])},
    {"name": "start_mission", "description": "Großes Ziel autonom in Schritten mit mehreren Agenten erledigen.",
     "parameters": obj({"goal": S}, ["goal"])},
    {"name": "fetch_url", "description": "Webseite abrufen und als Text lesen.", "parameters": obj({"url": S}, ["url"])},
    {"name": "second_opinion", "description": "Mehrere KI-Modelle unabhängig fragen und Antworten vergleichen.",
     "parameters": obj({"question": S}, ["question"])},
    {"name": "delete_file", "description": "Datei löschen (Nutzer muss freigeben).", "parameters": obj({"path": S}, ["path"])},
    {"name": "notify_me", "description": "Dem Nutzer eine Benachrichtigung schicken.",
     "parameters": obj({"title": S, "body": S}, ["title"])},
    {"name": "security_report", "description": "Sicherheitsprotokoll und offene Freigaben anzeigen.", "parameters": None},
]

LABELS = {"remember": "Merke mir das", "recall": "Durchsuche Gedächtnis", "forget": "Lösch-Freigabe angefragt",
          "add_task": "Lege Aufgabe an", "list_tasks": "Prüfe Aufgaben", "update_task": "Aktualisiere Aufgabe",
          "plan_day": "Plane den Tag", "propose_schedule": "Schlage Zeitplan vor", "start_mission": "Starte Mission",
          "fetch_url": "Lese Webseite", "second_opinion": "Hole zweite Meinung ein", "delete_file": "Lösch-Freigabe angefragt",
          "notify_me": "Sende Benachrichtigung", "security_report": "Prüfe Sicherheitsprotokoll"}


def _core(ctx):
    if getattr(ctx, "core", None) is None:
        raise ToolError("Diese Funktion ist hier nicht verfügbar.")
    return ctx.core


# ---------------------------------------------------------------------------- Web abrufen (mit SSRF-Schutz)
def _public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return True


def html_to_text(raw: str) -> tuple[str, str]:
    title = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
    raw = re.sub(r"(?is)<(script|style|noscript|svg|head|nav|footer)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h\d>|</tr>", "\n", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return (html.unescape(title.group(1).strip()) if title else ""), text


def fetch_url(url: str, max_chars: int = 12000, client: httpx.Client | None = None) -> str:
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    host = urlparse(url).hostname or ""
    if not host or not _public_host(host):
        raise ToolError("Diese Adresse ist nicht erlaubt (nur öffentliche Webseiten).")
    try:
        c = client or httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 JARVIS-Reader"})
        r = c.get(url)
    except httpx.HTTPError as e:
        raise ToolError(f"Seite nicht erreichbar: {type(e).__name__}") from e
    if r.status_code >= 400:
        raise ToolError(f"Die Seite antwortet mit Fehler {r.status_code}.")
    ctype = r.headers.get("content-type", "")
    if "html" not in ctype and "text" not in ctype and "json" not in ctype:
        raise ToolError(f"Das ist keine Textseite ({ctype or 'unbekannt'}).")
    title, text = html_to_text(r.text) if "html" in ctype else ("", r.text)
    more = f"\n[… gekürzt, {len(text)} Zeichen insgesamt]" if len(text) > max_chars else ""
    return f"Titel: {title}\nAdresse: {r.url}\n\n{text[:max_chars]}{more}"


# ---------------------------------------------------------------------------- Ausführung
def execute(name: str, args: dict, ctx) -> str:
    core = _core(ctx)
    agent = getattr(ctx, "agent", "master")
    mid = getattr(ctx, "mission_id", None)

    if name == "remember":
        if not args.get("content"):
            raise ToolError("Was soll ich mir merken?")
        rid, state = core.memory.add(str(args["content"]), str(args.get("kind") or "fact"), str(args.get("title") or ""),
                                     [str(t) for t in args.get("tags") or []], str(args.get("project") or ""),
                                     int(args.get("importance") or 2), source="user")
        core.db.event(f"Gedächtnis: {'aktualisiert' if state == 'updated' else 'gespeichert'} #{rid}", kind="ok",
                      agent="Memory Agent", detail=str(args["content"])[:300], mission_id=mid)
        ctx.emit({"type": "memory", "id": rid, "state": state, "title": str(args.get("title") or args["content"])[:80]})
        return {"new": f"Gespeichert (#{rid}).", "updated": f"Bestehende Erinnerung #{rid} aktualisiert.",
                "duplicate": f"War schon gespeichert (#{rid})."}[state]

    if name == "recall":
        hits = core.memory.search(str(args.get("query") or ""), limit=10)
        if not hits:
            return "Dazu ist nichts gespeichert."
        return "\n".join(f"#{m['id']} [{m['kind']}] {m['title']}: {m['content'][:400]}"
                         + (f" (Projekt {m['project']})" if m["project"] else "")
                         + f" – {datetime.fromtimestamp(m['updated']).strftime('%d.%m.%Y')}" for m in hits)

    if name == "forget":
        m = core.memory.get(int(args.get("id") or 0))
        if not m:
            raise ToolError("Diese Erinnerung gibt es nicht.")
        aid = core.security.request("forget", {"id": m["id"]}, f"Erinnerung #{m['id']} löschen: „{m['title']}“", agent, mid)
        ctx.emit({"type": "approval", "id": aid, "summary": f"Erinnerung „{m['title']}“ löschen"})
        return f"Freigabe angefragt (#{aid}). Der Nutzer muss das Löschen in der Oberfläche bestätigen."

    if name == "add_task":
        tid = core.tasks.add(str(args.get("title") or ""), str(args.get("due") or ""), int(args.get("priority") or 2),
                             str(args.get("project") or ""), str(args.get("notes") or ""), str(args.get("remind_at") or ""))
        core.db.event(f"Aufgabe angelegt: {args.get('title')}", kind="ok", agent="Personal Assistant", mission_id=mid)
        ctx.emit({"type": "task", "id": tid, "title": args.get("title")})
        return f"Aufgabe #{tid} angelegt."

    if name == "list_tasks":
        from ..planner import fmt_task

        status = str(args.get("status") or "open")
        rows = core.tasks.list(None if status == "all" else status, args.get("project") or None, args.get("day") or None)
        return "\n".join(fmt_task(t) for t in rows) or "Keine passenden Aufgaben."

    if name == "update_task":
        tid = int(args.get("id") or 0)
        ok = core.tasks.update(tid, **{k: args.get(k) for k in ("title", "status", "priority", "due", "notes", "remind_at")})
        if not ok:
            raise ToolError("Aufgabe nicht gefunden oder nichts geändert.")
        core.db.event(f"Aufgabe #{tid} aktualisiert", kind="ok", agent="Personal Assistant", mission_id=mid)
        return f"Aufgabe #{tid} aktualisiert."

    if name == "plan_day":
        from ..planner import fmt_task

        o = core.tasks.day_overview(args.get("day"))
        parts = [f"Datum: {o['date']}", "Bereits geplant:"] + [fmt_task(t) for t in o["scheduled"]] + \
                ["Noch zu planen (offen, fällig bis dahin):"] + [fmt_task(t) for t in o["unscheduled"]] + \
                ["Überschneidungen: " + ("; ".join(o["conflicts"]) or "keine"),
                 "Freie Zeitfenster: " + (", ".join(f"{a}–{b}" for a, b in o["free_slots"]) or "keine")]
        return "\n".join(parts)

    if name == "propose_schedule":
        from ..planner import resolve_day

        day = resolve_day(args.get("day"))
        items = []
        for it in args.get("items") or []:
            start = str(it.get("start") or "")
            if re.fullmatch(r"\d{1,2}:\d{2}", start):
                start = f"{day.isoformat()} {int(start.split(':')[0]):02d}:{start.split(':')[1]}"
            items.append({"task_id": it.get("task_id"), "title": it.get("title"), "start": start,
                          "duration": int(it.get("duration") or 30)})
        if not items:
            raise ToolError("Der Zeitplan ist leer.")
        lines = [f"{i['start'][-5:]} ({i['duration']} Min) {i['title'] or ('Aufgabe #' + str(i['task_id']))}" for i in items]
        aid = core.security.request("schedule", {"items": items}, f"Zeitplan für {day.strftime('%d.%m.%Y')}:\n" + "\n".join(lines),
                                    agent, mid)
        ctx.emit({"type": "approval", "id": aid, "summary": f"Zeitplan für {day.strftime('%d.%m.')} eintragen",
                  "detail": "\n".join(lines)})
        return "Zeitplan vorgeschlagen. Er wird eingetragen, sobald der Nutzer in der Oberfläche auf „Freigeben“ tippt."

    if name == "start_mission":
        if not core.security.allowed("autonomous"):
            raise ToolError("Autonome Missionen sind in den Einstellungen ausgeschaltet.")
        mid2 = core.missions.start(str(args.get("goal") or ""))
        ctx.emit({"type": "mission", "id": mid2, "goal": args.get("goal")})
        return f"Mission #{mid2} gestartet. Der Fortschritt ist live unter „Missionen“ zu sehen."

    if name == "fetch_url":
        if not core.security.allowed("web_fetch"):
            raise ToolError("Webseiten abrufen ist in den Einstellungen ausgeschaltet.")
        core.db.event(f"Webseite gelesen: {args.get('url')}", kind="info", agent="Browser Agent", mission_id=mid)
        return fetch_url(str(args.get("url") or ""))

    if name == "second_opinion":
        res = core.second_opinion(str(args.get("question") or ""))
        ctx.emit({"type": "second_opinion", "models": [a["model"] for a in res["answers"]]})
        return res["summary"]

    if name == "delete_file":
        from .files import existing_path, rel

        p = existing_path(ctx.output_dir, str(args.get("path") or ""))
        path = rel(ctx.output_dir, p)
        aid = core.security.request("delete_file", {"path": path}, f"Datei löschen: {path}", agent, mid)
        ctx.emit({"type": "approval", "id": aid, "summary": f"Datei „{p.name}“ löschen"})
        return f"Freigabe angefragt (#{aid}). Erst nach Bestätigung wird gelöscht."

    if name == "notify_me":
        core.notify(str(args.get("title") or "JARVIS"), str(args.get("body") or ""))
        return "Benachrichtigung gesendet."

    if name == "security_report":
        from sqlalchemy import desc, select

        from ..db import events

        rows = core.db.q(select(events).where(events.c.kind.in_(["security", "err"])).order_by(desc(events.c.id)).limit(20))
        pending = core.security.list("pending")
        perms = core.security.permissions()
        return ("Berechtigungen: " + ", ".join(f"{k}={'an' if v else 'aus'}" for k, v in perms.items()) +
                f"\nOffene Freigaben: {len(pending)}" + "".join(f"\n- #{p['id']} {p['summary'][:120]}" for p in pending) +
                "\nLetzte Sicherheitsereignisse:" + "".join(
                    f"\n- {datetime.fromtimestamp(r['ts']).strftime('%d.%m. %H:%M')} {r['title']}" for r in rows))

    raise ToolError(f"Unbekanntes Werkzeug: {name}")
