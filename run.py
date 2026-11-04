"""Servidor de desarrollo: python run.py"""
import os

from app import create_app

os.environ.setdefault("COOKIE_SECURE", "0")  # en local no hay HTTPS
app = create_app(start_scheduler=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
