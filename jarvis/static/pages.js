/* JARVIS OS – alle Bereiche des Command Centers. Jede Anzeige kommt aus echten Server-Daten. */
(() => {
  "use strict";
  const { $, $$, esc, call, toast, pages, fmtTime, fmtDate, ago } = J;
  const KIND_COLORS = { fact: "#22d3ee", preference: "#a78bfa", project: "#34d399", goal: "#fbbf24", decision: "#f472b6",
    person: "#fb923c", note: "#94a3b8", projectnode: "#34d399", personnode: "#fb923c", tag: "#38bdf8", task: "#facc15" };
  const KIND_DE = { fact: "Wissen", preference: "Vorliebe", project: "Projekt", goal: "Ziel", decision: "Entscheidung", person: "Person", note: "Notiz",
    projectnode: "Projekt", personnode: "Person", tag: "Schlagwort", task: "Aufgabe" };
  const STATUS_DE = { planning: "plant", running: "läuft", paused: "pausiert", waiting: "wartet", done: "fertig", failed: "fehlgeschlagen",
    cancelled: "abgebrochen", pending: "offen", approved: "freigegeben", denied: "abgelehnt", ok: "ok", error: "Fehler", skipped: "übersprungen" };
  const statusPill = (s) => `<span class="pill ${["done", "ok", "approved"].includes(s) ? "ok" : ["failed", "error", "denied", "cancelled"].includes(s) ? "err" : ["paused", "waiting", "pending"].includes(s) ? "warn" : ""}">${esc(STATUS_DE[s] || s)}</span>`;
  const panel = (title, body, cls = "", extra = "") => `<div class="panel ${cls}"><h3><span class="grow">${esc(title)}</span>${extra}</h3>${body}</div>`;
  const busyBtn = async (btn, fn) => { const t = btn.textContent; btn.disabled = true; btn.textContent = "…"; try { await fn(); } catch (e) { toast(e.message, "err"); } finally { btn.disabled = false; btn.textContent = t; } };
  const fmtUptime = (s) => (s < 3600 ? `${Math.round(s / 60)} Min` : s < 86400 ? `${(s / 3600).toFixed(1)} Std` : `${(s / 86400).toFixed(1)} Tage`);
  const prio = { 1: '<span class="pill err">hoch</span>', 2: '<span class="pill dim">normal</span>', 3: '<span class="pill dim">niedrig</span>' };

  // ============================================================ Diagramme (Canvas, echte Daten)
  function fit(cv) {
    const dpr = Math.min(devicePixelRatio || 1, 2), r = cv.getBoundingClientRect();
    cv.width = Math.max(1, r.width * dpr); cv.height = Math.max(1, r.height * dpr);
    const g = cv.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0);
    return [g, r.width, r.height];
  }
  /** Balken: Anfragen pro Stunde (ok/fehler) + Linie: Antwortzeit. */
  function usageChart(cv, data) {
    const [g, w, h] = fit(cv);
    g.clearRect(0, 0, w, h);
    const max = Math.max(1, ...data.map((d) => d.ok + d.err));
    const bw = w / data.length;
    g.strokeStyle = "rgba(56,189,248,.12)"; g.lineWidth = 1;
    for (let i = 1; i < 4; i++) { const y = (h - 16) * i / 4; g.beginPath(); g.moveTo(0, y); g.lineTo(w, y); g.stroke(); }
    data.forEach((d, i) => {
      const x = i * bw + 2, okH = (h - 18) * d.ok / max, errH = (h - 18) * d.err / max;
      g.fillStyle = "rgba(34,211,238,.75)"; g.fillRect(x, h - 16 - okH, bw - 4, okH);
      g.fillStyle = "rgba(248,113,113,.85)"; g.fillRect(x, h - 16 - okH - errH, bw - 4, errH);
    });
    const lat = data.map((d) => d.lat).filter((x) => x != null), lmax = Math.max(1, ...lat);
    g.strokeStyle = "#a78bfa"; g.lineWidth = 1.5; g.beginPath(); let started = false;
    data.forEach((d, i) => { if (d.lat == null) return; const x = i * bw + bw / 2, y = (h - 18) * (1 - d.lat / lmax); started ? g.lineTo(x, y) : g.moveTo(x, y); started = true; });
    g.stroke();
    g.fillStyle = "#7fa3c0"; g.font = "10px -apple-system, sans-serif";
    [0, 6, 12, 18, 23].forEach((i) => { if (data[i]) g.fillText(new Date(data[i].t * 1000).getHours() + "h", i * bw + 2, h - 3); });
    g.fillText(`max ${max}/h`, w - 52, 10);
    const total = data.reduce((s, d) => s + d.ok + d.err, 0);
    if (!total) { g.fillStyle = "#7fa3c0"; g.font = "12px -apple-system, sans-serif"; g.fillText("Noch keine KI-Anfragen in den letzten 24 h", 10, h / 2); }
  }
  function gauge(cv, value, label) {
    const [g, w, h] = fit(cv);
    const cx = w / 2, cy = h / 2, r = Math.min(w, h) / 2 - 8;
    g.clearRect(0, 0, w, h);
    g.lineWidth = 7; g.lineCap = "round";
    g.strokeStyle = "rgba(127,163,192,.18)"; g.beginPath(); g.arc(cx, cy, r, 0.75 * Math.PI, 2.25 * Math.PI); g.stroke();
    if (value != null) {
      const col = value > 85 ? "#f87171" : value > 65 ? "#fbbf24" : "#22d3ee";
      g.strokeStyle = col; g.shadowColor = col; g.shadowBlur = 10;
      g.beginPath(); g.arc(cx, cy, r, 0.75 * Math.PI, (0.75 + 1.5 * Math.min(100, value) / 100) * Math.PI); g.stroke(); g.shadowBlur = 0;
    }
    g.fillStyle = "#e2f3ff"; g.font = "700 18px -apple-system, sans-serif"; g.textAlign = "center";
    g.fillText(value == null ? "–" : Math.round(value) + "%", cx, cy + 4);
    g.fillStyle = "#7fa3c0"; g.font = "11px -apple-system, sans-serif"; g.fillText(label, cx, cy + 22);
  }

  /** Kraftbasiertes Netz (Memory Map / Knowledge Graph) mit Ziehen, Zoomen, Antippen. */
  function graphView(cv, data, onSelect) {
    const nodes = data.nodes.map((n, i) => ({ ...n, x: Math.cos(i) * 200 * Math.random(), y: Math.sin(i) * 200 * Math.random(), vx: 0, vy: 0, deg: 0 }));
    const idx = new Map(nodes.map((n, i) => [n.id, i]));
    const edges = data.edges.map(([a, b]) => [idx.get(a), idx.get(b)]).filter(([a, b]) => a != null && b != null);
    edges.forEach(([a, b]) => { nodes[a].deg++; nodes[b].deg++; });
    let scale = 1, ox = 0, oy = 0, sel = null, hi = "", drag = null, raf = 0, alive = true, steps = 0;
    function sim() {
      const k = 0.03, rep = 5200, rest = 90;
      for (let i = 0; i < nodes.length; i++) {
        const a = nodes[i];
        for (let j = i + 1; j < nodes.length; j++) {
          const b = nodes[j]; let dx = a.x - b.x, dy = a.y - b.y; const d2 = dx * dx + dy * dy + 0.01;
          if (d2 > 160000) continue;
          const f = rep / d2; dx *= f / Math.sqrt(d2); dy *= f / Math.sqrt(d2);
          a.vx += dx; a.vy += dy; b.vx -= dx; b.vy -= dy;
        }
        a.vx -= a.x * 0.004; a.vy -= a.y * 0.004;
      }
      edges.forEach(([i, j]) => {
        const a = nodes[i], b = nodes[j], dx = b.x - a.x, dy = b.y - a.y, dist = Math.hypot(dx, dy) || 1, f = k * (dist - rest) / dist;
        a.vx += dx * f; a.vy += dy * f; b.vx -= dx * f; b.vy -= dy * f;
      });
      nodes.forEach((n) => { if (n === drag) return; n.vx *= 0.82; n.vy *= 0.82; n.x += Math.max(-12, Math.min(12, n.vx)); n.y += Math.max(-12, Math.min(12, n.vy)); });
    }
    function draw() {
      if (!alive) return;
      raf = requestAnimationFrame(draw);
      if (steps < 400 || drag) { sim(); steps++; }
      const [g, w, h] = fit(cv);
      g.clearRect(0, 0, w, h);
      g.save(); g.translate(w / 2 + ox, h / 2 + oy); g.scale(scale, scale);
      const q = hi.toLowerCase();
      g.lineWidth = 1 / scale;
      edges.forEach(([i, j]) => {
        const a = nodes[i], b = nodes[j];
        const on = sel && (a === sel || b === sel);
        g.strokeStyle = on ? "rgba(34,211,238,.8)" : "rgba(56,189,248,.18)";
        g.beginPath(); g.moveTo(a.x, a.y); g.lineTo(b.x, b.y); g.stroke();
      });
      nodes.forEach((n) => {
        const r = (["projectnode", "personnode"].includes(n.group) ? 9 : n.group === "tag" ? 4.5 : 6.5) + Math.min(6, n.deg * 0.6);
        const match = q && n.label.toLowerCase().includes(q);
        const col = KIND_COLORS[n.group] || "#22d3ee";
        g.fillStyle = col; g.shadowColor = col; g.shadowBlur = n === sel || match ? 18 : 6;
        g.globalAlpha = q && !match ? 0.25 : 1;
        g.beginPath(); g.arc(n.x, n.y, r, 0, Math.PI * 2); g.fill();
        g.shadowBlur = 0;
        if (scale > 0.7 || n === sel || match || n.group === "projectnode") {
          g.fillStyle = "#cfe9ff"; g.font = `${11 / Math.max(0.8, scale)}px -apple-system, sans-serif`;
          g.fillText(n.label.slice(0, 28), n.x + r + 3, n.y + 4);
        }
        g.globalAlpha = 1;
      });
      g.restore();
      if (!nodes.length) { g.fillStyle = "#7fa3c0"; g.font = "14px -apple-system, sans-serif"; g.fillText("Noch nichts gespeichert – sag z. B. „Merk dir, dass …“", 16, 30); }
    }
    const toWorld = (e) => { const r = cv.getBoundingClientRect(); return [(e.clientX - r.left - r.width / 2 - ox) / scale, (e.clientY - r.top - r.height / 2 - oy) / scale]; };
    const hit = (x, y) => nodes.find((n) => (n.x - x) ** 2 + (n.y - y) ** 2 < (12 / scale) ** 2 + 60);
    let pan = null, moved = false;
    const pts = new Map(); let pinch = 0;
    cv.onpointerdown = (e) => {
      cv.setPointerCapture(e.pointerId); pts.set(e.pointerId, [e.clientX, e.clientY]); moved = false;
      if (pts.size === 2) { const [a, b] = [...pts.values()]; pinch = Math.hypot(a[0] - b[0], a[1] - b[1]); return; }
      const [x, y] = toWorld(e); drag = hit(x, y) || null;
      if (!drag) pan = [e.clientX - ox, e.clientY - oy];
    };
    cv.onpointermove = (e) => {
      if (!pts.has(e.pointerId)) return;
      pts.set(e.pointerId, [e.clientX, e.clientY]); moved = true;
      if (pts.size === 2) { const [a, b] = [...pts.values()]; const d = Math.hypot(a[0] - b[0], a[1] - b[1]); if (pinch) scale = Math.max(0.3, Math.min(3, scale * d / pinch)); pinch = d; return; }
      if (drag) { const [x, y] = toWorld(e); drag.x = x; drag.y = y; steps = Math.min(steps, 300); }
      else if (pan) { ox = e.clientX - pan[0]; oy = e.clientY - pan[1]; }
    };
    cv.onpointerup = (e) => {
      pts.delete(e.pointerId); pinch = 0;
      if (!moved) { const [x, y] = toWorld(e); sel = hit(x, y) || null; onSelect(sel); }
      drag = null; pan = null;
    };
    cv.onwheel = (e) => { e.preventDefault(); scale = Math.max(0.3, Math.min(3, scale * (e.deltaY < 0 ? 1.1 : 0.9))); };
    draw();
    return { stop() { alive = false; cancelAnimationFrame(raf); }, highlight(t) { hi = t; }, reset() { scale = 1; ox = oy = 0; } };
  }

  const feedItem = (e) => `<li data-id="${e.id}"><time>${fmtTime(e.ts)}</time><i class="k ${esc(e.kind)}"></i><span>${esc(e.title)}${e.agent ? ` <small>${esc(e.agent)}</small>` : ""}${e.detail ? `<small>${esc(e.detail.slice(0, 220))}</small>` : ""}</span></li>`;

  // ============================================================ Command Center
  const WIDGETS = {
    core: { cls: "w7", title: "AI Core",
      init: (b) => { b.innerHTML = `<div class="core-panel"><div class="reactor"><i></i><i></i><i></i></div><div>
          <div class="big" id="cc-hello"></div><div class="muted" id="cc-sub"></div>
          <form id="cc-form"><input id="cc-input" placeholder="Befehl an JARVIS …" aria-label="Befehl"><button class="btn primary">Senden</button></form>
          <div class="row tight"><button class="btn sm" data-action="talk">🎙️ Sprechen</button><button class="btn sm" data-go="agents">🚀 Mission starten</button></div></div></div>`;
        $("#cc-form", b).onsubmit = (e) => { e.preventDefault(); const v = $("#cc-input", b).value.trim(); if (v) { $("#cc-input", b).value = ""; J.ask(v); } };
      },
      update: (b, d) => {
        const h = new Date().getHours();
        $("#cc-hello", b).textContent = `${h < 11 ? "Guten Morgen" : h < 18 ? "Hallo" : "Guten Abend"}, ${d.user}`;
        const c = d.counts;
        $("#cc-sub", b).textContent = `${new Date().toLocaleDateString("de-DE", { weekday: "long", day: "numeric", month: "long" })} · ${c.tasks_open} offene Aufgaben · ${c.missions_running} Missionen aktiv · ${c.memories} Erinnerungen`;
      } },
    status: { cls: "w5", title: "System Status",
      update: (b, d) => { b.innerHTML = d.status.map((s) => `<div class="kv"><span><i class="dot ${s.ok ? "on" : "off"}"></i> ${esc(s.label)}</span><b class="${s.ok ? "" : "error"}">${esc(s.detail)}</b></div>`).join(""); } },
    agents: { cls: "w4", title: "Aktive Agenten", link: "agents",
      update: (b, d) => { b.innerHTML = `<div class="mini-agents">${d.agents.map((a) => `<div title="${esc(a.working.join("\n") || a.desc)}"><i class="dot ${a.working.length ? "busy" : "on"}"></i>${a.icon} ${esc(a.name.replace(" Agent", ""))}<small>${a.working.length ? "arbeitet" : a.actions_24h + "×"}</small></div>`).join("")}</div>
        <p class="muted" style="font-size:12px;margin:8px 0 0">Zahl = Aktionen in 24 h · „arbeitet“ = gerade im Chat oder in einer Mission</p>`; } },
    timeline: { cls: "w4", title: "Mission Timeline", link: "projects",
      update: (b, d) => {
        const t = d.today, ms = d.missions.filter((m) => !["done", "failed", "cancelled"].includes(m.status));
        const conflictIds = new Set(t.conflicts.join(" ").match(/#\d+/g) || []);
        b.innerHTML = (ms.length ? ms.map((m) => `<div style="margin-bottom:10px"><div class="kv"><span>🚀 ${esc(m.goal.slice(0, 50))}</span>${statusPill(m.status)}</div><div class="bar-prog"><i style="width:${m.plan.length ? Math.round(100 * m.step / m.plan.length) : 5}%"></i></div></div>`).join("") : "")
          + (t.scheduled.length ? `<div class="timeline">${t.scheduled.map((x) => `<div class="tl ${conflictIds.has("#" + x.id) ? "conflict" : ""}"><time>${esc((x.start || "").slice(11, 16))}</time><div>${esc(x.title)}${x.duration ? ` <small class="muted">${x.duration} Min</small>` : ""}</div></div>`).join("")}</div>`
            : `<p class="empty">Heute nichts fest eingeplant.</p>`)
          + (t.unscheduled.length ? `<p class="muted" style="font-size:13px">+ ${t.unscheduled.length} offene Aufgabe(n) ohne Uhrzeit</p>` : "")
          + (t.conflicts.length ? `<p class="error" style="font-size:13px">⚠ ${t.conflicts.length} Überschneidung(en)</p>` : "")
          + (d.overdue.length ? `<p class="warn-t" style="font-size:13px">⏰ ${d.overdue.length} überfällig</p>` : "");
      } },
    feed: { cls: "w4", title: "Live Intelligence Feed", link: "monitor",
      update: (b, d) => {
        const old = new Set($$("li[data-id]", b).map((li) => li.dataset.id));
        b.innerHTML = d.events.length ? `<ul class="feed">${d.events.map(feedItem).join("")}</ul>` : '<p class="empty">Noch keine Aktivität.</p>';
        if (old.size) $$("li[data-id]", b).forEach((li) => { if (!old.has(li.dataset.id)) li.classList.add("new"); });
      } },
    llm: { cls: "w6", title: "LLM Status", link: "models",
      init: (b) => { b.innerHTML = '<div class="llm-list"></div><canvas class="chart"></canvas><p class="muted" style="font-size:12px;margin:4px 0 0">Balken: Anfragen pro Stunde (rot = Fehler) · Linie: Antwortzeit</p>'; },
      update: (b, d) => {
        $(".llm-list", b).innerHTML = d.models.length ? d.models.map((m) => `<div class="kv"><span><i class="dot ${m.last_error && m.success_rate !== null && m.success_rate < 50 ? "off" : "on"}"></i> ${esc(m.label)} ${m.free ? '<span class="pill ok">gratis</span>' : ""}</span><b>${m.calls_24h} Anfragen · ${m.success_rate ?? "–"}% ok · ${m.avg_latency ?? "–"} s</b></div>`).join("")
          : '<p class="error">Kein KI-Anbieter eingerichtet.</p>';
        usageChart($("canvas", b), d.usage);
      } },
    monitor: { cls: "w6", title: "System Monitor", link: "monitor",
      init: (b) => { b.innerHTML = '<div class="gauges"><div class="gauge"><canvas></canvas></div><div class="gauge"><canvas></canvas></div><div class="gauge"><canvas></canvas></div></div><div class="sys-kv"></div>'; },
      update: (b, d) => {
        const s = d.system, cvs = $$("canvas", b);
        gauge(cvs[0], s.cpu, "CPU-Last"); gauge(cvs[1], s.ram, "RAM"); gauge(cvs[2], s.disk, "Speicher");
        $(".sys-kv", b).innerHTML = `<div class="kv"><span>Laufzeit Server</span><b>${fmtUptime(s.uptime)}</b></div><div class="kv"><span>Datenbank</span><b>${esc(s.db.kind)} · ${s.db.persistent ? "dauerhaft" : "nur bis Neustart"}</b></div><div class="kv"><span>Gesicherte Dateien</span><b>${s.db.files_mb} MB</b></div>`;
      } },
    memory: { cls: "w4", title: "Memory Center", link: "memory",
      update: (b, d) => {
        const by = d.memory.by_kind || {};
        b.innerHTML = `<div class="big">${d.memory.total}</div><p class="muted" style="margin:0 0 8px">gespeicherte Erinnerungen</p>`
          + (Object.keys(by).length ? Object.entries(by).map(([k, n]) => `<div class="kv"><span><i class="dot" style="background:${KIND_COLORS[k]}"></i> ${esc(KIND_DE[k] || k)}</span><b>${n}</b></div>`).join("") : '<p class="empty">Sag „Merk dir …“ oder lass JARVIS automatisch lernen.</p>')
          + '<div class="row tight"><button class="btn sm" data-go="knowledge">🕸️ Memory Map</button></div>';
      } },
    commands: { cls: "w4", title: "Quick Commands",
      update: (b) => { b.innerHTML = `<div class="qc">${(J.ui.quick_commands || []).map((q) => `<button data-q="${esc(q)}">› ${esc(q)}</button>`).join("")}</div><p class="muted" style="font-size:12px">Anpassen unter Einstellungen.</p>`; } },
    voice: { cls: "w4", title: "Voice Control",
      update: (b) => {
        b.innerHTML = `<p class="muted" style="margin-top:0">Stimme: ${esc(J.voices[J.ui.voice] || J.ui.voice || "Standard")}</p>
          <div class="row tight"><button class="btn primary" data-action="talk">🎙️ Jetzt sprechen</button><button class="btn" data-go="settings">Stimme wählen</button></div>
          <p class="muted" style="font-size:12px">Wake Word „Hey JARVIS“: ${J.ui.wake_word ? (J.wake.supported ? "aktiv, solange die App offen ist" : "von diesem Browser nicht unterstützt") : "aus (Einstellungen)"}</p>`;
      } },
  };
  const DEFAULT_WIDGETS = ["core", "status", "agents", "timeline", "feed", "llm", "monitor", "memory", "commands"];
  let ccTimer = 0, ccBuilt = "";
  async function ccRefresh() {
    const grid = $("#cc-grid");
    let d;
    try { d = await call("/api/dashboard"); } catch (e) { if (!grid.children.length) grid.innerHTML = `<div class="panel w12"><p class="error">${esc(e.message)}</p></div>`; return; }
    const list = (J.ui.widgets?.length ? J.ui.widgets : DEFAULT_WIDGETS).filter((w) => WIDGETS[w]);
    if (ccBuilt !== list.join()) {
      grid.innerHTML = list.map((w) => `<div class="panel ${WIDGETS[w].cls}" data-w="${w}"><h3><span class="grow">${WIDGETS[w].title}</span>${WIDGETS[w].link ? `<a href="#${WIDGETS[w].link}">öffnen ›</a>` : ""}</h3><div class="pb"></div></div>`).join("");
      list.forEach((w) => WIDGETS[w].init?.($(`[data-w=${w}] .pb`, grid)));
      ccBuilt = list.join();
    }
    list.forEach((w) => { try { WIDGETS[w].update($(`[data-w=${w}] .pb`, grid), d); } catch (e) { console.error(w, e); } });
    const ap = d.approvals, ban = $("#cc-approvals");
    ban.hidden = !ap.length;
    if (ap.length) ban.innerHTML = `🛡️ ${ap.length} Freigabe(n) warten: ${esc(ap[0].summary.split("\n")[0].slice(0, 80))} <button class="btn sm" data-go="security">Prüfen</button>`;
  }
  pages.command = {
    show() { ccBuilt = ""; ccRefresh(); clearInterval(ccTimer); ccTimer = setInterval(() => { if (!document.hidden) ccRefresh(); }, 5000); },
    hide() { clearInterval(ccTimer); },
  };

  // ============================================================ Agenten & Missionen
  let missionTimer = 0;
  pages.agents = {
    tab: "missions",
    show(el) {
      const open = J.store.get("jarvis-open-mission");
      if (open) this.tab = "missions";
      el.innerHTML = `<div class="tabs">${[["missions", "🚀 Missionen (Autonom)"], ["agents", "🤖 Agenten"], ["second", "⚖️ Second Opinion"]].map(([k, l]) => `<button data-tab="${k}" class="${this.tab === k ? "on" : ""}">${l}</button>`).join("")}</div><div id="ag-body"></div>`;
      $$("[data-tab]", el).forEach((b) => (b.onclick = () => { this.tab = b.dataset.tab; this.show(el); }));
      const body = $("#ag-body", el);
      clearInterval(missionTimer);
      if (this.tab === "agents") this.agents(body);
      else if (this.tab === "second") this.second(body);
      else { this.missions(body); if (open) { J.store.set("jarvis-open-mission", null); this.detail(Number(open)); } }
    },
    hide() { clearInterval(missionTimer); },
    async agents(body) {
      const list = await call("/api/agents");
      body.innerHTML = `<div class="cols">${list.map((a) => `<div class="panel agent-card"><div class="head"><div class="ic">${a.icon}</div><div><b>${esc(a.name)}</b><br>
        <span class="pill ${a.working.length ? "" : "ok"}">${a.working.length ? "arbeitet" : "bereit"}</span> <span class="pill dim">Modell-Stufe: ${esc(a.tier)}</span></div></div>
        <p>${esc(a.desc)}</p>
        ${a.working.length ? `<p class="ok">▶ ${a.working.map(esc).join("<br>▶ ")}</p>` : ""}
        <div class="kv"><span>Aktionen (24 h)</span><b>${a.actions_24h}</b></div><div class="kv"><span>Zuletzt aktiv</span><b>${ago(a.last_active)}</b></div>
        <details><summary class="muted" style="font-size:13px">Werkzeuge (${a.tools.length})</summary><p>${a.tools.map(esc).join(", ")}</p></details>
        <div class="row tight"><button class="btn sm" data-chat="${a.id}">Mit ${esc(a.name.split(" ")[0])} chatten</button></div></div>`).join("")}</div>`;
      $$("[data-chat]", body).forEach((b) => (b.onclick = () => { $("#agent-select").value = b.dataset.chat; J.go("chat"); }));
    },
    async missions(body) {
      body.innerHTML = `<div class="panel" style="margin-bottom:14px"><h3>Neues Ziel</h3>
        <form id="ms-form" class="form"><textarea id="ms-goal" rows="3" placeholder="z. B. Baue mir eine Landingpage für mein Café mit Speisekarte und Kontaktformular"></textarea>
        <div class="row tight"><button class="btn primary" id="ms-start">🚀 Autonom ausführen</button><button type="button" class="btn" id="ms-sim">🧪 Simulieren (nur planen)</button></div>
        <p class="muted" style="font-size:12.5px;margin:0">Ablauf: Master Agent plant → Agenten arbeiten Schritt für Schritt → automatische Prüfung → Zusammenfassung. Löschen, Mails und Zeitpläne nur mit deiner Freigabe.</p></form>
        <div id="ms-sim-out"></div></div><div id="ms-detail"></div><div class="panel"><h3>Verlauf</h3><ul class="list" id="ms-list"></ul></div>`;
      $("#ms-form", body).onsubmit = (e) => {
        e.preventDefault();
        busyBtn($("#ms-start", body), async () => {
          const r = await call("/api/missions", { body: { goal: $("#ms-goal", body).value.trim() } });
          $("#ms-goal", body).value = ""; toast("Mission gestartet", "ok"); this.loadList(); this.detail(r.id);
        });
      };
      $("#ms-sim", body).onclick = (e) => busyBtn(e.target, async () => {
        const r = await call("/api/missions/simulate", { body: { goal: $("#ms-goal", body).value.trim() } });
        $("#ms-sim-out", body).innerHTML = `<div class="banner info" style="margin-top:12px;display:block"><b>Simulation:</b> ${esc(r.note)}<ol class="steps-list" style="margin-top:8px">${r.steps.map((s, i) => `<li><b>${i + 1}. ${esc(s.title)}</b> <span class="pill dim">${esc(s.agent)}</span><p>${esc(s.instructions)}</p></li>`).join("")}</ol>
          <p>Beteiligte Agenten: ${r.agents.map(esc).join(", ")}<br>Voraussichtliche Freigaben: ${r.approvals.length ? r.approvals.map(esc).join(", ") : "keine"}</p></div>`;
      });
      this.loadList();
      missionTimer = setInterval(() => { if (!document.hidden) { this.loadList(); if (this.open) this.detail(this.open, true); } }, 3000);
    },
    async loadList() {
      const ul = $("#ms-list"); if (!ul) return;
      const list = await call("/api/missions").catch(() => []);
      ul.innerHTML = list.length ? "" : '<li class="empty">Noch keine Missionen.</li>';
      list.forEach((m) => {
        const li = document.createElement("li"); li.className = "item";
        li.innerHTML = `<div class="body"><b>#${m.id} ${esc(m.goal.slice(0, 120))}</b><p>${fmtDate(m.created)} · ${m.plan.length ? `${m.step}/${m.plan.length} Schritte` : "plant …"} · ${m.files.length} Dateien</p>
          <div class="bar-prog"><i style="width:${m.status === "done" ? 100 : m.plan.length ? Math.round(100 * m.step / m.plan.length) : 4}%"></i></div></div><div class="acts">${statusPill(m.status)}<button class="btn sm">Details</button></div>`;
        li.querySelector("button").onclick = () => this.detail(m.id);
        ul.append(li);
      });
    },
    open: 0,
    async detail(id, quiet = false) {
      const box = $("#ms-detail"); if (!box) return;
      this.open = id;
      let m;
      try { m = await call(`/api/missions/${id}`); } catch (e) { if (!quiet) toast(e.message, "err"); return; }
      const active = !["done", "failed", "cancelled"].includes(m.status);
      const scrollPos = $(".feed", box)?.scrollTop;
      box.innerHTML = `<div class="panel" style="margin-bottom:14px"><h3><span class="grow">Mission #${m.id}</span>${statusPill(m.status)}<button class="btn sm" data-x>✕</button></h3>
        <p style="margin-top:0"><b>${esc(m.goal)}</b></p>
        <div class="row tight">${m.status === "paused" ? '<button class="btn sm" data-c="resume">▶ Fortsetzen</button>' : active ? '<button class="btn sm" data-c="pause">⏸ Pausieren</button>' : ""}${active ? '<button class="btn sm danger" data-c="cancel">■ Abbrechen</button>' : ""}</div>
        ${m.approvals.filter((a) => a.status === "pending").map((a) => `<div class="banner warn" style="margin-top:10px">🛡️ ${esc(a.summary)} <button class="btn sm primary" data-ap="${a.id}" data-v="1">Freigeben</button><button class="btn sm danger" data-ap="${a.id}" data-v="0">Ablehnen</button></div>`).join("")}
        <div class="hud-grid" style="margin-top:12px"><div class="w6"><h4 class="muted" style="margin-bottom:8px">Plan</h4><ol class="steps-list">${m.plan.length ? m.plan.map((s, i) => `<li class="${esc(s.status)}"><b>${i + 1}. ${esc(s.title)}</b> <span class="pill dim">${esc(s.agent)}</span> ${statusPill(s.status)}${s.result ? `<p>${esc(s.result)}</p>` : ""}</li>`).join("") : '<li class="running">Master Agent plant …</li>'}</ol>
          ${m.files.length ? `<h4 class="muted" style="margin:12px 0 6px">Dateien</h4>${m.files.map((f) => `<div class="kv"><span>📄 ${esc(f.name)}</span><a href="${esc(J.withKey(f.url))}" target="_blank" rel="noopener">öffnen</a></div>`).join("")}` : ""}
          ${m.result ? `<h4 class="muted" style="margin:12px 0 6px">Ergebnis & Prüfung</h4><p style="white-space:pre-wrap">${esc(m.result)}</p>` : ""}</div>
          <div class="w6"><h4 class="muted" style="margin-bottom:8px">Live-Protokoll</h4><ul class="feed">${m.events.slice().reverse().map(feedItem).join("")}</ul></div></div></div>`;
      if (scrollPos) $(".feed", box).scrollTop = scrollPos;
      $("[data-x]", box).onclick = () => { this.open = 0; box.innerHTML = ""; };
      $$("[data-c]", box).forEach((b) => (b.onclick = () => busyBtn(b, async () => { await call(`/api/missions/${id}/${b.dataset.c}`, { body: {} }); this.detail(id); this.loadList(); })));
      $$("[data-ap]", box).forEach((b) => (b.onclick = () => busyBtn(b, async () => { await call(`/api/approvals/${b.dataset.ap}`, { body: { approve: b.dataset.v === "1" } }); this.detail(id); J.updateBadges(); })));
      if (!quiet) box.scrollIntoView({ behavior: "smooth", block: "start" });
    },
    second(body) {
      body.innerHTML = `<div class="panel"><h3>Second Opinion</h3><p class="muted" style="margin-top:0">Mehrere KI-Modelle beantworten die Frage unabhängig. Danach vergleicht JARVIS die Antworten, findet Widersprüche und bildet eine geprüfte Antwort.</p>
        <form class="form" id="so-form"><textarea id="so-q" rows="3" placeholder="Wichtige Frage, z. B. Lohnt sich eine Solaranlage für ein Einfamilienhaus in Bayern?"></textarea><div><button class="btn primary" id="so-btn">⚖️ Prüfen lassen</button></div></form><div id="so-out"></div></div>`;
      $("#so-form", body).onsubmit = (e) => {
        e.preventDefault();
        busyBtn($("#so-btn", body), async () => {
          $("#so-out", body).innerHTML = '<p class="muted">Modelle arbeiten … (bis zu einer Minute)</p>';
          const r = await call("/api/second-opinion", { body: { question: $("#so-q", body).value } });
          $("#so-out", body).innerHTML = `<h4 style="margin:14px 0 6px;color:var(--accent)">Geprüfte Antwort</h4><p style="white-space:pre-wrap">${esc(r.summary)}</p>
            <div class="cols">${r.answers.map((a) => `<div class="panel"><h3>${esc(a.provider)} · ${esc(a.model.split("/").pop())}</h3><p style="white-space:pre-wrap;font-size:13.5px">${esc(a.text || "Fehler: " + (a.error || "keine Antwort"))}</p></div>`).join("")}</div>`;
        });
      };
    },
  };

  // ============================================================ Gedächtnis
  function memoryForm(m = {}, kinds = KIND_DE, onDone) {
    const body = J.modal(m.id ? "Erinnerung bearbeiten" : "Neue Erinnerung", `<form class="form" id="mf">
      <label class="f">Inhalt<textarea name="content" rows="4" required></textarea></label>
      <div class="two"><label class="f">Art<select name="kind">${Object.entries(kinds).filter(([k]) => !k.endsWith("node") && k !== "tag" && k !== "task").map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select></label>
      <label class="f">Wichtigkeit<select name="importance"><option value="3">hoch</option><option value="2">normal</option><option value="1">niedrig</option></select></label></div>
      <div class="two"><label class="f">Titel (optional)<input name="title"></label><label class="f">Projekt (optional)<input name="project"></label></div>
      <label class="f">Schlagwörter (Komma, Personen als person:Name)<input name="tags"></label>
      <div class="row end"><button class="btn primary">Speichern</button></div></form>`);
    const f = $("#mf", body);
    f.content.value = m.content || ""; f.kind.value = m.kind || "fact"; f.importance.value = String(m.importance || 2);
    f.title.value = m.title || ""; f.project.value = m.project || ""; f.tags.value = (m.tags || []).join(", ");
    f.onsubmit = async (e) => {
      e.preventDefault();
      const data = { content: f.content.value, kind: f.kind.value, importance: Number(f.importance.value), title: f.title.value, project: f.project.value,
        tags: f.tags.value.split(",").map((t) => t.trim()).filter(Boolean) };
      try { await call(m.id ? `/api/memory/${m.id}` : "/api/memory", { method: m.id ? "PUT" : "POST", body: data }); $("#modal").close(); toast("Gespeichert", "ok"); onDone(); }
      catch (err) { toast(err.message, "err"); }
    };
  }
  pages.memory = {
    kind: "", q: "", project: "",
    async show(el) {
      el.innerHTML = `<div class="section-title"><h2>Langzeitgedächtnis</h2><button class="btn primary sm" id="mem-add">＋ Erinnerung</button><button class="btn sm" data-go="knowledge">🕸️ Memory Map</button></div>
        <div id="mem-stats" class="stats" style="margin-bottom:14px"></div>
        <div class="row" style="margin:0 0 12px"><input id="mem-q" placeholder="Suchen …" style="flex:1;min-width:160px"><select id="mem-proj"><option value="">Alle Projekte</option></select></div>
        <div class="tabs" id="mem-kinds"></div><ul class="list" id="mem-list"></ul>`;
      $("#mem-add", el).onclick = () => memoryForm({}, KIND_DE, () => this.load(el));
      let t = 0;
      $("#mem-q", el).value = this.q;
      $("#mem-q", el).oninput = (e) => { clearTimeout(t); t = setTimeout(() => { this.q = e.target.value.trim(); this.load(el); }, 250); };
      $("#mem-proj", el).onchange = (e) => { this.project = e.target.value; this.load(el); };
      this.load(el);
    },
    async load(el) {
      const p = new URLSearchParams({ kind: this.kind, q: this.q, project: this.project });
      let d;
      try { d = await call("/api/memory?" + p); } catch (e) { $("#mem-list", el).innerHTML = `<li class="empty error">${esc(e.message)}</li>`; return; }
      $("#mem-stats", el).innerHTML = `<div class="stat"><small>Gesamt</small><b class="big">${d.stats.total}</b></div>` + Object.entries(d.kinds).map(([k, v]) => `<div class="stat"><small>${esc(v)}</small><b style="color:${KIND_COLORS[k]}">${d.stats.by_kind[k] || 0}</b></div>`).join("");
      const ps = $("#mem-proj", el);
      ps.innerHTML = '<option value="">Alle Projekte</option>' + d.projects.map((x) => `<option>${esc(x)}</option>`).join(""); ps.value = this.project;
      $("#mem-kinds", el).innerHTML = [["", "Alle"], ...Object.entries(d.kinds)].map(([k, v]) => `<button data-k="${k}" class="${this.kind === k ? "on" : ""}">${esc(v)}</button>`).join("");
      $$("#mem-kinds button", el).forEach((b) => (b.onclick = () => { this.kind = b.dataset.k; this.load(el); }));
      const ul = $("#mem-list", el);
      ul.innerHTML = d.items.length ? "" : '<li class="empty">Keine Einträge. Sag im Chat z. B. „Merk dir, dass ich vegetarisch esse“.</li>';
      d.items.forEach((m) => {
        const li = document.createElement("li"); li.className = "item";
        li.innerHTML = `<i class="dot" style="background:${KIND_COLORS[m.kind]};margin-top:7px"></i><div class="body"><b></b><p></p>
          <small class="muted"><span class="pill dim">${esc(d.kinds[m.kind] || m.kind)}</span> ${m.importance === 3 ? '<span class="pill warn">wichtig</span>' : ""} ${m.project ? `<span class="pill ok">${esc(m.project)}</span>` : ""} ${m.tags.map((t) => `<span class="pill dim">#${esc(t)}</span>`).join(" ")} · ${m.source === "agent" ? "automatisch gelernt" : m.source === "user" ? "von dir" : esc(m.source)} · ${fmtDate(m.updated)}</small></div>
          <div class="acts"><button class="btn sm" data-e>Bearbeiten</button><button class="btn sm danger" data-d>Löschen</button></div>`;
        li.querySelector("b").textContent = m.title; li.querySelector("p").textContent = m.content;
        $("[data-e]", li).onclick = () => memoryForm(m, d.kinds, () => this.load(el));
        $("[data-d]", li).onclick = async () => { if (!confirm("Diese Erinnerung löschen?")) return; try { await call(`/api/memory/${m.id}`, { method: "DELETE" }); this.load(el); } catch (e) { toast(e.message, "err"); } };
        ul.append(li);
      });
    },
  };

  // ============================================================ Projekte & Aufgaben
  function taskForm(t = {}, onDone) {
    const body = J.modal(t.id ? "Aufgabe bearbeiten" : "Neue Aufgabe", `<form class="form" id="tf">
      <label class="f">Titel<input name="title" required></label>
      <div class="two"><label class="f">Frist (z. B. morgen, 2026-12-01 14:00)<input name="due"></label><label class="f">Priorität<select name="priority"><option value="1">hoch</option><option value="2">normal</option><option value="3">niedrig</option></select></label></div>
      <div class="two"><label class="f">Geplanter Start (Tagesplan)<input name="start" placeholder="morgen 09:00"></label><label class="f">Dauer (Min)<input name="duration" type="number" min="0" max="1440"></label></div>
      <div class="two"><label class="f">Projekt<input name="project"></label><label class="f">Erinnerung um<input name="remind_at" placeholder="morgen 08:30"></label></div>
      <label class="f">Notizen<textarea name="notes" rows="3"></textarea></label>
      <div class="row end"><button class="btn primary">Speichern</button></div></form>`);
    const f = $("#tf", body);
    ["title", "due", "start", "project", "notes"].forEach((k) => (f[k].value = t[k] || ""));
    f.priority.value = String(t.priority || 2); f.duration.value = t.duration || "";
    f.onsubmit = async (e) => {
      e.preventDefault();
      const data = { title: f.title.value, due: f.due.value, priority: Number(f.priority.value), start: f.start.value, duration: Number(f.duration.value || 0),
        project: f.project.value, notes: f.notes.value };
      if (f.remind_at.value) data.remind_at = f.remind_at.value;
      try { await call(t.id ? `/api/tasks/${t.id}` : "/api/tasks", { method: t.id ? "PUT" : "POST", body: data }); $("#modal").close(); toast("Gespeichert", "ok"); onDone(); }
      catch (err) { toast(err.message, "err"); }
    };
  }
  pages.projects = {
    tab: "plan", day: "heute", status: "open",
    show(el) {
      el.innerHTML = `<div class="tabs">${[["plan", "📅 Tagesplanung"], ["tasks", "✅ Aufgaben"], ["projects", "📁 Projekte (Project Brain)"]].map(([k, l]) => `<button data-tab="${k}" class="${this.tab === k ? "on" : ""}">${l}</button>`).join("")}</div><div id="pj-body"></div>`;
      $$("[data-tab]", el).forEach((b) => (b.onclick = () => { this.tab = b.dataset.tab; this.show(el); }));
      const body = $("#pj-body", el);
      ({ plan: () => this.plan(body), tasks: () => this.tasks(body), projects: () => this.projects(body) })[this.tab]();
    },
    async plan(body) {
      const o = await call("/api/tasks/day?day=" + encodeURIComponent(this.day));
      const conflictIds = new Set(o.conflicts.join(" ").match(/#\d+/g) || []);
      body.innerHTML = `<div class="row" style="margin:0 0 14px">${["heute", "morgen", "übermorgen"].map((d) => `<button class="btn sm ${this.day === d ? "primary" : ""}" data-day="${d}">${d[0].toUpperCase() + d.slice(1)}</button>`).join("")}<input type="date" id="pl-date" value="${esc(o.date)}"></div>
        <div class="hud-grid">
          ${panel(`Zeitplan ${new Date(o.date).toLocaleDateString("de-DE", { weekday: "long", day: "numeric", month: "long" })}`, o.scheduled.length ? `<div class="timeline">${o.scheduled.map((x) => `<div class="tl ${conflictIds.has("#" + x.id) ? "conflict" : ""}"><time>${esc(x.start.slice(11, 16))}</time><div>${esc(x.title)} <small class="muted">${x.duration || 30} Min</small></div></div>`).join("")}</div>` : '<p class="empty">Noch nichts eingeplant.</p>', "w6")}
          ${panel("Analyse", `<div class="kv"><span>Überschneidungen</span><b class="${o.conflicts.length ? "error" : "ok"}">${o.conflicts.length || "keine"}</b></div>${o.conflicts.map((c) => `<p class="error" style="font-size:13px;margin:4px 0">⚠ ${esc(c)}</p>`).join("")}
            <div class="kv"><span>Freie Zeitfenster (8–20 Uhr)</span><b>${o.free_slots.map(([a, b]) => `${a}–${b}`).join(", ") || "keine"}</b></div>
            <div class="kv"><span>Noch zu planen</span><b>${o.unscheduled.length}</b></div>
            <div class="row"><button class="btn primary" id="pl-ai">🤖 JARVIS plant den Tag</button><button class="btn" id="pl-add">＋ Aufgabe</button></div>
            <p class="muted" style="font-size:12.5px">JARVIS priorisiert, erkennt Konflikte und schlägt einen Zeitplan vor. Eingetragen wird erst nach deiner Freigabe.</p>`, "w6")}
          ${panel("Offen & fällig", o.unscheduled.length ? `<ul class="list">${o.unscheduled.map((t) => `<li class="item"><div class="body"><b>${esc(t.title)}</b><p>${prio[t.priority]} ${t.due ? "Frist " + esc(t.due) : ""} ${t.project ? "· " + esc(t.project) : ""}</p></div></li>`).join("")}</ul>` : '<p class="empty">Nichts offen.</p>', "w12")}
        </div>`;
      $$("[data-day]", body).forEach((b) => (b.onclick = () => { this.day = b.dataset.day; this.plan(body); }));
      $("#pl-date", body).onchange = (e) => { this.day = e.target.value; this.plan(body); };
      $("#pl-ai", body).onclick = () => J.ask(`Plane meinen Tag (${this.day}, ${o.date}): priorisiere meine Aufgaben, erkenne Konflikte, nutze freie Zeitfenster mit Pausen und schlag mir den Zeitplan zur Freigabe vor.`);
      $("#pl-add", body).onclick = () => taskForm({}, () => this.plan(body));
    },
    async tasks(body) {
      const list = await call("/api/tasks?status=" + this.status);
      body.innerHTML = `<div class="row" style="margin:0 0 12px"><button class="btn primary sm" id="tk-add">＋ Aufgabe</button>${[["open", "Offen"], ["done", "Erledigt"], ["all", "Alle"]].map(([k, l]) => `<button class="btn sm ${this.status === k ? "primary" : ""}" data-st="${k}">${l}</button>`).join("")}</div><ul class="list" id="tk-list"></ul>`;
      $("#tk-add", body).onclick = () => taskForm({}, () => this.tasks(body));
      $$("[data-st]", body).forEach((b) => (b.onclick = () => { this.status = b.dataset.st; this.tasks(body); }));
      const ul = $("#tk-list", body);
      ul.innerHTML = list.length ? "" : '<li class="empty">Keine Aufgaben. Sag im Chat z. B. „Erinnere mich morgen um 9 an den Zahnarzt“.</li>';
      list.forEach((t) => {
        const li = document.createElement("li"); li.className = "item" + (t.status === "done" ? " done" : "");
        li.innerHTML = `<label class="switch" title="Erledigt"><input type="checkbox" ${t.status === "done" ? "checked" : ""}></label><div class="body"><b></b>
          <p>${prio[t.priority]} ${t.due ? "📅 " + esc(t.due) : ""} ${t.start ? "🕒 " + esc(t.start.slice(11, 16) || t.start) : ""} ${t.project ? "📁 " + esc(t.project) : ""} ${t.remind_at ? "🔔 " + fmtDate(t.remind_at) : ""}</p>${t.notes ? `<p>${esc(t.notes)}</p>` : ""}</div>
          <div class="acts"><button class="btn sm" data-e>Bearbeiten</button><button class="btn sm danger" data-d>Löschen</button></div>`;
        li.querySelector("b").textContent = t.title;
        $("input", li).onchange = async (e) => { try { await call(`/api/tasks/${t.id}`, { method: "PUT", body: { status: e.target.checked ? "done" : "open" } }); this.tasks(body); } catch (err) { toast(err.message, "err"); } };
        $("[data-e]", li).onclick = () => taskForm(t, () => this.tasks(body));
        $("[data-d]", li).onclick = async () => { if (!confirm("Aufgabe löschen?")) return; await call(`/api/tasks/${t.id}`, { method: "DELETE" }).catch((e) => toast(e.message, "err")); this.tasks(body); };
        ul.append(li);
      });
    },
    async projects(body) {
      const list = await call("/api/projects");
      body.innerHTML = list.length ? `<div class="cols">${list.map((p) => `<div class="panel"><h3><span class="grow">${esc(p.name)}</span>${p.progress != null ? `<span class="pill">${p.progress}%</span>` : ""}</h3>
        <div class="bar-prog"><i style="width:${p.progress || 0}%"></i></div>
        <div class="kv"><span>Offen / erledigt</span><b>${p.tasks_open} / ${p.tasks_done}</b></div><div class="kv"><span>Überfällig</span><b class="${p.overdue ? "error" : ""}">${p.overdue}</b></div>
        <div class="kv"><span>Wissen</span><b>${p.memories} Einträge</b></div><div class="kv"><span>Letzte Aktivität</span><b>${ago(p.last_activity)}</b></div>
        ${p.risks.length ? `<p class="warn-t" style="font-size:13px">⚠ ${p.risks.map(esc).join("<br>⚠ ")}</p>` : '<p class="ok" style="font-size:13px">✓ Keine Risiken erkannt</p>'}
        ${p.next.length ? `<p class="muted" style="font-size:13px"><b>Nächste Schritte:</b><br>${p.next.map(esc).join("<br>")}</p>` : ""}
        ${p.decisions.length ? `<p class="muted" style="font-size:13px"><b>Entscheidungen:</b><br>${p.decisions.map((d) => "• " + esc(d)).join("<br>")}</p>` : ""}
        <div class="row tight"><button class="btn sm" data-rep="${esc(p.name)}">📄 Statusbericht</button><button class="btn sm" data-ask="${esc(p.name)}">💬 Besprechen</button></div></div>`).join("")}</div>
        <p class="muted" style="font-size:12.5px">Projekte entstehen automatisch aus Aufgaben und Erinnerungen mit Projektnamen. Risiken (Engpässe, Überfälliges, Stillstand) werden aus echten Daten berechnet.</p>`
        : '<p class="empty">Noch keine Projekte. Lege eine Aufgabe oder Erinnerung mit einem Projektnamen an – oder sag „Merk dir: Projekt X …“.</p>';
      $$("[data-rep]", body).forEach((b) => (b.onclick = () => busyBtn(b, async () => { const r = await call(`/api/projects/${encodeURIComponent(b.dataset.rep)}/report`, { body: {} }); J.modal("Statusbericht: " + b.dataset.rep, `<p style="white-space:pre-wrap">${esc(r.text)}</p>`); })));
      $$("[data-ask]", body).forEach((b) => (b.onclick = () => J.ask(`Lass uns über mein Projekt „${b.dataset.ask}“ sprechen. Was ist der Stand, welche Risiken siehst du und was sollte ich als Nächstes tun?`)));
    },
  };

  // ============================================================ Automation Center
  const DAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
  function describeTrigger(t, meta) {
    if (t.type === "daily") return `täglich ${t.time}`;
    if (t.type === "weekly") return `${(t.days || []).map((d) => DAYS[d]).join(", ")} ${t.time}`;
    if (t.type === "interval") return `alle ${t.hours} Std`;
    return meta.triggers[t.type] || t.type;
  }
  function automationForm(a, meta, onDone) {
    a = a || { name: "", enabled: true, trigger: { type: "daily", time: "08:00" }, condition: { type: "none" }, action: { type: "daily_report" }, notify: { inapp: true, email: false } };
    const opts = (obj, cur) => Object.entries(obj).map(([k, v]) => `<option value="${k}" ${k === cur ? "selected" : ""}>${esc(v)}</option>`).join("");
    const body = J.modal(a.id ? "Automation bearbeiten" : "Neue Automation", `<form class="form" id="af">
      <label class="f">Name<input name="name" placeholder="z. B. Morgenbericht"></label>
      <div class="panel"><h3>① Auslöser</h3><div class="two"><select name="ttype">${opts(meta.triggers, a.trigger.type)}</select><input name="time" type="time" value="${esc(a.trigger.time || "08:00")}"></div>
        <div class="row tight" id="af-days">${DAYS.map((d, i) => `<label class="switch"><input type="checkbox" value="${i}" ${(a.trigger.days || [0]).includes(i) ? "checked" : ""}><span>${d}</span></label>`).join("")}</div>
        <label class="f" id="af-hours">Alle X Stunden<input name="hours" type="number" min="0.25" step="0.25" value="${esc(a.trigger.hours || 1)}"></label></div>
      <div class="panel"><h3>② Bedingung</h3><div class="two"><select name="ctype">${opts(meta.conditions, a.condition.type)}</select><input name="cvalue" placeholder="Wert (Text oder Anzahl)" value="${esc(a.condition.value || "")}"></div></div>
      <div class="panel"><h3>③ Aktion & Agent</h3><div class="two"><select name="atype">${opts(meta.actions, a.action.type)}</select><select name="agent">${opts(meta.agents, a.action.agent || "master")}</select></div>
        <label class="f" id="af-prompt">Auftrag / Thema / Projekt / Text<textarea name="prompt" rows="3">${esc(a.action.prompt || a.action.topic || a.action.project || a.action.text || "")}</textarea></label></div>
      <div class="panel"><h3>④ Benachrichtigung</h3><div class="row tight"><label class="switch"><input type="checkbox" name="inapp" ${a.notify.inapp !== false ? "checked" : ""}><span>In der App</span></label>
        <label class="switch"><input type="checkbox" name="email" ${a.notify.email ? "checked" : ""}><span>Per E-Mail an mich</span></label></div></div>
      <label class="switch"><input type="checkbox" name="enabled" ${a.enabled !== false ? "checked" : ""}><span>Aktiv</span></label>
      <div class="row end"><button class="btn primary">Speichern</button></div></form>`);
    const f = $("#af", body);
    f.name.value = a.name || "";
    const sync = () => {
      const t = f.ttype.value, at = f.atype.value;
      f.time.hidden = !["daily", "weekly"].includes(t);
      $("#af-days", body).hidden = t !== "weekly";
      $("#af-hours", body).hidden = t !== "interval";
      f.cvalue.hidden = !["contains", "open_tasks"].includes(f.ctype.value);
      f.agent.hidden = at !== "agent";
      $("#af-prompt", body).hidden = !["agent", "research", "project_report", "notify"].includes(at);
    };
    [f.ttype, f.ctype, f.atype].forEach((s) => (s.onchange = sync)); sync();
    f.onsubmit = async (e) => {
      e.preventDefault();
      const t = f.ttype.value, at = f.atype.value, txt = f.prompt.value.trim();
      const trigger = { type: t };
      if (["daily", "weekly"].includes(t)) trigger.time = f.time.value || "08:00";
      if (t === "weekly") trigger.days = $$("#af-days input:checked", body).map((x) => Number(x.value));
      if (t === "interval") trigger.hours = Number(f.hours.value || 1);
      const action = { type: at };
      if (at === "agent") { action.agent = f.agent.value; action.prompt = txt; }
      if (at === "research") action.topic = txt;
      if (at === "project_report") action.project = txt;
      if (at === "notify") action.text = txt;
      const data = { name: f.name.value, enabled: f.enabled.checked, trigger, condition: { type: f.ctype.value, value: f.cvalue.value },
        action, notify: { inapp: f.inapp.checked, email: f.email.checked } };
      try { await call(a.id ? `/api/automations/${a.id}` : "/api/automations", { method: a.id ? "PUT" : "POST", body: data }); $("#modal").close(); toast("Automation gespeichert", "ok"); onDone(); }
      catch (err) { toast(err.message, "err"); }
    };
  }
  pages.automations = {
    async show(el) {
      let d;
      try { d = await call("/api/automations"); } catch (e) { el.innerHTML = `<p class="error">${esc(e.message)}</p>`; return; }
      const names = Object.fromEntries(d.items.map((a) => [a.id, a.name]));
      el.innerHTML = `<div class="section-title"><h2>Automation Center</h2><button class="btn primary sm" id="au-new">＋ Neue Automation</button></div>
        <div class="banner ${d.scheduler ? "info" : "warn"}"><span><i class="dot ${d.scheduler ? "on" : "off"}"></i> Zeitplaner ${d.scheduler ? "läuft (prüft jede Minute)" : "läuft nicht"}.
        Render (kostenlos) schläft nach 15 Min ohne Besuch ein – ${d.cron ? "✓ externer Wecker ist eingerichtet (JARVIS_CRON_TOKEN)." : "für pünktliche Automationen einen kostenlosen Wecker einrichten (siehe README: JARVIS_CRON_TOKEN + cron-job.org)."}</span></div>
        <div class="panel" style="margin-bottom:14px"><h3>Vorlagen</h3><div class="qc">${d.meta.templates.map((t, i) => `<button data-tpl="${i}">＋ ${esc(t.name)}</button>`).join("")}</div></div>
        <div class="panel" style="margin-bottom:14px"><h3>Deine Automationen</h3><ul class="list" id="au-list"></ul></div>
        <div class="panel"><h3>Laufprotokoll</h3><div class="scroll-x"><table class="t"><thead><tr><th>Zeit</th><th>Automation</th><th>Status</th><th>Dauer</th><th>Ergebnis</th></tr></thead><tbody>
        ${d.runs.map((r) => `<tr><td>${fmtDate(r.ts)}</td><td>${esc(names[r.automation_id] || "#" + r.automation_id)}</td><td>${statusPill(r.status)}</td><td>${r.duration}s</td><td><details><summary>${esc(r.output.slice(0, 70))}</summary><p style="white-space:pre-wrap">${esc(r.output)}</p></details></td></tr>`).join("") || '<tr><td colspan="5" class="empty">Noch keine Läufe.</td></tr>'}
        </tbody></table></div></div>`;
      const reload = () => this.show(el);
      $("#au-new", el).onclick = () => automationForm(null, d.meta, reload);
      $$("[data-tpl]", el).forEach((b) => (b.onclick = () => automationForm(JSON.parse(JSON.stringify(d.meta.templates[b.dataset.tpl])), d.meta, reload)));
      const ul = $("#au-list", el);
      ul.innerHTML = d.items.length ? "" : '<li class="empty">Noch keine Automation – nimm eine Vorlage.</li>';
      d.items.forEach((a) => {
        const li = document.createElement("li"); li.className = "item";
        li.innerHTML = `<label class="switch" title="Aktiv"><input type="checkbox" ${a.enabled ? "checked" : ""}></label><div class="body"><b></b>
          <p>⏱ ${esc(describeTrigger(a.trigger, d.meta))} ${a.condition.type !== "none" ? "· wenn " + esc(d.meta.conditions[a.condition.type]) + (a.condition.value ? ` „${esc(a.condition.value)}“` : "") : ""} → ${esc(d.meta.actions[a.action.type])}${a.action.agent ? " (" + esc(d.meta.agents[a.action.agent]) + ")" : ""}</p>
          <p>Nächster Lauf: ${a.enabled && a.next_run ? fmtDate(a.next_run) : "–"} · Letzter: ${a.last_run ? ago(a.last_run) : "nie"}</p></div>
          <div class="acts"><button class="btn sm" data-r>▶ Jetzt ausführen</button><button class="btn sm" data-e>Bearbeiten</button><button class="btn sm danger" data-d>Löschen</button></div>`;
        li.querySelector("b").textContent = a.name;
        $("input", li).onchange = async (e) => { await call(`/api/automations/${a.id}`, { method: "PUT", body: { ...a, enabled: e.target.checked } }).catch((err) => toast(err.message, "err")); reload(); };
        $("[data-r]", li).onclick = (e) => busyBtn(e.target, async () => { const r = await call(`/api/automations/${a.id}/run`, { body: {} }); J.modal(a.name, `<p>${statusPill(r.status)}</p><p style="white-space:pre-wrap">${esc(r.output)}</p>`); reload(); });
        $("[data-e]", li).onclick = () => automationForm(a, d.meta, reload);
        $("[data-d]", li).onclick = async () => { if (!confirm("Automation löschen?")) return; await call(`/api/automations/${a.id}`, { method: "DELETE" }); reload(); };
        ul.append(li);
      });
    },
  };

  // ============================================================ Browser (Web-Leser)
  pages.browser = {
    show(el) {
      el.innerHTML = `<div class="panel"><h3>Web-Leser</h3><form class="row" id="br-form" style="margin-top:0"><input type="url" id="br-url" placeholder="https://…" required style="flex:1;min-width:200px"><button class="btn primary">Öffnen</button></form>
        <p class="muted" style="font-size:12.5px">JARVIS lädt die Seite auf dem Server und zeigt den Text. Klicks, Logins und Formulare auf fremden Seiten sind technisch nicht möglich – dafür ehrlich keine Fake-Buttons.</p>
        <div id="br-actions" class="row tight" hidden><button class="btn sm" id="br-sum">🧠 Zusammenfassen</button><button class="btn sm" id="br-ask">💬 Frage dazu</button><button class="btn sm" id="br-pdf">📄 Als PDF-Bericht</button><a class="btn sm" id="br-open" target="_blank" rel="noopener">↗ Original öffnen</a></div>
        <div id="br-out" style="margin-top:12px"></div></div>`;
      const url = () => $("#br-url", el).value.trim();
      $("#br-form", el).onsubmit = async (e) => {
        e.preventDefault();
        const out = $("#br-out", el); out.innerHTML = '<p class="muted">Lade …</p>';
        try { const r = await call("/api/browse", { body: { url: url() } }); out.innerHTML = `<div class="reader"></div>`; $(".reader", out).textContent = r.text; $("#br-actions", el).hidden = false; $("#br-open", el).href = url(); }
        catch (err) { out.innerHTML = `<p class="error">${esc(err.message)}</p>`; }
      };
      $("#br-sum", el).onclick = () => J.ask(`Lies ${url()} und fasse das Wichtigste kurz zusammen.`);
      $("#br-ask", el).onclick = () => { J.go("chat"); const i = $("#input"); i.value = `Lies ${url()} und beantworte: `; i.focus(); };
      $("#br-pdf", el).onclick = () => J.ask(`Lies ${url()} und erstelle daraus einen übersichtlichen PDF-Bericht mit Quellenangabe.`);
    },
  };

  // ============================================================ Dateien
  pages.files = {
    folder: "", q: "",
    async show(el) {
      this.el = el;
      el.innerHTML = `<div class="section-title"><h2>Dateien</h2><button class="btn primary sm" id="fl-up">⬆ Hochladen</button><button class="btn sm" id="fl-sort">🗂️ In Ordner sortieren</button><button class="btn sm danger" id="fl-clear">Alle löschen</button></div>
        <input type="file" id="fl-input" multiple hidden>
        <div class="row" style="margin:0 0 12px"><input id="fl-q" placeholder="Dateien filtern …" style="flex:1"></div><div class="tabs" id="fl-folders"></div><ul class="list" id="fl-list"></ul>
        <p class="muted" style="font-size:12.5px">Alle Dateien werden zusätzlich in der Datenbank gesichert und nach einem Server-Neustart wiederhergestellt.</p>`;
      $("#fl-up", el).onclick = () => $("#fl-input", el).click();
      $("#fl-input", el).onchange = (e) => { this.upload(e.target.files); e.target.value = ""; };
      $("#fl-sort", el).onclick = (e) => busyBtn(e.target, async () => { const r = await call("/api/files/sort", { body: {} }); toast(r.text.split("\n")[0], "ok"); this.load(); });
      $("#fl-clear", el).onclick = async () => { if (!confirm("Wirklich ALLE Dateien löschen?")) return; await call("/api/files/delete-all", { body: {} }); this.load(); };
      $("#fl-q", el).value = this.q;
      $("#fl-q", el).oninput = (e) => { this.q = e.target.value.toLowerCase(); this.render(); };
      this.load();
    },
    async upload(files) {
      for (const f of Array.from(files || [])) {
        if (f.size > 8 * 1024 * 1024) { toast(`${f.name}: max. 8 MB`, "err"); continue; }
        const data = await new Promise((res) => { const r = new FileReader(); r.onload = () => res(r.result); r.readAsDataURL(f); });
        try { await call("/api/files/upload", { body: { name: f.name, data } }); toast(`${f.name} hochgeladen`, "ok"); } catch (e) { toast(`${f.name}: ${e.message}`, "err"); }
      }
      this.load();
    },
    async load() {
      try { this.files = await call("/api/files?limit=300"); } catch (e) { toast(e.message, "err"); this.files = []; }
      this.render();
    },
    render() {
      const el = this.el, files = this.files || [];
      const folders = [...new Set(files.map((f) => f.folder))].sort();
      $("#fl-folders", el).innerHTML = [["", `Alle (${files.length})`], ...folders.map((f) => [f || "/", f || "Hauptordner"])].map(([k, l]) => `<button data-f="${esc(k)}" class="${this.folder === k ? "on" : ""}">${esc(l)}</button>`).join("");
      $$("#fl-folders button", el).forEach((b) => (b.onclick = () => { this.folder = b.dataset.f; this.render(); }));
      const shown = files.filter((f) => (!this.folder || (this.folder === "/" ? f.folder === "" : f.folder === this.folder)) && (!this.q || f.path.toLowerCase().includes(this.q)));
      const ul = $("#fl-list", el);
      ul.innerHTML = shown.length ? "" : '<li class="empty">Keine Dateien.</li>';
      shown.forEach((f) => {
        const ext = (f.name.split(".").pop() || "").toLowerCase();
        const li = document.createElement("li"); li.className = "item";
        li.innerHTML = `${J.IMG_EXT.includes(ext) ? `<img src="${esc(J.withKey(f.url))}" alt="" style="width:44px;height:44px;object-fit:cover;border-radius:10px">` : `<span class="badge ${esc(ext)}">${esc(ext.toUpperCase().slice(0, 4))}</span>`}
          <div class="body"><b></b><p>${esc(f.folder || "Hauptordner")} · ${f.upload ? "hochgeladen" : "erstellt"} · ${fmtDate(f.time)} · ${Math.max(1, Math.round(f.size / 1024))} KB</p></div>
          <div class="acts"><a class="btn sm" target="_blank" rel="noopener">Öffnen</a><button class="btn sm" data-a>🧠 Analysieren</button><button class="btn sm danger" data-d>Löschen</button></div>`;
        li.querySelector("b").textContent = f.name;
        const a = li.querySelector("a"); a.href = J.withKey(f.url);
        $("[data-a]", li).onclick = () => J.ask(`Lies die Datei ${f.path} und fasse sie kurz zusammen.`);
        $("[data-d]", li).onclick = async () => { if (await J.deleteFile(f.path, f.name)) this.load(); };
        ul.append(li);
      });
    },
  };

  // ============================================================ Wissensnetz + Self-Improvement
  let graph = null;
  pages.knowledge = {
    async show(el) {
      el.innerHTML = `<div class="section-title"><h2>Knowledge Graph / Memory Map</h2><input id="kg-q" placeholder="Hervorheben …" style="max-width:220px"><button class="btn sm" id="kg-reset">Ansicht zurücksetzen</button></div>
        <div class="legend">${["fact", "preference", "goal", "decision", "projectnode", "personnode", "tag", "task"].map((k) => `<span><i style="background:${KIND_COLORS[k]}"></i>${KIND_DE[k]}</span>`).join("")}</div>
        <div class="graph-wrap panel"><canvas id="kg-canvas"></canvas><div class="graph-info glass" id="kg-info" hidden></div></div>
        <div class="panel" style="margin-top:14px"><h3><span class="grow">Self-Improvement – gelernte Regeln</span><button class="btn sm" id="ls-run">🔍 Fehler analysieren</button></h3>
          <p class="muted" style="margin-top:0;font-size:13px">JARVIS analysiert Fehler, schlechte Bewertungen (👎) und Modell-Ausfälle und schlägt Regeln vor. Aktiv werden sie nur mit deiner Zustimmung. Sicherheitsregeln und eigener Programmcode werden dabei nie verändert.</p>
          <ul class="list" id="ls-list"></ul></div>`;
      graph?.stop();
      let data;
      try { data = await call("/api/memory/graph"); } catch (e) { toast(e.message, "err"); data = { nodes: [], edges: [] }; }
      const info = $("#kg-info", el);
      graph = graphView($("#kg-canvas", el), data, (n) => {
        info.hidden = !n;
        if (n) info.innerHTML = `<b>${esc(n.label)}</b> <span class="pill dim">${esc(KIND_DE[n.group] || n.group)}</span>${n.content ? `<p style="margin:4px 0 0">${esc(n.content)}</p>` : ""}${n.mid ? `<div class="row tight"><button class="btn sm" data-go="memory">Im Gedächtnis bearbeiten</button></div>` : ""}`;
      });
      $("#kg-q", el).oninput = (e) => graph.highlight(e.target.value);
      $("#kg-reset", el).onclick = () => graph.reset();
      $("#ls-run", el).onclick = (e) => busyBtn(e.target, async () => { const r = await call("/api/lessons/analyze", { body: {} }); toast(r.length ? `${r.length} Vorschläge` : "Keine Probleme gefunden – nichts vorzuschlagen.", "ok"); this.lessons(el); });
      this.lessons(el);
    },
    hide() { graph?.stop(); graph = null; },
    async lessons(el) {
      const list = await call("/api/lessons").catch(() => []);
      const ul = $("#ls-list", el);
      ul.innerHTML = list.length ? "" : '<li class="empty">Noch keine Vorschläge.</li>';
      list.forEach((l) => {
        const li = document.createElement("li"); li.className = "item";
        li.innerHTML = `<div class="body"><b></b><p></p><small class="muted">${fmtDate(l.ts)}</small></div><div class="acts">${statusPill(l.status === "active" ? "ok" : l.status === "rejected" ? "denied" : "pending")}
          ${l.status !== "active" ? '<button class="btn sm ok" data-s="active">Übernehmen</button>' : ""}${l.status !== "rejected" ? '<button class="btn sm danger" data-s="rejected">Verwerfen</button>' : ""}</div>`;
        li.querySelector("b").textContent = l.text; li.querySelector("p").textContent = l.reason;
        $$("[data-s]", li).forEach((b) => (b.onclick = async () => { await call(`/api/lessons/${l.id}`, { body: { status: b.dataset.s } }); this.lessons(el); }));
        ul.append(li);
      });
    },
  };

  // ============================================================ KI-Modelle
  const TIER_DE = { fast: "Schnell (kurze Fragen)", smart: "Stark (Standard)", code: "Code", research: "Recherche", vision: "Bilder" };
  pages.models = {
    async show(el) {
      let d;
      try { d = await call("/api/models"); } catch (e) { el.innerHTML = `<p class="error">${esc(e.message)}</p>`; return; }
      const label = Object.fromEntries(d.providers.map((p) => [p.id, p.label]));
      el.innerHTML = `<div class="section-title"><h2>KI-Modelle & Anbieter</h2></div>
        <div class="cols" style="margin-bottom:14px">${d.providers.map((p) => `<div class="panel"><h3><span class="grow">${esc(p.label)}</span>${p.configured ? '<span class="pill ok">aktiv</span>' : '<span class="pill dim">nicht eingerichtet</span>'}</h3>
          <div class="kv"><span>Kosten</span><b>${p.free ? "kostenlos (mit Limits)" : "kostenpflichtig – Preise beim Anbieter"}</b></div>
          ${p.configured ? `<div class="kv"><span>Anfragen (24 h)</span><b>${p.calls_24h}</b></div><div class="kv"><span>Erfolgsquote</span><b>${p.success_rate ?? "–"}%</b></div>
            <div class="kv"><span>Ø Antwortzeit</span><b>${p.avg_latency ?? "–"} s</b></div><div class="kv"><span>Tokens (ca., 24 h)</span><b>${p.tokens_24h.toLocaleString("de-DE")}</b></div>
            ${p.last_error ? `<p class="error" style="font-size:12px">Letzter Fehler: ${esc(p.last_error)}</p>` : ""}
            <details><summary class="muted" style="font-size:13px">Modelle</summary>${Object.entries(p.models).map(([t, ms]) => `<p style="font-size:12.5px;margin:4px 0"><b>${esc(TIER_DE[t] || t)}:</b> ${ms.map(esc).join(", ") || "–"}</p>`).join("")}</details>
            <div class="row tight"><button class="btn sm" data-test="${p.id}">⚡ Verbindung testen</button><span class="muted" data-res="${p.id}" style="font-size:13px"></span></div>`
          : `<p class="muted" style="font-size:13px">Bei Render → Environment <code>${esc(p.env)}</code> eintragen.${p.site ? ` Key: <a href="${esc(/^https?:/.test(p.site) ? p.site : "https://" + p.site)}" target="_blank" rel="noopener">${esc(p.site.replace(/^https?:\/\//, ""))}</a>` : ""}</p>`}</div>`).join("")}</div>
        <div class="panel" style="margin-bottom:14px"><h3>Automatische Modellwahl (Reihenfolge je Aufgabentyp)</h3>
          <p class="muted" style="margin-top:0;font-size:13px">JARVIS erkennt den Aufgabentyp und nimmt den ersten verfügbaren Anbieter. Fällt einer aus (Limit, Fehler), springt er automatisch zum nächsten.</p>
          <div class="cols">${Object.entries(d.order).map(([tier, order]) => `<div><b>${esc(TIER_DE[tier] || tier)}</b><ol class="steps-list" data-tier="${tier}" style="margin-top:6px">${order.map((p) => `<li data-p="${p}" style="display:flex;gap:6px;align-items:center;padding:6px 10px"><span style="flex:1">${esc(label[p] || p)}${d.providers.find((x) => x.id === p)?.configured ? "" : ' <small class="muted">(nicht eingerichtet)</small>'}</span><button class="btn sm" data-up>↑</button><button class="btn sm" data-down>↓</button></li>`).join("")}</ol></div>`).join("")}</div></div>
        <div class="panel" style="margin-bottom:14px"><h3>Nutzung (24 h)</h3><canvas class="chart" id="md-chart" style="height:150px"></canvas></div>
        <div class="panel"><h3>Letzte Anfragen</h3><div class="scroll-x"><table class="t"><thead><tr><th>Zeit</th><th>Anbieter</th><th>Modell</th><th>Status</th><th>Dauer</th><th>Tokens</th></tr></thead><tbody>
          ${d.recent.map((r) => `<tr><td>${fmtTime(r.ts)}</td><td>${esc(r.provider)}</td><td>${esc(r.model)}</td><td>${r.ok ? '<span class="pill ok">ok</span>' : `<span class="pill err" title="${esc(r.error)}">Fehler</span>`}</td><td>${r.latency}s</td><td>${r.tokens_in + r.tokens_out}</td></tr>`).join("") || '<tr><td colspan="6" class="empty">Noch keine Anfragen.</td></tr>'}</tbody></table></div></div>`;
      usageChart($("#md-chart", el), d.usage);
      $$("[data-test]", el).forEach((b) => (b.onclick = () => busyBtn(b, async () => {
        const r = await call("/api/models/test", { body: { provider: b.dataset.test } });
        const out = $(`[data-res="${b.dataset.test}"]`, el);
        out.className = r.ok ? "ok" : "error"; out.textContent = r.ok ? `✓ ${r.latency}s · ${r.model}` : `✕ ${r.message}`;
      })));
      $$("ol[data-tier]", el).forEach((ol) => {
        $$("li", ol).forEach((li) => {
          const move = async (dir) => {
            const sib = dir < 0 ? li.previousElementSibling : li.nextElementSibling;
            if (!sib) return;
            dir < 0 ? ol.insertBefore(li, sib) : ol.insertBefore(sib, li);
            try { await call("/api/models/order", { method: "PUT", body: { tier: ol.dataset.tier, order: $$("li", ol).map((x) => x.dataset.p) } }); toast("Reihenfolge gespeichert", "ok"); }
            catch (e) { toast(e.message, "err"); }
          };
          $("[data-up]", li).onclick = () => move(-1); $("[data-down]", li).onclick = () => move(1);
        });
      });
    },
  };

  // ============================================================ System Monitor + Live Intelligence Center
  let monTimer = 0;
  pages.monitor = {
    filter: { kind: "", agent: "", q: "" },
    async show(el) {
      el.innerHTML = `<div class="hud-grid">
        <div class="panel w6"><h3>Server</h3><div class="gauges"><div class="gauge"><canvas></canvas></div><div class="gauge"><canvas></canvas></div><div class="gauge"><canvas></canvas></div></div><div id="mo-kv"></div></div>
        <div class="panel w6"><h3>Dienste</h3><div id="mo-status"></div></div>
        <div class="panel w12"><h3>KI-Anfragen (24 h)</h3><canvas class="chart" id="mo-chart"></canvas></div>
        <div class="panel w12"><h3><span class="grow">Live Intelligence Center – vollständiger Verlauf</span><span class="pill" id="mo-live">live</span></h3>
          <div class="row" style="margin:0 0 10px"><select id="mo-kind"><option value="">Alle Arten</option><option value="agent">Agenten</option><option value="ok">Erfolge</option><option value="info">Info</option><option value="warn,security">Warnungen & Sicherheit</option><option value="err">Fehler</option></select>
          <input id="mo-agent" placeholder="Agent (z. B. Coding Agent)"><input id="mo-q" placeholder="Suche …" style="flex:1"></div>
          <ul class="feed tall" id="mo-feed"></ul></div></div>`;
      const f = this.filter;
      $("#mo-kind", el).value = f.kind; $("#mo-agent", el).value = f.agent; $("#mo-q", el).value = f.q;
      let t = 0;
      const upd = () => { f.kind = $("#mo-kind", el).value; f.agent = $("#mo-agent", el).value.trim(); f.q = $("#mo-q", el).value.trim(); this.lastId = 0; this.feed(el, true); };
      $("#mo-kind", el).onchange = upd;
      ["#mo-agent", "#mo-q"].forEach((s) => ($(s, el).oninput = () => { clearTimeout(t); t = setTimeout(upd, 300); }));
      this.lastId = 0;
      this.tick(el); this.feed(el, true);
      clearInterval(monTimer);
      monTimer = setInterval(() => { if (!document.hidden) { this.tick(el); this.feed(el, false); } }, 4000);
    },
    hide() { clearInterval(monTimer); },
    async tick(el) {
      try {
        const d = await call("/api/dashboard");
        const s = d.system, cvs = $$(".gauges canvas", el);
        gauge(cvs[0], s.cpu, "CPU-Last"); gauge(cvs[1], s.ram, "RAM"); gauge(cvs[2], s.disk, "Speicher");
        $("#mo-kv", el).innerHTML = `<div class="kv"><span>Laufzeit</span><b>${fmtUptime(s.uptime)}</b></div><div class="kv"><span>Offene Sitzungen</span><b>${s.sessions}</b></div>
          <div class="kv"><span>Threads</span><b>${s.threads}</b></div><div class="kv"><span>Datenbank</span><b>${esc(s.db.kind)} · ${s.db.persistent ? "dauerhaft" : "nur bis Neustart"}</b></div><div class="kv"><span>Gesicherte Dateien</span><b>${s.db.files_mb} MB</b></div>`;
        $("#mo-status", el).innerHTML = d.status.map((x) => `<div class="kv"><span><i class="dot ${x.ok ? "on" : "off"}"></i> ${esc(x.label)}</span><b>${esc(x.detail)}</b></div>`).join("");
        usageChart($("#mo-chart", el), d.usage);
      } catch { /* offline */ }
    },
    async feed(el, full) {
      const f = this.filter, p = new URLSearchParams({ limit: full ? 200 : 50, kind: f.kind, agent: f.agent, q: f.q, after: full ? 0 : this.lastId || 0 });
      try {
        const rows = await call("/api/events?" + p);
        const ul = $("#mo-feed", el); if (!ul) return;
        if (full) ul.innerHTML = rows.length ? rows.map(feedItem).join("") : '<li class="empty">Keine Einträge.</li>';
        else if (rows.length) { ul.querySelector(".empty")?.remove(); ul.insertAdjacentHTML("afterbegin", rows.map(feedItem).join("")); $$("li", ul).slice(0, rows.length).forEach((li) => li.classList.add("new")); }
        if (rows.length) this.lastId = Math.max(this.lastId || 0, rows[0].id);
      } catch { /* offline */ }
    },
  };

  // ============================================================ Security Center
  pages.security = {
    async show(el) {
      let d;
      try { d = await call("/api/security"); } catch (e) { el.innerHTML = `<p class="error">${esc(e.message)}</p>`; return; }
      const pending = d.approvals.filter((a) => a.status === "pending");
      el.innerHTML = `<div class="hud-grid">
        <div class="panel w12"><h3>Offene Freigaben (${pending.length})</h3><ul class="list" id="sc-pending">${pending.length ? "" : '<li class="empty">Nichts wartet auf dich.</li>'}</ul></div>
        <div class="panel w6"><h3>Berechtigungen</h3>${d.permissions.map((p) => `<div class="kv"><label class="switch"><input type="checkbox" data-perm="${p.key}" ${p.value ? "checked" : ""}><span>${esc(p.label)}</span></label></div>`).join("")}
          <p class="muted" style="font-size:12.5px">Immer mit Freigabe: ${Object.values(d.sensitive).map(esc).join(", ")}. Käufe und Zahlungen kann JARVIS grundsätzlich nicht ausführen.</p></div>
        <div class="panel w6"><h3>Schutz & Schlüssel</h3>
          <div class="kv"><span>Passwortschutz</span><b class="${d.password ? "ok" : "error"}">${d.password ? "aktiv" : "AUS – JARVIS_WEB_PASSWORD setzen!"}</b></div>
          <div class="kv"><span>Automatische Sperre</span><b>nach 5 Min im Hintergrund</b></div>
          <div class="kv"><span>Datenbank</span><b class="${d.database.persistent ? "ok" : "warn-t"}">${esc(d.database.kind)} · ${d.database.persistent ? "dauerhaft" : "nicht dauerhaft (DATABASE_URL fehlt)"}</b></div>
          ${Object.entries(d.keys).map(([k, v]) => `<div class="kv"><span><code>${esc(k)}</code></span><b class="${v ? "ok" : "muted"}">${v ? "gesetzt (nur auf dem Server)" : "nicht gesetzt"}</b></div>`).join("")}
          <p class="muted" style="font-size:12.5px">API-Keys liegen nur in den Render-Umgebungsvariablen – nie im Browser oder auf GitHub.</p></div>
        <div class="panel w6"><h3>Freigabe-Verlauf</h3><ul class="feed">${d.approvals.filter((a) => a.status !== "pending").map((a) => `<li><time>${fmtTime(a.ts)}</time><i class="k ${a.status === "done" ? "ok" : a.status === "denied" ? "warn" : "err"}"></i><span>${esc(a.summary.split("\n")[0])} <small>${esc(STATUS_DE[a.status] || a.status)}${a.agent ? " · " + esc(a.agent) : ""}${a.result ? " · " + esc(a.result.slice(0, 120)) : ""}</small></span></li>`).join("") || '<li class="empty">Noch nichts.</li>'}</ul></div>
        <div class="panel w6"><h3>Sicherheitsprotokoll</h3><ul class="feed">${d.audit.map(feedItem).join("") || '<li class="empty">Keine Einträge.</li>'}</ul></div></div>`;
      const ul = $("#sc-pending", el);
      pending.forEach((a) => {
        const li = document.createElement("li"); li.className = "item";
        li.innerHTML = `<div class="body"><b></b><p></p><small class="muted">${fmtDate(a.ts)} · ${esc(a.agent || "")}${a.mission_id ? " · Mission #" + a.mission_id : ""}</small></div><div class="acts"><button class="btn sm primary" data-v="1">Freigeben</button><button class="btn sm danger" data-v="0">Ablehnen</button></div>`;
        const [first, ...rest] = a.summary.split("\n");
        li.querySelector("b").textContent = first; li.querySelector("p").textContent = rest.join("\n");
        $$("[data-v]", li).forEach((b) => (b.onclick = () => busyBtn(b, async () => {
          const r = await call(`/api/approvals/${a.id}`, { body: { approve: b.dataset.v === "1" } });
          toast(r.status === "done" ? "Ausgeführt" : r.status === "denied" ? "Abgelehnt" : r.status === "failed" ? "Fehler: " + r.result : "Freigegeben", r.status === "failed" ? "err" : "ok");
          J.updateBadges(); this.show(el);
        })));
        ul.append(li);
      });
      $$("[data-perm]", el).forEach((c) => (c.onchange = async () => {
        try { await call("/api/permissions", { method: "PUT", body: { key: c.dataset.perm, value: c.checked } }); toast("Gespeichert", "ok"); }
        catch (e) { toast(e.message, "err"); c.checked = !c.checked; }
      }));
    },
  };

  // ============================================================ Einstellungen
  const WIDGET_DE = { core: "AI Core", status: "System Status", agents: "Aktive Agenten", timeline: "Mission Timeline", feed: "Live Feed", llm: "LLM Status", monitor: "System Monitor", memory: "Memory Center", commands: "Quick Commands", voice: "Voice Control" };
  pages.settings = {
    async show(el) {
      await J.loadSettings();
      const ui = J.ui;
      let widgets = [...(ui.widgets || DEFAULT_WIDGETS)];
      el.innerHTML = `<div class="hud-grid">
        <div class="panel w6"><h3>Stimme</h3><form class="form" id="st-form">
          <label class="f">Stimme<select name="voice">${Object.entries(J.voices).map(([k, v]) => `<option value="${esc(k)}" ${k === ui.voice ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></label>
          <div class="row tight"><button type="button" class="btn sm" id="st-test">🔊 Probehören</button></div>
          <label class="switch"><input type="checkbox" name="speak_answers" ${ui.speak_answers ? "checked" : ""}><span>Antworten im Chat vorlesen (Standard)</span></label>
          <label class="switch"><input type="checkbox" name="continuous" ${ui.continuous ? "checked" : ""}><span>Dauergespräch (nach jeder Antwort weiter zuhören)</span></label>
          <label class="switch"><input type="checkbox" name="wake_word" ${ui.wake_word ? "checked" : ""}><span>Wake Word „Hey JARVIS“</span></label>
          <p class="muted" style="font-size:12.5px;margin:0">${J.wake.supported ? "Funktioniert nur, solange JARVIS geöffnet und im Vordergrund ist (Browser-Grenze). Unterbrechen: einfach auf den Kern tippen." : "⚠ Dieser Browser unterstützt keine dauerhafte Spracherkennung – das Wake Word geht hier nicht. Nutze die Sprechen-Taste."}</p>
        </form></div>
        <div class="panel w6"><h3>Command Center – Widgets</h3><ul class="list" id="st-widgets"></ul></div>
        <div class="panel w6"><h3>Quick Commands</h3><textarea id="st-qc" rows="6" style="width:100%">${esc((ui.quick_commands || []).join("\n"))}</textarea><p class="muted" style="font-size:12.5px">Ein Befehl pro Zeile.</p></div>
        <div class="panel w6"><h3>Konto & Verbindungen</h3>
          <div class="row tight"><button class="btn" id="st-mail">📬 E-Mail verbinden / prüfen</button><button class="btn" data-action="new">💬 Neues Gespräch</button><button class="btn" data-action="lock">🔒 Jetzt sperren</button></div>
          <h4 class="muted" style="margin:14px 0 6px">Tastenkürzel</h4>
          <div class="kv"><span>Suche</span><b>Strg/⌘ + K</b></div><div class="kv"><span>Bereiche wechseln</span><b>Alt + 1 … 9</b></div><div class="kv"><span>Chat-Eingabe</span><b>/</b></div><div class="kv"><span>Stimme stoppen</span><b>Esc</b></div>
          <h4 class="muted" style="margin:14px 0 6px">Ehrliche Grenzen</h4>
          <p class="muted" style="font-size:12.5px;margin:0">Computer-Steuerung: nicht verfügbar (nur iPhone, kein PC-Connector). Webseiten: lesen ja, klicken/Formulare nein. Käufe/Zahlungen: nie. Hintergrund-Automationen brauchen auf Render (gratis) einen externen Wecker.</p></div>
        <div class="w12 row end"><button class="btn primary" id="st-save">Einstellungen speichern</button></div></div>`;
      const renderW = () => {
        const ul = $("#st-widgets", el);
        const all = [...widgets, ...Object.keys(WIDGETS).filter((w) => !widgets.includes(w))];
        ul.innerHTML = all.map((w) => `<li class="item" data-w="${w}" style="align-items:center"><label class="switch"><input type="checkbox" ${widgets.includes(w) ? "checked" : ""}><span>${esc(WIDGET_DE[w])}</span></label><div class="acts" style="margin-left:auto;width:auto"><button class="btn sm" data-up>↑</button><button class="btn sm" data-down>↓</button></div></li>`).join("");
        $$("li", ul).forEach((li) => {
          const w = li.dataset.w;
          $("input", li).onchange = (e) => { widgets = e.target.checked ? [...widgets, w] : widgets.filter((x) => x !== w); renderW(); };
          const mv = (d) => { const i = widgets.indexOf(w); if (i < 0) return; const j = i + d; if (j < 0 || j >= widgets.length) return; [widgets[i], widgets[j]] = [widgets[j], widgets[i]]; renderW(); };
          $("[data-up]", li).onclick = () => mv(-1); $("[data-down]", li).onclick = () => mv(1);
        });
      };
      renderW();
      const f = $("#st-form", el);
      $("#st-test", el).onclick = async () => {
        J.voice.unlock(); J.voice.stop();
        try {
          const r = await J.api("/api/tts?text=" + encodeURIComponent("Hallo, ich bin JARVIS. So klinge ich mit dieser Stimme.") + "&voice=" + encodeURIComponent(f.voice.value));
          if (!r.ok) throw new Error("Stimme gerade nicht verfügbar");
          const url = URL.createObjectURL(await r.blob());
          J.voice.el.src = url; J.voice.el.onended = () => URL.revokeObjectURL(url); await J.voice.el.play();
        } catch (e) { toast(e.message, "err"); }
      };
      $("#st-mail", el).onclick = () => J.openMail();
      $("#st-save", el).onclick = (e) => busyBtn(e.target, async () => {
        const body = { ui: { voice: f.voice.value, speak_answers: f.speak_answers.checked, continuous: f.continuous.checked, wake_word: f.wake_word.checked,
          widgets, quick_commands: $("#st-qc", el).value.split("\n").map((x) => x.trim()).filter(Boolean).slice(0, 12) } };
        const r = await call("/api/settings", { method: "PUT", body });
        J.setSettings(r.ui);
        $("#btn-voice").setAttribute("aria-pressed", r.ui.speak_answers ? "true" : "false"); J.store.set("jarvis-voice", r.ui.speak_answers ? "1" : "0");
        $("#sp-auto").checked = !!r.ui.continuous; J.store.set("jarvis-auto", r.ui.continuous ? "1" : "0");
        toast("Gespeichert", "ok");
      });
    },
  };
})();
