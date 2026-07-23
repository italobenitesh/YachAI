"""Modo de contingencia offline: banco local de Q&A + cola de pendientes.

La escuela del piloto solo tiene señal móvil en puntos específicos fuera del
aula. Flujo:

1. ANTES de la visita (donde SÍ hay señal): `python precache.py` llena la
   tabla `question_bank` con preguntas y respuestas modelo por competencia
   CNEB (C20-C26) y nivel (3-5), y procesa las pendientes acumuladas.
2. EN EL AULA (sin señal): si la llamada a Gemini falla o supera
   config.FALLBACK_TIMEOUT, agent.chat_with_fallback busca aquí la entrada
   más parecida a la pregunta del estudiante y la entrega.
3. Si nada del banco se parece lo suficiente (config.MATCH_THRESHOLD), la
   pregunta queda en `pending_questions` para procesarla con conexión.

CÓMO BUSCA find_match (y sus límites):
Bolsa de palabras normalizada — minúsculas, sin tildes, sin signos, sin
números, sin stopwords, plurales simples recortados — comparada contra los
tokens de la pregunta del banco MÁS sus palabras clave (generadas junto con
cada entrada). Dos tokens también coinciden si comparten las primeras 4
letras ("vendí" ~ "vender", "cerco" ~ "cercar"): amortigua conjugaciones
verbales sin un stemmer real. NO es búsqueda semántica: un sinónimo que no
esté en las palabras clave ("contorno" por "perímetro") no matchea nada.
Los números se ignoran a propósito: la coincidencia es por TEMA, y la
respuesta del banco enseña el método con sus propios números (por eso se
antepone "te explico con un problema parecido").
"""
import re
import unicodedata

from . import config
from .memory import _db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS question_bank (
    id INTEGER PRIMARY KEY,
    competencia TEXT NOT NULL,
    nivel INTEGER NOT NULL,
    lang TEXT NOT NULL,
    pregunta TEXT NOT NULL,
    keywords TEXT NOT NULL,   -- tokens ya normalizados, separados por espacio
    respuesta TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'precache',  -- 'precache' | 'pendiente'
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (lang, competencia, nivel, pregunta)
);
CREATE TABLE IF NOT EXISTS pending_questions (
    id INTEGER PRIMARY KEY,
    student_id INTEGER,
    student_name TEXT,
    lang TEXT NOT NULL,
    grade INTEGER NOT NULL,
    question TEXT NOT NULL,
    ts TEXT NOT NULL DEFAULT (datetime('now')),
    processed INTEGER NOT NULL DEFAULT 0,
    processed_at TEXT
);
"""


def init():
    with _db() as conn:
        conn.executescript(_SCHEMA)


# ---------------------------------------------------------------- matching

# Palabras sin contenido temático (ya "dobladas": minúsculas y sin tildes).
# Incluye interrogativos y verbos de cortesía: "¿cuántos...?" no dice el tema.
_STOPWORDS = {
    # español
    "de", "la", "el", "los", "las", "un", "una", "unos", "unas", "y", "o",
    "u", "a", "en", "que", "cual", "cuales", "cuanto", "cuanta", "cuantos",
    "cuantas", "como", "donde", "cuando", "por", "para", "con", "sin", "del",
    "al", "mi", "mis", "tu", "tus", "su", "sus", "se", "si", "no", "es",
    "son", "esta", "estan", "estoy", "hay", "tengo", "tiene", "tienen",
    "quiero", "necesito", "puedo", "puede", "puedes", "ayuda", "ayudame",
    "me", "te", "le", "lo", "les", "nos", "yo", "mas", "muy", "este",
    "estos", "estas", "ese", "esa", "eso", "esos", "esas", "cada", "ser",
    "entre", "sobre", "hasta", "desde", "porque", "pero", "entonces",
    "quien", "quienes",
    # inglés
    "the", "an", "of", "in", "on", "is", "are", "was", "how", "what",
    "which", "where", "when", "why", "many", "much", "do", "does", "did",
    "my", "to", "for", "with", "and", "or", "want", "need", "help", "can",
    "have", "has", "there", "it", "its",
    # quechua (interrogativos y partículas frecuentes)
    "ima", "imayna", "hayka", "maypi", "maypim", "chay", "kay", "nuqa",
    "nuqapa", "qam", "hina", "utaq", "kanchu", "kan",
}

_WORD_RE = re.compile(r"[a-z]+")


def _fold(text):
    """Minúsculas y sin marcas diacríticas (perímetro -> perimetro, ñ -> n)."""
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def _stem(token):
    """Recorte de plural simple: fracciones->fraccion, cuyes->cuy, papas->papa."""
    if len(token) > 5 and token.endswith("es"):
        return token[:-2]
    if len(token) > 4 and token.endswith("s"):
        return token[:-1]
    return token


def tokenize(text):
    """Set de tokens normalizados con contenido temático."""
    return {
        _stem(t)
        for t in _WORD_RE.findall(_fold(text))
        if len(t) >= 2 and t not in _STOPWORDS
    }


def normalize_keywords(keywords):
    """Lista de palabras clave -> string normalizado para guardar en la BD."""
    tokens = set()
    for kw in keywords:
        tokens |= tokenize(str(kw))
    return " ".join(sorted(tokens))


def _tokens_match(a, b):
    """Igualdad exacta, o prefijo común de 4 letras entre tokens de >=4
    letras: cubre conjugaciones y derivados (vendí~vender, cerco~cercar)
    sin necesitar un stemmer real de español/quechua."""
    if a == b:
        return True
    return len(a) >= 4 and len(b) >= 4 and a[:4] == b[:4]


def _overlap(q_tokens, entry_tokens):
    return sum(1 for t in q_tokens if any(_tokens_match(t, u) for u in entry_tokens))


def find_match(question, lang, nivel):
    """Mejor entrada del banco para la pregunta, o None si ninguna supera
    config.MATCH_THRESHOLD.

    score = 0.7 * (tokens de la pregunta cubiertos por la entrada)
          + 0.3 * (palabras clave de la entrada presentes en la pregunta)
          - 0.05 por cada nivel CNEB de distancia
    """
    q_tokens = tokenize(question)
    if not q_tokens:
        return None
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM question_bank WHERE lang = ?", (lang,)
        ).fetchall()

    best_score, best_row = 0.0, None
    for row in rows:
        kw = set(row["keywords"].split())
        entry_tokens = tokenize(row["pregunta"]) | kw
        if not entry_tokens:
            continue
        coverage = _overlap(q_tokens, entry_tokens) / len(q_tokens)
        kw_hit = _overlap(q_tokens, kw) / len(kw) if kw else 0.0
        score = 0.7 * coverage + 0.3 * kw_hit - 0.05 * abs(nivel - row["nivel"])
        if score > best_score:
            best_score, best_row = score, row

    if best_row is None or best_score < config.MATCH_THRESHOLD:
        return None
    result = dict(best_row)
    result["score"] = round(best_score, 3)
    return result


# ------------------------------------------------------------------- banco

def add_bank_item(competencia, nivel, lang, pregunta, keywords, respuesta,
                  source="precache"):
    """Inserta una entrada; devuelve 1 si es nueva, 0 si ya existía."""
    with _db() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO question_bank "
            "(competencia, nivel, lang, pregunta, keywords, respuesta, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (competencia, nivel, lang, pregunta, keywords, respuesta, source),
        )
        return cur.rowcount


def bank_stats():
    """[(lang, competencia, nivel, cantidad)] para reportar cobertura."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT lang, competencia, nivel, COUNT(*) AS n FROM question_bank "
            "GROUP BY lang, competencia, nivel ORDER BY lang, competencia, nivel"
        ).fetchall()
        return [tuple(r) for r in rows]


