"""Buzón 💌 (notas con foto/audio y respuestas) y Estudio 📚 (guías + cuestionarios)."""
import json
import secrets

from flask import (Blueprint, Response, abort, flash, jsonify, redirect, render_template, request,
                   session, url_for)
from flask_login import current_user, login_required

from .models import Guide, Message, QuizAttempt, Reminder, User, db, utcnow
from .push import send_to_user
from .routes import csrf_protect

bp = Blueprint("extra", __name__)

MAX_PHOTO = 4 * 1024 * 1024
MAX_AUDIO = 4 * 1024 * 1024
MAX_GUIDE = 10 * 1024 * 1024
GUIDE_EXT = (".pdf", ".docx", ".txt", ".md", ".jpg", ".jpeg", ".png", ".webp", ".heic")


def _check_csrf():
    sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
    if not sent or not secrets.compare_digest(sent, session.get("_csrf", "")):
        abort(400, "Token CSRF inválido. Recarga la página.")


def _wants_json() -> bool:
    return request.headers.get("X-Requested-With") == "fetch"


# ======================= BUZÓN =======================
@bp.app_context_processor
def inject_mailbox():
    if not current_user.is_authenticated:
        return {}
    unread = (Message.query.filter(Message.recipient_id == current_user.id, Message.read_at.is_(None))
              .order_by(Message.created_at.desc()).all())
    return {"unread_messages": unread}


def _other_user():
    return User.query.filter(User.id != current_user.id).order_by(User.display_name).all()


def _read_media(field: str, max_size: int, prefix: str):
    f = request.files.get(field)
    if not f or not f.filename and not f.mimetype:
        return None, None
    data = f.read()
    if not data:
        return None, None
    mime = (f.mimetype or "").split(";")[0]
    if not mime.startswith(prefix):
        raise ValueError("Ese archivo no es " + ("una foto" if prefix == "image/" else "un audio"))
    if len(data) > max_size:
        raise ValueError("El archivo es muy grande (máximo 4 MB)")
    return data, f.mimetype[:60]


def _notify_message(m: Message, is_reply: bool):
    if m.text:
        body = m.text[:140]
    elif m.photo:
        body = "📷 Te mandó una foto"
    else:
        body = "🎤 Te mandó un audio"
    title = (f"💬 {m.author.display_name} te respondió" if is_reply
             else f"💌 {m.author.display_name} te dejó una nota")
    send_to_user(m.recipient, {"title": title, "body": body, "tag": f"msg-{m.id}", "kind": "info",
                               "url": "/buzon"})


def _create_message(recipient: User, parent: Message | None) -> Message:
    text = (request.form.get("texto") or "").strip()[:2000]
    photo, photo_mime = _read_media("foto", MAX_PHOTO, "image/")
    audio, audio_mime = _read_media("audio", MAX_AUDIO, "audio/")
    if not (text or photo or audio):
        raise ValueError("Escribe algo, o agrega una foto o un audio 💌")
    m = Message(author_id=current_user.id, recipient_id=recipient.id, parent_id=parent.id if parent else None,
                text=text, photo=photo, photo_mime=photo_mime, audio=audio, audio_mime=audio_mime)
    db.session.add(m)
    root = parent or m
    root.last_activity = utcnow()
    db.session.flush()
    _notify_message(m, is_reply=parent is not None)
    return m


@bp.route("/buzon", methods=["GET", "POST"])
@login_required
def mailbox():
    others = _other_user()
    if request.method == "POST":
        _check_csrf()
        to = db.session.get(User, request.form.get("para", type=int) or 0)
        try:
            if not to or to.id == current_user.id:
                raise ValueError("Elige para quién es la nota")
            _create_message(to, None)
            db.session.commit()
        except ValueError as exc:
            db.session.rollback()
            if _wants_json():
                return jsonify({"error": str(exc)}), 400
            flash(str(exc), "error")
            return redirect(url_for("extra.mailbox"))
        if _wants_json():
            return jsonify({"ok": True, "redirect": url_for("extra.mailbox")})
        flash(f"💌 Nota enviada a {to.display_name}", "ok")
        return redirect(url_for("extra.mailbox"))

    threads = (Message.query.filter(Message.parent_id.is_(None),
                                    (Message.author_id == current_user.id) | (Message.recipient_id == current_user.id))
               .order_by(Message.last_activity.desc()).limit(50).all())
    # Al abrir el buzón se marcan como leídas
    now = utcnow()
    for m in Message.query.filter(Message.recipient_id == current_user.id, Message.read_at.is_(None)):
        m.read_at = now
    db.session.commit()
    return render_template("mailbox.html", threads=threads, others=others)


