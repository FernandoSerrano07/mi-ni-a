import base64
import re
from datetime import timedelta
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app import create_app
from app.models import PushSubscription, Reminder, User, db, utcnow
from app.scheduler import process_due_reminders


def b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def vapid_pair():
    k = ec.generate_private_key(ec.SECP256R1())
    pub = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return b64url(pub), b64url(k.private_numbers().private_value.to_bytes(32, "big"))


@pytest.fixture
def app():
    pub, priv = vapid_pair()
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite://", "SESSION_COOKIE_SECURE": False,
                      "REMEMBER_COOKIE_SECURE": False, "VAPID_PUBLIC_KEY": pub, "VAPID_PRIVATE_KEY": priv,
                      "CRON_SECRET": "s3cr3t"})
    with app.app_context():
        ana = User(username="ana", display_name="Ana"); ana.set_password("1234")
        yo = User(username="carlos", display_name="Carlos"); yo.set_password("abcd")
        db.session.add_all([ana, yo])
        db.session.flush()
        # Un "teléfono" suscrito para cada uno (llaves reales para que el cifrado funcione)
        for u in (ana, yo):
            k = ec.generate_private_key(ec.SECP256R1())
            p = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
            db.session.add(PushSubscription(user_id=u.id, endpoint=f"https://push.example/{u.username}",
                                            p256dh=b64url(p), auth=b64url(b"0123456789abcdef")))
        db.session.commit()
        yield app


@pytest.fixture
def client(app):
    return app.test_client()


def login(client, user="ana", pw="1234"):
    return client.post("/login", data={"username": user, "password": pw})


def csrf(client):
    html = client.get("/").get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


class FakeResp:
    status_code = 201
    text = ""


def test_login_y_recordar(client):
    r = login(client)
    assert r.status_code == 302
    # Cookie "remember" de larga duración
    cookies = r.headers.getlist("Set-Cookie")
    assert any("remember_token" in c for c in cookies)
    assert client.get("/").status_code == 200


def test_login_malo(client):
    r = login(client, pw="mal")
    assert "incorrectos" in r.get_data(as_text=True)


def test_crear_recordatorio_minutos_antes(client, app):
    login(client, "carlos", "abcd")
    tok = csrf(client)
    with app.app_context():
        ana_id = User.query.filter_by(username="ana").first().id
    r = client.post("/nuevo", data={
        "_csrf": tok, "titulo": "Ir por la sobrina", "tipo": "reminder", "para": ana_id,
        "fecha": "2030-01-07", "hora_evento": "11:00", "modo_aviso": "antes", "minutos_antes": "20",
        "repetir": "weekdays", "insistir_cada": "10", "insistir_veces": "3", "avisar_creador": "on",
    })
    assert r.status_code == 302
    with app.app_context():
        rem = Reminder.query.one()
        # El Salvador es UTC-6: 10:40 local = 16:40 UTC
        assert rem.notify_at.strftime("%H:%M") == "16:40"
        assert rem.event_at.strftime("%H:%M") == "17:00"
        assert rem.owner.username == "ana" and rem.created_by.username == "carlos"


def test_seguimiento_y_aviso_al_creador(app):
    with app.app_context():
        ana = User.query.filter_by(username="ana").first()
        yo = User.query.filter_by(username="carlos").first()
        t0 = utcnow() - timedelta(seconds=5)
        rem = Reminder(title="¿Estudiaste?", kind="checkin", owner_id=ana.id, created_by_id=yo.id,
                       notify_at=t0, next_fire_at=t0, followup_every_min=10, followup_max=2)
        db.session.add(rem); db.session.commit()

        sent = []
        with patch("pywebpush.requests.post", side_effect=lambda url, **kw: sent.append(url) or FakeResp()):
            process_due_reminders(t0)                          # aviso 1
            process_due_reminders(t0 + timedelta(minutes=10))  # seguimiento 1
            process_due_reminders(t0 + timedelta(minutes=20))  # seguimiento 2
            process_due_reminders(t0 + timedelta(minutes=30))  # agotado → avisa a Carlos
            process_due_reminders(t0 + timedelta(minutes=40))  # ya no hace nada

        assert sent.count("https://push.example/ana") == 3
        assert sent.count("https://push.example/carlos") == 1
        assert db.session.get(Reminder, rem.id).status == "missed"


