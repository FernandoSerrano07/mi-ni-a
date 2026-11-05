import logging
import os

import click
from dotenv import load_dotenv
from flask import Flask
from flask_login import LoginManager

from .config import Config
from .models import User, db

login_manager = LoginManager()
login_manager.login_view = "main.login"
login_manager.login_message = None


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
    from .routes_extra import bp as bp_extra
    app.register_blueprint(bp)
    app.register_blueprint(bp_extra)

    with app.app_context():
        db.create_all()
        _auto_migrate()
        _bootstrap_users(app)

    _register_cli(app)

    # El programador interno solo arranca desde run.py / wsgi.py (no en comandos `flask ...` ni tests)
    if start_scheduler and app.config["ENABLE_SCHEDULER"] and not app.config.get("TESTING"):
        from .scheduler import start_scheduler
        start_scheduler(app)

    return app


def _auto_migrate() -> None:
    """Agrega columnas nuevas a tablas que ya existen (create_all no lo hace).
    Así se puede actualizar la app sin perder los datos guardados."""
    from sqlalchemy import inspect, text

    insp = inspect(db.engine)
    dialect = db.engine.dialect
    with db.engine.begin() as conn:
        for table in db.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col.type.compile(dialect=dialect)}'
                d = col.default.arg if (col.default is not None and col.default.is_scalar) else None
                if isinstance(d, bool):
                    ddl += " DEFAULT " + ("TRUE" if d else "FALSE")
                elif isinstance(d, (int, float)):
                    ddl += f" DEFAULT {d}"
                elif isinstance(d, str):
                    ddl += " DEFAULT '" + d.replace("'", "''") + "'"
                conn.execute(text(ddl))
                logging.getLogger(__name__).info("Migración: columna %s.%s agregada", table.name, col.name)


def _bootstrap_users(app: Flask) -> None:
    """Crea usuarios desde la variable USUARIOS="usuario:Nombre:contraseña;usuario2:Nombre2:contraseña2".
    Solo crea los que no existen (no cambia contraseñas). Útil en hostings sin consola."""
    raw = os.environ.get("USUARIOS", "").strip()
    if not raw:
        return
    created = False
    for item in raw.split(";"):
        parts = item.strip().split(":", 2)
        if len(parts) != 3 or not all(p.strip() for p in parts):
            continue
        username, name, password = (p.strip() for p in parts)
        if not User.query.filter_by(username=username.lower()).first():
            u = User(username=username.lower(), display_name=name)
            u.set_password(password)
            db.session.add(u)
            created = True
            logging.getLogger(__name__).info("Usuario creado desde USUARIOS: %s", username)
    if created:
        db.session.commit()


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