@bp.route("/buzon/<int:mid>/responder", methods=["POST"])
@login_required
def reply(mid):
    _check_csrf()
    m = db.session.get(Message, mid)
    if not m or current_user.id not in (m.author_id, m.recipient_id):
        abort(404)
    root = m.parent or m
    other_id = root.author_id if root.author_id != current_user.id else root.recipient_id
    try:
        _create_message(db.session.get(User, other_id), root)
        if m.recipient_id == current_user.id and m.read_at is None:
            m.read_at = utcnow()
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        if _wants_json():
            return jsonify({"error": str(exc)}), 400
        flash(str(exc), "error")
        return redirect(url_for("extra.mailbox"))
    if _wants_json():
        return jsonify({"ok": True})
    flash("💬 Respuesta enviada", "ok")
    return redirect(request.form.get("next") or url_for("extra.mailbox"))


@bp.route("/buzon/<int:mid>/leido", methods=["POST"])
@login_required
@csrf_protect
def mark_read(mid):
    m = db.session.get(Message, mid)
    if m and m.recipient_id == current_user.id and m.read_at is None:
        m.read_at = utcnow()
        db.session.commit()
    return jsonify({"ok": True})


@bp.route("/buzon/<int:mid>/borrar", methods=["POST"])
@login_required
@csrf_protect
def delete_message(mid):
    m = db.session.get(Message, mid)
    if not m or m.author_id != current_user.id:
        abort(404)
    db.session.delete(m)
    db.session.commit()
    flash("Nota borrada", "ok")
    return redirect(url_for("extra.mailbox"))


@bp.route("/buzon/media/<int:mid>/<kind>")
@login_required
def message_media(mid, kind):
    m = db.session.get(Message, mid)
    if not m or current_user.id not in (m.author_id, m.recipient_id) or kind not in ("foto", "audio"):
        abort(404)
    data, mime = (m.photo, m.photo_mime) if kind == "foto" else (m.audio, m.audio_mime)
    if not data:
        abort(404)
    resp = Response(data, mimetype=mime or "application/octet-stream")
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


# ======================= ESTUDIO =======================
def _guide_or_404(gid) -> Guide:
    g = db.session.get(Guide, gid)
    if not g:
        abort(404)
    return g


def _exams_for(user_ids):
    return (Reminder.query.filter(Reminder.kind == "exam", Reminder.status == "pending",
                                  Reminder.owner_id.in_(user_ids)).order_by(Reminder.event_at).all())


