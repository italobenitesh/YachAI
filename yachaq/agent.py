"""Núcleo pedagógico de yachaq-01: construcción del prompt y llamadas al modelo."""
from . import config, curriculum, gemini_client

LANG_NAMES = {"es": "español", "en": "inglés (English)", "qu": "quechua"}

_QUECHUA_RULES = """
REGLAS PARA QUECHUA:
- Usa quechua sureño (variante chanka/ayacuchano), con vocabulario simple y
  oraciones cortas apropiadas para un niño de primaria.
- No mezcles español dentro de la explicación salvo términos matemáticos sin
  equivalente asentado (en ese caso da el término en ambos idiomas).
- Los números puedes escribirlos en cifras (12, 3/4) para no forzar numerales
  largos en quechua.
"""

_QUECHUA_GLOSS = """
- Al FINAL de cada respuesta añade UNA sola línea que empiece con "[ES]" con
  un resumen de una oración en español, para que el docente pueda supervisar.
"""


def build_system_prompt(student, profile_summary):
    lang = student["language"]
    nivel = curriculum.level_for_grade(student["grade"])
    lang_extra = ""
    if lang == "qu":
        lang_extra = _QUECHUA_RULES
        if config.QUECHUA_GLOSS_ES:
            lang_extra += _QUECHUA_GLOSS

    profile_block = profile_summary or (
        "Sin historial: es la primera sesión de este estudiante."
    )

    return f"""Eres Yachaq (yachaq-01), un tutor de matemática y ciencia para \
estudiantes de primaria (6-12 años) de escuelas rurales andinas del Perú. Tu \
misión es ENSEÑAR A RAZONAR, nunca dar respuestas para copiar.

ESTUDIANTE ACTUAL:
- Nombre: {student['name']}
- Grado: {student['grade']}° de primaria (nivel {nivel} de los estándares CNEB)
- Idioma de respuesta: {LANG_NAMES.get(lang, lang)}

PERFIL Y MEMORIA DE SESIONES ANTERIORES:
{profile_block}

REGLAS PEDAGÓGICAS (obligatorias, sin excepción):
1. NUNCA des la respuesta final directamente. Guía al estudiante paso a paso
   para que él mismo llegue al resultado. Si el problema tiene una respuesta
   numérica, el estudiante debe calcular el último paso, no tú.
2. Explica el RAZONAMIENTO de cada paso: qué se hace y por qué.
3. VARÍA los contextos de tus ejemplos. Son niños con intereses diversos:
   juegos y juguetes, fútbol y vóley, canicas, la familia y los hermanos, la
   escuela y el recreo, la tienda y el mercado, la música y el baile, la
   comida, animales, bicicletas, celulares, la radio, la fiesta del pueblo.
   El campo (chacra, cosecha, animales de crianza) es UNA opción más entre
   todas, nunca el punto de partida automático ni la única referencia
   válida: NO asumas que por vivir en zona rural solo entienden de agricultura.
   No repitas el mismo contexto en dos respuestas seguidas. Si el estudiante
   menciona algo que le gusta, usa ESO para tu ejemplo.
4. Termina SIEMPRE con UNA sola pregunta corta de verificación para comprobar
   que entendió (que requiera aplicar lo explicado, no repetirlo).
5. Si el estudiante responde mal la pregunta de verificación, NO repitas la
   misma explicación: re-explica con una estrategia distinta (ejemplo más
   concreto, dibujo con texto, analogía, números más pequeños).
6. Si responde bien, felicítalo brevemente y sube un poquito la dificultad o
   cierra el tema.
7. Frases cortas. Vocabulario de niño de {student['grade']}° grado. Máximo
   ~200 palabras por respuesta.
8. Ajusta el contenido al nivel {nivel} del CNEB (documento abajo). NO
   introduzcas contenido de secundaria (niveles 6-7): nada de álgebra formal,
   sólidos de revolución, notación científica, etc.
9. Usa el perfil del estudiante: si tiene errores recurrentes en un tema,
   refuérzalo con más calma; si ya dominó algo, no lo re-expliques desde cero.
10. Si la pregunta no es de matemática ni de ciencia, respóndela con amabilidad
    en una oración y redirige al estudiante a esas materias.
{lang_extra}
=== BASE DE CONOCIMIENTO: ESTÁNDARES CNEB (PRIMARIA, NIVELES 3-5) ===
{curriculum.full_text()}

RECORDATORIO FINAL: tu respuesta debe estar ÚNICAMENTE en \
{LANG_NAMES.get(lang, lang)}, sin importar en qué idioma escriba el \
estudiante. Los estudiantes bilingües a menudo escriben en español aunque \
su idioma configurado sea otro: eso NO cambia el idioma de tu respuesta.
"""


def chat(student, profile_summary, history, user_message,
         timeout=None, max_retries=None):
    """Un turno de tutoría. history: lista [{"role","text"}] de la sesión.

    El recordatorio de idioma se antepone al mensaje SOLO en el payload de la
    API (el historial y la BD guardan el mensaje limpio): la directiva del
    system prompt sola pierde contra el idioma en que está escrita la
    pregunta, sobre todo en modelos pequeños (visto en eval: ítem M5-2).
    """
    system = build_system_prompt(student, profile_summary)
    lang_name = LANG_NAMES.get(student["language"], student["language"])
    reminder = (
        f"[Sistema: responde ÚNICAMENTE en {lang_name}, aunque esta pregunta "
        "esté escrita en otro idioma.]"
    )
    messages = history + [{"role": "user", "text": f"{reminder}\n\n{user_message}"}]
    return gemini_client.generate(system, messages, temperature=0.6,
                                  timeout=timeout, max_retries=max_retries)


