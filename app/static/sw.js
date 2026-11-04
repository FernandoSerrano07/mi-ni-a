// Service worker: recibe las notificaciones aunque la app esté cerrada.
const VERSION = "v1";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

function actionsFor(kind) {
  if (kind === "reminder") return [
    { action: "done", title: "✓ Ya lo hice" },
    { action: "snooze", title: "⏰ 10 min más" },
  ];
  if (kind === "checkin") return [
    { action: "done", title: "✓ Sí, ya" },
    { action: "snooze", title: "Todavía no" },
  ];
  return [];
}

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (_) { data = { title: event.data && event.data.text() }; }

  const title = data.title || "Recordatorio";
  const options = {
    body: data.body || "",
    tag: data.tag || undefined,
    renotify: true,              // vuelve a sonar/vibrar aunque reemplace a la anterior
    requireInteraction: data.kind !== "info", // se queda en pantalla hasta que la toque
    vibrate: [300, 120, 300, 120, 600],
    icon: "/static/icons/icon-192.png",
    badge: "/static/icons/badge-96.png",
    actions: actionsFor(data.kind),
    data,
  };

  event.waitUntil((async () => {
    await self.registration.showNotification(title, options);
    // Si la app está abierta, que la lea en voz alta
    const clients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    clients.forEach((c) => c.postMessage({ type: "push", title, speak: data.speak || title }));
  })());
});

self.addEventListener("notificationclick", (event) => {
  const n = event.notification;
  const data = n.data || {};
  n.close();

  if ((event.action === "done" || event.action === "snooze") && data.token) {
    event.waitUntil(
      fetch("/api/action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: data.token, action: event.action, minutes: 10 }),
      }).then(async (res) => {
        if (event.action === "done" && res.ok) {
          await self.registration.showNotification("¡Bien hecho! 🐧💜", {
            body: data.title ? `Marcado como hecho` : "", tag: "ok-" + (data.reminder_id || ""),
            icon: "/static/icons/icon-192.png", badge: "/static/icons/badge-96.png",
          });
          setTimeout(() => self.registration.getNotifications({ tag: "ok-" + (data.reminder_id || "") })
            .then((ns) => ns.forEach((x) => x.close())), 4000);
        }
      }).catch(() => {})
    );
    return;
  }

  // Toque normal: abre la app en el recordatorio
  const url = data.url || "/";
  event.waitUntil((async () => {
    const clients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const c of clients) {
      if ("focus" in c) { await c.navigate(url).catch(() => {}); return c.focus(); }
    }
    return self.clients.openWindow(url);
  })());
});

// Si el navegador renueva la suscripción, avisar a la app la próxima vez que abra
self.addEventListener("pushsubscriptionchange", () => {});
