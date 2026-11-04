"""Envío de notificaciones Web Push (estándar VAPID, sin Firebase)."""
import json
import logging

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pywebpush import WebPushException, webpush

from .models import PushSubscription, db

log = logging.getLogger(__name__)


# ---- Tokens para los botones de la notificación ("Ya lo hice", "10 min más") ----
# Así los botones funcionan aunque el teléfono no mande la cookie de sesión.
def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="reminder-action")


def make_action_token(reminder_id: int, user_id: int) -> str:
    return _serializer().dumps({"r": reminder_id, "u": user_id})


def read_action_token(token: str, max_age: int = 3 * 24 * 3600) -> dict | None:
    try:
        return _serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None


def send_to_user(user, payload: dict) -> int:
    """Envía `payload` a todos los dispositivos del usuario. Devuelve cuántos se enviaron bien."""
    cfg = current_app.config
    if not cfg["VAPID_PRIVATE_KEY"]:
        log.warning("VAPID_PRIVATE_KEY no configurada; no se envían notificaciones")
        return 0

    ok = 0
    data = json.dumps(payload, ensure_ascii=False)
    for sub in list(user.subscriptions):
        try:
            webpush(
                subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                data=data,
                vapid_private_key=cfg["VAPID_PRIVATE_KEY"],
                vapid_claims={"sub": cfg["VAPID_SUBJECT"]},
                ttl=3600,
                headers={"Urgency": "high"},  # que llegue aunque el teléfono esté en ahorro de batería
            )
            ok += 1
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (404, 410):  # el dispositivo ya no existe / se desinstaló
                db.session.delete(sub)
            else:
                log.error("Error enviando push a %s: %s", user.username, exc)
        except Exception as exc:  # noqa: BLE001
            log.exception("Error inesperado enviando push: %s", exc)
    return ok