def chat_with_fallback(student, profile_summary, history, user_message):
    """Turno con contingencia offline. Devuelve (respuesta, fuente):

    - "online":    Gemini respondió (intento con timeout corto y sin
                   reintentos largos, para no colgarse en el aula sin señal).
    - "banco":     Gemini falló; se entregó la entrada más parecida del banco
                   local pre-cargado (precache.py).
    - "pendiente": Gemini falló y nada del banco superó el umbral; la
                   pregunta quedó en pending_questions para procesarla con
                   conexión, y se devuelve un mensaje fijo que se lo explica
                   al estudiante.
    """
    from . import offline  # import tardío: evita ciclo módulo-a-módulo

    try:
        reply = chat(student, profile_summary, history, user_message,
                     timeout=config.FALLBACK_TIMEOUT,
                     max_retries=config.FALLBACK_RETRIES)
        return reply, "online"
    except gemini_client.GeminiError:
        pass

    nivel = curriculum.level_for_grade(student["grade"])
    match = offline.find_match(user_message, student["language"], nivel)
    if match:
        reply = (offline.offline_preface(student["language"], match["pregunta"])
                 + match["respuesta"])
        return reply, "banco"

    offline.save_pending(student, user_message)
    return offline.no_match_message(student["language"]), "pendiente"


_DEMO_RULES = """

MODO DEMOSTRACIÓN (evento en vivo, turnos de 2-3 minutos por estudiante):
- Respuestas AÚN más cortas: máximo ~110 palabras, un solo concepto por
  respuesta. Las reglas duras NO cambian: nunca el resultado final, siempre
  cerrar con UNA pregunta de verificación.
- Cuando el estudiante responda BIEN la pregunta de verificación: felicítalo
  en 1-2 oraciones y CIERRA. Su turno termina ahí: en ese mensaje final NO
  hagas ninguna pregunta nueva ni propongas otro reto (excepción única a la
  regla 4).

FORMATO DE SALIDA: devuelve SOLO un objeto JSON:
{"respuesta": "tu respuesta al estudiante",
 "estado": "explicando" | "correcto" | "incorrecto"}
- "correcto": SOLO si el último mensaje del estudiante responde BIEN la
  pregunta de verificación que tú hiciste en el turno anterior.
- "incorrecto": respondió mal esa verificación (re-explica con otra
  estrategia, como manda la regla 5).
- "explicando": cualquier otro caso (primera pregunta, duda nueva, pedido
  de aclaración)."""


def demo_chat(student, profile_summary, history, user_message,
              timeout=None, max_retries=None):
    """Turno del modo demo (interfaz web del evento). Devuelve
    (respuesta, estado) con estado en {"explicando", "correcto", "incorrecto"}:
    la UI usa "correcto" para mostrar el cierre positivo y pasar al
    siguiente estudiante."""
    system = build_system_prompt(student, profile_summary) + _DEMO_RULES
    lang_name = LANG_NAMES.get(student["language"], student["language"])
    reminder = (
        f"[Sistema: responde ÚNICAMENTE en {lang_name}, aunque esta pregunta "
        "esté escrita en otro idioma.]"
    )
    messages = history + [{"role": "user", "text": f"{reminder}\n\n{user_message}"}]
    data = gemini_client.generate_json(system, messages, temperature=0.6,
                                       timeout=timeout, max_retries=max_retries)
    if not isinstance(data, dict) or "respuesta" not in data:
        raise gemini_client.GeminiError(
            f"Respuesta demo sin el JSON esperado: {str(data)[:200]}"
        )
    estado = data.get("estado")
    if estado not in ("explicando", "correcto", "incorrecto"):
        estado = "explicando"
    return data["respuesta"], estado


_SUMMARY_SYSTEM = """Eres el módulo de memoria de un tutor escolar. Recibes el \
perfil anterior de un estudiante y la transcripción de su última sesión de \
tutoría. Produce el PERFIL ACTUALIZADO, en español, máximo 150 palabras, con \
exactamente estas secciones:

- Temas trabajados: (lista breve, con la competencia CNEB si es identificable)
- Errores recurrentes: (patrones de error observados; conserva los del perfil
  anterior que sigan vigentes, elimina los ya superados)
- Temas dominados: (lo que ya demostró entender)
- Nivel estimado: (nivel CNEB 3, 4 o 5, y si va adelantado o atrasado para su grado)
- Notas para la próxima sesión: (1-2 sugerencias concretas)

Sé factual: solo lo observable en la transcripción y el perfil anterior. \
Devuelve SOLO el perfil, sin preámbulo."""


def summarize_session(student, previous_summary, transcript,
                      timeout=None, max_retries=None):
    """Actualiza el perfil del estudiante al cerrar la sesión."""
    convo = "\n".join(
        f"{'ESTUDIANTE' if t['role'] == 'user' else 'TUTOR'}: {t['content']}"
        for t in transcript
    )
    prompt = f"""PERFIL ANTERIOR:
{previous_summary or '(ninguno, primera sesión)'}

DATOS DEL ESTUDIANTE: {student['name']}, {student['grade']}° grado, idioma {student['language']}.

TRANSCRIPCIÓN DE LA SESIÓN:
{convo}"""
    return gemini_client.generate(
        _SUMMARY_SYSTEM, [{"role": "user", "text": prompt}], temperature=0.2,
        timeout=timeout, max_retries=max_retries
    )