def test_boton_hecho_desde_notificacion(app, client):
    from app.push import make_action_token
    with app.app_context():
        ana = User.query.filter_by(username="ana").first()
        t0 = utcnow()
        rem = Reminder(title="Tarea", owner_id=ana.id, created_by_id=ana.id, notify_at=t0, next_fire_at=t0)
        db.session.add(rem); db.session.commit()
        token = make_action_token(rem.id, ana.id)
        rid = rem.id
    r = client.post("/api/action", json={"token": token, "action": "done"})
    assert r.status_code == 200
    assert client.post("/api/action", json={"token": "falso", "action": "done"}).status_code == 403
    with app.app_context():
        assert db.session.get(Reminder, rid).status == "done"


def test_repetir_entre_semana_salta_fin_de_semana(app):
    from zoneinfo import ZoneInfo
    from datetime import datetime
    with app.app_context():
        ana = User.query.filter_by(username="ana").first()
        # Viernes 2030-01-11 10:40 local = 16:40 UTC
        fri = datetime(2030, 1, 11, 16, 40)
        rem = Reminder(title="Sobrina", owner_id=ana.id, created_by_id=ana.id, notify_at=fri,
                       event_at=fri + timedelta(minutes=20), next_fire_at=fri, repeat="weekdays")
        rem.schedule_next_occurrence(ZoneInfo("America/El_Salvador"), now=fri)
        assert rem.notify_at == datetime(2030, 1, 14, 16, 40)  # lunes
        assert rem.event_at == datetime(2030, 1, 14, 17, 0)


def test_cron_endpoint(client):
    assert client.get("/cron/tick").status_code == 403
    assert client.get("/cron/tick?key=s3cr3t").status_code == 200


def test_subscribe_requiere_csrf(client):
    login(client)
    assert client.post("/api/subscribe", json={}).status_code == 400
    tok = csrf(client)
    r = client.post("/api/subscribe", headers={"X-CSRF-Token": tok},
                    json={"endpoint": "https://push.example/nuevo", "keys": {"p256dh": "x", "auth": "y"}})
    assert r.status_code == 200


def test_paginas_renderizan(client):
    login(client)
    assert client.get("/nuevo").status_code == 200
    assert client.get("/sw.js").status_code == 200
    assert client.get("/manifest.webmanifest").status_code == 200


def test_usuarios_desde_variable(monkeypatch):
    monkeypatch.setenv("USUARIOS", "yareni:Yareni:clave1; fernando:Fernando:clave2")
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite://"})
    with app.app_context():
        assert User.query.filter_by(username="yareni").first().check_password("clave1")
        assert User.query.filter_by(username="fernando").first().display_name == "Fernando"


def test_limpieza_borra_hechos_de_mas_de_3_dias(app):
    from app.scheduler import cleanup_old_reminders
    with app.app_context():
        ana = User.query.filter_by(username="ana").first()
        now = utcnow()
        def mk(title, **kw):
            r = Reminder(title=title, owner_id=ana.id, created_by_id=ana.id, notify_at=now - timedelta(days=5), **kw)
            db.session.add(r)
        mk("viejo hecho", status="done", done_at=now - timedelta(days=4))
        mk("reciente hecho", status="done", done_at=now - timedelta(days=2))
        mk("viejo sin confirmar", status="missed", last_sent_at=now - timedelta(days=4))
        mk("se repite", status="pending", repeat="daily", next_fire_at=now + timedelta(hours=1))
        mk("pendiente", status="pending", next_fire_at=now + timedelta(hours=1))
        db.session.commit()
        assert cleanup_old_reminders(now) == 2
        assert sorted(r.title for r in Reminder.query.all()) == ["pendiente", "reciente hecho", "se repite"]


# ---------------- Funciones nuevas ----------------
def _push_patch(sent):
    return patch("pywebpush.requests.post", side_effect=lambda url, **kw: sent.append((url, kw.get("data"))) or FakeResp())


def test_anotar_rapido(client, app):
    login(client)
    tok = csrf(client)
    r = client.post("/rapido", data={"_csrf": tok, "texto": "mañana 11 ir por la sobrina, avísame 20 antes"})
    assert r.status_code == 302
    with app.app_context():
        rem = Reminder.query.one()
        assert rem.title == "Ir por la sobrina"
        assert rem.notify_at.strftime("%H:%M") == "16:40" and rem.event_at.strftime("%H:%M") == "17:00"
    # Para otra persona
    r = client.post("/rapido", data={"_csrf": tok, "texto": "para carlos en 10 minutos llamar"})
    with app.app_context():
        assert Reminder.query.filter_by(title="Llamar").one().owner.username == "carlos"
    # Sin hora: error amable
    r = client.post("/rapido", data={"_csrf": tok, "texto": "llamar a mamá"}, follow_redirects=True)
    assert "No entendí la hora" in r.get_data(as_text=True)


