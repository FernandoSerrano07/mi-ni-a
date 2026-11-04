import logging

import click
from dotenv import load_dotenv
from flask import Flask
from flask_login import LoginManager

from .config import Config
from .models import User, db

login_manager = LoginManager()
login_manager.login_view = "main.login"
login_manager.login_message = "Inicia sesión para continuar"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def create_app(config_overrides: dict | None = None, start_scheduler: bool = False) -> Flask:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    app = Flask(__name__)
    app.config.from_object(Config)
    if config_overrides:
        app.config.update(config_overrides)

    db.init_app(app)
    login_manager.init_app(app)

    from .routes import bp
    app.register_blueprint(bp)

    with app.app_context():
        db.create_all()

    _register_cli(app)

    # El programador interno solo arranca desde run.py / wsgi.py (no en comandos `flask ...` ni tests)
    if start_scheduler and app.config["ENABLE_SCHEDULER"] and not app.config.get("TESTING"):
        from .scheduler import start_scheduler
        start_scheduler(app)

    return app


def _register_cli(app: Flask) -> None:
    @app.cli.command("crear-usuario")
    @click.argument("username")
    @click.argument("nombre")
    @click.password_option("--password", prompt="Contraseña")
    def crear_usuario(username, nombre, password):
        """Crea un usuario. Ej: flask crear-usuario ana "Ana" """
        if User.query.filter_by(username=username.lower()).first():
            raise click.ClickException("Ese usuario ya existe")
        u = User(username=username.lower(), display_name=nombre)
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        click.echo(f"Usuario '{username}' creado ✅")

    @app.cli.command("cambiar-password")
    @click.argument("username")
    @click.password_option("--password", prompt="Nueva contraseña")
    def cambiar_password(username, password):
        """Cambia la contraseña de un usuario."""
        u = User.query.filter_by(username=username.lower()).first()
        if not u:
            raise click.ClickException("No existe ese usuario")
        u.set_password(password)
        db.session.commit()
        click.echo("Contraseña actualizada ✅")

    @app.cli.command("tick")
    def tick():
        """Procesa los recordatorios vencidos una vez (útil para pruebas o cron)."""
        from .scheduler import process_due_reminders
        click.echo(f"Procesados: {process_due_reminders()}")
