# 🐧 Sr. Patroclo 💜 — la ayuda de Yareni

Recordatorios con notificaciones y seguimiento, hechos con cariño para Yareni.

App web (Python + Flask) que se instala en el teléfono como una app y envía **notificaciones push** con **seguimiento**: si no se marca como hecho, vuelve a avisar cada X minutos y, si nunca se confirma, le avisa a quien escribió el recordatorio.

## Qué hace

- **Inicio de sesión una sola vez**: la sesión dura 1 año en ese teléfono.
- **Dos (o más) usuarios**: tú puedes escribirle recordatorios a ella y ver todos los suyos.
- **Hora del evento + hora del aviso**: "la sobrina sale a las 11:00, avísame 20 min antes" → aviso a las 10:40. También puedes poner una hora exacta.
- **Preguntas de seguimiento**: "¿Terminaste tu tarea de matemática?" con botones **✓ Sí, ya** / **Todavía no** dentro de la notificación.
- **Insistir**: si no responde, vuelve a avisar cada 5/10/15… min hasta N veces.
- **Aviso al creador**: si nunca confirma, te llega a ti "⚠️ Yareni no confirmó: …". Y cuando lo marca como hecho, te llega "✅ Yareni completó: …".
- **Repetir**: diario, lunes a viernes o semanal (ideal para el colegio).
- **Notificaciones que se notan**: se quedan en pantalla hasta tocarlas, vibran fuerte, vuelven a sonar en cada seguimiento y llevan prioridad alta.
- **Lectura en voz alta** (opcional): si la app está abierta, lee el recordatorio en voz alta.
- **Anotar en 5 segundos**: escribe o dicta 🎤 «mañana 11 ir por la sobrina, avísame 20 antes» y la app lo entiende sola.
- **Modo examen 📚**: pon el día del parcial y cada día pregunta «¿estudiaste hoy?» con cuenta regresiva.
- **Resumen de la mañana ☀️** (7:00 por defecto) con lo del día, y **resumen del domingo 🏆** con lo cumplido.
- **Notitas 💌**: escribe notitas sorpresa; le llega una a la otra persona cuando cumple algo (máx. 1 por día).
- **Buzón 💌**: notas entre ustedes con foto o audio; aparecen en una ventanita al entrar y se pueden responder.
- **Estudio 📚**: sube la guía (PDF, Word, foto o texto) y Gemini arma cuestionarios con preguntas distintas cada vez. Requiere `GEMINI_API_KEY` (gratis en aistudio.google.com/apikey).
- **Limpieza automática**: los terminados se borran solos al cuarto día.
- Diseño morado con tulipanes, gerberas y pingüinos (ilustraciones propias en SVG, sin imágenes externas).
- Modo oscuro automático.

## ¿Firebase u otra cosa?

| Parte | Recomendación | Por qué |
|---|---|---|
| Base de datos | **PostgreSQL gratis en [Neon](https://neon.tech) o [Supabase](https://supabase.com)** (SQLite en tu compu) | Encaja natural con Python/SQLAlchemy, es relacional (usuarios ↔ recordatorios) y no te amarra a Google. Firestore funciona, pero con Python es más incómodo y no aporta nada aquí. |
| Notificaciones | **Web Push estándar (VAPID)**, ya incluido | Gratis, sin cuenta de Firebase. Funciona en Android (Chrome) y en iPhone con iOS 16.4+ si la app se agrega a la pantalla de inicio. |
| Servidor | **[Render](https://render.com)** (incluye `render.yaml`) o Railway / Fly.io | Despliegue directo desde el repo de GitHub. |

> **Importante sobre el plan gratis de Render:** se "duerme" tras 15 min sin visitas y entonces no envía avisos. Solución gratis: crea una tarea en [cron-job.org](https://cron-job.org) que llame cada minuto a
> `https://TU-APP.onrender.com/cron/tick?key=TU_CRON_SECRET`. Eso la mantiene despierta y procesa los recordatorios. (Con un plan pagado de ~7 USD/mes no hace falta.)

## Probar en tu computadora

```bash
python -m venv .venv && source .venv/bin/activate   # En Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python generar_vapid.py          # copia las 2 líneas al .env
flask --app app crear-usuario yareni "Yareni"
flask --app app crear-usuario fernando "Fernando"
python run.py                    # abre http://localhost:5000
```

Las notificaciones push necesitan HTTPS (en `localhost` funcionan en Chrome de la compu, pero para el teléfono hay que desplegar).

## Publicar (Render + Neon)

1. Crea una base de datos gratis en **[Neon](https://neon.tech)** y copia la *connection string* (`postgresql://...`).
2. Genera las llaves de notificaciones con `python generar_vapid.py` (o pídeselas a quien te ayude a configurar).
3. En **[Render](https://render.com)**: *New → Blueprint* → elige este repo. Te pedirá:
   - `DATABASE_URL`: la URL de Neon
   - `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` (`mailto:tu-correo`)
   - `CRON_SECRET`: una clave cualquiera larga
   - `GEMINI_API_KEY`: llave gratis de https://aistudio.google.com/apikey (para los cuestionarios)
   - `USUARIOS`: las cuentas, así → `yareni:Yareni:SuContraseña;fernando:Fernando:TuContraseña`
     (se crean solas al arrancar; después puedes borrar esta variable)
4. (Plan gratis) en **[cron-job.org](https://cron-job.org)** crea una tarea **cada minuto** a
   `https://TU-APP.onrender.com/cron/tick?key=TU_CRON_SECRET`.

## Instalar en el teléfono

**Android (Chrome):** abre la página → inicia sesión → toca **Activar** notificaciones → menú ⋮ → **Instalar app**.

**iPhone (iOS 16.4 o más):** abre en **Safari** → **Compartir** → **Agregar a pantalla de inicio** → abre la app *desde el ícono* → inicia sesión → **Activar**.
En iPhone, revisa también *Ajustes → Notificaciones → Sr. Patroclo* y ponlas como **Destacadas / Sensibles al tiempo** y fuera del modo *Concentración* para que se vean siempre.

Después toca **Ajustes → Enviar notificación de prueba** para confirmar.

## Comandos útiles

```bash
flask --app app crear-usuario <usuario> "<Nombre>"
flask --app app cambiar-password <usuario>
flask --app app tick          # procesa recordatorios vencidos una vez
python -m pytest -q           # pruebas
```

## Estructura

```
app/
  __init__.py     # crea la app, comandos de consola
  config.py       # configuración por variables de entorno
  models.py       # Usuario, Suscripción push, Recordatorio (+ lógica de repetición)
  routes.py       # pantallas, API de notificaciones, /cron/tick
  scheduler.py    # revisa cada 30 s qué avisar e insiste si no se confirmó
  push.py         # envío Web Push y tokens de los botones
  templates/      # HTML
  static/         # CSS, JS, service worker, íconos, manifest
run.py            # servidor de desarrollo
wsgi.py           # producción (gunicorn)
```

## Ideas para después

- Bot de **Telegram/WhatsApp** como segundo canal (si el teléfono bloquea las notificaciones web).
- Recordatorios por **ubicación** ("cuando salgas del trabajo").
- Racha semanal de tareas cumplidas 🎯.
- Escribir el recordatorio en lenguaje natural: "mañana a las 11 ir por la sobrina, avísame 20 min antes".