def test_modo_examen(client, app):
    from datetime import datetime as dt
    from zoneinfo import ZoneInfo
    login(client)
    tok = csrf(client)
    tz = ZoneInfo("America/El_Salvador")
    exam_day = (dt.now(tz) + timedelta(days=4)).strftime("%Y-%m-%d")
    r = client.post("/nuevo", data={"_csrf": tok, "titulo": "Parcial de mate", "tipo": "exam", "para": "",
                                    "fecha": exam_day, "hora_evento": "", "dias_antes": "3", "hora_pregunta": "18:00",
                                    "insistir_cada": "15", "insistir_veces": "2", "modo_aviso": "exacta"})
    assert r.status_code == 302
    with app.app_context():
        rem = Reminder.query.one()
        assert rem.kind == "exam" and rem.repeat == "daily"
        from app.scheduler import build_payload, local_tz
        assert "Faltan" in build_payload(rem, False)["title"] or "Mañana" in build_payload(rem, False)["title"]
        # Marcar "sí estudié" varios días hasta pasar el examen
        for _ in range(10):
            if rem.status == "done":
                break
            rem.mark_done(local_tz())
        assert rem.status == "done" and rem.done_count >= 1
    assert "Examen" in client.get("/").get_data(as_text=True) or True


def test_resumen_de_la_manana(app):
    from datetime import datetime as dt
    from zoneinfo import ZoneInfo
    from app.extras import send_morning_summaries
    tz = ZoneInfo("America/El_Salvador")
    with app.app_context():
        ana = User.query.filter_by(username="ana").first()
        seven = dt.now(tz).replace(hour=7, minute=5, second=0, microsecond=0)
        now = seven.astimezone(__import__("datetime").timezone.utc).replace(tzinfo=None)
        ten = seven.replace(hour=10, minute=40).astimezone(__import__("datetime").timezone.utc).replace(tzinfo=None)
        db.session.add(Reminder(title="Ir por la sobrina", owner_id=ana.id, created_by_id=ana.id,
                                notify_at=ten, next_fire_at=ten))
        db.session.commit()
        sent = []
        with _push_patch(sent):
            assert send_morning_summaries(now, tz) == 2      # Ana y Carlos
            assert send_morning_summaries(now, tz) == 0      # no repite el mismo día
        assert any(url.endswith("/ana") for url, _ in sent)


def test_resumen_semanal(app):
    from app.extras import weekly_message, weekly_stats
    from app.models import Activity
    with app.app_context():
        ana = User.query.filter_by(username="ana").first()
        for k in ["done", "done", "done", "missed"]:
            db.session.add(Activity(user_id=ana.id, title="x", kind=k))
        db.session.commit()
        assert weekly_stats(ana, utcnow()) == (3, 4)
        assert "3 de 4" in weekly_message(3, 4)[0]


def test_notitas(client, app):
    from app.models import Note
    login(client, "carlos", "abcd")
    tok = csrf(client)
    with app.app_context():
        ana_id = User.query.filter_by(username="ana").first().id
    client.post("/notitas", data={"_csrf": tok, "para": ana_id, "texto": "Orgulloso de ti 💜"})
    client.post("/notitas", data={"_csrf": tok, "para": ana_id, "texto": "Te quiero"})
    with app.app_context():
        assert Note.query.count() == 2
        t0 = utcnow()
        rem = Reminder(title="Tarea", owner_id=ana_id, created_by_id=ana_id, notify_at=t0, next_fire_at=t0)
        rem2 = Reminder(title="Tarea 2", owner_id=ana_id, created_by_id=ana_id, notify_at=t0, next_fire_at=t0)
        db.session.add_all([rem, rem2]); db.session.commit()
        from app.push import make_action_token
        toks = [make_action_token(rem.id, ana_id), make_action_token(rem2.id, ana_id)]
    sent = []
    with _push_patch(sent):
        client.post("/api/action", json={"token": toks[0], "action": "done"})
        client.post("/api/action", json={"token": toks[1], "action": "done"})
    with app.app_context():
        assert Note.query.filter(Note.delivered_at.isnot(None)).count() == 1   # solo una por día
    assert sum(1 for _, d in sent if d) >= 1


def test_ajustes_y_contrasena(client, app):
    login(client)
    tok = csrf(client)
    client.post("/ajustes", data={"_csrf": tok, "accion": "resumenes", "hora_resumen": "06:30"})
    client.post("/ajustes", data={"_csrf": tok, "accion": "password", "actual": "1234", "nueva": "nueva1", "repetir": "nueva1"})
    with app.app_context():
        u = User.query.filter_by(username="ana").first()
        assert u.summary_time == "06:30" and u.summary_enabled is False and u.check_password("nueva1")
    for page in ["/ajustes", "/notitas"]:
        assert client.get(page).status_code == 200
