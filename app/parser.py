"""Entiende recordatorios escritos en español normal.

Ejemplos:
  "mañana 11 ir por la sobrina, avísame 20 antes"
  "en 20 minutos sacar la ropa"
  "el sábado a las 3 de la tarde cumpleaños de mi mamá"
  "todos los días a las 9 de la noche tomar la pastilla"
  "¿ya estudiaste para el parcial? hoy 6 pm"
  "para yareni viernes 7:30 llevar el suéter"
"""
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

DIAS = {"lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "domingo": 6}
NUM_PALABRAS = {"un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "diez": 10,
                "quince": 15, "veinte": 20, "treinta": 30, "media": 30}


class ParseError(ValueError):
    pass


@dataclass
class Parsed:
    title: str
    kind: str                    # reminder | checkin
    notify_at: datetime          # hora local (con tzinfo)
    event_at: datetime | None    # hora local del evento (si se dijo "avísame X antes")
    repeat: str                  # none | daily | weekdays | weekly
    owner_username: str | None


def _norm(s: str) -> str:
    """Minúsculas y sin tildes, conservando la misma longitud (para mapear posiciones)."""
    return "".join(unicodedata.normalize("NFD", ch)[0] for ch in s.lower())


class _Text:
    def __init__(self, original: str):
        self.orig = original
        self.norm = _norm(original)
        self.used = [False] * len(original)

    def find(self, pattern: str):
        for m in re.finditer(pattern, self.norm):
            if not any(self.used[m.start():m.end()]):
                return m
        return None

    def take(self, m) -> None:
        for i in range(m.start(), m.end()):
            self.used[i] = True

    def rest(self) -> str:
        return "".join(" " if u else c for c, u in zip(self.orig, self.used))


def _num(tok: str) -> int:
    return int(tok) if tok.isdigit() else NUM_PALABRAS[tok]


_NUM = r"(\d{1,3}|un|una|uno|dos|tres|cuatro|cinco|diez|quince|veinte|treinta|media)"


def parse(text: str, now: datetime, usernames: dict[str, str] | None = None) -> Parsed:
    """`now` es la hora local actual (con tzinfo). `usernames`: {nombre_normalizado: username}."""
    t = _Text(text.strip())
    if not t.orig:
        raise ParseError("Escribe qué quieres recordar")
    tz = now.tzinfo

    owner = None
    for name_norm, username in (usernames or {}).items():
        m = t.find(rf"\bpara (?:{re.escape(name_norm)})\b")
        if m:
            owner = username
            t.take(m)
            break

    # --- Repetición ---
    repeat = "none"
    weekly_day = None
    for pat, rep in [(r"\b(todos los dias|cada dia|diario|diariamente)\b", "daily"),
                     (r"\b(entre semana|de lunes a viernes|lunes a viernes)\b", "weekdays"),
                     (r"\b(cada semana|todas las semanas|semanalmente)\b", "weekly")]:
        m = t.find(pat)
        if m:
            repeat = rep
            t.take(m)
            break
    m = t.find(r"\b(?:cada|todos los) (lunes|martes|miercoles|jueves|viernes|sabado|domingo)s?\b")
    if m:
        repeat, weekly_day = "weekly", DIAS[m.group(1)]
        t.take(m)

    # --- "en 20 minutos" / "en 2 horas" ---
    m = t.find(rf"\b(?:en|dentro de) {_NUM} ?(minutos?|mins?|horas?|hrs?)\b")
    if m:
        n = _num(m.group(1))
        delta = timedelta(hours=n) if m.group(2).startswith("h") else timedelta(minutes=n)
        if m.group(1) == "media":
            delta = timedelta(minutes=30)
        t.take(m)
        return _finish(t, now + delta, None, repeat, owner)

    # --- Aviso anticipado: "avísame 20 antes", "20 min antes", "media hora antes" ---
    lead = None
    m = t.find(rf"(?:,\s*)?\b(?:y\s+)?(?:avisame|recuerdame|me avisas)?\s*(?:con\s+)?{_NUM}\s*(minutos?|mins?|horas?|hrs?|hora)?\s*antes\b")
    if m:
        n = _num(m.group(1))
        unit = m.group(2) or "min"
        lead = timedelta(hours=n) if unit.startswith("h") else timedelta(minutes=n)
        if m.group(1) == "media" and unit.startswith("h"):
            lead = timedelta(minutes=30)
        t.take(m)

    # --- Día ---
    day = None
    for pat, delta in [(r"\bpasado manana\b", 2), (r"\bmanana(?! (?:en la|por la))\b(?<!de la manana)", 1),
                       (r"\bhoy\b", 0)]:
        m = t.find(pat)
        # "de la mañana" es una hora, no un día
        if m and not t.norm[max(0, m.start() - 6):m.start()].endswith("de la "):
            day = now.date() + timedelta(days=delta)
            t.take(m)
            break
    if day is None:
        m = t.find(r"\b(?:el |este |proximo |el proximo )?(lunes|martes|miercoles|jueves|viernes|sabado|domingo)\b")
        if m:
            wd = DIAS[m.group(1)]
            ahead = (wd - now.weekday()) % 7
            day = now.date() + timedelta(days=ahead)
            t.take(m)
    if day is None:
        m = t.find(r"\b(?:el )?(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b")
        if m:
            d, mo = int(m.group(1)), int(m.group(2))
            y = int(m.group(3)) if m.group(3) else now.year
            y = y + 2000 if y < 100 else y
            try:
                day = date(y, mo, d)
            except ValueError:
                raise ParseError("Esa fecha no existe 🤔")
            if not m.group(3) and day < now.date():
                day = date(y + 1, mo, d)
            t.take(m)
    if weekly_day is not None and day is None:
        day = now.date() + timedelta(days=(weekly_day - now.weekday()) % 7)

    # --- Hora ---
    hhmm = None
    m = t.find(r"\b(?:al |a )?(mediodia|medio dia)\b")
    if m:
        hhmm = (12, 0)
        t.take(m)
    if hhmm is None:
        explicit = (r"\b(?:a las |a la |alas |las )?(\d{1,2})(?:[:.](\d{2}))?\s*"
                    r"(am|pm|a\.\s?m\.?|p\.\s?m\.?|de la manana|de la tarde|de la noche|de la madrugada)\b")
        m = t.find(explicit) or t.find(r"\b(?:a las |a la |alas )(\d{1,2})(?:[:.](\d{2}))?()\b") \
            or t.find(r"\b(\d{1,2})[:.](\d{2})()\b")
        if m is None and (day is not None or repeat != "none"):
            m = t.find(r"(?<![\d/])\b(\d{1,2})()()\b(?![/\d])")  # número suelto: "mañana 11 ..."
        if m:
            h = int(m.group(1))
            mi = int(m.group(2)) if m.group(2) else 0
            marker = (m.group(3) or "").replace(" ", "").replace(".", "")
            if h > 23 or mi > 59:
                raise ParseError("Esa hora no existe 🤔")
            if marker in ("pm", "delatarde", "delanoche") and h < 12:
                h += 12
            elif marker in ("am", "delamanana", "delamadrugada") and h == 12:
                h = 0
            elif not marker and 1 <= h <= 6:
                h += 12  # "a las 3" casi siempre es de la tarde
            hhmm = (h, mi)
            t.take(m)

    if hhmm is None:
        raise ParseError("No entendí la hora 😅 Prueba: «mañana a las 3 pm …» o «en 20 minutos …»")

    if day is None:
        day = now.date()
        if datetime.combine(day, time(*hhmm), tz) <= now:
            day += timedelta(days=1)
    target = datetime.combine(day, time(*hhmm), tz)
    if repeat == "weekdays":
        while target.weekday() >= 5:
            target += timedelta(days=1)

    if lead:
        return _finish(t, target - lead, target, repeat, owner)
    return _finish(t, target, None, repeat, owner)


_FILLER = re.compile(r"^(?:\s|,|\.|-|y\b|que\b|recu[eé]rdame\b|recordarme\b|av[ií]same\b|acu[eé]rdame\b|a las\b)+"
                     r"|(?:\s|,|\.|-|y\b|a las\b|que\b)+$", re.IGNORECASE)


def _finish(t: _Text, notify_at, event_at, repeat, owner) -> Parsed:
    title = re.sub(r"\s+", " ", t.rest()).strip()
    prev = None
    while prev != title:
        prev = title
        title = _FILLER.sub("", _norm_spaces(title)).strip()
    title = re.sub(r"\s+([,?.!])", r"\1", title).strip(" ,")
    if not title or not re.search(r"[a-zA-Záéíóúñ]", title):
        raise ParseError("¿Qué hay que recordar? Escribe también la tarea 🙂")
    kind = "checkin" if ("?" in title or title.startswith("¿")) else "reminder"
    if kind == "checkin" and not title.startswith("¿"):
        title = "¿" + title
    if kind == "checkin" and not title.endswith("?"):
        title = title.split("?")[0].strip() + "?"
    title = title[0].upper() + title[1:] if title[0] != "¿" else "¿" + title[1:2].upper() + title[2:]
    return Parsed(title=title[:200], kind=kind, notify_at=notify_at, event_at=event_at,
                  repeat=repeat, owner_username=owner)


def _norm_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s)