# --------------------------------------------------------------- pendientes

def save_pending(student, question):
    with _db() as conn:
        conn.execute(
            "INSERT INTO pending_questions "
            "(student_id, student_name, lang, grade, question) "
            "VALUES (?, ?, ?, ?, ?)",
            (student.get("id"), student.get("name"), student["language"],
             student["grade"], question),
        )


def pending_unprocessed():
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM pending_questions WHERE processed = 0 ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


def pending_count():
    with _db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM pending_questions WHERE processed = 0"
        ).fetchone()
        return row["n"]


def mark_processed(pending_id):
    with _db() as conn:
        conn.execute(
            "UPDATE pending_questions SET processed = 1, "
            "processed_at = datetime('now') WHERE id = ?",
            (pending_id,),
        )


# ------------------------------------------------------------ mensajes fijos
# Los textos en quechua son BORRADORES (sureño/chanka) y deben pasar por el
# mismo validador nativo que el resto del quechua del proyecto.

_PREFACE = {
    "es": "Ahora no tengo conexión, así que te explico con un problema "
          "parecido de mi banco de ejercicios:\n«{pregunta}»\n\n",
    "en": "I have no connection right now, so let me explain with a similar "
          "problem from my exercise bank:\n“{pregunta}”\n\n",
    "qu": "Kunan mana internet kanchu; chayrayku kay rikchakuq tapuywan "
          "yachachisqayki:\n«{pregunta}»\n\n",
}

_NO_MATCH = {
    "es": "Ahora mismo no tengo conexión y en mi banco de ejercicios no "
          "encontré nada parecido a tu pregunta. La guardé como pendiente: "
          "cuando haya internet prepararé la respuesta y tu profesor(a) te "
          "la traerá. Mientras tanto, ¿quieres hacerme otra pregunta de "
          "matemática o de ciencia?",
    "en": "I have no connection right now and I couldn't find anything "
          "similar to your question in my exercise bank. I saved it as "
          "pending: when there is internet I will prepare the answer and "
          "your teacher will bring it to you. Meanwhile, would you like to "
          "ask me another math or science question?",
    "qu": "Kunan mana internet kanchu, hinaspa tapuyniykiman rikchakuq "
          "yachachiyta mana tarinichu. Tapuyniykita waqaychani: internet "
          "kaptinña kutichiyta wakichisaq, yachachiqniykitaq apamusunki. "
          "Kunanqa, ¿huk matematica utaq ciencia tapuyta ruwawankimanchu?\n"
          "[ES] Sin conexión: guardé tu pregunta como pendiente para "
          "prepararla cuando haya internet.",
}


def offline_preface(lang, pregunta_banco):
    tpl = _PREFACE.get(lang, _PREFACE["es"])
    return tpl.format(pregunta=pregunta_banco)


def no_match_message(lang):
    return _NO_MATCH.get(lang, _NO_MATCH["es"])
