import os
from datetime import timedelta


def _db_url() -> str:
    url = os.environ.get("DATABASE_URL", "sqlite:///recordatorios.db")
    # Muchos proveedores (Render, Heroku, Neon) dan "postgres://"; SQLAlchemy necesita "postgresql+psycopg://"
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "cambia-esto-en-produccion")
    SQLALCHEMY_DATABASE_URI = _db_url()
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # "Inicia sesión una sola vez": la cookie dura un año
    REMEMBER_COOKIE_DURATION = timedelta(days=365)
    PERMANENT_SESSION_LIFETIME = timedelta(days=365)
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "1") == "1"
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE

    TIMEZONE = os.environ.get("TIMEZONE", "America/El_Salvador")
    MAX_CONTENT_LENGTH = 15 * 1024 * 1024  # fotos, audios y guías

    # Web Push (genera las llaves con: python generar_vapid.py)
    VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
    VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
    VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:admin@example.com")

    # Programador interno (revisa cada minuto). Desactívalo si usas un cron externo.
    ENABLE_SCHEDULER = os.environ.get("ENABLE_SCHEDULER", "1") == "1"
    # Clave para el endpoint /cron/tick (cron externo tipo cron-job.org)
    CRON_SECRET = os.environ.get("CRON_SECRET", "")
