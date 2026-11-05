"""Resumen de la mañana, resumen del domingo y notitas sorpresa."""
import logging
import random
from datetime import datetime, time, timedelta, timezone

from .models import Activity, Note, Reminder, User, db, utcnow
from .push import send_to_user

log = logging.getLogger(__name__)


def _local(dt, tz):
    return dt.replace(tzinfo=timezone.utc).astimezone(tz)


def _hora(dt, tz) -> str:
    return _local(dt, tz).strftime("%I:%M %p").lstrip("0").replace("AM", "a. m.").replace("PM", "p. m.")


def days_until(event_at, tz, now_local) -> int:
    return (_local(event_at, tz).date() - now_local.date()).days


# ---------------- Resumen de la mañana ----------------
def today_items(user: User, tz, now_local) -> list[str]:
    start = datetime.combine(now_local.date(), time.min, tz).astimezone(timezone.utc).replace(tzinfo=None)
    end = start + timedelta(days=1)
    items = []
    q = Reminder.query.filter(Reminder.owner_id == user.id, Reminder.status == "pending")
    for r in q.filter(Reminder.notify_at >= start, Reminder.notify_at < end).order_by(Reminder.notify_at):
        if r.kind == "exam":
            continue
        when = _hora(r.event_at, tz) if r.event_at else _hora(r.notify_at, tz)
        items.append(f"• {when} {r.title}")
    for r in q.filter(Reminder.kind == "exam").order_by(Reminder.event_at):
        d = days_until(r.event_at, tz, now_local)
        if d == 0:
            items.append(f"• 📚 ¡HOY: {r.title}! Tú puedes 💜")
        elif 0 < d <= 14:
            items.append(f"• 📚 {r.title}: faltan {d} {'día' if d == 1 else 'días'}")
    return items


def send_morning_summaries(now=None, tz=None) -> int:
    now = now or utcnow()
    now_local = _local(now, tz)
    sent = 0
    for u in User.query.all():
        if not u.wants_summary or u.last_summary_date == now_local.date():
            continue
        try:
            hh, mm = (int(x) for x in (u.summary_time or "07:00").split(":"))
        except ValueError:
            hh, mm = 7, 0
        start = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if not (start <= now_local < start + timedelta(hours=3)):
            continue
        items = today_items(u, tz, now_local)
        if items:
            body = "Hoy tienes:\n" + "\n".join(items[:7]) + ("\n…y más en la app" if len(items) > 7 else "")
        else:
            body = "Hoy no tienes nada anotado. ¡Disfruta tu día! 🐧"
        send_to_user(u, {"title": f"☀️ Buenos días, {u.display_name} 💜", "body": body,
                         "tag": "resumen-dia", "kind": "info", "url": "/", "speak": body})
        u.last_summary_date = now_local.date()
        sent += 1
    if sent:
        db.session.commit()
    return sent


# ---------------- Resumen del domingo ----------------
WEEKLY_HOUR = 19  # 7 p. m.


def weekly_stats(user: User, now) -> tuple[int, int]:
    since = now - timedelta(days=7)
    q = Activity.query.filter(Activity.user_id == user.id, Activity.at >= since)
    done = q.filter(Activity.kind == "done").count()
    missed = q.filter(Activity.kind == "missed").count()
    return done, done + missed


def weekly_message(done: int, total: int) -> tuple[str, str]:
    if total == 0:
        return "🐧 Resumen de la semana", "Esta semana no hubo recordatorios. ¿Anotamos los de la próxima? 📝"
    ratio = done / total
    if ratio == 1:
        cheer = "¡Semana perfecta! Sr. Patroclo está orgulloso 🎉🐧"
    elif ratio >= 0.75:
        cheer = "¡Excelente semana! Sigue así 💪"
    elif ratio >= 0.5:
        cheer = "¡Bien! La próxima semana vamos por más 🌷"
    else:
        cheer = "Fue una semana pesada, está bien. Esta semana nueva empieza de cero 💜"
    return f"🏆 Esta semana cumpliste {done} de {total} 💜", cheer


def send_weekly_summaries(now=None, tz=None) -> int:
    now = now or utcnow()
    now_local = _local(now, tz)
    if now_local.weekday() != 6 or now_local.hour < WEEKLY_HOUR:
        return 0
    sent = 0
    for u in User.query.all():
        if not u.wants_weekly or u.last_weekly_date == now_local.date():
            continue
        done, total = weekly_stats(u, now)
        title, body = weekly_message(done, total)
        send_to_user(u, {"title": title, "body": body, "tag": "resumen-semana", "kind": "info", "url": "/"})
        u.last_weekly_date = now_local.date()
        sent += 1
    if sent:
        db.session.commit()
    return sent


def cleanup_activity(now=None) -> None:
    now = now or utcnow()
    Activity.query.filter(Activity.at < now - timedelta(days=35)).delete(synchronize_session=False)


# ---------------- Notitas sorpresa ----------------
def deliver_note_after_done(user: User, tz, now=None, rng=random) -> Note | None:
    """Al cumplir algo, le llega una notita (máximo una por día)."""
    now = now or utcnow()
    today = _local(now, tz).date()
    if user.last_note_date == today:
        return None
    pending = Note.query.filter(Note.recipient_id == user.id, Note.delivered_at.is_(None)).all()
    if not pending:
        return None
    note = rng.choice(pending)
    note.delivered_at = now
    user.last_note_date = today
    send_to_user(user, {"title": f"💌 Notita de {note.author.display_name}", "body": note.text,
                        "tag": f"notita-{note.id}", "kind": "info", "url": "/notitas", "speak": note.text})
    return note
