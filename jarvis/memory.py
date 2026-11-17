"""Gedächtnis von JARVIS (Memory Agent).

Arten: fact (Wissen über dich), preference (Vorlieben), project (Projektwissen), goal (Ziele),
decision (Entscheidungen), person (Personen), note (Notizen). Kurzzeitgedächtnis = laufendes Gespräch,
Gesprächsverlauf = Tabelle messages.

- Doppelte Erinnerungen werden erkannt (Textähnlichkeit) und stattdessen aktualisiert.
- Zusammenhänge entstehen über gemeinsame Tags, Projekte und Personen → Memory Map / Knowledge Graph.
- Nach jeder Antwort extrahiert ein schnelles Modell neue, wichtige Informationen (kann abgeschaltet werden).
"""

from __future__ import annotations

import difflib
import json
import logging
import re

from sqlalchemy import delete, desc, func, or_, select, update

from .db import DB, loads, memories, now

log = logging.getLogger("jarvis.memory")

KINDS = {"fact": "Wissen", "preference": "Vorliebe", "project": "Projekt", "goal": "Ziel", "decision": "Entscheidung",
         "person": "Person", "note": "Notiz"}
STOP = set("der die das und oder ein eine einen einem ist sind war wie was wer wo wann ich du er sie es wir ihr mir mich "
           "dir dich mein meine dein deine bitte mal auch noch nur zu im in am an auf aus bei mit von für über the a an "
           "of to and is are jarvis kannst kann soll sollst hast habe hat".split())


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zäöüß0-9]{3,}", (text or "").lower()) if w not in STOP}