@bp.route("/estudio", methods=["GET", "POST"])
@login_required
def study():
    from .study import StudyError, extract_text, needs_ocr, ocr
    users = User.query.order_by(User.display_name).all()
    if request.method == "POST":
        _check_csrf()
        title = (request.form.get("titulo") or "").strip()[:200]
        owner = db.session.get(User, request.form.get("para", type=int) or current_user.id) or current_user
        exam_id = request.form.get("examen", type=int)
        f = request.files.get("archivo")
        pasted = (request.form.get("texto") or "").strip()
        data, mime, filename, text = None, None, None, pasted
        if f and f.filename:
            data = f.read()
            filename = f.filename[:200]
            mime = (f.mimetype or "").split(";")[0]
            if len(data) > MAX_GUIDE:
                flash("El archivo es muy grande (máximo 10 MB)", "error")
                return redirect(url_for("extra.study"))
            if not filename.lower().endswith(GUIDE_EXT) and not mime.startswith("image/"):
                flash("Sube un PDF, Word (.docx), texto o foto de la guía", "error")
                return redirect(url_for("extra.study"))
            text = extract_text(data, mime, filename) or pasted
            if needs_ocr(mime, text):
                try:
                    text = ocr(data, mime if mime != "image/heic" else "image/heic") or text
                except StudyError as exc:
                    flash(f"Guardé la guía, pero no pude leer su texto: {exc}", "warn")
        if not title:
            title = (filename or "Mi guía").rsplit(".", 1)[0][:200]
        if not (data or text):
            flash("Sube un archivo o pega el texto de la guía", "error")
            return redirect(url_for("extra.study"))
        exam = db.session.get(Reminder, exam_id) if exam_id else None
        g = Guide(owner_id=owner.id, title=title, filename=filename, mime=mime, data=data, text=text or "",
                  exam_id=exam.id if exam and exam.kind == "exam" else None)
        db.session.add(g)
        db.session.commit()
        if len(g.text) < 80:
            flash("📄 Guía guardada, pero casi no tiene texto legible para hacer preguntas.", "warn")
        else:
            flash(f"📚 Guía guardada ({len(g.text):,} letras). ¡Ya puedes practicar!".replace(",", "."), "ok")
        return redirect(url_for("extra.guide", gid=g.id))

    ids = [u.id for u in users]
    guides = Guide.query.filter(Guide.owner_id.in_(ids)).order_by(Guide.created_at.desc()).all()
    return render_template("study.html", guides=guides, users=users, exams=_exams_for(ids),
                           pre_exam=request.args.get("examen", type=int))


@bp.route("/estudio/<int:gid>")
@login_required
def guide(gid):
    g = _guide_or_404(gid)
    return render_template("guide.html", g=g)


@bp.route("/estudio/<int:gid>/archivo")
@login_required
def guide_file(gid):
    g = _guide_or_404(gid)
    if not g.data:
        abort(404)
    resp = Response(g.data, mimetype=g.mime or "application/octet-stream")
    resp.headers["Content-Disposition"] = f'inline; filename="{(g.filename or "guia").replace(chr(34), "")}"'
    return resp


@bp.route("/estudio/<int:gid>/borrar", methods=["POST"])
@login_required
@csrf_protect
def delete_guide(gid):
    g = _guide_or_404(gid)
    db.session.delete(g)
    db.session.commit()
    flash("Guía borrada", "ok")
    return redirect(url_for("extra.study"))


@bp.route("/estudio/<int:gid>/practicar")
@login_required
def practice(gid):
    g = _guide_or_404(gid)
    return render_template("quiz.html", g=g)


@bp.route("/api/estudio/<int:gid>/cuestionario", methods=["POST"])
@login_required
@csrf_protect
def new_quiz(gid):
    from .study import StudyError, generate_quiz
    g = _guide_or_404(gid)
    avoid = []
    for a in g.attempts[:8]:
        try:
            avoid += [q["q"] for q in json.loads(a.questions)]
        except (ValueError, KeyError, TypeError):
            pass
    nivel = (request.get_json(silent=True) or {}).get("nivel", "normal")
    try:
        qs = generate_quiz(g.text, g.title, avoid, nivel=nivel if nivel in ("facil", "normal", "dificil") else "normal")
    except StudyError as exc:
        return jsonify({"error": str(exc)}), 400
    a = QuizAttempt(guide_id=g.id, user_id=current_user.id, questions=json.dumps(qs, ensure_ascii=False),
                    total=len(qs))
    db.session.add(a)
    db.session.commit()
    return jsonify({"attempt": a.id, "questions": qs})


@bp.route("/api/estudio/intento/<int:aid>/resultado", methods=["POST"])
@login_required
@csrf_protect
def quiz_result(aid):
    a = db.session.get(QuizAttempt, aid)
    if not a or a.user_id != current_user.id:
        abort(404)
    score = (request.get_json(silent=True) or {}).get("score")
    if isinstance(score, int) and 0 <= score <= a.total:
        a.score = score
        a.finished_at = utcnow()
        db.session.commit()
    return jsonify({"ok": True})
