/* JARVIS – Oberfläche: Sperre, Chat, Sprechen, Anhänge, Dateien, E-Mail */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const store = {
    get(k, d = null) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch { return d; } },
    set(k, v) { try { v === null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch { /* egal */ } },
  };
  const newId = () => (crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2) + Date.now().toString(36));
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // Passwort nur im Arbeitsspeicher → bei jedem Öffnen neu eingeben
  store.set("jarvis-key", null);
  let password = "";
  let info = {};
  let sessionId = store.get("jarvis-session") || newId();
  store.set("jarvis-session", sessionId);
  let busy = false;
  let mode = store.get("jarvis-mode") === "speak" ? "speak" : "write";
  let lastQuestion = "";
  const LOCK_AFTER_MS = 5 * 60 * 1000;

  const headers = (extra = {}) => {
    const h = { ...extra };
    if (password) h["X-Jarvis-Key"] = password;
    const mt = store.get("jarvis-mail-token");
    if (mt) h["X-Mail-Token"] = mt;
    return h;
  };
  const withKey = (url) => (password ? `${url}${url.includes("?") ? "&" : "?"}key=${encodeURIComponent(password)}` : url);
  const api = (path, opts = {}) => fetch(path, { ...opts, headers: headers(opts.headers || {}) });
  const postJSON = (path, data) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

  // ============================================================ Anzeige
  const log = $("#log"), input = $("#input");
  const ICON_TRASH = '<svg viewBox="0 0 24 24"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/></svg>';
  const STATES = { listening: "Hört zu …", thinking: "Denkt nach …", speaking: "Spricht …" };

  function setState(cls = "") {
    document.body.classList.remove("listening", "thinking", "speaking");
    if (cls) document.body.classList.add(cls);
    $("#state").textContent = STATES[cls] || "Bereit";
    if (mode === "speak") {
      $("#sp-state").textContent = { listening: "Ich höre zu …", thinking: "Ich denke nach …", speaking: "JARVIS spricht …" }[cls]
        || "Tippe auf den Kreis und sprich";
    }
  }
  function refresh() {
    if (recog.active) setState("listening");
    else if (voice.active) setState("speaking");
    else if (busy) setState("thinking");
    else setState("");
  }
  const scroll = () => {
    log.scrollTop = log.scrollHeight;
    const talk = $(".sp-talk"); talk.scrollTop = talk.scrollHeight;
  };

  function userBubble(text, shown = []) {
    $("#welcome")?.remove();
    const el = document.createElement("div");
    el.className = "msg user";
    if (shown.length) {
      const t = document.createElement("div"); t.className = "thumbs";
      shown.forEach((p) => {
        if (p.preview) { const im = document.createElement("img"); im.src = p.preview; im.alt = p.name; t.append(im); }
        else { const sp = document.createElement("span"); sp.textContent = "📄 " + p.name; t.append(sp); }
      });
      el.append(t);
    }
    if (text) el.append(document.createTextNode(text));
    log.append(el); scroll();
    return el;
  }
  function jarvisBubble(text = "", cls = "") {
    $("#welcome")?.remove();
    const el = document.createElement("div");
    el.className = `msg jarvis ${cls}`.trim();
    const t = document.createElement("div"); t.className = "txt"; t.textContent = text;
    el.append(t); log.append(el); scroll();
    return el;
  }
  function addRetry(el) {
    if (!lastQuestion || el.querySelector(".retry")) return;
    const b = document.createElement("button");
    b.className = "retry"; b.type = "button"; b.textContent = "↻ Nochmal";
    b.onclick = () => { b.remove(); send(lastQuestion); };
    el.querySelector(".txt").after(b);
  }
  function errorBubble(text) { const el = jarvisBubble(text, "error"); addRetry(el); return el; }
  function statusLine(text) {
    const el = document.createElement("div");
    el.className = "status"; el.textContent = text + " …";
    log.append(el); scroll();
    return el;
  }

  const IMG_EXT = ["png", "jpg", "jpeg", "webp", "gif", "svg"];
  async function deleteFile(path, name) {
    if (!confirm(`„${name}“ wirklich löschen?`)) return false;
    const r = await api("/api/files?path=" + encodeURIComponent(path), { method: "DELETE" });
    if (!r.ok && r.status !== 404) { alert("Löschen hat nicht geklappt."); return false; }
    return true;
  }
  function fileCard(name, url, target = log) {
    const ext = (name.split(".").pop() || "").toLowerCase();
    const path = decodeURIComponent(url.replace(/^\/files\//, ""));
    const card = document.createElement("div");
    const del = `<button class="del" type="button" aria-label="${esc(name)} löschen" title="Löschen">${ICON_TRASH}</button>`;
    if (IMG_EXT.includes(ext)) {
      card.className = "card pic";
      card.innerHTML = `<a target="_blank" rel="noopener"><img alt=""></a><div class="row2"><div class="title"></div><div class="acts"><a class="dl">Laden</a>${del}</div></div>`;
      const src = withKey(url);
      const img = card.querySelector("img"); img.src = src; img.alt = name; img.onload = scroll;
      card.querySelector("a").href = src;
    } else {
      card.className = "card";
      card.innerHTML = `<div class="badge"></div><div class="meta"><div class="title"></div><div class="sub">Bereit zum Herunterladen</div></div><div class="acts"><a class="dl">Laden</a>${del}</div>`;
      const b = card.querySelector(".badge"); b.textContent = ext.toUpperCase().slice(0, 4); b.classList.add(ext);
    }
    card.dataset.path = path;
    card.querySelector(".title").textContent = name;
    const dl = card.querySelector("a.dl"); dl.href = withKey(url); dl.download = name;
    card.querySelector(".del").onclick = async () => {
      if (!(await deleteFile(path, name))) return;
      document.querySelectorAll(".card").forEach((c) => {
        if (c.dataset.path === path) { c.classList.add("deleted"); c.querySelector(".acts").textContent = "Gelöscht"; }
      });
    };
    target.append(card); scroll();
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

  // ============================================================ Stimme (Browser, kostenlos)
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
  // Stimme: natürliche MP3-Stimme vom Server über EIN <audio>-Element.
  // Das ist auf dem iPhone auch bei Lautlos-Schalter und in der Home-Bildschirm-App hörbar.
  // Notlösung pro Satz: die Browserstimme (speechSynthesis).
  const voice = {
    active: false, queue: [], current: null, el: new Audio(), unlocked: false, raf: 0, usedSynth: false, chosen: null,
    on() { return mode === "speak" || $("#btn-voice").getAttribute("aria-pressed") === "true"; },
    unlock() {
      if (this.unlocked) return;
      this.unlocked = true;
      try { if (navigator.audioSession) navigator.audioSession.type = "playback"; } catch { /* egal */ }
      this.el.setAttribute("playsinline", "");
      this.el.preload = "auto";
      this.el.src = "silent.mp3";
      const p = this.el.play();
      if (p) p.then(() => this.el.pause()).catch(() => { this.unlocked = false; });
      try { if ("speechSynthesis" in window) { const u = new SpeechSynthesisUtterance(" "); u.volume = 0; speechSynthesis.speak(u); } } catch { /* egal */ }
    },
    say(text, force = false) {
      text = clean(text);
      if (!text || (!force && !this.on())) return;
      this.queue.push({ text });
      this.prefetch();
      if (!this.current) this.next();
    },
    prefetch() {
      for (const it of this.queue.slice(0, 3)) {
        if (!it.blob) it.blob = api("/api/tts?text=" + encodeURIComponent(it.text))
          .then((r) => (r.ok ? r.blob() : null)).catch(() => null);
      }
    },
    async next() {
      const item = this.queue.shift();
      if (!item) { this.current = null; this.done(); return; }
      this.current = item;
      if (!this.active) { this.active = true; this.pulse(); refresh(); }
      this.prefetch();
      const finish = () => { if (this.current === item) { this.current = null; this.next(); } };
      const blob = item.blob ? await item.blob : null;
      if (this.current !== item) return;               // inzwischen gestoppt
      if (blob && blob.size > 100) {
        const url = URL.createObjectURL(blob);
        this.el.onended = () => { URL.revokeObjectURL(url); finish(); };
        this.el.onerror = () => { URL.revokeObjectURL(url); this.synth(item.text, finish); };
        this.el.src = url;
        try { await this.el.play(); return; } catch { URL.revokeObjectURL(url); }
      }
      this.synth(item.text, finish);
    },
    synth(text, done) {
      this.el.onended = this.el.onerror = null;
      if (!("speechSynthesis" in window)) { done(); return; }
      this.usedSynth = true;
      const u = new SpeechSynthesisUtterance(text);
      u.lang = "de-DE"; u.rate = 1.05;
      if (!this.chosen) {
        const de = speechSynthesis.getVoices().filter((v) => v.lang && v.lang.toLowerCase().startsWith("de"));
        this.chosen = de.find((v) => /natural|neural|online|premium|enhanced|siri/i.test(v.name)) || de[0] || null;
      }
      if (this.chosen) u.voice = this.chosen;
      let ended = false;
      const end = () => { if (!ended) { ended = true; done(); } };
      u.onend = end; u.onerror = end;
      setTimeout(end, 4000 + text.length * 120);   // falls der Browser nie "fertig" meldet
      speechSynthesis.speak(u);
    },
    pulse() {
      const tick = (t) => {
        document.documentElement.style.setProperty("--level", this.active ? (0.3 + 0.3 * Math.abs(Math.sin(t / 150))).toFixed(2) : "0");
        if (this.active) this.raf = requestAnimationFrame(tick);
      };
      this.raf = requestAnimationFrame(tick);
    },
    stop() {
      this.queue = []; this.current = null;
      this.el.onended = this.el.onerror = null;
      try { this.el.pause(); } catch { /* egal */ }
      if (this.usedSynth && "speechSynthesis" in window) { speechSynthesis.cancel(); this.usedSynth = false; }
      this.done();
    },
    done() {
      if (!this.active || this.current || this.queue.length) return;
      this.active = false;
      document.documentElement.style.setProperty("--level", "0");
      refresh(); listenAgain();
    },
  };
  if ("speechSynthesis" in window) speechSynthesis.onvoiceschanged = () => { voice.chosen = null; };

  // ============================================================ Mikrofon
  // Normal: Spracherkennung des Browsers. Ersatz (z. B. iPhone-Home-Bildschirm-App):
  // Aufnahme mit MediaRecorder → Groq Whisper auf dem Server (/api/stt).
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  function heard(t) {
    t = (t || "").trim();
    $("#interim").textContent = "";
    refresh();
    if (mode === "write") {  // Chat: Diktat landet im Eingabefeld
      if (t) { input.value = (input.value ? input.value.trimEnd() + " " : "") + t; grow(); input.focus(); }
      return;
    }
    if (t) send(t); else listenAgain();
  }
  function showLive(text) {
    if (mode === "speak") { $("#sp-user").textContent = text; $("#sp-answer").textContent = ""; }
    else $("#interim").textContent = text;
  }

  const rec = {
    active: false, mr: null, stream: null, ctx: null, cancelled: false,
    supported: !!(navigator.mediaDevices?.getUserMedia && window.MediaRecorder),
    async start() {
      this.cancelled = false;
      try { this.stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
      catch { jarvisBubble("Kein Zugriff aufs Mikrofon. Bitte in den Einstellungen erlauben.", "error"); $("#sp-auto").checked = false; return; }
      const types = ["audio/mp4", "audio/webm;codecs=opus", "audio/webm", "audio/ogg"];
      const mime = types.find((t) => window.MediaRecorder.isTypeSupported?.(t)) || "";
      const mr = new MediaRecorder(this.stream, mime ? { mimeType: mime } : undefined);
      const chunks = [];
      mr.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
      mr.onstop = async () => {
        this.stream?.getTracks().forEach((t) => t.stop());
        try { this.ctx?.close(); } catch { /* egal */ }
        this.active = false; this.mr = null;
        const blob = new Blob(chunks, { type: mr.mimeType || mime || "audio/webm" });
        if (this.cancelled || blob.size < 1500) { refresh(); if (!this.cancelled) listenAgain(); return; }
        showLive("Erkenne Sprache …"); setState("thinking");
        let text = "";
        try {
          const r = await api("/api/stt", { method: "POST", headers: { "Content-Type": blob.type }, body: blob });
          if (r.ok) text = (await r.json()).text || "";
          else jarvisBubble("Ich konnte dich gerade nicht verstehen. Bitte nochmal.", "error");
        } catch { jarvisBubble("Spracherkennung nicht erreichbar.", "error"); }
        showLive(text);
        heard(text);
      };
      this.mr = mr; this.active = true; refresh();
      mr.start(250);
      showLive("…");
      // Satzende an Stille erkennen
      try {
        this.ctx = new (window.AudioContext || window.webkitAudioContext)();
        const an = this.ctx.createAnalyser(); an.fftSize = 1024;
        this.ctx.createMediaStreamSource(this.stream).connect(an);
        const buf = new Uint8Array(an.fftSize);
        const t0 = Date.now(); let lastLoud = t0, spoke = false;
        const tick = () => {
          if (!this.active || this.mr !== mr) return;
          an.getByteTimeDomainData(buf);
          let sum = 0; for (const v of buf) { const x = (v - 128) / 128; sum += x * x; }
          const rms = Math.sqrt(sum / buf.length), now = Date.now();
          document.documentElement.style.setProperty("--level", Math.min(1, rms * 6).toFixed(2));
          if (rms > 0.035) { lastLoud = now; spoke = true; }
          if ((spoke && now - lastLoud > 1200) || now - t0 > 30000 || (!spoke && now - t0 > 8000)) { this.stop(); return; }
          requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
      } catch { setTimeout(() => this.stop(), 8000); }
    },
    stop() { if (this.mr && this.mr.state !== "inactive") this.mr.stop(); },
    abort() { this.cancelled = true; this.stop(); },
  };

  const recog = {
    r: null, srActive: false, text: "", useRec: !SR,
    get active() { return this.srActive || rec.active; },
    start() {
      if (this.active || busy) return;
      voice.stop();
      if (this.useRec) {
        if (rec.supported) { rec.start(); return; }
        jarvisBubble("Spracheingabe geht in diesem Browser nicht – bitte tippen.", "error"); return;
      }
      const r = new SR();
      r.lang = "de-DE"; r.interimResults = true; r.continuous = false;
      this.text = "";
      let switchToRec = false;
      r.onresult = (e) => {
        let interim = "";
        for (let i = e.resultIndex; i < e.results.length; i++) {
          if (e.results[i].isFinal) this.text += e.results[i][0].transcript;
          else interim += e.results[i][0].transcript;
        }
        showLive((this.text + " " + interim).trim());
      };
      r.onerror = (e) => {
        if (e.error === "not-allowed") {
          jarvisBubble("Kein Zugriff aufs Mikrofon. Bitte in den Einstellungen erlauben.", "error");
          $("#sp-auto").checked = false;
        } else if (["service-not-allowed", "network", "audio-capture", "language-not-supported"].includes(e.error) && rec.supported) {
          this.useRec = true; switchToRec = true;   // Browser-Erkennung geht nicht → Server-Erkennung
        }
      };
      r.onend = () => {
        this.srActive = false; this.r = null;
        if (switchToRec) { refresh(); rec.start(); return; }
        heard(this.text);
      };
      this.r = r; this.srActive = true; refresh();
      try { r.start(); } catch { this.srActive = false; if (rec.supported) { this.useRec = true; rec.start(); } else refresh(); }
    },
    stop() { if (this.r) this.r.stop(); else rec.stop(); },
    abort() { if (this.r) { this.text = ""; this.r.abort(); } rec.abort(); },
  };
  const convOn = () => mode === "speak" && $("#sp-auto").checked;
  function listenAgain() {
    if (convOn() && !busy && !voice.active && !recog.active)
      setTimeout(() => { if (convOn() && !busy && !voice.active && !recog.active) recog.start(); }, 400);
  }

  // ============================================================ Anhänge
  const MAX_FILES = 4, MAX_DOC = 8 * 1024 * 1024;
  let pending = [];
  const readAsDataURL = (file) => new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(file); });
  async function shrinkImage(file) {
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
      if (pending.length >= MAX_FILES) { jarvisBubble(`Maximal ${MAX_FILES} Dateien pro Nachricht.`, "error"); break; }
      const isImg = /^image\//.test(file.type) || /\.(jpe?g|png|webp|gif|heic|bmp)$/i.test(file.name);
      if (!isImg && file.size > MAX_DOC) { jarvisBubble(`„${file.name}“ ist zu groß (max. 8 MB).`, "error"); continue; }
      const item = { name: file.name || "foto.jpg", type: file.type, isImg, data: null, preview: isImg ? URL.createObjectURL(file) : null };
      pending.push(item); renderStrip();
      try { item.data = isImg ? await shrinkImage(file) : await readAsDataURL(file); }
      catch { pending = pending.filter((p) => p !== item); jarvisBubble(`„${file.name}“ konnte nicht gelesen werden.`, "error"); }
      if (isImg && item.data) item.name = item.name.replace(/\.[^.]+$/, "") + ".jpg";
      renderStrip();
    }
    input.focus();
  }
  function renderStrip() {
    const strip = $("#strip");
    strip.innerHTML = ""; strip.hidden = pending.length === 0;
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

  // ============================================================ Senden (mit automatischem Neuverbinden)
  async function openChat(body) {
    const waits = [2000, 4000, 8000];
    for (let i = 0; ; i++) {
      let res = null;
      try { res = await postJSON("/api/chat", body); } catch { res = null; }
      if (res && res.status === 401) { lock(); throw new Error("locked"); }
      if (res && res.ok && res.body) return res;
      const retryable = !res || [502, 503, 504].includes(res.status);
      if (!retryable || i >= waits.length) throw new Error(res ? "Server-Fehler " + res.status : "keine Verbindung");
      $("#state").textContent = "Verbinde neu …";
      await sleep(waits[i]);
    }
  }

  const BUSY_MARKERS = ["überlastet", "nicht erreichen", "schiefgelaufen", "keine Antwort"];
  const queue = [];  // Nachrichten, die während einer laufenden Antwort geschickt wurden
  async function send(text) {
    text = (text || "").trim();
    if (!text && !pending.length) return;
    if (busy) { if (text) { queue.push(text); statusLine("Kommt gleich dran: „" + text.slice(0, 40) + "“"); } return; }
    if (pending.some((p) => !p.data)) { jarvisBubble("Einen Moment – die Datei wird noch vorbereitet.", "error"); return; }
    const attachments = pending.map((p) => ({ name: p.name, type: p.isImg ? "image/jpeg" : p.type, data: p.data }));
    const shown = pending.map((p) => ({ name: p.name, preview: p.preview }));
    pending = []; renderStrip();
    if (text) lastQuestion = text;

    busy = true;
    voice.stop();
    userBubble(text, shown);
    refresh();
    let el = null, answer = "", gotDone = false;
    const sentences = new Sentences();
    const close = () => { el?.classList.remove("streaming"); };
    const spExtra = $("#sp-extra");
    $("#sp-user").textContent = text || "📎";
    $("#sp-answer").textContent = ""; spExtra.innerHTML = "";

    const handle = (ev) => {
      if (ev.type === "text") {
        if (!el) el = jarvisBubble("", "streaming");
        answer += ev.text;
        el.querySelector(".txt").textContent = answer; scroll();
        $("#sp-answer").textContent = answer;
        sentences.feed(ev.text).forEach((s) => voice.say(s));
      } else if (ev.type === "status") { close(); el = null; answer = ""; statusLine(ev.text); }
      else if (ev.type === "file") { close(); el = null; answer = ""; fileCard(ev.name, ev.url); fileCard(ev.name, ev.url, spExtra); }
      else if (ev.type === "mail_sent") { close(); el = null; answer = ""; mailCard(ev); mailCard(ev, spExtra); }
      else if (ev.type === "mail_token") { store.set("jarvis-mail-token", ev.token); }
      else if (ev.type === "error") { close(); el = null; answer = ""; errorBubble(ev.text); $("#sp-answer").textContent = ev.text; }
      else if (ev.type === "done") { gotDone = true; }
    };

    try {
      const res = await openChat({ session: sessionId, text, attachments });
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
      if (!gotDone) throw new Error("abgebrochen");
      if (el && BUSY_MARKERS.some((m) => answer.includes(m))) addRetry(el);
    } catch (e) {
      close();
      if (e.message !== "locked") {
        errorBubble(e.message === "abgebrochen" ? "Die Verbindung wurde kurz unterbrochen." : "Ich erreiche den Server gerade nicht. Prüf bitte die Internetverbindung.");
      }
    } finally {
      sentences.flush().forEach((s) => voice.say(s));
      close(); busy = false; $("#send").disabled = false;
      refresh();
      if (queue.length) setTimeout(() => send(queue.shift()), 50);
      else if (!voice.active) listenAgain();
    }
  }

  async function newChat() {
    voice.stop(); recog.abort();
    try { await postJSON("/api/reset", { session: sessionId }); } catch { /* egal */ }
    sessionId = newId(); store.set("jarvis-session", sessionId);
    lastQuestion = "";
    log.innerHTML = "";
    const w = document.createElement("div");
    w.id = "welcome"; w.className = "welcome";
    w.innerHTML = welcomeHTML;
    log.append(w);
    $("#hello").textContent = greeting();
    $("#sp-user").textContent = ""; $("#sp-answer").textContent = ""; $("#sp-extra").innerHTML = "";
  }

  // ============================================================ Dateien
  async function loadFiles() {
    const ul = $("#files-list");
    ul.innerHTML = '<li class="empty">Lade …</li>';
    let files = [];
    try { const r = await api("/api/files"); if (r.ok) files = await r.json(); } catch { /* offline */ }
    $("#files-clear").hidden = !files.length;
    if (!files.length) { ul.innerHTML = '<li class="empty">Noch keine Dateien.</li>'; return; }
    ul.innerHTML = "";
    files.forEach((f) => {
      const ext = (f.name.split(".").pop() || "").toLowerCase();
      const when = new Date(f.time * 1000).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
      const li = document.createElement("li");
      li.innerHTML = `<span class="badge ${IMG_EXT.includes(ext) ? "img" : esc(ext)}">${esc(ext.toUpperCase().slice(0, 4))}</span>
        <div class="fn"><b></b><small>${f.upload ? "Hochgeladen" : "Erstellt"} · ${when} · ${Math.max(1, Math.round(f.size / 1024))} KB</small></div>
        <a download>Laden</a><button class="del" type="button" title="Löschen">${ICON_TRASH}</button>`;
      li.querySelector("b").textContent = f.name;
      const a = li.querySelector("a"); a.href = withKey(f.url); a.download = f.name;
      const del = li.querySelector(".del"); del.setAttribute("aria-label", f.name + " löschen");
      del.onclick = async () => {
        if (!(await deleteFile(f.path, f.name))) return;
        document.querySelectorAll(".card").forEach((c) => {
          if (c.dataset.path === f.path) { c.classList.add("deleted"); c.querySelector(".acts").textContent = "Gelöscht"; }
        });
        loadFiles();
      };
      ul.append(li);
    });
  }
  $("#files-clear").onclick = async () => {
    if (!confirm("Wirklich ALLE Dateien löschen?")) return;
    await postJSON("/api/files/delete-all", {});
    document.querySelectorAll(".card[data-path]").forEach((c) => { c.classList.add("deleted"); c.querySelector(".acts").textContent = "Gelöscht"; });
    loadFiles();
  };

  // ============================================================ E-Mail
  let mailState = { connected: false };
  let mailPoll = 0;
  const dlg = $("#mail-dialog"), body = $("#mail-body");

  async function loadMail() {
    try {
      const r = await api(`/api/mail/status?session=${encodeURIComponent(sessionId)}`);
      if (!r.ok) return mailState;
      mailState = await r.json();
      if (mailState.token) store.set("jarvis-mail-token", mailState.token);
    } catch { /* offline */ }
    $("#mail-dot").classList.toggle("on", !!mailState.connected);
    return mailState;
  }
  function renderMail() {
    const s = mailState;
    body.innerHTML = "";
    const p = (html) => { const el = document.createElement("div"); el.innerHTML = html; body.append(el); return el; };
    const testButton = () => {
      const el = p('<div class="row"><button class="btn" id="mail-test">Verbindung testen</button></div><p id="mail-test-result"></p>');
      el.querySelector("#mail-test").onclick = async (e) => {
        const out = el.querySelector("#mail-test-result");
        e.target.disabled = true; out.className = ""; out.textContent = "Teste …";
        try {
          const j = await (await postJSON("/api/mail/test", { session: sessionId })).json();
          out.className = j.ok ? "ok" : "error"; out.textContent = (j.ok ? "✓ " : "✕ ") + j.message;
        } catch { out.className = "error"; out.textContent = "✕ Server nicht erreichbar"; }
        e.target.disabled = false;
      };
    };
    if (s.connected) {
      const el = p('<p class="ok">✓ Verbunden</p><p></p><p>Frag einfach: „Habe ich neue Mails?“ oder „Antworte ihm, dass …“.</p>');
      el.querySelectorAll("p")[1].textContent = s.address + (s.provider === "outlook" ? " (Outlook)" : "");
      if (s.provider === "outlook") {
        const row = p('<div class="row"><button class="btn" id="mail-off">Trennen</button></div>');
        row.querySelector("#mail-off").onclick = async () => {
          await postJSON("/api/mail/outlook/disconnect", { session: sessionId });
          store.set("jarvis-mail-token", null); await loadMail(); renderMail();
        };
      }
      testButton();
      return;
    }
    if (s.pending) {
      const el = p(`<p>1. Öffne <a target="_blank" rel="noopener"></a></p><p>2. Gib diesen Code ein:</p><div class="code"></div>
        <p>3. Mit Outlook/Hotmail anmelden und „Akzeptieren“ tippen.</p>
        <div class="row"><button class="btn primary" id="copy-code">Code kopieren & Seite öffnen</button></div><p>Ich warte …</p>`);
      const a = el.querySelector("a"); a.href = s.pending.verification_uri; a.textContent = s.pending.verification_uri.replace(/^https?:\/\//, "");
      el.querySelector(".code").textContent = s.pending.user_code;
      el.querySelector("#copy-code").onclick = () => {
        navigator.clipboard?.writeText(s.pending.user_code).catch(() => {});
        window.open(s.pending.verification_uri, "_blank", "noopener");
      };
      return;
    }
    if (s.provider === "imap" && s.error) {
      const el = p('<p class="error">Problem mit der E-Mail-Einrichtung:</p><p></p>');
      el.querySelectorAll("p")[1].textContent = s.error;
      testButton();
      return;
    }
    if (s.error) p('<p class="error"></p>').querySelector("p").textContent = "Anmeldung fehlgeschlagen: " + s.error;
    if (s.outlook_available) {
      const el = p('<p>Verbinde dein Outlook-/Hotmail-Postfach. Dein Passwort gibst du nur bei Microsoft ein.</p><div class="row"><button class="btn primary" id="ol-start">Outlook verbinden</button></div>');
      el.querySelector("#ol-start").onclick = async (e) => {
        e.target.disabled = true; e.target.textContent = "Starte …";
        const r = await postJSON("/api/mail/outlook/start", { session: sessionId });
        if (!r.ok) { const j = await r.json().catch(() => ({})); mailState.error = j.detail || "Fehler " + r.status; renderMail(); return; }
        await loadMail(); renderMail(); startPolling();
      };
    } else {
      p(`<p>Noch kein Postfach eingerichtet. Bei Render → <b>Environment</b> eintragen:</p>
        <ul class="steps"><li><b>iCloud/Gmail/GMX/Web.de:</b> <code>MAIL_ADDRESS</code> und <code>MAIL_APP_PASSWORD</code></li>
        <li><b>Outlook/Hotmail:</b> <code>MS_CLIENT_ID</code> (siehe README)</li></ul>`);
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

  // ============================================================ Sprechen: leuchtender Kreis
  const orbCanvas = $("#sp-orb canvas"), og = orbCanvas.getContext("2d");
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  function fitOrb() {
    const dpr = Math.min(devicePixelRatio || 1, 2), r = orbCanvas.getBoundingClientRect();
    orbCanvas.width = Math.max(1, r.width * dpr); orbCanvas.height = Math.max(1, r.height * dpr);
    og.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  new ResizeObserver(fitOrb).observe(orbCanvas);
  const hex = (c) => (/^#[0-9a-f]{6}$/i.test(c) ? c : "#22d3ee");
  function drawOrb(t) {
    requestAnimationFrame(drawOrb);
    if (mode !== "speak" || document.hidden) return;
    const w = orbCanvas.clientWidth, h = orbCanvas.clientHeight; if (!w) return;
    const cs = getComputedStyle(document.documentElement);
    const b = document.body.classList;
    const col = hex((b.contains("listening") ? cs.getPropertyValue("--gold") : cs.getPropertyValue("--accent")).trim());
    const lvl = parseFloat(document.documentElement.style.getPropertyValue("--level")) || 0;
    const active = b.contains("listening") || b.contains("speaking") || b.contains("thinking");
    const base = Math.min(w, h) * 0.3;
    const breathe = reduceMotion ? 0 : Math.sin(t / (b.contains("thinking") ? 220 : 900)) * 0.04;
    const R = base * (1 + breathe + lvl * 0.12);
    og.clearRect(0, 0, w, h);
    og.globalAlpha = 1;
    const g = og.createRadialGradient(w / 2, h / 2, R * 0.2, w / 2, h / 2, R * 1.7);
    g.addColorStop(0, col); g.addColorStop(0.45, col + "55"); g.addColorStop(1, col + "00");
    og.fillStyle = g; og.beginPath(); og.arc(w / 2, h / 2, R * 1.7, 0, Math.PI * 2); og.fill();
    if (active && !reduceMotion) {
      for (let i = 0; i < 3; i++) {
        const p = ((t / 1600) + i / 3) % 1;
        og.strokeStyle = col; og.globalAlpha = (1 - p) * 0.35; og.lineWidth = 2;
        og.beginPath(); og.arc(w / 2, h / 2, R * (1 + p * 0.7), 0, Math.PI * 2); og.stroke();
      }
      og.globalAlpha = 1;
    }
    const c = og.createRadialGradient(w / 2, h / 2 - R * 0.15, R * 0.05, w / 2, h / 2, R);
    c.addColorStop(0, "#ffffff"); c.addColorStop(0.35, col); c.addColorStop(1, col + "cc");
    og.fillStyle = c; og.beginPath(); og.arc(w / 2, h / 2, R, 0, Math.PI * 2); og.fill();
  }
  requestAnimationFrame(drawOrb);

  // ============================================================ Modus
  function setMode(m, startListening = true) {
    const changed = mode !== m;
    mode = m; store.set("jarvis-mode", m);
    $("#view-write").hidden = m !== "write";
    $("#view-speak").hidden = m !== "speak";
    document.querySelectorAll(".seg [data-view]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.view === m)));
    document.body.classList.toggle("mode-speak", m === "speak");
    if (changed) { recog.abort(); voice.stop(); }
    refresh();
    if (m === "speak" && startListening) recog.start();
  }

  // ============================================================ Sperre & Start
  const welcomeHTML = $("#welcome").innerHTML;
  const greeting = () => {
    const h = new Date().getHours();
    const g = h < 11 ? "Guten Morgen" : h < 18 ? "Hallo" : "Guten Abend";
    return info.user_name ? `${g}, ${info.user_name}` : "Wie kann ich helfen?";
  };

  function showLockForm(msg = "Bitte Passwort eingeben") {
    $("#lock-msg").textContent = msg;
    $("#lock-fields").hidden = false;
    $("#lock-pw").value = "";
    setTimeout(() => $("#lock-pw").focus(), 50);
  }
  function lock() {
    if (!info.needs_password) return;
    password = "";
    voice.stop(); recog.abort();
    $("#app").hidden = true; $("#lock").hidden = false; $("#lock-error").hidden = true;
    for (const d of document.querySelectorAll("dialog[open]")) d.close();
    showLockForm();
  }
  async function unlocked() {
    $("#lock").hidden = true; $("#app").hidden = false;
    const hello = $("#hello"); if (hello) hello.textContent = greeting();
    $("#btn-lock").hidden = !info.needs_password;
    $("#btn-voice").setAttribute("aria-pressed", store.get("jarvis-voice", "0") === "1" ? "true" : "false");
    $("#sp-auto").checked = store.get("jarvis-auto", "1") === "1";
    setMode(mode, false);
    if (!info.ai_ready && !log.querySelector(".msg")) jarvisBubble("Mir fehlt noch der kostenlose GROQ_API_KEY (console.groq.com). Bitte bei Render unter Environment eintragen.", "error");
    await loadMail();
    if (mailState.pending) startPolling();
  }

  $("#lock-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const pw = $("#lock-pw").value;
    if (!pw) return;
    const btn = $("#lock-form button[type=submit]"); btn.disabled = true;
    $("#lock-error").textContent = "Falsches Passwort";
    try {
      const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: pw }) });
      if (r.ok) { password = pw; $("#lock-error").hidden = true; await unlocked(); }
      else { $("#lock-error").hidden = false; $("#lock-pw").select(); }
    } catch { $("#lock-error").textContent = "Server nicht erreichbar – bitte nochmal."; $("#lock-error").hidden = false; }
    btn.disabled = false;
  });

  async function boot() {
    // Render schläft im Gratis-Tarif ein → bis zu 60 s geduldig warten statt Fehler zu zeigen
    const start = Date.now();
    for (let i = 0; ; i++) {
      try {
        const r = await fetch("/api/info", { cache: "no-store" });
        if (r.ok) { info = await r.json(); break; }
      } catch { /* wacht noch auf */ }
      if (Date.now() - start > 60000) { $("#lock-msg").textContent = "JARVIS ist gerade nicht erreichbar. Bitte später neu öffnen."; return; }
      $("#lock-msg").textContent = i < 2 ? "JARVIS startet …" : "JARVIS wird geweckt … (kann bis zu 1 Minute dauern)";
      await sleep(2500);
    }
    if (info.needs_password) showLockForm();
    else unlocked();
  }

  // Automatisch sperren, wenn die App länger im Hintergrund war
  let hiddenAt = 0;
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) hiddenAt = Date.now();
    else if (hiddenAt && Date.now() - hiddenAt > LOCK_AFTER_MS && password) lock();
  });

  // ============================================================ Ereignisse
  ["pointerdown", "touchend", "keydown"].forEach((ev) => window.addEventListener(ev, () => voice.unlock(), { capture: true }));
  const grow = () => { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 160) + "px"; };
  $("#composer").addEventListener("submit", (e) => { e.preventDefault(); const t = input.value; input.value = ""; grow(); send(t); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#composer").requestSubmit(); }
    if (e.key === "Escape") voice.stop();
  });
  input.addEventListener("input", grow);
  input.addEventListener("paste", (e) => { if (e.clipboardData?.files?.length) { e.preventDefault(); addFiles(e.clipboardData.files); } });
  $("#mic").addEventListener("click", () => (recog.active ? recog.stop() : recog.start()));
  $("#attach").addEventListener("click", () => $("#file").click());
  $("#file").addEventListener("change", (e) => { addFiles(e.target.files); e.target.value = ""; });
  $("#btn-voice").addEventListener("click", (e) => {
    const on = e.currentTarget.getAttribute("aria-pressed") !== "true";
    e.currentTarget.setAttribute("aria-pressed", String(on)); store.set("jarvis-voice", on ? "1" : "0");
    if (!on) voice.stop();
  });
  $("#sp-orb").addEventListener("click", () => (recog.active ? recog.stop() : voice.active ? voice.stop() : recog.start()));
  $("#sp-stop").addEventListener("click", () => { recog.abort(); voice.stop(); refresh(); });
  $("#sp-test").addEventListener("click", () => { recog.abort(); voice.stop(); voice.unlock(); voice.say("Hallo, ich bin JARVIS. Ich bin bereit.", true); });
  $("#sp-auto").addEventListener("change", (e) => { store.set("jarvis-auto", e.target.checked ? "1" : "0"); if (!e.target.checked) recog.abort(); });
  $("#mail-close").addEventListener("click", () => dlg.close());
  $("#files-close").addEventListener("click", () => $("#files-dialog").close());
  window.addEventListener("dragover", (e) => { if (e.dataTransfer?.types?.includes("Files")) { e.preventDefault(); document.body.classList.add("dragging"); } });
  window.addEventListener("dragleave", (e) => { if (!e.relatedTarget) document.body.classList.remove("dragging"); });
  window.addEventListener("drop", (e) => { if (e.dataTransfer?.files?.length) { e.preventDefault(); document.body.classList.remove("dragging"); if (mode !== "write") setMode("write", false); addFiles(e.dataTransfer.files); } });

  document.addEventListener("click", async (e) => {
    const t = e.target.closest("[data-view], [data-open], [data-action], [data-q], [data-attach]");
    if (!t) return;
    if (t.dataset.view) { if (mode !== t.dataset.view) setMode(t.dataset.view, t.dataset.view === "speak"); return; }
    if (t.dataset.open === "mail") { await loadMail(); renderMail(); dlg.showModal(); return; }
    if (t.dataset.open === "files") { $("#files-dialog").showModal(); loadFiles(); return; }
    if (t.dataset.action === "new") { newChat(); return; }
    if (t.dataset.action === "lock") { lock(); return; }
    if (t.dataset.attach) { if (mode !== "write") setMode("write", false); $("#file").click(); return; }
    if (t.dataset.q) {
      const q = t.dataset.q;
      if (q.endsWith(" ")) { input.value = q; input.focus(); grow(); } else send(q);
    }
  });

  boot();
})();
