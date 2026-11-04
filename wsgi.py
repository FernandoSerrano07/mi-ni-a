"""Punto de entrada para producción: gunicorn wsgi:app"""
from app import create_app

app = create_app(start_scheduler=True)
