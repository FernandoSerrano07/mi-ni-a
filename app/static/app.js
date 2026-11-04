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

  // ---------------- Formulario ----------------
  const form = $("#rem-form");
  if (!form) return;
  const ev = $("#hora_evento"), mins = $("#minutos_antes"), exact = $("#hora_aviso"), prev = $("#preview");
  const modeRadios = $$('input[name="modo_aviso"]');
  const mode = () => (modeRadios.find((r) => r.checked) || {}).value;

  const fmt = (h, m) => { const ap = h >= 12 ? "p. m." : "a. m."; return `${((h + 11) % 12) + 1}:${String(m).padStart(2, "0")} ${ap}`; };

  function refresh() {
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
    if (d.ev) { ev.value = d.ev; mins.value = d.min || 20; modeRadios.find((r) => r.value === "antes").checked = true; }
    else { ev.value = ""; exact.value = d.ex || ""; modeRadios.find((r) => r.value === "exacta").checked = true; }
    if (d.rep) $("#repetir").value = d.rep;
    refresh();
  });
  refresh();
})();
