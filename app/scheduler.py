"""Revisa cada minuto qué recordatorios toca enviar y hace el seguimiento."""
import logging
import random
from datetime import timedelta
from zoneinfo import ZoneInfo

from flask import current_app

from .models import Reminder, db, utcnow
from .push import make_action_token, send_to_user

log = logging.getLogger(__name__)

# Frases para cuando insiste (seguimiento), para que no se sienta robótico
FOLLOWUP_PREFIXES = [
    "¿Ya lo hiciste? 👀",
    "Sr. Patroclo te recuerda 🐧",
    "No lo olvides 💜",
    "Seguimos esperando ✅",
]


def local_tz() -> ZoneInfo:
    return ZoneInfo(current_app.config["TIMEZONE"])


def fmt_local(dt) -> str:
    from datetime import timezone

    return dt.replace(tzinfo=timezone.utc).astimezone(local_tz()).strftime("%I:%M %p").lstrip("0")


def build_payload(r: Reminder, is_followup: bool) -> dict:
    body_parts = []
    if r.event_at:
        body_parts.append(f"⏰ Es a las {fmt_local(r.event_at)}")
    if r.notes:
        body_parts.append(r.notes)
    if r.created_by_id != r.owner_id:
        body_parts.append(f"— {r.created_by.display_name}")

    title = r.title
    if is_followup:
        title = f"{random.choice(FOLLOWUP_PREFIXES)} {r.title}"

    return {
        "title": title,
        "body": "\n".join(body_parts) or ("Tócalo cuando lo termines" if r.kind == "checkin" else ""),
        "tag": f"reminder-{r.id}",  # reemplaza la notificación anterior del mismo recordatorio
        "reminder_id": r.id,
        "kind": r.kind,
        "token": make_action_token(r.id, r.owner_id),
        "url": f"/?r={r.id}",
        "speak": r.title,  # texto que la app lee en voz alta si está abierta
    }


# Días que se guardan los recordatorios terminados antes de borrarse solos
KEEP_DAYS = 3


def cleanup_old_reminders(now=None) -> int:
    """Borra recordatorios ya hechos (o que nunca se confirmaron) con más de KEEP_DAYS días.
    Los que se repiten nunca se borran: siguen programándose para la próxima vez."""
    now = now or utcnow()
    limit = now - timedelta(days=KEEP_DAYS)
    done = Reminder.query.filter(Reminder.status == "done", Reminder.done_at < limit)
    missed = Reminder.query.filter(Reminder.status == "missed",
                                   db.func.coalesce(Reminder.last_sent_at, Reminder.notify_at) < limit)
    n = done.delete(synchronize_session=False) + missed.delete(synchronize_session=False)
    if n:
        db.session.commit()
        log.info("Limpieza: %d recordatorios viejos borrados", n)
    return n


_last_cleanup = None


def process_due_reminders(now=None) -> int:
    """Procesa los recordatorios vencidos. Devuelve cuántos se procesaron."""
    now = now or utcnow()
    tz = local_tz()

    q = Reminder.query.filter(Reminder.status == "pending", Reminder.next_fire_at <= now)
    if db.engine.dialect.name == "postgresql":
        q = q.with_for_update(skip_locked=True)  # evita duplicados si hay varios procesos
    due = q.all()

    for r in due:
        followups_on = (r.followup_every_min or 0) > 0
        exhausted = followups_on and r.sent_count > (r.followup_max or 0)

        if exhausted:
            # Ya insistimos todas las veces y no respondió
            if r.notify_creator_on_miss and r.created_by_id != r.owner_id:
                send_to_user(r.created_by, {
                    "title": f"⚠️ {r.owner.display_name} no confirmó: {r.title}",
                    "body": f"Se le avisó {r.sent_count} veces. Quizás quieras escribirle o llamarle.",
                    "tag": f"miss-{r.id}",
                    "reminder_id": r.id,
                    "kind": "info",
                    "url": f"/?r={r.id}",
                })
            if not r.schedule_next_occurrence(tz, now):
                r.status = "missed"
                r.next_fire_at = None
            continue

        send_to_user(r.owner, build_payload(r, is_followup=r.sent_count > 0))
        r.sent_count = (r.sent_count or 0) + 1
        r.last_sent_at = now

        if followups_on:
            r.next_fire_at = now + timedelta(minutes=r.followup_every_min)
        elif not r.schedule_next_occurrence(tz, now):
            r.next_fire_at = None  # queda pendiente hasta que lo marque como hecho

    db.session.commit()
    if due:
        log.info("Procesados %d recordatorios", len(due))

    # Limpieza automática, una vez por hora
    global _last_cleanup
    if _last_cleanup is None or now - _last_cleanup >= timedelta(hours=1):
        _last_cleanup = now
        cleanup_old_reminders(now)
    return len(due)


def start_scheduler(app) -> None:
    from apscheduler.schedulers.background import BackgroundScheduler

    def job():
        with app.app_context():
            try:
                process_due_reminders()
            except Exception:  # noqa: BLE001
                db.session.rollback()
                log.exception("Fallo procesando recordatorios")

    sched = BackgroundScheduler(daemon=True, timezone="UTC")
    sched.add_job(job, "interval", seconds=30, id="tick", max_instances=1, coalesce=True)
    sched.start()
    app.extensions["reminder_scheduler"] = sched
