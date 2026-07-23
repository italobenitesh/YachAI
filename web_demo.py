"""Interfaz web de yachaq-01 para el evento de entrega (11-13 de setiembre).

Contexto: NO es una clase sostenida — es una demostración puntual dentro del
evento. Estudiantes voluntarios pasan por turnos de 2-3 minutos, con
proyector si lo hay. Por eso el flujo es:

  nombre (crea el perfil en SQLite si es nuevo)
    -> pregunta escrita o dictada
    -> respuestas cortas del tutor (reglas pedagógicas intactas)
    -> verificación bien respondida => pantalla "¡Muy bien!"
    -> vuelve a la entrada para el siguiente estudiante

Diseño: texto grande, alto contraste (fondo claro: los proyectores débiles
lavan los fondos oscuros), Flask en localhost. Si Gemini falla o no hay
señal, cae al banco offline igual que el CLI (correr `python precache.py`
donde haya señal ANTES del evento).

Uso:
  python web_demo.py        # abre http://localhost:5000
"""
import threading

from flask import Flask, jsonify, render_template, request

from yachaq import agent, config, curriculum, memory, offline
from yachaq.gemini_client import GeminiError

app = Flask(__name__)
# Recarga la plantilla al vuelo: permite ajustar textos o colores durante el
# evento sin reiniciar el servidor (bastan un guardado y un F5).
app.config["TEMPLATES_AUTO_RELOAD"] = True

# Un solo kiosco, un turno a la vez: el estado del turno vive en el servidor.
_turn = {}
_turn_lock = threading.Lock()


@app.get("/")
def index():
    return render_template("demo.html")


@app.post("/api/start")
def start():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()[:40]
    if not name:
        return jsonify({"error": "Falta el nombre"}), 400
    grade = int(data.get("grade") or 4)
    lang = data.get("lang") if data.get("lang") in ("es", "en", "qu") else "es"

    student, is_new = memory.get_or_create_student(name, language=lang, grade=grade)
    if not is_new and (student["grade"] != grade or student["language"] != lang):
        memory.set_grade(student["id"], grade)
        memory.set_language(student["id"], lang)
        student["grade"], student["language"] = grade, lang

    with _turn_lock:
        _turn.clear()
        _turn.update({
            "student": student,
            "profile": memory.get_profile(student["id"]),
            "session_id": memory.new_session_id(),
            "history": [],
            "turns": 0,
        })
    return jsonify({
        "name": student["name"],
        "grade": student["grade"],
        "lang": student["language"],
        "returning": not is_new,
    })


@app.post("/api/ask")
def ask():
    with _turn_lock:
        if not _turn:
            return jsonify({"error": "No hay turno activo"}), 400
        student = _turn["student"]
        profile = _turn["profile"]
        history = list(_turn["history"])
        session_id = _turn["session_id"]

    message = (request.get_json(force=True).get("message") or "").strip()
    if not message:
        return jsonify({"error": "Mensaje vacío"}), 400

    source, estado = "online", "explicando"
    try:
        reply, estado = agent.demo_chat(
            student, profile, history, message,
            timeout=config.FALLBACK_TIMEOUT if config.OFFLINE_FALLBACK else None,
            max_retries=config.FALLBACK_RETRIES if config.OFFLINE_FALLBACK else None,
        )
    except GeminiError:
        # Mismo plan de contingencia que el CLI: banco local o pendientes.
        # Sin modelo no hay quién juzgue la verificación: estado "manual"
        # hace que la UI muestre botones para que el facilitador decida.
        nivel = curriculum.level_for_grade(student["grade"])
        match = offline.find_match(message, student["language"], nivel)
        if match:
            reply = (offline.offline_preface(student["language"], match["pregunta"])
                     + match["respuesta"])
            source, estado = "banco", "manual"
        else:
            offline.save_pending(student, message)
            reply = offline.no_match_message(student["language"])
            source, estado = "pendiente", "manual"

    memory.save_turn(student["id"], session_id, "user", message)
    memory.save_turn(student["id"], session_id, "model", reply)
    with _turn_lock:
        _turn["history"].append({"role": "user", "text": message})
        _turn["history"].append({"role": "model", "text": reply})
        _turn["turns"] += 1

    return jsonify({"reply": reply, "estado": estado, "source": source})


@app.post("/api/end")
def end():
    """Cierra el turno; el resumen de perfil corre en segundo plano para que
    el siguiente estudiante pueda empezar de inmediato."""
    with _turn_lock:
        if not _turn:
            return jsonify({"ok": True})
        student = _turn["student"]
        profile = _turn["profile"]
        session_id = _turn["session_id"]
        turns = _turn["turns"]
        _turn.clear()

    if turns >= 2:
        threading.Thread(
            target=_update_profile_bg, args=(student, profile, session_id),
            daemon=True,
        ).start()
    return jsonify({"ok": True, "pending": offline.pending_count()})


def _update_profile_bg(student, profile, session_id):
    try:
        transcript = memory.session_transcript(student["id"], session_id)
        summary = agent.summarize_session(student, profile, transcript,
                                          timeout=30, max_retries=2)
        memory.update_profile(student["id"], summary)
    except GeminiError:
        pass  # sin señal: la transcripción ya quedó en la BD


if __name__ == "__main__":
    memory.init()
    offline.init()
    print("\n  YachAI — demo del evento:  http://localhost:5000\n")
    app.run(host="127.0.0.1", port=5000, threaded=True)
