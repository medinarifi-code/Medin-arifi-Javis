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
  function setState(text, cls = "") {
    $("#state").textContent = text;
    document.body.className = cls;
  }
  function refresh() {
    if (recog.active) setState("Hört zu …", "listening");
    else if (busy) setState(voice.active ? "Spricht …" : "Denkt nach …", voice.active ? "speaking" : "thinking");
    else if (voice.active) setState("Spricht …", "speaking");
    else setState(mailState.connected ? `Bereit · ✉ ${mailState.address}` : "Bereit");
  }
  const scroll = () => { log.scrollTop = log.scrollHeight; };
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
  function fileCard(name, url) {
    const ext = (name.split(".").pop() || "").toLowerCase();
    const el = document.createElement("div");
    el.className = "card";
    el.innerHTML = '<div class="badge"></div><div class="meta"><div class="title"></div><div class="sub">Zum Herunterladen bereit</div></div><a>Laden</a>';
    el.querySelector(".badge").textContent = ext.toUpperCase().slice(0, 4);
    el.querySelector(".badge").classList.add(ext);
    el.querySelector(".title").textContent = name;
    const a = el.querySelector("a"); a.href = withKey(url); a.download = name;
    log.append(el); scroll();
  }
  function mailCard(ev) {
    const el = document.createElement("div");
    el.className = "card mail-card";
    el.innerHTML = '<div class="sent"></div><div class="title"></div><pre></pre>';
    el.querySelector(".sent").textContent = "✓ Gesendet an " + ev.to;
    el.querySelector(".title").textContent = ev.subject || "";
    el.querySelector("pre").textContent = ev.text || "";
    log.append(el); scroll();
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
    on() { return $("#btn-voice").getAttribute("aria-pressed") === "true"; },
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
        $("#interim").textContent = (this.text + " " + interim).trim();
      };
      r.onerror = (e) => {
        if (e.error === "not-allowed" || e.error === "service-not-allowed") {
          bubble("jarvis", "Kein Zugriff aufs Mikrofon. Bitte in den Browser-Einstellungen erlauben.", "error");
          setConv(false);
        }
      };
      r.onend = () => {
        this.active = false; this.r = null; $("#interim").textContent = "";
        const t = this.text.trim(); refresh();
        if (t) send(t); else listenAgain();
      };
      this.r = r; this.active = true; refresh();
      try { r.start(); } catch { this.active = false; refresh(); }
    },
    stop() { this.r?.stop(); },
    abort() { if (this.r) { this.text = ""; this.r.abort(); } },
  };
  const convOn = () => $("#btn-conv").getAttribute("aria-pressed") === "true";
  function setConv(on) { $("#btn-conv").setAttribute("aria-pressed", String(on)); store.set("jarvis-conv", on ? "1" : "0"); }
  function listenAgain() {
    if (convOn() && !busy && !voice.active && !recog.active) setTimeout(() => { if (convOn() && !busy && !voice.active && !recog.active) recog.start(); }, 400);
  }

  // ------------------------------------------------------------ Chat
  async function send(text) {
    text = (text || "").trim();
    if (!text || busy) return;
    busy = true; $("#send").disabled = true;
    voice.stop();
    bubble("user", text);
    refresh();
    let el = null;
    const sentences = new Sentences();
    const close = () => { el?.classList.remove("streaming"); el = null; };

    const handle = (ev) => {
      if (ev.type === "text") {
        if (!el) el = bubble("jarvis", "", "streaming");
        el.textContent += ev.text; scroll();
        sentences.feed(ev.text).forEach((s) => voice.say(s));
      } else if (ev.type === "status") { close(); status(ev.text); }
      else if (ev.type === "file") { close(); fileCard(ev.name, ev.url); }
      else if (ev.type === "mail_sent") { close(); mailCard(ev); }
      else if (ev.type === "mail_token") { store.set("jarvis-mail-token", ev.token); }
      else if (ev.type === "error") { close(); bubble("jarvis", ev.text, "error"); }
    };

    try {
      const res = await fetch("/api/chat", {
        method: "POST", headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify({ session: sessionId, text }),
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
      close(); busy = false; $("#send").disabled = false;
      refresh(); if (!voice.active) listenAgain();
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
    $("#mail-dot").classList.toggle("on", !!mailState.connected);
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

  // ------------------------------------------------------------ Anmeldung & Start
  function showLogin() { $("#app").hidden = true; $("#login").hidden = false; $("#login-pw").focus(); }
  async function showApp() {
    $("#login").hidden = true; $("#app").hidden = false;
    $("#hello").textContent = `Hallo ${info.user_name || ""}, wie kann ich helfen?`.replace(" ,", ",");
    if (!info.ai_ready) bubble("jarvis", "Mir fehlt noch der kostenlose GROQ_API_KEY (console.groq.com). Bitte bei Render unter Environment eintragen.", "error");
    $("#btn-voice").setAttribute("aria-pressed", store.get("jarvis-voice", "1") === "1" ? "true" : "false");
    setConv(store.get("jarvis-conv") === "1");
    await loadMail();
    if (mailState.pending) startPolling();
    input.focus();
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
  $("#btn-conv").addEventListener("click", () => { setConv(!convOn()); if (convOn()) listenAgain(); else recog.abort(); });
  $("#btn-new").addEventListener("click", newChat);
  $("#btn-mail").addEventListener("click", async () => { await loadMail(); renderMail(); dlg.showModal(); });
  $("#mail-close").addEventListener("click", () => dlg.close());
  document.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => {
    const q = c.dataset.q;
    if (q.endsWith(" ")) { input.value = q; input.focus(); grow(); } else send(q);
  }));

  init();
})();
