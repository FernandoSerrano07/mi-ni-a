from datetime import datetime, timedelta, timezone

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


def utcnow() -> datetime:
    """Hora actual en UTC, sin tzinfo (así se guarda en la base de datos)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    display_name = db.Column(db.String(80), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    subscriptions = db.relationship("PushSubscription", backref="user", cascade="all, delete-orphan")

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class PushSubscription(db.Model):
    """Un dispositivo (teléfono, compu) que puede recibir notificaciones."""

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    endpoint = db.Column(db.Text, unique=True, nullable=False)
    p256dh = db.Column(db.String(255), nullable=False)
    auth = db.Column(db.String(255), nullable=False)
    user_agent = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=utcnow)


REPEAT_CHOICES = {
    "none": "No se repite",
    "daily": "Todos los días",
    "weekdays": "Lunes a viernes",
    "weekly": "Cada semana",
}

KIND_CHOICES = {
    "reminder": "Recordatorio",   # "Ir por la sobrina al colegio"
    "checkin": "Pregunta de seguimiento",  # "¿Ya estudiaste para el parcial?"
}


class Reminder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    notes = db.Column(db.Text, default="")
    kind = db.Column(db.String(20), default="reminder")

    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)       # quién recibe el aviso
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)  # quién lo escribió

    # Todas las fechas en UTC (sin tzinfo)
    event_at = db.Column(db.DateTime, nullable=True)   # ej. 11:00 sale la sobrina (opcional)
    notify_at = db.Column(db.DateTime, nullable=False)  # ej. 10:40 primer aviso
    repeat = db.Column(db.String(20), default="none")

    # Seguimiento: si no lo marca como hecho, insistir cada X minutos, máximo N veces
    followup_every_min = db.Column(db.Integer, default=10)
    followup_max = db.Column(db.Integer, default=3)
    notify_creator_on_miss = db.Column(db.Boolean, default=True)

    # Estado de la ocurrencia actual
    status = db.Column(db.String(20), default="pending")  # pending | done | missed
    next_fire_at = db.Column(db.DateTime, nullable=True, index=True)
    sent_count = db.Column(db.Integer, default=0)
    last_sent_at = db.Column(db.DateTime, nullable=True)
    done_at = db.Column(db.DateTime, nullable=True)
    done_count = db.Column(db.Integer, default=0)  # veces cumplido (útil en recordatorios que se repiten)

    created_at = db.Column(db.DateTime, default=utcnow)

    owner = db.relationship("User", foreign_keys=[owner_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    # ---- Lógica de repetición ----
    def _advance_once(self, dt: datetime, local_tz) -> datetime:
        """Avanza una fecha UTC al siguiente periodo según `repeat`, respetando la hora local."""
        local = dt.replace(tzinfo=timezone.utc).astimezone(local_tz)
        if self.repeat == "daily":
            local = local + timedelta(days=1)
        elif self.repeat == "weekly":
            local = local + timedelta(weeks=1)
        elif self.repeat == "weekdays":
            local = local + timedelta(days=1)
            while local.weekday() >= 5:  # 5=sábado, 6=domingo
                local = local + timedelta(days=1)
        return local.astimezone(timezone.utc).replace(tzinfo=None)

    def schedule_next_occurrence(self, local_tz, now: datetime | None = None) -> bool:
        """Para recordatorios que se repiten: pasa a la siguiente fecha futura.
        Devuelve False si no se repite."""
        if self.repeat == "none":
            return False
        now = now or utcnow()
        lead = (self.event_at - self.notify_at) if self.event_at else None
        nxt = self._advance_once(self.notify_at, local_tz)
        while nxt <= now:
            nxt = self._advance_once(nxt, local_tz)
        self.notify_at = nxt
        if lead is not None:
            self.event_at = nxt + lead
        self.next_fire_at = nxt
        self.sent_count = 0
        self.status = "pending"
        self.done_at = None
        return True

    def mark_done(self, local_tz) -> None:
        self.done_count = (self.done_count or 0) + 1
        if not self.schedule_next_occurrence(local_tz):
            self.status = "done"
            self.done_at = utcnow()
            self.next_fire_at = None

    def snooze(self, minutes: int = 10) -> None:
        self.status = "pending"
        self.next_fire_at = utcnow() + timedelta(minutes=minutes)