class MemoryStore:
    def __init__(self, db: DB):
        self.db = db

    # ------------------------------------------------------------------ Grundfunktionen
    def _row(self, r: dict) -> dict:
        r = dict(r)
        r["tags"] = loads(r.get("tags"), [])
        return r

    def get(self, mid: int) -> dict | None:
        r = self.db.one(select(memories).where(memories.c.id == mid))
        return self._row(r) if r else None

    def add(self, content: str, kind: str = "fact", title: str = "", tags: list[str] | None = None,
            project: str = "", importance: int = 2, source: str = "chat") -> tuple[int, str]:
        """Speichert eine Erinnerung. Rückgabe (id, 'new'|'updated'|'duplicate')."""
        content = (content or "").strip()
        if not content:
            raise ValueError("Leere Erinnerung")
        kind = kind if kind in KINDS else "fact"
        tags = sorted({t.strip().lower()[:40] for t in (tags or []) if t and t.strip()})
        title = (title or content.split(".")[0])[:200]
        # Doppelte/ähnliche Erinnerung? → aktualisieren statt neu anlegen
        for r in self.db.q(select(memories).where(memories.c.kind == kind).order_by(desc(memories.c.updated)).limit(300)):
            ratio = difflib.SequenceMatcher(None, r["content"].lower(), content.lower()).ratio()
            if ratio > 0.9:
                return r["id"], "duplicate"
            same_title = r["title"].lower() == title.lower()
            if ratio > 0.72 or (same_title and kind in ("preference", "goal", "project", "person")):
                merged = sorted(set(loads(r["tags"], [])) | set(tags))
                self.db.run(update(memories).where(memories.c.id == r["id"]).values(
                    content=content, tags=json.dumps(merged), project=project or r["project"],
                    importance=max(importance, r["importance"] or 1), updated=now()))
                return r["id"], "updated"
        mid = self.db.add(memories, kind=kind, title=title, content=content[:4000], tags=json.dumps(tags),
                          project=(project or "")[:120], importance=max(1, min(3, int(importance or 2))),
                          source=source, created=now(), updated=now())
        return mid, "new"

    def update(self, mid: int, **fields) -> bool:
        allowed = {k: v for k, v in fields.items() if k in ("kind", "title", "content", "project", "importance", "tags")}
        if "tags" in allowed:
            allowed["tags"] = json.dumps(sorted({t.strip().lower() for t in allowed["tags"] if t.strip()}))
        if not allowed:
            return False
        allowed["updated"] = now()
        return self.db.run(update(memories).where(memories.c.id == mid).values(**allowed)).rowcount > 0

    def delete(self, mid: int) -> bool:
        return self.db.run(delete(memories).where(memories.c.id == mid)).rowcount > 0

    def list(self, kind: str | None = None, project: str | None = None, limit: int = 200) -> list[dict]:
        stmt = select(memories)
        if kind:
            stmt = stmt.where(memories.c.kind == kind)
        if project:
            stmt = stmt.where(memories.c.project == project)
        return [self._row(r) for r in self.db.q(stmt.order_by(desc(memories.c.updated)).limit(limit))]

    def search(self, query: str, limit: int = 8) -> list[dict]:
        q = words(query)
        if not q:
            return []
        like = [memories.c.content.ilike(f"%{w}%") for w in list(q)[:8]] + \
               [memories.c.title.ilike(f"%{w}%") for w in list(q)[:8]] + \
               [memories.c.tags.ilike(f"%{w}%") for w in list(q)[:8]]
        rows = self.db.q(select(memories).where(or_(*like)).limit(200))
        scored = []
        for r in rows:
            hay = words(r["title"] + " " + r["content"] + " " + " ".join(loads(r["tags"], [])) + " " + r["project"])
            score = len(q & hay) + 0.3 * (r["importance"] or 1)
            scored.append((score, r))
        scored.sort(key=lambda x: -x[0])
        return [self._row(r) for _, r in scored[:limit]]

    def stats(self) -> dict:
        rows = self.db.q(select(memories.c.kind, func.count().label("n")).group_by(memories.c.kind))
        by = {r["kind"]: r["n"] for r in rows}
        return {"total": sum(by.values()), "by_kind": by}

    def projects(self) -> list[str]:
        rows = self.db.q(select(memories.c.project).where(memories.c.project != "").distinct())
        return sorted(r["project"] for r in rows)

    # ------------------------------------------------------------------ Kontext für die KI
    def context_for(self, text: str) -> str:
        prefs = self.list(kind="preference", limit=8)
        goals = self.list(kind="goal", limit=4)
        hits = self.search(text, limit=6)
        seen, lines = set(), []
        for m in prefs + goals + hits:
            if m["id"] in seen:
                continue
            seen.add(m["id"])
            proj = f" [Projekt: {m['project']}]" if m["project"] else ""
            lines.append(f"- ({KINDS.get(m['kind'], m['kind'])}#{m['id']}){proj} {m['content'][:300]}")
        if not lines:
            return ""
        return "Was du über den Nutzer weißt (Gedächtnis, nutze es natürlich, erwähne IDs nicht):\n" + "\n".join(lines[:14])

    # ------------------------------------------------------------------ Memory Map / Knowledge Graph
    def graph(self, limit: int = 150) -> dict:
        nodes, edges, index = [], [], {}

        def node(key: str, label: str, group: str, **extra) -> str:
            if key not in index:
                index[key] = len(nodes)
                nodes.append({"id": key, "label": label[:60], "group": group, **extra})
            return key

        for m in self.list(limit=limit):
            mk = node(f"m{m['id']}", m["title"], m["kind"], content=m["content"][:300], mid=m["id"])
            if m["project"]:
                edges.append([mk, node(f"p:{m['project'].lower()}", m["project"], "projectnode")])
            for t in m["tags"]:
                group = "personnode" if t.startswith("person:") else "tag"
                label = t.split(":", 1)[1] if ":" in t else t
                edges.append([mk, node(f"t:{t}", label, group)])
        return {"nodes": nodes, "edges": edges}

    # ------------------------------------------------------------------ automatische Extraktion
    def extract(self, registry, question: str, answer: str) -> list[dict]:
        """Lässt ein schnelles Modell neue, dauerhaft wichtige Informationen finden und speichert sie."""
        prompt = (
            "Du bist der Memory Agent von JARVIS. Finde in diesem Gesprächsausschnitt NUR Informationen über den Nutzer, "
            "die langfristig nützlich sind: Fakten über ihn, Vorlieben, Ziele, Projekte, Entscheidungen, wichtige Personen. "
            "Keine allgemeinen Wissensfragen, keine Einmal-Anfragen, nichts Sensibles wie Passwörter.\n"
            'Antworte NUR mit JSON: {"memories": [{"kind": "fact|preference|goal|project|decision|person|note", '
            '"title": "kurz", "content": "ein vollständiger Satz", "tags": ["..."], "project": "", "importance": 1-3}]} '
            'oder {"memories": []}.\n\n'
            f"Nutzer: {question[:1500]}\nJARVIS: {answer[:1500]}")
        try:
            raw = registry.quick([{"role": "user", "content": prompt}], tier="fast")
        except Exception as e:  # noqa: BLE001
            log.info("Gedächtnis-Extraktion übersprungen: %s", e)
            return []
        data = parse_json(raw) or {}
        saved = []
        for item in (data.get("memories") or [])[:5]:
            if not isinstance(item, dict) or not item.get("content"):
                continue
            try:
                mid, state = self.add(str(item["content"]), str(item.get("kind") or "fact"), str(item.get("title") or ""),
                                      [str(t) for t in item.get("tags") or []], str(item.get("project") or ""),
                                      int(item.get("importance") or 2), source="agent")
            except (ValueError, TypeError):
                continue
            if state != "duplicate":
                saved.append({"id": mid, "state": state, "title": item.get("title") or item["content"][:60]})
        return saved


def parse_json(text: str):
    """Holt das erste JSON-Objekt aus einer Modell-Antwort (auch mit ```json-Rahmen)."""
    if not text:
        return None
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except ValueError:
                    return None
    return None
