/* JARVIS – Weboberfläche (Chat, Sprache, E-Mail) */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const store = {
    get(k, d = null) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch { return d; } },
    set(k, v) { try { v === null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch { /* egal */ } },
  };
  const newId = () => (crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2) + Date.now().toString(36));

  let info = {};
  let password = store.get("jarvis-key", "");
  let sessionId = store.get("jarvis-session") || newId();
  store.set("jarvis-session", sessionId);
  let busy = false;
  let mode = ["dash", "write", "speak"].includes(store.get("jarvis-mode")) ? store.get("jarvis-mode") : "dash";

  const headers = (extra = {}) => {
    const h = { ...extra };
    if (password) h["X-Jarvis-Key"] = password;
    const mt = store.get("jarvis-mail-token");
    if (mt) h["X-Mail-Token"] = mt;
    return h;
  };
  const withKey = (url) => (password ? `${url}${url.includes("?") ? "&" : "?"}key=${encodeURIComponent(password)}` : url);

  // ------------------------------------------------------------ Anzeige
  const log = $("#log"), input = $("#input");
  const STATE_HERO = { listening: "HÖRT ZU", thinking: "DENKT NACH", speaking: "SPRICHT" };
  function setState(text, cls = "") {
    document.querySelectorAll("[data-state-text]").forEach((el) => { el.textContent = text; });
    $("#hero-state").textContent = "v2.0 · " + (STATE_HERO[cls] || "BEREIT");
    document.body.classList.remove("listening", "thinking", "speaking");
    if (cls) document.body.classList.add(cls);
    if (mode === "speak") {
      const big = { listening: "Ich höre zu …", thinking: "Ich denke nach …", speaking: "JARVIS spricht …" }[cls];
      $("#sp-state").textContent = big || (busy ? "…" : "Tippe auf den Kern und sprich");
    }
  }
  function refresh() {
    if (recog.active) setState("Hört zu …", "listening");
    else if (busy) setState(voice.active ? "Spricht …" : "Denkt nach …", voice.active ? "speaking" : "thinking");
    else if (voice.active) setState("Spricht …", "speaking");
    else setState("Bereit");
  }
  const scroll = () => {
    log.scrollTop = log.scrollHeight;
    const talk = document.querySelector(".sp-talk");
    if (talk) talk.scrollTop = talk.scrollHeight;
  };
  function hideWelcome() { $("#welcome")?.remove(); }

  function bubble(who, text, cls = "") {
    hideWelcome();
    const el = document.createElement("div");
    el.className = `msg ${who} ${cls}`.trim();
    el.textContent = text;
    log.append(el); scroll();
    return el;
  }
  function status(text) {
    const el = document.createElement("div");
    el.className = "status";
    el.textContent = "⟳ " + text + " …";
    log.append(el); scroll();
  }
  function fileCard(name, url, target = log) {
    const ext = (name.split(".").pop() || "").toLowerCase();
    if (["png", "jpg", "jpeg", "webp", "gif", "svg"].includes(ext)) {
      const card = document.createElement("div");
      card.className = "card pic";
      card.innerHTML = '<a class="imglink" target="_blank" rel="noopener"><img alt=""></a><div class="row2"><div class="title"></div><a class="dl">Laden</a></div>';
      const src = withKey(url);
      card.querySelector("img").src = src;
      card.querySelector("img").alt = name;
      card.querySelector(".imglink").href = src;
      card.querySelector(".title").textContent = name;
      const dl = card.querySelector(".dl"); dl.href = src; dl.download = name;
      card.querySelector("img").onload = scroll;
      target.append(card); scroll();
      return;
    }
    const el = document.createElement("div");
    el.className = "card";
    el.innerHTML = '<div class="badge"></div><div class="meta"><div class="title"></div><div class="sub">Zum Herunterladen bereit</div></div><a>Laden</a>';
    el.querySelector(".badge").textContent = ext.toUpperCase().slice(0, 4);
    el.querySelector(".badge").classList.add(ext);
    el.querySelector(".title").textContent = name;
    const a = el.querySelector("a"); a.href = withKey(url); a.download = name;
    target.append(el); scroll();
  }
  function mailCard(ev, target = log) {
    const el = document.createElement("div");
    el.className = "card mail-card";
    el.innerHTML = '<div class="sent"></div><div class="title"></div><pre></pre>';
    el.querySelector(".sent").textContent = "✓ Gesendet an " + ev.to;
    el.querySelector(".title").textContent = ev.subject || "";
    el.querySelector("pre").textContent = ev.text || "";
    target.append(el); scroll();
  }

  // ------------------------------------------------------------ Stimme (kostenlose Browserstimme)
  function clean(t) {
    return t.replace(/```[\s\S]*?```/g, " ").replace(/https?:\/\/\S+/g, "").replace(/[*_#`>|]+/g, "")
      .replace(/^\s*[-•]\s+/gm, "").replace(/\s+/g, " ").trim();
  }
  class Sentences {
    constructor() { this.buf = ""; }
    feed(chunk) {
      this.buf += chunk;
      const out = [], re = /([.!?…:;])\s+|\n+/g;
      let start = 0, m;
      while ((m = re.exec(this.buf))) {
        const piece = this.buf.slice(start, m.index + (m[1] ? 1 : 0)).trim();
        if (piece.length >= 20) { out.push(piece); start = re.lastIndex; }
      }
      this.buf = this.buf.slice(start);
      return out;
    }
    flush() { const r = this.buf.trim(); this.buf = ""; return r ? [r] : []; }
  }
  const voice = {
    active: false, pending: 0, chosen: null, raf: 0,
    on() { return mode === "speak" || $("#btn-voice").getAttribute("aria-pressed") === "true"; },
    pick() {
      if (this.chosen || !("speechSynthesis" in window)) return this.chosen;
      const de = speechSynthesis.getVoices().filter((v) => v.lang && v.lang.toLowerCase().startsWith("de"));
      this.chosen = de.find((v) => /natural|neural|online|premium|enhanced|siri/i.test(v.name)) || de[0] || null;
      return this.chosen;
    },
    unlock() { // iPhone: Sprachausgabe muss einmal durch Antippen freigeschaltet werden
      if (this.unlocked || !("speechSynthesis" in window)) return;
      this.unlocked = true;
      try { const u = new SpeechSynthesisUtterance(" "); u.volume = 0; speechSynthesis.speak(u); } catch { /* egal */ }
    },
    say(text) {
      text = clean(text);
      if (!text || !this.on() || !("speechSynthesis" in window)) return;
      const u = new SpeechSynthesisUtterance(text);
      u.lang = "de-DE"; u.rate = 1.05;
      const v = this.pick(); if (v) u.voice = v;
      const end = () => { this.pending = Math.max(0, this.pending - 1); if (!this.pending) this.done(); };
      u.onend = end; u.onerror = end;
      this.pending++;
      if (!this.active) { this.active = true; this.animate(); refresh(); }
      speechSynthesis.speak(u);
    },
    animate() {
      const tick = (t) => {
        document.documentElement.style.setProperty("--level", this.active ? (0.3 + 0.3 * Math.abs(Math.sin(t / 150))).toFixed(2) : "0");
        if (this.active) this.raf = requestAnimationFrame(tick);
      };
      this.raf = requestAnimationFrame(tick);
    },
    stop() { if ("speechSynthesis" in window) speechSynthesis.cancel(); this.pending = 0; this.done(); },
    done() {
      if (!this.active) return;
      this.active = false;
      document.documentElement.style.setProperty("--level", "0");
      refresh(); listenAgain();
    },
  };
  if ("speechSynthesis" in window) speechSynthesis.onvoiceschanged = () => { voice.chosen = null; };

  // ------------------------------------------------------------ Mikrofon
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const recog = {
    active: false, r: null, text: "",
    start() {
      if (!SR) { bubble("jarvis", "Spracheingabe geht in diesem Browser nicht. Bitte Safari, Chrome oder Edge verwenden – oder einfach tippen.", "error"); return; }
      if (this.active || busy) return;
      voice.stop();
      const r = new SR();
      r.lang = "de-DE"; r.interimResults = true; r.continuous = false;
      this.text = "";
      r.onresult = (e) => {
        let interim = "";
        for (let i = e.resultIndex; i < e.results.length; i++) {
          if (e.results[i].isFinal) this.text += e.results[i][0].transcript;
          else interim += e.results[i][0].transcript;
        }
        const live = (this.text + " " + interim).trim();
        if (mode === "speak") { $("#sp-user").textContent = live; $("#sp-answer").textContent = ""; }
        else $("#interim").textContent = live;
      };
      r.onerror = (e) => {
        if (e.error === "not-allowed" || e.error === "service-not-allowed") {
          bubble("jarvis", "Kein Zugriff aufs Mikrofon. Bitte in den Browser-Einstellungen erlauben.", "error");
          $("#sp-auto").checked = false;
        }
      };
      r.onend = () => {
        this.active = false; this.r = null; $("#interim").textContent = "";
        const t = this.text.trim(); refresh();
        if (mode === "write") {  // Schreiben: Diktat landet im Eingabefeld, du kannst es noch ändern
          if (t) { input.value = (input.value ? input.value.trimEnd() + " " : "") + t; grow(); input.focus(); }
          return;
        }
        if (t) send(t); else listenAgain();
      };
      this.r = r; this.active = true; refresh();
      try { r.start(); } catch { this.active = false; refresh(); }
    },
    stop() { this.r?.stop(); },
    abort() { if (this.r) { this.text = ""; this.r.abort(); } },
  };
  const convOn = () => mode === "speak" && $("#sp-auto").checked;
  function listenAgain() {
    if (convOn() && !busy && !voice.active && !recog.active) setTimeout(() => { if (convOn() && !busy && !voice.active && !recog.active) recog.start(); }, 400);
  }

  // ------------------------------------------------------------ Chat
  // ------------------------------------------------------------ Anhänge
  const MAX_FILES = 4, MAX_DOC = 8 * 1024 * 1024;
  let pending = [];
  const IMG_RE = /^image\//;

  function readAsDataURL(file) {
    return new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(file); });
  }
  async function shrinkImage(file) {
    // Fotos verkleinern (max. 1600 px, JPEG) – schneller Upload, weniger Datenvolumen
    const url = URL.createObjectURL(file);
    try {
      const img = await new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = rej; i.src = url; });
      const scale = Math.min(1, 1600 / Math.max(img.naturalWidth, img.naturalHeight));
      const c = document.createElement("canvas");
      c.width = Math.round(img.naturalWidth * scale); c.height = Math.round(img.naturalHeight * scale);
      const g = c.getContext("2d"); g.fillStyle = "#fff"; g.fillRect(0, 0, c.width, c.height);
      g.drawImage(img, 0, 0, c.width, c.height);
      return c.toDataURL("image/jpeg", 0.85);
    } catch { return readAsDataURL(file); } finally { URL.revokeObjectURL(url); }
  }
  async function addFiles(list) {
    for (const file of Array.from(list || [])) {
      if (pending.length >= MAX_FILES) { bubble("jarvis", `Maximal ${MAX_FILES} Dateien pro Nachricht.`, "error"); break; }
      const isImg = IMG_RE.test(file.type) || /\.(jpe?g|png|webp|gif|heic|bmp)$/i.test(file.name);
      if (!isImg && file.size > MAX_DOC) { bubble("jarvis", `„${file.name}“ ist zu groß (max. 8 MB).`, "error"); continue; }
      const item = { name: file.name || "foto.jpg", type: file.type, isImg, data: null, preview: isImg ? URL.createObjectURL(file) : null };
      pending.push(item); renderStrip();
      try { item.data = isImg ? await shrinkImage(file) : await readAsDataURL(file); }
      catch { pending = pending.filter((p) => p !== item); bubble("jarvis", `„${file.name}“ konnte nicht gelesen werden.`, "error"); }
      if (isImg && item.data) item.name = item.name.replace(/\.[^.]+$/, "") + ".jpg";
      renderStrip();
    }
    input.focus();
  }
  function renderStrip() {
    const strip = $("#strip");
    strip.innerHTML = "";
    strip.hidden = pending.length === 0;
    pending.forEach((p) => {
      const el = document.createElement("div");
      el.className = "att" + (p.data ? "" : " loading");
      if (p.preview) { const im = document.createElement("img"); im.src = p.preview; im.alt = p.name; el.append(im); }
      else el.append(document.createTextNode("📄 " + p.name.slice(0, 18)));
      const x = document.createElement("button");
      x.className = "x"; x.type = "button"; x.textContent = "✕"; x.setAttribute("aria-label", p.name + " entfernen");
      x.onclick = () => { pending = pending.filter((q) => q !== p); renderStrip(); };
      el.append(x); strip.append(el);
    });
  }

  async function send(text) {
    text = (text || "").trim();
    if (busy || (!text && !pending.length)) return;
    if (pending.some((p) => !p.data)) { bubble("jarvis", "Einen Moment – die Datei wird noch vorbereitet.", "error"); return; }
    const attachments = pending.map((p) => ({ name: p.name, type: p.isImg ? "image/jpeg" : p.type, data: p.data }));
    const shown = pending.map((p) => ({ name: p.name, preview: p.preview }));
    pending = []; renderStrip();
    busy = true; $("#send").disabled = true;
    voice.stop();
    const ub = bubble("user", text || (shown.some((p) => p.preview) ? "📷" : "📄"));
    if (shown.length) {
      const t = document.createElement("div"); t.className = "thumbs";
      shown.forEach((p) => {
        if (p.preview) { const im = document.createElement("img"); im.src = p.preview; im.alt = p.name; t.append(im); }
        else { const sp = document.createElement("span"); sp.textContent = "📄 " + p.name; t.append(sp); }
      });
      ub.prepend(t);
    }
    refresh();
    let el = null;
    const sentences = new Sentences();
    const close = () => { el?.classList.remove("streaming"); el = null; };
    const spExtra = $("#sp-extra");
    feed("💬", text || "Datei gesendet", "Befehl an JARVIS", "info");
    msgCount++; renderBadges();
    $("#sp-user").textContent = text || "📎";
    $("#sp-answer").textContent = ""; $("#sp-status").textContent = ""; spExtra.innerHTML = "";

    const handle = (ev) => {
      if (ev.type === "text") {
        if (!el) el = bubble("jarvis", "", "streaming");
        el.textContent += ev.text; scroll();
        $("#sp-answer").textContent += ev.text;
        sentences.feed(ev.text).forEach((s) => voice.say(s));
      } else if (ev.type === "status") { close(); status(ev.text); $("#sp-status").textContent = "⟳ " + ev.text + " …"; }
      else if (ev.type === "file") { close(); fileCard(ev.name, ev.url); fileCard(ev.name, ev.url, spExtra); feed(fileIcon(ev.name), ev.name, "Datei erstellt", "ok"); }
      else if (ev.type === "mail_sent") { close(); mailCard(ev); mailCard(ev, spExtra); feed("✉️", "Mail an " + ev.to, ev.subject || "Gesendet", "ok"); }
      else if (ev.type === "mail_token") { store.set("jarvis-mail-token", ev.token); }
      else if (ev.type === "error") { close(); bubble("jarvis", ev.text, "error"); $("#sp-answer").textContent = ev.text; feed("⚠️", ev.text, "Fehler", "err"); }
    };

    try {
      const res = await fetch("/api/chat", {
        method: "POST", headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify({ session: sessionId, text, attachments }),
      });
      if (res.status === 401) { showLogin(); throw new Error("Bitte erneut anmelden."); }
      if (!res.ok || !res.body) throw new Error("Server antwortet nicht (" + res.status + ")");
      const reader = res.body.getReader(), dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let nl;
        while ((nl = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1);
          if (line) { try { handle(JSON.parse(line)); } catch (e) { console.warn(e); } }
        }
      }
    } catch (e) {
      close(); bubble("jarvis", "Verbindung unterbrochen. " + (e.message || ""), "error");
    } finally {
      sentences.flush().forEach((s) => voice.say(s));
      close(); busy = false; $("#send").disabled = false; $("#sp-status").textContent = "";
      refresh(); if (!voice.active) listenAgain();
      loadFiles();
    }
  }

  async function newChat() {
    voice.stop();
    try { await fetch("/api/reset", { method: "POST", headers: headers({ "Content-Type": "application/json" }), body: JSON.stringify({ session: sessionId }) }); } catch { /* egal */ }
    sessionId = newId(); store.set("jarvis-session", sessionId);
    location.reload();
  }

  // ------------------------------------------------------------ E-Mail
  let mailState = { connected: false };
  let mailPoll = 0;
  const dlg = $("#mail-dialog"), body = $("#mail-body");

  async function loadMail() {
    try {
      const r = await fetch(`/api/mail/status?session=${encodeURIComponent(sessionId)}`, { headers: headers() });
      if (!r.ok) return mailState;
      mailState = await r.json();
      if (mailState.token) store.set("jarvis-mail-token", mailState.token);
    } catch { /* offline */ }
    document.querySelectorAll("[data-mail-dot]").forEach((d) => d.classList.toggle("on", !!mailState.connected));
    renderMailPanel(); renderCore();
    refresh();
    return mailState;
  }

  function renderMail() {
    const s = mailState;
    body.innerHTML = "";
    const p = (html) => { const el = document.createElement("div"); el.innerHTML = html; body.append(el); return el; };
    const testButton = () => {
      const el = p('<div class="row"><button class="secondary" id="mail-test">Verbindung testen</button></div><p id="mail-test-result"></p>');
      el.querySelector("#mail-test").onclick = async (e) => {
        const out = el.querySelector("#mail-test-result");
        e.target.disabled = true; out.className = ""; out.textContent = "Teste …";
        try {
          const r = await fetch("/api/mail/test", { method: "POST", headers: headers({ "Content-Type": "application/json" }), body: JSON.stringify({ session: sessionId }) });
          const j = await r.json();
          out.className = j.ok ? "ok" : "error";
          out.textContent = (j.ok ? "✓ " : "✕ ") + j.message;
        } catch { out.className = "error"; out.textContent = "✕ Server nicht erreichbar"; }
        e.target.disabled = false;
      };
    };
    if (s.connected) {
      const el = p('<p class="ok">✓ Verbunden</p><p></p><p>Frag einfach: „Habe ich neue Mails?“, „Lies mir die Mail von … vor“ oder „Antworte ihm, dass …“.</p>');
      el.querySelectorAll("p")[1].textContent = s.address + (s.provider === "outlook" ? " (Outlook)" : "");
      if (s.provider === "outlook") {
        const row = p('<div class="row"><button class="secondary" id="mail-off">Trennen</button></div>');
        row.querySelector("#mail-off").onclick = async () => {
          await fetch("/api/mail/outlook/disconnect", { method: "POST", headers: headers({ "Content-Type": "application/json" }), body: JSON.stringify({ session: sessionId }) });
          store.set("jarvis-mail-token", null); await loadMail(); renderMail();
        };
      }
      testButton();
      return;
    }
    if (s.pending) {
      const el = p(`<p>1. Öffne <a target="_blank" rel="noopener"></a></p><p>2. Gib diesen Code ein:</p><div class="code"></div>
        <p>3. Melde dich mit deinem Outlook/Hotmail-Konto an und tippe auf „Akzeptieren“.</p>
        <div class="row"><button class="primary" id="copy-code">Code kopieren & Seite öffnen</button></div><p>Ich warte …</p>`);
      const a = el.querySelector("a"); a.href = s.pending.verification_uri; a.textContent = s.pending.verification_uri.replace(/^https?:\/\//, "");
      el.querySelector(".code").textContent = s.pending.user_code;
      el.querySelector("#copy-code").onclick = () => {
        navigator.clipboard?.writeText(s.pending.user_code).catch(() => {});
        window.open(s.pending.verification_uri, "_blank", "noopener");
      };
      return;
    }
    if (s.provider === "imap" && s.error) {
      const el = p('<p class="error"></p><p></p>');
      el.querySelectorAll("p")[0].textContent = "Problem mit der E-Mail-Einrichtung:";
      el.querySelectorAll("p")[1].textContent = s.error;
      testButton();
      return;
    }
    if (s.error) p('<p class="error"></p>').querySelector("p").textContent = "Anmeldung fehlgeschlagen: " + s.error;
    if (s.outlook_available) {
      const el = p('<p>Verbinde dein Outlook-/Hotmail-Postfach. Dein Passwort gibst du nur bei Microsoft ein.</p><div class="row"><button class="primary" id="ol-start">Outlook verbinden</button></div>');
      el.querySelector("#ol-start").onclick = async (e) => {
        e.target.disabled = true; e.target.textContent = "Starte …";
        const r = await fetch("/api/mail/outlook/start", { method: "POST", headers: headers({ "Content-Type": "application/json" }), body: JSON.stringify({ session: sessionId }) });
        if (!r.ok) { const j = await r.json().catch(() => ({})); mailState.error = j.detail || "Fehler " + r.status; renderMail(); return; }
        await loadMail(); renderMail(); startPolling();
      };
    } else {
      p(`<p>Noch kein Postfach eingerichtet. So geht's (bei Render → <b>Environment</b>):</p>
        <ul class="steps"><li><b>Outlook/Hotmail:</b> <code>MS_CLIENT_ID</code> eintragen (Anleitung im README).</li>
        <li><b>iCloud/Gmail/GMX/Web.de:</b> <code>MAIL_ADDRESS</code> und <code>MAIL_APP_PASSWORD</code> (App-Passwort) eintragen.</li></ul>`);
    }
  }

  function startPolling() {
    clearInterval(mailPoll);
    mailPoll = setInterval(async () => {
      await loadMail();
      if (dlg.open) renderMail();
      if (mailState.connected || !mailState.pending) clearInterval(mailPoll);
    }, 3000);
  }

  // ============================================================ HUD / Command Center
  let msgCount = 0, fileCount = 0;
  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmtTime = (ts) => new Date(ts).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
  const fileIcon = (name) => {
    const ext = (name.split(".").pop() || "").toLowerCase();
    return { pptx: "📊", docx: "📝", xlsx: "📈", pdf: "📕", png: "🖼️", jpg: "🖼️", jpeg: "🖼️", webp: "🖼️", mp3: "🔊", zip: "📦" }[ext] || "📄";
  };

  // Live-Feed: echte Aktionen dieser Sitzung (im Browser gespeichert)
  function feedItems() { try { return JSON.parse(store.get("jarvis-feed", "[]")) || []; } catch { return []; } }
  function feed(icon, title, sub, tag) {
    const items = feedItems();
    items.unshift({ icon, title: String(title).slice(0, 140), sub, tag, ts: Date.now() });
    store.set("jarvis-feed", JSON.stringify(items.slice(0, 30)));
    renderFeed();
  }
  function renderFeed() {
    const ul = $("#feed"); if (!ul) return;
    const items = feedItems();
    const tags = { info: "INFO", ok: "DONE", warn: "WARN", err: "ERROR" };
    ul.innerHTML = items.length ? items.map((i) => `<li><span class="fi">${esc(i.icon)}</span><div class="ft"><b>${esc(i.title)}</b><small>${esc(i.sub)} · ${fmtTime(i.ts)}</small></div><span class="tag ${esc(i.tag)}">${tags[i.tag] || "INFO"}</span></li>`).join("")
      : '<li class="empty">Noch keine Aktivität. Gib JARVIS einen Befehl – alles erscheint hier live.</li>';
  }

  // Dateien
  async function loadFiles() {
    try {
      const r = await fetch("/api/files", { headers: headers() });
      if (!r.ok) return;
      const files = await r.json();
      fileCount = files.length;
      const row = (f) => {
        const ext = (f.name.split(".").pop() || "").toLowerCase();
        const cls = ["png", "jpg", "jpeg", "webp", "gif"].includes(ext) ? "img" : ext;
        const when = new Date(f.time * 1000).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
        return `<li><span class="fx ${esc(cls)}">${esc(ext.toUpperCase().slice(0, 4))}</span><div class="fn"><b>${esc(f.name)}</b><small>${f.upload ? "Hochgeladen" : "Erstellt"} · ${when} · ${Math.max(1, Math.round(f.size / 1024))} KB</small></div><a href="${esc(withKey(f.url))}" download="${esc(f.name)}">Laden</a></li>`;
      };
      const empty = '<li class="empty">Noch keine Dateien – lass JARVIS etwas erstellen.</li>';
      $("#dash-files").innerHTML = files.length ? files.slice(0, 5).map(row).join("") : empty;
      $("#files-list").innerHTML = files.length ? files.map(row).join("") : empty;
      renderBadges(); renderCore();
    } catch { /* offline */ }
  }
  function renderBadges() {
    const bm = $("#badge-msgs"), bf = $("#badge-files");
    bm.hidden = !msgCount; bm.textContent = msgCount;
    bf.hidden = !fileCount; bf.textContent = fileCount;
  }

  // AI-Core-Übersicht (echte Zustände)
  function renderCore() {
    const ul = $("#core-list"); if (!ul) return;
    const srOk = !!(window.SpeechRecognition || window.webkitSpeechRecognition);
    const rows = [
      ["🧠", "AI Core", info.ai_ready ? "Groq · Aktiv" : "API-Key fehlt", info.ai_ready],
      ["🎙️", "Voice", srOk ? "Mikrofon & Stimme bereit" : "Nur Stimme (Browser)", srOk],
      ["✉️", "E-Mail", mailState.connected ? mailState.address : "Nicht verbunden", !!mailState.connected],
      ["🗂️", "Dateien", `${fileCount} gespeichert`, true],
      ["💬", "Gespräch", `${msgCount} Befehle in dieser Sitzung`, true],
      ["🛡️", "Sicherheit", info.needs_password ? "Passwortschutz aktiv" : "Lokal (ohne Passwort)", true],
    ];
    ul.innerHTML = rows.map(([i, t, sub, on]) => `<li><span class="ci">${i}</span><div><b>${esc(t)}</b><small class="${on ? "on" : "off"}">${esc(sub)}</small></div></li>`).join("");
  }
  function renderMailPanel() {
    const s = mailState;
    $("#mail-title").textContent = s.connected ? "Verbunden" : s.error ? "Problem" : "Nicht verbunden";
    $("#mail-sub").textContent = s.connected ? s.address : s.error ? "Tippe auf Verbindung" : "Tippe auf Verbindung";
    $("#dock-mail").textContent = s.connected ? s.address : "nicht verbunden";
  }

  // Systemmonitor (echte Server-Auslastung)
  async function loadSystem() {
    try {
      const r = await fetch("/api/system", { headers: headers() });
      if (!r.ok) return;
      const d = await r.json();
      const set = (id, v) => { const ring = $(id + " .ring"); ring.style.setProperty("--p", v ?? 0); ring.querySelector("b").textContent = v == null ? "–" : Math.round(v) + "%"; };
      set("#g-cpu", d.cpu); set("#g-ram", d.ram); set("#g-disk", d.disk);
      const high = (d.cpu ?? 0) > 85 || (d.ram ?? 0) > 90;
      const st = $("#sys-text");
      st.classList.toggle("warn", high);
      st.innerHTML = `<i class="led"></i>${high ? "AUSGELASTET" : "OPTIMAL"}`;
    } catch { /* offline */ }
  }

  // Uhr
  function tick() {
    const d = new Date();
    $("#time").textContent = d.toLocaleTimeString("de-DE");
    $("#date").textContent = d.toLocaleDateString("de-DE", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  }

  // ---------- Animierter 3D-Kern (Canvas) ----------
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const levelNow = () => parseFloat(document.documentElement.style.getPropertyValue("--level")) || 0;
  const tint = () => (document.body.classList.contains("listening") ? [255, 197, 66] : [56, 225, 255]);

  class Globe {
    constructor(canvas) {
      this.c = canvas; this.ctx = canvas.getContext("2d"); this.kind = canvas.dataset.size || "hero";
      this.rot = Math.random() * 6; this.pts = [];
      const latStep = this.kind === "hero" ? 12 : 15, lonStep = this.kind === "hero" ? 7 : 9;
      for (let lat = -84; lat <= 84; lat += latStep)
        for (let lon = 0; lon < 360; lon += lonStep) {
          const a = lat * Math.PI / 180, b = lon * Math.PI / 180;
          this.pts.push([Math.cos(a) * Math.cos(b), Math.sin(a), Math.cos(a) * Math.sin(b)]);
        }
      this.nodes = Array.from({ length: 26 }, () => {
        const u = Math.random() * 2 - 1, th = Math.random() * Math.PI * 2, r = Math.sqrt(1 - u * u);
        return [r * Math.cos(th), u, r * Math.sin(th)];
      });
      const fit = () => {
        const dpr = Math.min(window.devicePixelRatio || 1, 2), r = this.c.getBoundingClientRect();
        this.w = r.width; this.h = r.height;
        this.c.width = Math.max(1, r.width * dpr); this.c.height = Math.max(1, r.height * dpr);
        this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      };
      fit(); new ResizeObserver(fit).observe(this.c);
    }
    project(p, cy, sy, cx, sx) { // Drehung um y, dann leichte Neigung um x
      const x = p[0] * cy - p[2] * sy, z = p[0] * sy + p[2] * cy;
      const y = p[1] * cx - z * sx, z2 = p[1] * sx + z * cx;
      return [x, y, z2];
    }
    draw(t) {
      if (!this.c.offsetParent || !this.w) return;
      const g = this.ctx, w = this.w, h = this.h, [r0, g0, b0] = tint();
      const col = (a) => `rgba(${r0},${g0},${b0},${a})`;
      const thinking = document.body.classList.contains("thinking");
      const lvl = levelNow();
      this.rot += reduceMotion ? 0 : (thinking ? 0.014 : 0.0035);
      const R = Math.min(w, h) * (this.kind === "hero" ? 0.33 : 0.4) * (1 + lvl * 0.07);
      const cx0 = w / 2, cy0 = h / 2 - (this.kind === "hero" ? h * 0.02 : 0);
      g.clearRect(0, 0, w, h);
      // Leuchten
      const glow = g.createRadialGradient(cx0, cy0, R * 0.1, cx0, cy0, R * 1.6);
      glow.addColorStop(0, col(0.28 + lvl * 0.2)); glow.addColorStop(0.45, col(0.08)); glow.addColorStop(1, col(0));
      g.fillStyle = glow; g.fillRect(0, 0, w, h);
      // Boden-Ringe
      if (this.kind === "hero") {
        for (let i = 0; i < 4; i++) {
          g.beginPath(); g.ellipse(cx0, cy0 + R * 1.18, R * (0.7 + i * 0.25), R * (0.12 + i * 0.04), 0, 0, Math.PI * 2);
          g.strokeStyle = col(0.35 - i * 0.07); g.lineWidth = 1; g.stroke();
        }
      }
      const cyr = Math.cos(this.rot), syr = Math.sin(this.rot), cxr = Math.cos(0.38), sxr = Math.sin(0.38);
      // Gitterpunkte
      for (const p of this.pts) {
        const [x, y, z] = this.project(p, cyr, syr, cxr, sxr);
        const a = 0.12 + 0.75 * ((z + 1) / 2);
        g.fillStyle = col(a);
        const s = z > 0 ? 1.7 : 1.1;
        g.fillRect(cx0 + x * R - s / 2, cy0 + y * R - s / 2, s, s);
      }
      // Netzwerk-Knoten mit Verbindungen
      const proj = this.nodes.map((p) => this.project(p, cyr, syr, cxr, sxr));
      g.lineWidth = 0.7;
      for (let i = 0; i < proj.length; i++) for (let j = i + 1; j < proj.length; j++) {
        const a = proj[i], b = proj[j];
        const d = Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
        if (d < 0.75 && a[2] > -0.2 && b[2] > -0.2) {
          g.strokeStyle = col(0.25 * (1 - d / 0.75)); g.beginPath();
          g.moveTo(cx0 + a[0] * R, cy0 + a[1] * R); g.lineTo(cx0 + b[0] * R, cy0 + b[1] * R); g.stroke();
        }
      }
      for (const [x, y, z] of proj) if (z > -0.1) {
        g.fillStyle = col(0.9); g.beginPath(); g.arc(cx0 + x * R, cy0 + y * R, 2.2, 0, Math.PI * 2); g.fill();
        g.fillStyle = col(0.2); g.beginPath(); g.arc(cx0 + x * R, cy0 + y * R, 6, 0, Math.PI * 2); g.fill();
      }
      // Umlaufbahnen mit Satelliten
      const orbits = [[1.45, 0.32, -0.35, 0.0009], [1.7, 0.22, 0.25, -0.0006], [1.25, 0.5, 1.2, 0.0012]];
      orbits.forEach(([rx, ry, ang, sp], i) => {
        g.save(); g.translate(cx0, cy0); g.rotate(ang);
        g.beginPath(); g.ellipse(0, 0, R * rx, R * rx * ry, 0, 0, Math.PI * 2);
        g.strokeStyle = col(0.28); g.lineWidth = 1; g.stroke();
        const ph = (reduceMotion ? i : t * sp * (thinking ? 3 : 1)) + i * 2;
        const px = Math.cos(ph) * R * rx, py = Math.sin(ph) * R * rx * ry;
        g.fillStyle = col(1); g.shadowColor = col(1); g.shadowBlur = 12;
        g.beginPath(); g.arc(px, py, 2.6, 0, Math.PI * 2); g.fill();
        g.restore();
      });
      // Kern
      const core = g.createRadialGradient(cx0, cy0, 0, cx0, cy0, R * 0.35);
      core.addColorStop(0, col(0.35 + lvl * 0.4)); core.addColorStop(1, col(0));
      g.fillStyle = core; g.beginPath(); g.arc(cx0, cy0, R * 0.35, 0, Math.PI * 2); g.fill();
    }
  }

  class Wave {
    constructor(canvas) {
      this.c = canvas; this.ctx = canvas.getContext("2d");
      this.n = canvas.classList.contains("mini") ? 44 : canvas.classList.contains("wide") ? 60 : 36;
      const fit = () => {
        const dpr = Math.min(window.devicePixelRatio || 1, 2), r = this.c.getBoundingClientRect();
        this.w = r.width; this.h = r.height;
        this.c.width = Math.max(1, r.width * dpr); this.c.height = Math.max(1, r.height * dpr);
        this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      };
      fit(); new ResizeObserver(fit).observe(this.c);
    }
    draw(t) {
      if (!this.c.offsetParent || !this.w) return;
      const g = this.ctx, w = this.w, h = this.h, [r0, g0, b0] = tint(), b = document.body.classList;
      const amp = b.contains("listening") ? 0.85 : b.contains("speaking") ? 0.45 + levelNow() * 0.6 : b.contains("thinking") ? 0.35 : 0.12;
      g.clearRect(0, 0, w, h);
      const bw = w / this.n;
      for (let i = 0; i < this.n; i++) {
        const env = Math.sin((i / (this.n - 1)) * Math.PI);
        const v = reduceMotion ? 0.3 : (Math.sin(t / 180 + i * 0.7) * 0.5 + 0.5) * (Math.sin(t / 97 + i * 1.3) * 0.5 + 0.5);
        const bh = Math.max(2, h * (0.08 + amp * env * (0.25 + v * 0.75)));
        const grd = g.createLinearGradient(0, h / 2 - bh / 2, 0, h / 2 + bh / 2);
        grd.addColorStop(0, `rgba(${r0},${g0},${b0},0.15)`); grd.addColorStop(0.5, `rgba(${r0},${g0},${b0},0.95)`); grd.addColorStop(1, `rgba(${r0},${g0},${b0},0.15)`);
        g.fillStyle = grd;
        g.fillRect(i * bw + bw * 0.2, h / 2 - bh / 2, bw * 0.6, bh);
      }
    }
  }

  let hudStarted = false;
  function startHud() {
    if (hudStarted) return;
    hudStarted = true;
    const fx = [...document.querySelectorAll("#app canvas.globe")].map((c) => new Globe(c))
      .concat([...document.querySelectorAll("canvas.wave")].map((c) => new Wave(c)));
    let last = 0;
    const loop = (t) => {
      if (!document.hidden && t - last > 33) { last = t; fx.forEach((f) => f.draw(t)); } // ~30 Bilder/s
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
    tick(); setInterval(tick, 1000);
    setInterval(() => { if (mode === "dash" && !document.hidden) loadSystem(); }, 10000);
  }
  function startLoginGlobe() {
    const c = document.querySelector("#login canvas.globe");
    if (!c) return;
    const gl = new Globe(c);
    const loop = (t) => { if ($("#login").hidden) return; gl.draw(t); requestAnimationFrame(loop); };
    requestAnimationFrame(loop);
  }

  // ------------------------------------------------------------ Anmeldung & Start
  function showLogin() { $("#app").hidden = true; $("#login").hidden = false; $("#login-pw").focus(); startLoginGlobe(); }
  async function showApp() {
    $("#login").hidden = true; $("#app").hidden = false;
    $("#hello").textContent = `Hallo ${info.user_name || ""}, wie kann ich helfen?`.replace(" ,", ",");
    if (!info.ai_ready) bubble("jarvis", "Mir fehlt noch der kostenlose GROQ_API_KEY (console.groq.com). Bitte bei Render unter Environment eintragen.", "error");
    $("#user-name").textContent = info.user_name || "Operator";
    $("#dock-ai").textContent = info.ai_ready ? "Groq · online" : "Key fehlt";
    $("#btn-voice").setAttribute("aria-pressed", store.get("jarvis-voice", "0") === "1" ? "true" : "false");
    renderFeed(); renderCore(); loadFiles(); loadSystem(); startHud();
    $("#sp-auto").checked = store.get("jarvis-auto", "1") === "1";
    setMode(mode, false);
    await loadMail();
    if (mailState.pending) startPolling();
  }

  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const pw = $("#login-pw").value;
    const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: pw }) });
    if (r.ok) { password = pw; store.set("jarvis-key", pw); $("#login-error").hidden = true; showApp(); }
    else { $("#login-error").hidden = false; }
  });

  async function init() {
    try { info = await (await fetch("/api/info")).json(); } catch { info = {}; }
    if (info.needs_password) {
      if (!password) return showLogin();
      const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password }) });
      if (!r.ok) return showLogin();
    }
    showApp();
  }

  // ------------------------------------------------------------ Ereignisse
  ["pointerdown", "touchend", "keydown"].forEach((ev) => window.addEventListener(ev, () => voice.unlock(), { capture: true }));
  $("#composer").addEventListener("submit", (e) => { e.preventDefault(); const t = input.value; input.value = ""; grow(); send(t); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#composer").requestSubmit(); }
    if (e.key === "Escape") voice.stop();
  });
  const grow = () => { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 140) + "px"; };
  input.addEventListener("input", grow);
  $("#mic").addEventListener("click", () => (recog.active ? recog.stop() : voice.active ? voice.stop() : recog.start()));
  $("#btn-voice").addEventListener("click", (e) => {
    const on = e.currentTarget.getAttribute("aria-pressed") !== "true";
    e.currentTarget.setAttribute("aria-pressed", String(on)); store.set("jarvis-voice", on ? "1" : "0");
    if (!on) voice.stop();
  });
  // ------------------------------------------------------------ Bereiche umschalten
  function setMode(m, startListening = true) {
    const changed = mode !== m;
    mode = m;
    store.set("jarvis-mode", m);
    ["dash", "write", "speak"].forEach((v) => { $("#view-" + v).hidden = v !== m; });
    document.querySelectorAll("[data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === m));
    document.body.classList.toggle("mode-speak", m === "speak");
    if (changed || !startListening) { recog.abort(); voice.stop(); }
    refresh();
    if (m === "speak" && startListening) recog.start();  // Antippen zählt als Erlaubnis fürs Mikrofon
    if (m === "write" && matchMedia("(min-width: 901px)").matches) input.focus();
    if (m === "dash") { loadSystem(); loadFiles(); }
  }
  $("#sp-orb").addEventListener("click", () => (recog.active ? recog.stop() : voice.active ? voice.stop() : recog.start()));
  $("#sp-stop").addEventListener("click", () => { recog.abort(); voice.stop(); refresh(); });
  $("#sp-auto").addEventListener("change", (e) => { store.set("jarvis-auto", e.target.checked ? "1" : "0"); if (!e.target.checked) recog.abort(); });
  $("#mail-close").addEventListener("click", () => dlg.close());
  $("#files-close").addEventListener("click", () => $("#files-dialog").close());
  $("#attach").addEventListener("click", () => $("#file").click());
  $("#file").addEventListener("change", (e) => { addFiles(e.target.files); e.target.value = ""; });
  input.addEventListener("paste", (e) => { if (e.clipboardData?.files?.length) { e.preventDefault(); addFiles(e.clipboardData.files); } });
  window.addEventListener("dragover", (e) => { if (e.dataTransfer?.types?.includes("Files")) { e.preventDefault(); document.body.classList.add("dragging"); } });
  window.addEventListener("dragleave", (e) => { if (!e.relatedTarget) document.body.classList.remove("dragging"); });
  window.addEventListener("drop", (e) => { if (e.dataTransfer?.files?.length) { e.preventDefault(); document.body.classList.remove("dragging"); addFiles(e.dataTransfer.files); } });
  // Zentrale Klick-Steuerung für Navigation, Schnellbefehle usw.
  document.addEventListener("click", async (e) => {
    const t = e.target.closest("[data-view], [data-open], [data-action], [data-q], [data-attach]");
    if (!t || t.closest("#login")) return;
    if (t.dataset.view) { setMode(t.dataset.view, t.dataset.view === "speak"); return; }
    if (t.dataset.open === "mail") { await loadMail(); renderMail(); dlg.showModal(); return; }
    if (t.dataset.open === "files") { await loadFiles(); $("#files-dialog").showModal(); return; }
    if (t.dataset.action === "new") { newChat(); return; }
    if (t.dataset.action === "talk") {
      if (mode !== "speak") setMode("speak");
      else if (recog.active) recog.stop(); else if (voice.active) voice.stop(); else recog.start();
      return;
    }
    if (t.dataset.attach) { if (mode !== "write") setMode("write", false); $("#file").click(); return; }
    if (t.dataset.q) {
      const q = t.dataset.q;
      if (mode === "dash") setMode("write", false);
      if (q.endsWith(" ")) { input.value = q; input.focus(); grow(); } else send(q);
    }
  });

  init();
})();
