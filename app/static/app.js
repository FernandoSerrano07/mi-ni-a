(() => {
  const CSRF = document.querySelector('meta[name="csrf-token"]').content;
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));

  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch (_) { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (_) {} },
  };

  const post = (url, body) => fetch(url, {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF },
    body: JSON.stringify(body || {}),
  });

  // ---------------- Notificaciones push ----------------
  const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
  const isStandalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone;
  const pushSupported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

  function b64ToUint8(b64) {
    const pad = "=".repeat((4 - (b64.length % 4)) % 4);
    const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
  }

  async function subscribe() {
    const reg = await navigator.serviceWorker.ready;
    const { key } = await (await fetch("/api/vapid-public-key")).json();
    if (!key) throw new Error("El servidor no tiene llaves VAPID configuradas");
    let sub = await reg.pushManager.getSubscription();
    if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToUint8(key) });
    await post("/api/subscribe", sub.toJSON());
    return sub;
  }

  function setStatus(t) { const el = $("#push-status"); if (el) el.textContent = t; }

  async function initPush() {
    const banner = $("#push-banner");
    if (!banner) return;
    const help = $("#push-help");
    const btn = $("#push-enable");

    if (isIOS && !isStandalone) {
      banner.hidden = false;
      help.innerHTML = "En iPhone: toca <b>Compartir</b> → <b>Agregar a pantalla de inicio</b>. Luego abre la app desde el ícono y activa aquí.";
      btn.hidden = true;
      setStatus("Instala la app en la pantalla de inicio para recibir avisos.");
      return;
    }
    if (!pushSupported) {
      banner.hidden = false; btn.hidden = true;
      help.textContent = "Este navegador no permite notificaciones. Usa Chrome (Android) o Safari (iPhone).";
      return;
    }
    if (Notification.permission === "granted") {
      try { await subscribe(); setStatus("✅ Notificaciones activas en este dispositivo."); }
      catch (e) { setStatus("⚠️ " + e.message); }
      return;
    }
    if (Notification.permission === "denied") {
      banner.hidden = false; btn.hidden = true;
      help.textContent = "Bloqueaste las notificaciones. Actívalas en los ajustes del navegador para este sitio.";
      return;
    }
    banner.hidden = false;
    btn.onclick = async () => {
      const perm = await Notification.requestPermission();
      if (perm !== "granted") { help.textContent = "Necesito permiso para avisarte 🙏"; return; }
      try { await subscribe(); banner.hidden = true; setStatus("✅ Notificaciones activas en este dispositivo."); }
      catch (e) { help.textContent = "No se pudo activar: " + e.message; }
    };
  }

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).then(initPush).catch((e) => setStatus("⚠️ " + e.message));
  } else { initPush(); }

  const testBtn = $("#push-test");
  if (testBtn) testBtn.onclick = async () => {
    const r = await (await post("/api/test-push")).json();
    setStatus(r.sent ? `Enviada a ${r.sent} dispositivo(s) 📲` : "No hay dispositivos suscritos. Activa las notificaciones primero.");
  };

  // ---------------- Voz (lee el recordatorio en voz alta si la app está abierta) ----------------
  const voiceToggle = $("#voice-toggle");
  const voiceOn = () => store.get("voz", "1") === "1";
  if (voiceToggle) {
    voiceToggle.checked = voiceOn();
    voiceToggle.onchange = () => { store.set("voz", voiceToggle.checked ? "1" : "0"); if (voiceToggle.checked) speak("Listo, te leeré los recordatorios"); };
  }
  function speak(text) {
    if (!voiceOn() || !("speechSynthesis" in window) || !text) return;
    const u = new SpeechSynthesisUtterance(text);
    const voices = speechSynthesis.getVoices();
    u.voice = voices.find((v) => /es[-_](MX|US|419|SV)/i.test(v.lang)) || voices.find((v) => v.lang.startsWith("es")) || null;
    u.lang = (u.voice && u.voice.lang) || "es-MX";
    u.rate = 0.95;
    speechSynthesis.cancel(); speechSynthesis.speak(u);
  }
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.addEventListener("message", (e) => {
      if (e.data && e.data.type === "push") { speak("Recordatorio: " + e.data.speak); setTimeout(() => location.reload(), 4000); }
    });
  }

  // ---------------- Pantalla principal ----------------
  const hl = $(".rem.highlight");
  if (hl) hl.scrollIntoView({ behavior: "smooth", block: "center" });
  if ($("#push-banner")) {
    // Refresca la lista cada minuto mientras está abierta
    setInterval(() => { if (document.visibilityState === "visible") location.reload(); }, 60000);
  }

  // ---------------- Anotar en 5 segundos ----------------
  const qi = $("#quick-input");
  if (qi) {
    $$(".quick .hint").forEach((h) => h.onclick = () => { qi.value = h.textContent; qi.focus(); });
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    const mic = $("#quick-mic");
    if (SR && mic) {
      mic.hidden = false;
      mic.onclick = () => {
        const rec = new SR();
        rec.lang = "es-SV"; rec.interimResults = true; rec.maxAlternatives = 1;
        mic.classList.add("on");
        rec.onresult = (e) => { qi.value = Array.from(e.results).map((r) => r[0].transcript).join(" "); };
        rec.onend = () => mic.classList.remove("on");
        rec.onerror = () => mic.classList.remove("on");
        rec.start();
      };
    }
  }

  // ---------------- Notitas ----------------
  $$(".note-idea").forEach((b) => b.onclick = () => { const t = $('textarea[name="texto"]'); if (t) t.value = b.textContent.trim(); });

  // ---------------- Ventana flotante de nota nueva ----------------
  const pop = $("#note-pop");
  if (pop) {
    const close = () => { pop.classList.add("bye"); setTimeout(() => pop.remove(), 250); post(`/buzon/${pop.dataset.id}/leido`); };
    $("#note-close").onclick = close;
    pop.addEventListener("click", (e) => { if (e.target === pop) close(); });
    const txt = pop.querySelector(".note-text");
    if (txt) setTimeout(() => speak(txt.textContent), 600);
  }

  // ---------------- Buzón: foto (comprimida) y audio ----------------
  function compressImage(file, maxSide = 1400, quality = 0.82) {
    return new Promise((resolve) => {
      const img = new Image();
      img.onload = () => {
        const s = Math.min(1, maxSide / Math.max(img.width, img.height));
        const c = document.createElement("canvas");
        c.width = Math.round(img.width * s); c.height = Math.round(img.height * s);
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        c.toBlob((b) => resolve(b || file), "image/jpeg", quality);
        URL.revokeObjectURL(img.src);
      };
      img.onerror = () => resolve(file);
      img.src = URL.createObjectURL(file);
    });
  }

  $$(".msg-form, .note-reply").forEach((f) => {
    const state = { photo: null, audio: null };
    const photoIn = f.querySelector(".photo-input");
    const photoPrev = f.querySelector(".photo-preview");
    const recBtn = f.querySelector(".rec-btn");
    const recStatus = f.querySelector(".rec-status");
    const audioPrev = f.querySelector(".audio-preview");

    if (photoIn) photoIn.onchange = async () => {
      const file = photoIn.files[0]; if (!file) return;
      state.photo = await compressImage(file);
      photoPrev.src = URL.createObjectURL(state.photo); photoPrev.hidden = false;
    };

    if (recBtn) {
      if (!navigator.mediaDevices || !window.MediaRecorder) { recBtn.hidden = true; }
      let rec = null, chunks = [], timer = null, secs = 0;
      recBtn.onclick = async () => {
        if (rec && rec.state === "recording") { rec.stop(); return; }
        try {
          const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
          const type = ["audio/webm;codecs=opus", "audio/mp4", "audio/webm", "audio/ogg"].find((t) => MediaRecorder.isTypeSupported(t)) || "";
          rec = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
          chunks = []; secs = 0;
          rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
          rec.onstop = () => {
            clearInterval(timer); stream.getTracks().forEach((t) => t.stop());
            state.audio = new Blob(chunks, { type: (rec.mimeType || "audio/webm").split(";")[0] });
            audioPrev.src = URL.createObjectURL(state.audio); audioPrev.hidden = false;
            recBtn.textContent = "🎤 Grabar otro"; recBtn.classList.remove("on"); recStatus.textContent = `Audio de ${secs} s listo`;
          };
          rec.start(); recBtn.textContent = "⏹ Detener"; recBtn.classList.add("on");
          timer = setInterval(() => { secs++; recStatus.textContent = `🔴 Grabando… ${secs} s`; if (secs >= 90) rec.stop(); }, 1000);
        } catch (_) { recStatus.textContent = "No tengo permiso para el micrófono"; }
      };
    }

    f.addEventListener("submit", async (e) => {
      if (!state.photo && !state.audio) return; // sin archivos: envío normal
      e.preventDefault();
      const fd = new FormData(f);
      fd.delete("foto");
      if (state.photo) fd.append("foto", state.photo, "foto.jpg");
      if (state.audio) fd.append("audio", state.audio, "audio." + (state.audio.type.includes("mp4") ? "m4a" : "webm"));
      const btn = f.querySelector("button.primary, button.btn.primary"); if (btn) { btn.disabled = true; btn.textContent = "Enviando…"; }
      const r = await fetch(f.action || location.href, { method: "POST", body: fd, headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { alertBox(j.error || "No se pudo enviar"); if (btn) { btn.disabled = false; btn.textContent = "Enviar"; } return; }
      location.href = j.redirect || location.pathname;
    });
  });
  function alertBox(t) { const d = document.createElement("div"); d.className = "flash error"; d.textContent = t; ($("main") || document.body).prepend(d); window.scrollTo(0, 0); }

  // ---------------- Estudio ----------------
  const gf = $("#guide-file");
  if (gf) {
    gf.onchange = () => { $("#guide-file-name").textContent = gf.files[0] ? "✓ " + gf.files[0].name : ""; };
    gf.form.addEventListener("submit", () => { const b = $("#guide-submit"); b.textContent = "Leyendo tu guía… 📖"; b.disabled = true; });
  }

  const quiz = $("#quiz");
  if (quiz) {
    const cheers = ["¡Eso! 🎉", "¡Muy bien! 💜", "¡Correcto! 🐧", "¡Excelente! 🌷", "¡Así se hace! ✨"];
    const oops = ["Casi 💪", "No pasa nada, así se aprende 🌱", "Ups, esta era otra 🙈"];
    const pick = (a) => a[Math.floor(Math.random() * a.length)];
    (async () => {
      const r = await post(`/api/estudio/${quiz.dataset.gid}/cuestionario`);
      const j = await r.json().catch(() => ({ error: "Error de conexión" }));
      $("#quiz-loading").hidden = true;
      if (!r.ok) { $("#quiz-error").hidden = false; $("#quiz-error-text").textContent = j.error; return; }
      const qs = j.questions; let i = 0, score = 0;
      const box = $("#quiz-box"); box.hidden = false;
      const show = () => {
        const q = qs[i];
        $("#quiz-bar").style.width = `${(i / qs.length) * 100}%`;
        $("#quiz-count").textContent = `Pregunta ${i + 1} de ${qs.length} · ${score} correctas`;
        $("#quiz-q").textContent = q.q;
        const opts = $("#quiz-options"); opts.innerHTML = "";
        $("#quiz-why").hidden = true; $("#quiz-next").hidden = true;
        q.options.forEach((o, k) => {
          const b = document.createElement("button"); b.className = "quiz-opt"; b.textContent = o;
          b.onclick = () => {
            $$(".quiz-opt").forEach((x) => x.disabled = true);
            const ok = k === q.answer; if (ok) score++;
            b.classList.add(ok ? "right" : "wrong");
            opts.children[q.answer].classList.add("right");
            const w = $("#quiz-why"); w.hidden = false;
            w.innerHTML = `<b>${ok ? pick(cheers) : pick(oops)}</b><br>`; w.append(document.createTextNode(q.why || ""));
            const n = $("#quiz-next"); n.hidden = false; n.textContent = i + 1 < qs.length ? "Siguiente →" : "Ver resultado 🏁";
          };
          opts.append(b);
        });
      };
      $("#quiz-next").onclick = () => { i++; if (i < qs.length) { show(); window.scrollTo(0, 0); } else finish(); };
      const finish = () => {
        box.hidden = true; $("#quiz-end").hidden = false;
        const pct = score / qs.length;
        $("#quiz-end-art").innerHTML = $(pct >= 0.6 ? "#art-feliz" : "#art-libro").innerHTML;
        $("#quiz-score").textContent = `${score} de ${qs.length} correctas`;
        $("#quiz-msg").textContent = pct === 1 ? "¡Perfecto! Vas a sacar 10 💜🎉" : pct >= 0.75 ? "¡Muy bien! Ya casi lo dominas 🌷" :
          pct >= 0.5 ? "¡Bien! Repasa las que fallaste y vuelve a intentar 💪" : "Tranquila, cada repaso cuenta. Lee la guía otra vez y probamos de nuevo 🐧";
        post(`/api/estudio/intento/${j.attempt}/resultado`, { score });
        speak($("#quiz-score").textContent);
      };
      show();
    })();
  }

  // ---------------- Formulario ----------------
  const form = $("#rem-form");
  if (!form) return;
  const ev = $("#hora_evento"), mins = $("#minutos_antes"), exact = $("#hora_aviso"), prev = $("#preview");
  const modeRadios = $$('input[name="modo_aviso"]');
  const mode = () => (modeRadios.find((r) => r.checked) || {}).value;

  const fmt = (h, m) => { const ap = h >= 12 ? "p. m." : "a. m."; return `${((h + 11) % 12) + 1}:${String(m).padStart(2, "0")} ${ap}`; };

  const kindRadios = $$('input[name="tipo"]');
  const kind = () => (kindRadios.find((r) => r.checked) || {}).value;
  const HELP = {
    reminder: "Te aviso a la hora que elijas y, si no lo marcas, insisto.",
    checkin: "Una pregunta como “¿terminaste la tarea?”: insiste hasta que respondas “Sí, ya”.",
    exam: "Pon el día del examen y cada día te pregunto si estudiaste, con cuenta regresiva 📚",
  };
  kindRadios.forEach((r) => r.addEventListener("change", refresh));

  function refresh() {
    const isExam = kind() === "exam";
    $("#bloque-examen").hidden = !isExam;
    $("fieldset.aviso").hidden = isExam;
    $("#bloque-repetir").hidden = isExam;
    $$(".lbl-normal").forEach((e) => e.hidden = isExam);
    $$(".lbl-exam").forEach((e) => e.hidden = !isExam);
    $("#tipo-help").textContent = HELP[kind()] || "";
    if (isExam) { exact.required = false; prev.textContent = ""; return; }
    const hasEvent = !!ev.value;
    if (!hasEvent && mode() === "antes") modeRadios.find((r) => r.value === "exacta").checked = true;
    modeRadios.find((r) => r.value === "antes").disabled = !hasEvent;
    $("#bloque-antes").hidden = mode() !== "antes";
    $("#bloque-exacta").hidden = mode() !== "exacta";
    exact.required = mode() === "exacta";
    $$(".min-chip").forEach((c) => c.classList.toggle("on", c.dataset.min === mins.value));

    let text = "";
    if (mode() === "antes" && hasEvent) {
      const [h, m] = ev.value.split(":").map(Number);
      const t = h * 60 + m - Number(mins.value || 0);
      const day = t < 0 ? " (el día anterior)" : "";
      const tt = ((t % 1440) + 1440) % 1440;
      text = `🔔 Te aviso a las ${fmt(Math.floor(tt / 60), tt % 60)}${day} — ${mins.value || 0} min antes de las ${fmt(h, m)}`;
    } else if (exact.value) {
      const [h, m] = exact.value.split(":").map(Number);
      text = `🔔 Te aviso a las ${fmt(h, m)}`;
    }
    prev.textContent = text;
  }

  $$(".min-chip").forEach((c) => c.onclick = () => { mins.value = c.dataset.min; refresh(); });
  [ev, mins, exact, ...modeRadios].forEach((el) => el.addEventListener("input", refresh));
  modeRadios.forEach((el) => el.addEventListener("change", refresh));

  $$("#plantillas .chip").forEach((c) => c.onclick = () => {
    const d = c.dataset;
    $("#titulo").value = d.t;
    const k = $(`input[name="tipo"][value="${d.k}"]`); if (k) k.checked = true;
    if (d.k === "exam") { ev.value = ""; refresh(); return; }
    if (d.ev) { ev.value = d.ev; mins.value = d.min || 20; modeRadios.find((r) => r.value === "antes").checked = true; }
    else { ev.value = ""; exact.value = d.ex || ""; modeRadios.find((r) => r.value === "exacta").checked = true; }
    if (d.rep) $("#repetir").value = d.rep;
    refresh();
  });
  refresh();
})();
