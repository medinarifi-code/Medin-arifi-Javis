/* J.A.R.V.I.S. – Weboberfläche
 * Mikrofon: Web Speech API (Chrome/Edge)
 * Stimme:   ElevenLabs über /api/tts (satzweise, sofortiges Abspielen) – sonst Browserstimme
 * Gehirn:   /api/chat (gestreamtes NDJSON)
 */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const logEl = $("#log"), input = $("#input"), sendBtn = $("#send");
  const stateLabel = $("#state-label"), interimEl = $("#interim");
  const convToggle = $("#conv-mode"), voiceToggle = $("#voice-on");

  // ---------- Hilfsfunktionen für Speicher (kann in privaten Fenstern fehlen) ----------
  const store = {
    get(k, d = null) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* egal */ } },
  };

  const newId = () => (crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2) + Date.now().toString(36));
  let sessionId = store.get("jarvis-session") || newId();
  store.set("jarvis-session", sessionId);
  let password = store.get("jarvis-key", "");

  const authHeaders = () => (password ? { "X-Jarvis-Key": password } : {});
  const withKey = (url) => (password ? url + (url.includes("?") ? "&" : "?") + "key=" + encodeURIComponent(password) : url);

  let status = { user_name: "Sir", voice: "browser", brain_ok: false };
  let busy = false;

  // ---------- Zustand / Anzeige ----------
  const STATES = { idle: "BEREIT", listening: "HÖRE ZU", thinking: "DENKE NACH", speaking: "SPRECHE" };
  function setState(s) {
    document.body.className = "state-" + s;
    stateLabel.textContent = STATES[s];
  }
  function refreshState() {
    if (recog.active) setState("listening");
    else if (busy) setState(voice.active ? "speaking" : "thinking");
    else if (voice.active) setState("speaking");
    else setState("idle");
  }

  setInterval(() => {
    const d = new Date();
    $("#clock").textContent = d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
  }, 1000);

  function scrollDown() { logEl.scrollTop = logEl.scrollHeight; }

  function addMessage(who, text, cls = "") {
    const el = document.createElement("div");
    el.className = `msg ${who} ${cls}`.trim();
    const label = document.createElement("span");
    label.className = "who";
    label.textContent = who === "user" ? "DU" : "JARVIS";
    const body = document.createElement("div");
    body.className = "body";
    body.textContent = text;
    el.append(label, body);
    logEl.append(el);
    scrollDown();
    return el;
  }

  function addStatus(text) {
    const el = document.createElement("div");
    el.className = "status-line";
    el.textContent = text + " …";
    logEl.append(el);
    scrollDown();
  }

  const TYPE_NAMES = { pptx: "PowerPoint-Präsentation", docx: "Word-Dokument", xlsx: "Excel-Tabelle", txt: "Textdatei",
    md: "Markdown", py: "Python-Code", html: "Webseite", csv: "CSV-Tabelle", json: "JSON", js: "JavaScript" };

  function fileCard(name, url) {
    const node = $("#file-card").content.firstElementChild.cloneNode(true);
    const ext = (name.split(".").pop() || "").toLowerCase();
    const icon = node.querySelector(".file-icon");
    icon.textContent = ext.toUpperCase().slice(0, 4);
    icon.classList.add(ext);
    node.querySelector(".file-name").textContent = name;
    node.querySelector(".file-type").textContent = TYPE_NAMES[ext] || "Datei";
    const a = node.querySelector(".file-dl");
    a.href = withKey(url);
    a.setAttribute("download", name);
    return node;
  }

  function downloadNow(url, name) {
    const a = document.createElement("a");
    a.href = withKey(url);
    a.download = name;
    document.body.append(a);
    a.click();
    a.remove();
  }

  // ---------- Text fürs Vorlesen ----------
  function cleanForSpeech(t) {
    return t.replace(/```[\s\S]*?```/g, " ")
      .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
      .replace(/https?:\/\/\S+/g, "")
      .replace(/[*_#`>|]+/g, "")
      .replace(/^\s*[-•▸]\s+/gm, "")
      .replace(/\s+/g, " ").trim();
  }

  class SentenceBuffer {
    constructor(minLen = 25) { this.buf = ""; this.minLen = minLen; }
    feed(chunk) {
      this.buf += chunk;
      const out = [];
      const re = /([.!?…:;])\s+|\n+/g;
      let start = 0, m;
      while ((m = re.exec(this.buf)) !== null) {
        const end = m.index + (m[1] ? 1 : 0);
        const piece = this.buf.slice(start, end).trim();
        if (piece.length >= this.minLen) { out.push(piece); start = re.lastIndex; }
      }
      this.buf = this.buf.slice(start);
      return out;
    }
    flush() { const r = this.buf.trim(); this.buf = ""; return r ? [r] : []; }
  }

  // ---------- Stimme ----------
  // ElevenLabs-Audio wird satzweise geladen, dekodiert und über Web Audio abgespielt.
  // Das funktioniert auch auf dem iPhone, sobald der Ton einmal per Fingertipp freigeschaltet wurde.
  const voice = {
    queue: [],          // [{text, prev, buffer: Promise<AudioBuffer|null>}]
    current: null,
    active: false,
    lastText: "",
    ctx: null, analyser: null, levelRaf: 0,
    browserVoice: null,

    enabled() { return voiceToggle.checked; },
    useEleven() { return status.voice === "elevenlabs" && this.ctx && this.ctx.state === "running"; },

    say(text) {
      text = cleanForSpeech(text);
      if (!text || !this.enabled()) return;
      this.queue.push({ text, prev: this.lastText });
      this.lastText = text;
      this.prefetch();
      this.pump();
    },

    prefetch() {
      // Den laufenden und die nächsten zwei Sätze parallel vorbereiten -> keine Pausen zwischen Sätzen.
      for (const item of this.queue.slice(0, 2)) this.prepare(item);
    },

    prepare(item) {
      if (item.buffer || !this.useEleven()) return;
      const url = withKey(`/api/tts?text=${encodeURIComponent(item.text)}&prev=${encodeURIComponent(item.prev.slice(-300))}`);
      item.buffer = fetch(url)
        .then((r) => (r.ok ? r.arrayBuffer() : null))
        .then((ab) => (ab ? new Promise((res, rej) => this.ctx.decodeAudioData(ab, res, rej)) : null))
        .catch(() => null);
    },

    pump() {
      if (this.current) return;
      if (!this.queue.length) { this.finished(); return; }
      this.active = true;
      refreshState();
      const item = this.queue.shift();
      this.current = item;
      this.prepare(item);
      this.prefetch();
      const done = () => { if (this.current === item) { this.current = null; this.pump(); } };

      if (!item.buffer) { this.speakBrowser(item.text, done); return; }
      item.buffer.then((buf) => {
        if (this.current !== item) return;               // inzwischen gestoppt
        if (!buf) { this.speakBrowser(item.text, done); return; }
        const src = this.ctx.createBufferSource();
        src.buffer = buf;
        src.connect(this.analyser);
        src.onended = done;
        item.source = src;
        src.start();
        this.trackLevel();
      });
    },

    speakBrowser(text, done) {
      if (!("speechSynthesis" in window)) { done(); return; }
      const u = new SpeechSynthesisUtterance(text);
      u.lang = status.language || "de-DE";
      if (!this.browserVoice) {
        const voices = speechSynthesis.getVoices().filter((v) => v.lang.startsWith("de"));
        this.browserVoice = voices.find((v) => /natural|online|neural|premium|enhanced/i.test(v.name)) || voices[0] || null;
      }
      if (this.browserVoice) u.voice = this.browserVoice;
      u.rate = 1.05;
      u.onend = done; u.onerror = done;
      this.fakeLevel(true);
      speechSynthesis.speak(u);
    },

    unlock() {
      // Muss innerhalb einer Nutzeraktion (Klick/Tippen) passieren, sonst bleibt der Ton stumm.
      try {
        if (navigator.audioSession) navigator.audioSession.type = "playback"; // iPhone: auch bei Stummschalter hörbar
        if (!this.ctx) {
          this.ctx = new (window.AudioContext || window.webkitAudioContext)();
          this.analyser = this.ctx.createAnalyser();
          this.analyser.fftSize = 256;
          this.analyser.connect(this.ctx.destination);
          // kurzer stiller Ton schaltet Audio auf iOS frei
          const b = this.ctx.createBuffer(1, 1, 22050);
          const s = this.ctx.createBufferSource();
          s.buffer = b; s.connect(this.ctx.destination); s.start(0);
        }
        if (this.ctx.state !== "running") this.ctx.resume();
      } catch { /* dann eben Browserstimme */ }
    },

    trackLevel() {
      cancelAnimationFrame(this.levelRaf);
      const data = new Uint8Array(this.analyser.frequencyBinCount);
      const tick = () => {
        this.analyser.getByteFrequencyData(data);
        let sum = 0; for (const v of data) sum += v;
        setLevel(Math.min(1, (sum / data.length) / 90));
        if (this.active) this.levelRaf = requestAnimationFrame(tick);
        else setLevel(0);
      };
      tick();
    },

    fakeLevel(on) {
      cancelAnimationFrame(this.levelRaf);
      if (!on) { setLevel(0); return; }
      const tick = (t) => {
        setLevel(0.35 + 0.3 * Math.abs(Math.sin(t / 140)));
        if (this.active) this.levelRaf = requestAnimationFrame(tick); else setLevel(0);
      };
      this.levelRaf = requestAnimationFrame(tick);
    },

    stop() {
      this.queue = [];
      const cur = this.current;
      this.current = null;
      if (cur && cur.source) { cur.source.onended = null; try { cur.source.stop(); } catch { /* schon zu Ende */ } }
      if ("speechSynthesis" in window) speechSynthesis.cancel();
      this.lastText = "";
      this.finished();
    },

    finished() {
      if (!this.active) return;
      this.active = false;
      setLevel(0);
      refreshState();
      maybeListenAgain();
    },
  };

  function setLevel(v) { document.documentElement.style.setProperty("--level", v.toFixed(3)); }

  // ---------- Mikrofon ----------
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const recog = {
    r: null, active: false, finalText: "",
    supported: !!SR,
    start() {
      if (!this.supported) {
        addMessage("jarvis", "Ihr Browser unterstützt leider keine Spracheingabe. Bitte verwenden Sie Google Chrome oder Microsoft Edge – tippen funktioniert natürlich trotzdem.", "error");
        return;
      }
      if (this.active) return;
      voice.stop();
      const r = new SR();
      r.lang = status.language || "de-DE";
      r.interimResults = true;
      r.continuous = false;
      r.maxAlternatives = 1;
      this.finalText = "";
      r.onresult = (e) => {
        let interim = "";
        for (let i = e.resultIndex; i < e.results.length; i++) {
          const res = e.results[i];
          if (res.isFinal) this.finalText += res[0].transcript;
          else interim += res[0].transcript;
        }
        interimEl.textContent = (this.finalText + " " + interim).trim();
      };
      r.onerror = (e) => {
        if (e.error === "not-allowed" || e.error === "service-not-allowed") {
          addMessage("jarvis", "Ich habe keinen Zugriff auf das Mikrofon. Bitte erlauben Sie den Zugriff über das Schloss-Symbol in der Adressleiste.", "error");
          convToggle.checked = false;
        }
      };
      r.onend = () => {
        this.active = false;
        this.r = null;
        interimEl.textContent = "";
        const text = this.finalText.trim();
        refreshState();
        if (text) send(text);
        else maybeListenAgain();
      };
      this.r = r;
      this.active = true;
      refreshState();
      try { r.start(); } catch { this.active = false; refreshState(); }
    },
    stop() { if (this.r) this.r.stop(); },
    abort() { if (this.r) { this.finalText = ""; this.r.abort(); } },
  };

  function maybeListenAgain() {
    if (convToggle.checked && !busy && !voice.active && !recog.active) {
      setTimeout(() => { if (convToggle.checked && !busy && !voice.active && !recog.active) recog.start(); }, 350);
    }
  }

  function toggleListening() {
    if (recog.active) { recog.stop(); return; }
    if (busy) return;
    if (voice.active) voice.stop();
    recog.start();
  }

  // ---------- Gespräch ----------
  async function send(text) {
    text = text.trim();
    if (!text || busy) return;
    if (/^(neues gespräch|reset|vergiss alles)[.!]?$/i.test(text)) { resetConversation(); return; }

    busy = true;
    sendBtn.disabled = true;
    voice.stop();
    addMessage("user", text);
    refreshState();

    let msgEl = null, body = null;
    const sentences = new SentenceBuffer();
    const ensureMsg = () => {
      if (!msgEl) { msgEl = addMessage("jarvis", "", "streaming"); body = msgEl.querySelector(".body"); }
    };
    const closeMsg = () => {
      if (msgEl) msgEl.classList.remove("streaming");
      msgEl = null; body = null;
    };

    const handle = (ev) => {
      switch (ev.type) {
        case "text":
          ensureMsg();
          body.textContent += ev.text;
          scrollDown();
          for (const s of sentences.feed(ev.text)) voice.say(s);
          break;
        case "status":
          closeMsg();
          addStatus(ev.text);
          break;
        case "file": {
          closeMsg();
          const card = fileCard(ev.name, ev.url);
          logEl.append(card);
          scrollDown();
          if (ev.action === "open") downloadNow(ev.url, ev.name);
          break;
        }
        case "open": {
          closeMsg();
          const w = window.open(ev.url, "_blank", "noopener");
          if (!w) {
            const div = document.createElement("div");
            div.className = "status-line link-card";
            div.innerHTML = 'Webseite öffnen: <a target="_blank" rel="noopener"></a>';
            const a = div.querySelector("a"); a.href = ev.url; a.textContent = ev.url;
            logEl.append(div); scrollDown();
          }
          break;
        }
        case "error":
          closeMsg();
          addMessage("jarvis", ev.text, "error");
          break;
      }
    };

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ session: sessionId, text }),
      });
      if (res.status === 401) { await askPassword(); throw new Error("Bitte erneut senden."); }
      if (!res.ok || !res.body) throw new Error(`Server antwortet mit Status ${res.status}`);

      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let nl;
        while ((nl = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, nl).trim();
          buf = buf.slice(nl + 1);
          if (line) { try { handle(JSON.parse(line)); } catch (e) { console.warn(e); } }
        }
      }
    } catch (e) {
      closeMsg();
      addMessage("jarvis", "Verbindung zum JARVIS-Server unterbrochen. " + (e.message || ""), "error");
    } finally {
      for (const s of sentences.flush()) voice.say(s);
      closeMsg();
      busy = false;
      sendBtn.disabled = false;
      refreshState();
      if (!voice.active) maybeListenAgain();
      loadFiles();
    }
  }

  async function resetConversation() {
    voice.stop();
    try {
      await fetch("/api/reset", { method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ session: sessionId }) });
    } catch { /* neue Sitzung reicht */ }
    sessionId = newId();
    store.set("jarvis-session", sessionId);
    logEl.innerHTML = "";
    const t = "Gedächtnis gelöscht. Wir beginnen von vorn.";
    addMessage("jarvis", t);
    voice.say(t);
  }

  async function askPassword() {
    const p = window.prompt("JARVIS ist passwortgeschützt. Passwort:");
    if (p) { password = p; store.set("jarvis-key", p); }
  }

  // ---------- Dateien ----------
  async function loadFiles() {
    try {
      const res = await fetch("/api/files", { headers: authHeaders() });
      if (!res.ok) return;
      const files = await res.json();
      const list = $("#files-list");
      list.innerHTML = "";
      if (!files.length) {
        const p = document.createElement("div"); p.className = "files-empty";
        p.textContent = "Noch keine Dateien. Bitten Sie JARVIS z. B. um eine Präsentation.";
        list.append(p);
      }
      for (const f of files) list.append(fileCard(f.name, f.url));
    } catch { /* still */ }
  }

  // ---------- Start ----------
  function greeting() {
    const h = new Date().getHours();
    const g = h < 11 ? "Guten Morgen" : h < 18 ? "Guten Tag" : "Guten Abend";
    return `${g}, ${status.user_name}. Alle Systeme sind online. Wie kann ich helfen?`;
  }

  function pill(id, ok, title) {
    const el = $(id);
    el.classList.add(ok ? "ok" : "warn");
    el.title = title;
  }

  let greeted = false;
  function greetOnce() {
    if (greeted) return;
    greeted = true;
    voice.say(logEl.querySelector(".msg.jarvis .body")?.textContent || greeting());
  }

  async function init() {
    try {
      const res = await fetch("/api/status");
      status = await res.json();
    } catch { /* Standardwerte */ }
    if (status.password_required && !password) await askPassword();

    pill("#sys-brain", status.brain_ok, status.brain_ok ? `Gehirn: ${status.model}` : "API-Key fehlt");
    pill("#sys-voice", status.voice === "elevenlabs", status.voice === "elevenlabs" ? "ElevenLabs" : "Browserstimme (kein ElevenLabs-Key)");
    pill("#sys-mic", recog.supported, recog.supported ? "Spracheingabe verfügbar" : "Spracheingabe nur in Chrome/Edge");

    convToggle.checked = store.get("jarvis-conv") === "1";
    voiceToggle.checked = store.get("jarvis-voice", "1") === "1";

    addMessage("jarvis", greeting());
    if (!status.brain_ok) {
      addMessage("jarvis", "Hinweis: Mir fehlt noch mein API-Key (kostenlos: GEMINI_API_KEY von aistudio.google.com). Bitte eintragen und mich neu starten.", "error");
    }
    loadFiles();
    setState("idle");
    input.focus();
  }

  // Browser erlauben Ton erst nach einer Interaktion -> Begrüßung beim ersten Klick/Tastendruck sprechen.
  ["pointerdown", "keydown", "touchend", "click"].forEach((evt) =>
    window.addEventListener(evt, () => voice.unlock(), { capture: true }));
  window.addEventListener("pointerdown", (e) => {
    // Begrüßung nur sprechen, wenn der erste Klick nicht schon Mikrofon/Eingabe startet
    if (!e.target.closest("#mic, #reactor, #composer, .toggle")) greetOnce(); else greeted = true;
  }, { once: true, capture: true });

  // ---------- Ereignisse ----------
  $("#composer").addEventListener("submit", (e) => {
    e.preventDefault();
    greeted = true;
    const t = input.value;
    input.value = "";
    autoGrow();
    send(t);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#composer").requestSubmit(); }
    if (e.key === "Escape") voice.stop();
  });
  function autoGrow() { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 160) + "px"; }
  input.addEventListener("input", autoGrow);

  $("#mic").addEventListener("click", () => { greeted = true; toggleListening(); });
  $("#reactor").addEventListener("click", () => { greeted = true; voice.active ? voice.stop() : toggleListening(); });
  $("#btn-reset").addEventListener("click", resetConversation);
  $("#btn-files").addEventListener("click", () => { $("#files-panel").hidden = !$("#files-panel").hidden; loadFiles(); });
  $("#files-close").addEventListener("click", () => { $("#files-panel").hidden = true; });

  convToggle.addEventListener("change", () => {
    store.set("jarvis-conv", convToggle.checked ? "1" : "0");
    if (convToggle.checked) { greeted = true; maybeListenAgain(); } else recog.abort();
  });
  voiceToggle.addEventListener("change", () => {
    store.set("jarvis-voice", voiceToggle.checked ? "1" : "0");
    if (!voiceToggle.checked) voice.stop();
  });

  // Leertaste (außerhalb des Textfelds) = Sprechen
  window.addEventListener("keydown", (e) => {
    if (e.code === "Space" && document.activeElement === document.body && !e.repeat) { e.preventDefault(); greeted = true; toggleListening(); }
  });

  if ("speechSynthesis" in window) speechSynthesis.onvoiceschanged = () => { voice.browserVoice = null; };

  init();
})();
