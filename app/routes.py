import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (Blueprint, abort, current_app, flash, jsonify, redirect, render_template,
                   request, send_from_directory, session, url_for)
from flask_login import current_user, login_required, login_user, logout_user

from .models import KIND_CHOICES, REPEAT_CHOICES, PushSubscription, Reminder, User, db, utcnow
from .push import read_action_token, send_to_user
from .scheduler import local_tz, process_due_reminders

bp = Blueprint("main", __name__)


# ---------------- Utilidades ----------------
def to_local(dt):
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone(local_tz())


def local_to_utc(date_str: str, time_str: str) -> datetime:
    local = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M").replace(tzinfo=local_tz())
    return local.astimezone(timezone.utc).replace(tzinfo=None)


@bp.app_template_filter("hora")
def hora_filter(dt):
    d = to_local(dt)
    return d.strftime("%I:%M %p").lstrip("0") if d else ""


DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


@bp.app_template_filter("dia")
def dia_filter(dt):
    d = to_local(dt)
    if not d:
        return ""
    hoy = datetime.now(local_tz()).date()
    if d.date() == hoy:
        return "Hoy"
    if d.date() == hoy + timedelta(days=1):
        return "Mañana"
    if d.date() == hoy - timedelta(days=1):
        return "Ayer"
    return f"{DIAS[d.weekday()].capitalize()} {d.day} {MESES[d.month - 1]}"


# ---- CSRF sencillo para formularios y fetch ----
def csrf_token() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


@bp.app_context_processor
def inject_globals():
    return {"csrf_token": csrf_token, "REPEAT_CHOICES": REPEAT_CHOICES, "KIND_CHOICES": KIND_CHOICES}


