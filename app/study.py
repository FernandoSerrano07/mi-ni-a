"""Guías de estudio: sacar el texto del archivo y generar cuestionarios con Gemini (Google)."""
import base64
import io
import json
import logging
import os
import random

import requests

log = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Se prueban en orden; los alias "-latest" apuntan siempre al modelo Flash vigente
DEFAULT_MODELS = ["gemini-flash-latest", "gemini-2.5-flash", "gemini-flash-lite-latest"]
MAX_TEXT = 60_000          # caracteres de la guía que se mandan a la IA
QUESTIONS_PER_QUIZ = 8

ENFOQUES = [
    "conceptos y definiciones clave", "causas y consecuencias", "fechas, datos y cifras importantes",
    "comparaciones y diferencias", "aplicación práctica con un ejemplo", "detalles que suelen confundirse",
    "relaciones entre ideas", "procedimientos y pasos", "ideas principales de cada sección",
]


class StudyError(Exception):
    pass


def api_key() -> str:
    return os.environ.get("GEMINI_API_KEY", "").strip()


# ---------------- Extraer texto ----------------
def extract_text(data: bytes, mime: str, filename: str) -> str:
    name = (filename or "").lower()
    try:
        if mime == "application/pdf" or name.endswith(".pdf"):
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            return "\n".join((p.extract_text() or "") for p in reader.pages[:80]).strip()
        if name.endswith(".docx") or "wordprocessingml" in (mime or ""):
            import docx
            d = docx.Document(io.BytesIO(data))
            parts = [p.text for p in d.paragraphs]
            for t in d.tables:
                for row in t.rows:
                    parts.append(" | ".join(c.text for c in row.cells))
            return "\n".join(parts).strip()
        if (mime or "").startswith("text/") or name.endswith((".txt", ".md")):
            return data.decode("utf-8", errors="ignore").strip()
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo leer %s: %s", filename, exc)
    return ""


def needs_ocr(mime: str, text: str) -> bool:
    """Fotos de la guía o PDF escaneado (sin texto): se le pide a Gemini que lea la imagen."""
    return (mime or "").startswith("image/") or (mime == "application/pdf" and len(text) < 200)


# ---------------- Gemini ----------------
def _gemini(parts: list, schema: dict | None = None, temperature: float = 0.9) -> str:
    key = api_key()
    if not key:
        raise StudyError("Falta configurar la llave de Gemini (GEMINI_API_KEY) en Render.")
    models = [os.environ["GEMINI_MODEL"]] if os.environ.get("GEMINI_MODEL") else DEFAULT_MODELS
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": temperature}}
    if schema:
        body["generationConfig"].update({"responseMimeType": "application/json", "responseSchema": schema})
    last = None
    for model in models:
        try:
            r = requests.post(GEMINI_URL.format(model=model), headers={"x-goog-api-key": key}, json=body, timeout=90)
        except requests.RequestException as exc:
            last = f"No se pudo conectar con Gemini ({exc.__class__.__name__})"
            continue
        if r.status_code == 404:
            last = f"Modelo {model} no disponible"
            continue
        if r.status_code == 429:
            raise StudyError("Se alcanzó el límite gratis de Gemini por ahora. Intenta en unos minutos 🙏")
        if r.status_code in (400, 401, 403):
            # Las llaves nuevas (AQ.) pueden rechazarse con un modelo retirado: se prueba el siguiente
            msg = r.json().get("error", {}).get("message", "") if r.headers.get("content-type", "").startswith("application/json") else ""
            log.warning("Gemini %s con %s: %s", r.status_code, model, msg[:200])
            if r.status_code in (401, 403) or "API key" in msg:
                last = "La llave de Gemini no funcionó. Revisa GEMINI_API_KEY en Render (debe estar completa, sin espacios)."
            else:
                last = msg or f"Error {r.status_code}"
            continue
        if not r.ok:
            last = f"Gemini respondió {r.status_code}"
            continue
        try:
            cand = r.json()["candidates"][0]
            return "".join(p.get("text", "") for p in cand["content"]["parts"])
        except (KeyError, IndexError, ValueError):
            last = "Gemini no devolvió respuesta (quizás el contenido fue bloqueado)"
            continue
    raise StudyError(last or "No se pudo usar Gemini")


def ocr(data: bytes, mime: str) -> str:
    parts = [{"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}},
             {"text": "Transcribe todo el texto de esta guía de estudio, tal cual, en español. "
                      "Conserva títulos y listas. No agregues comentarios."}]
    return _gemini(parts, temperature=0.1).strip()


QUIZ_SCHEMA = {
    "type": "OBJECT",
    "properties": {"preguntas": {"type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": {
            "pregunta": {"type": "STRING"},
            "opciones": {"type": "ARRAY", "items": {"type": "STRING"}},
            "correcta": {"type": "INTEGER"},
            "explicacion": {"type": "STRING"},
        },
        "required": ["pregunta", "opciones", "correcta", "explicacion"],
    }}},
    "required": ["preguntas"],
}


NIVELES = {
    "facil": "Nivel FÁCIL: preguntas directas sobre ideas principales y definiciones, para empezar a repasar.",
    "normal": ("Nivel UNIVERSITARIO: como en un parcial real. Mezcla preguntas de memoria con preguntas que "
               "exijan entender y aplicar (por ejemplo un caso corto o un ejemplo)."),
    "dificil": ("Nivel DIFÍCIL: como las preguntas más exigentes del parcial. Usa casos prácticos, comparaciones "
                "finas entre conceptos parecidos y detalles que suelen confundirse. Que haya que pensar."),
}


def generate_quiz(guide_text: str, title: str, avoid: list[str], n: int = QUESTIONS_PER_QUIZ,
                  nivel: str = "normal") -> list[dict]:
    text = guide_text[:MAX_TEXT]
    if len(text) < 80:
        raise StudyError("La guía casi no tiene texto para hacer preguntas.")
    enfoques = ", ".join(random.sample(ENFOQUES, 3))
    avoid_txt = "\n".join(f"- {q}" for q in avoid[-60:]) or "(ninguna todavía)"
    prompt = f"""Eres un tutor amable que ayuda a una estudiante universitaria a prepararse para su examen «{title}».
Con base SOLO en la guía de abajo, crea {n} preguntas de opción múltiple en español.

{NIVELES.get(nivel, NIVELES["normal"])}

Reglas para las opciones (muy importante):
- 4 opciones por pregunta y una sola correcta.
- Las 3 incorrectas deben ser CREÍBLES: del mismo tema de la guía, usando conceptos reales que aparecen
  en ella pero que no responden la pregunta, o errores típicos de estudiantes. Nada absurdo ni obviamente falso.
- Las 4 opciones deben tener un largo y un estilo parecidos. La correcta NO debe ser la más larga ni la más detallada.
- No uses palabras que delaten la respuesta ("exclusivamente", "siempre", "nunca", "solo") únicamente en las falsas.
- Evita "todas las anteriores" y "ninguna de las anteriores".
- "correcta" es el índice (0 a 3) de la opción correcta. Reparte la correcta en posiciones distintas.
- "explicacion": 1 o 2 frases que expliquen por qué es la correcta, citando la idea de la guía.
- Esta vez enfócate sobre todo en: {enfoques}. Cubre partes distintas de la guía.
- NO repitas ni reformules estas preguntas que ya se hicieron antes:
{avoid_txt}

GUÍA:
\"\"\"
{text}
\"\"\""""
    raw = _gemini([{"text": prompt}], schema=QUIZ_SCHEMA, temperature=1.0)
    try:
        items = json.loads(raw)["preguntas"]
    except (ValueError, KeyError, TypeError):
        raise StudyError("La IA respondió algo raro. Intenta otra vez.")
    out = []
    for q in items:
        opts = [str(o).strip() for o in (q.get("opciones") or [])][:4]
        c = q.get("correcta")
        if len(opts) < 2 or not isinstance(c, int) or not 0 <= c < len(opts) or not q.get("pregunta"):
            continue
        right = opts[c]
        random.shuffle(opts)
        out.append({"q": str(q["pregunta"]).strip(), "options": opts, "answer": opts.index(right),
                    "why": str(q.get("explicacion", "")).strip()})
    if len(out) < 3:
        raise StudyError("No se pudieron armar suficientes preguntas. Intenta otra vez.")
    return out[:n]