def csrf_protect(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
        if not sent or not secrets.compare_digest(sent, session.get("_csrf", "")):
            abort(400, "Token CSRF inválido. Recarga la página.")
        return f(*args, **kwargs)
    return wrapper


def get_reminder_or_404(rid: int) -> Reminder:
    r = db.session.get(Reminder, rid)
    if not r:
        abort(404)
    return r


# ---------------- Sesión ----------------
@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    if request.method == "POST":
        u = User.query.filter_by(username=request.form.get("username", "").strip().lower()).first()
        if u and u.check_password(request.form.get("password", "")):
            session.permanent = True
            login_user(u, remember=True)  # recordar por 1 año: solo inicia sesión una vez
            return redirect(request.args.get("next") or url_for("main.index"))
        flash("Usuario o contraseña incorrectos", "error")
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
@login_required
@csrf_protect
def logout():
    logout_user()
    return redirect(url_for("main.login"))


# ---------------- Pantallas ----------------
@bp.route("/")
@login_required
def index():
    ver = request.args.get("ver", "mios")
    q = Reminder.query
    if ver == "mios":
        q = q.filter(Reminder.owner_id == current_user.id)

    pendientes = q.filter(Reminder.status == "pending").order_by(Reminder.notify_at).all()
    sin_confirmar = q.filter(Reminder.status == "missed").order_by(Reminder.notify_at.desc()).limit(20).all()
    hechos = (q.filter(Reminder.status == "done", Reminder.done_at >= utcnow() - timedelta(days=3))
              .order_by(Reminder.done_at.desc()).limit(20).all())

    # Pendientes que ya sonaron (están esperando confirmación) van arriba
    sonando = [r for r in pendientes if r.sent_count > 0]
    proximos = [r for r in pendientes if r.sent_count == 0]
    return render_template("index.html", sonando=sonando, proximos=proximos,
                           sin_confirmar=sin_confirmar, hechos=hechos, ver=ver,
                           highlight=request.args.get("r", type=int))


@bp.route("/nuevo", methods=["GET", "POST"])
@bp.route("/editar/<int:rid>", methods=["GET", "POST"])
@login_required
def form(rid=None):
    r = get_reminder_or_404(rid) if rid else None
    users = User.query.order_by(User.display_name).all()

    if request.method == "POST":
        if not secrets.compare_digest(request.form.get("_csrf", ""), session.get("_csrf", "")):
            abort(400)
        f = request.form
        try:
            date_str = f["fecha"]
            event_time = f.get("hora_evento", "").strip()
            event_at = local_to_utc(date_str, event_time) if event_time else None

            if event_at and f.get("modo_aviso", "antes") == "antes":
                notify_at = event_at - timedelta(minutes=int(f.get("minutos_antes") or 0))
            else:
                hora_aviso = f.get("hora_aviso", "").strip() or event_time
                if not hora_aviso:
                    raise ValueError("Pon la hora a la que quieres el aviso")
                notify_at = local_to_utc(date_str, hora_aviso)

            title = f.get("titulo", "").strip()
            if not title:
                raise ValueError("Escribe qué hay que recordar")
        except (KeyError, ValueError) as exc:
            flash(str(exc) if str(exc) else "Revisa la fecha y la hora", "error")
            return render_template("form.html", r=r, users=users, v=f.to_dict())

        if r is None:
            r = Reminder(created_by_id=current_user.id)
            db.session.add(r)
        r.title = title
        r.notes = f.get("notas", "").strip()
        r.kind = f.get("tipo", "reminder") if f.get("tipo") in KIND_CHOICES else "reminder"
        r.owner_id = int(f.get("para") or current_user.id)
        r.event_at = event_at
        r.notify_at = notify_at
        r.repeat = f.get("repetir") if f.get("repetir") in REPEAT_CHOICES else "none"
        r.followup_every_min = max(0, min(240, int(f.get("insistir_cada") or 0)))
        r.followup_max = max(0, min(20, int(f.get("insistir_veces") or 0)))
        r.notify_creator_on_miss = bool(f.get("avisar_creador"))

        # (Re)programar
        r.status = "pending"
        r.sent_count = 0
        r.done_at = None
        r.next_fire_at = notify_at
        if notify_at < utcnow() - timedelta(minutes=1):
            if r.repeat != "none":
                r.schedule_next_occurrence(local_tz())
            else:
                flash("Ojo: esa hora ya pasó, el aviso se enviará de inmediato", "warn")
        db.session.commit()
        flash("Recordatorio guardado ✅", "ok")
        return redirect(url_for("main.index", ver="todos" if r.owner_id != current_user.id else "mios"))

    return render_template("form.html", r=r, users=users, v=_form_values(r))


def _form_values(r: Reminder | None) -> dict:
    """Valores iniciales del formulario (en hora local)."""
    now = datetime.now(local_tz())
    if r is None:
        return {"fecha": now.strftime("%Y-%m-%d"), "hora_evento": "", "modo_aviso": "antes",
                "minutos_antes": "20", "hora_aviso": "", "para": str(current_user.id), "tipo": "reminder",
                "repetir": "none", "insistir_cada": "10", "insistir_veces": "3", "avisar_creador": "on"}
    n = to_local(r.notify_at)
    e = to_local(r.event_at)
    mins = int((r.event_at - r.notify_at).total_seconds() // 60) if r.event_at else 0
    return {
        "titulo": r.title, "notas": r.notes or "", "tipo": r.kind, "para": str(r.owner_id),
        "fecha": (e or n).strftime("%Y-%m-%d"), "hora_evento": e.strftime("%H:%M") if e else "",
        "modo_aviso": "antes" if e else "exacta", "minutos_antes": str(mins),
        "hora_aviso": n.strftime("%H:%M"), "repetir": r.repeat,
        "insistir_cada": str(r.followup_every_min or 0), "insistir_veces": str(r.followup_max or 0),
        "avisar_creador": "on" if r.notify_creator_on_miss else "",
    }


@bp.route("/recordatorio/<int:rid>/<accion>", methods=["POST"])
@login_required
@csrf_protect
def reminder_action(rid, accion):
    r = get_reminder_or_404(rid)
    if accion == "hecho":
        r.mark_done(local_tz())
        if r.created_by_id != current_user.id:
            _notify_creator_done(r, current_user)
        flash("¡Listo! 🎉", "ok")
    elif accion == "posponer":
        r.snooze(int(request.form.get("min", 10)))
        flash(f"Te lo recuerdo en {request.form.get('min', 10)} minutos ⏳", "ok")
    elif accion == "eliminar":
        db.session.delete(r)
        flash("Recordatorio eliminado", "ok")
    elif accion == "reactivar":
        r.status, r.sent_count, r.next_fire_at = "pending", 0, utcnow()
    else:
        abort(404)
    db.session.commit()
    return redirect(request.referrer or url_for("main.index"))


def _notify_creator_done(r: Reminder, who: User) -> None:
    """Si él le escribió el recordatorio, le avisa cuando ella lo marca como hecho."""
    send_to_user(r.created_by, {
        "title": f"✅ {who.display_name} completó: {r.title}",
        "body": "", "tag": f"done-{r.id}", "kind": "info", "url": "/?ver=todos",
    })


# ---------------- PWA ----------------
@bp.route("/sw.js")
def service_worker():
    # Se sirve desde la raíz para que controle toda la app
    resp = send_from_directory(current_app.static_folder, "sw.js", mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@bp.route("/manifest.webmanifest")
def manifest():
    return send_from_directory(current_app.static_folder, "manifest.webmanifest",
                               mimetype="application/manifest+json")


# ---------------- API (notificaciones) ----------------
@bp.route("/api/vapid-public-key")
def vapid_public_key():
    return jsonify({"key": current_app.config["VAPID_PUBLIC_KEY"]})


@bp.route("/api/subscribe", methods=["POST"])
@login_required
@csrf_protect
def subscribe():
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint")
    keys = data.get("keys") or {}
    if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
        return jsonify({"error": "suscripción inválida"}), 400
    sub = PushSubscription.query.filter_by(endpoint=endpoint).first()
    if sub is None:
        sub = PushSubscription(endpoint=endpoint)
        db.session.add(sub)
    sub.user_id = current_user.id
    sub.p256dh = keys["p256dh"]
    sub.auth = keys["auth"]
    sub.user_agent = (request.headers.get("User-Agent") or "")[:255]
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/unsubscribe", methods=["POST"])
@login_required
@csrf_protect
def unsubscribe():
    endpoint = (request.get_json(silent=True) or {}).get("endpoint")
    PushSubscription.query.filter_by(endpoint=endpoint, user_id=current_user.id).delete()
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/test-push", methods=["POST"])
@login_required
@csrf_protect
def test_push():
    n = send_to_user(current_user, {
        "title": "🔔 ¡Así se verán tus recordatorios!",
        "body": "Si ves esto, las notificaciones funcionan.",
        "tag": "test", "kind": "info", "url": "/", "speak": "Las notificaciones funcionan",
    })
    db.session.commit()
    return jsonify({"sent": n})


@bp.route("/api/action", methods=["POST"])
def notification_action():
    """Botones dentro de la notificación. Usa un token firmado (no requiere cookie)."""
    data = request.get_json(silent=True) or {}
    info = read_action_token(data.get("token", ""))
    if not info:
        return jsonify({"error": "token inválido"}), 403
    r = db.session.get(Reminder, info["r"])
    if not r or r.owner_id != info["u"]:
        return jsonify({"error": "no encontrado"}), 404
    if data.get("action") == "done":
        r.mark_done(local_tz())
        if r.created_by_id != r.owner_id:
            _notify_creator_done(r, r.owner)
    elif data.get("action") == "snooze":
        r.snooze(int(data.get("minutes", 10)))
    else:
        return jsonify({"error": "acción desconocida"}), 400
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/estado")
@login_required
def estado():
    """Para que la app abierta sepa si hay algo sonando (y lo lea en voz alta)."""
    sonando = Reminder.query.filter(Reminder.owner_id == current_user.id, Reminder.status == "pending",
                                    Reminder.sent_count > 0).all()
    return jsonify({"sonando": [{"id": r.id, "title": r.title, "last": r.last_sent_at.isoformat() + "Z"}
                                for r in sonando if r.last_sent_at]})


# ---------------- Cron externo ----------------
@bp.route("/cron/tick", methods=["GET", "POST"])
def cron_tick():
    secret = current_app.config["CRON_SECRET"]
    sent = request.headers.get("X-Cron-Secret") or request.args.get("key", "")
    if not secret or not secrets.compare_digest(sent, secret):
        abort(403)
    return jsonify({"procesados": process_due_reminders()})


@bp.route("/salud")
def health():
    return "ok"
