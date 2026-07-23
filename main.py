"""yachaq-01 — Tutor de matemática y ciencia (CLI).

Uso:  python main.py
"""
import sys

from yachaq import agent, config, memory, offline
from yachaq.gemini_client import GeminiError

LANG_OPTIONS = {"1": "es", "2": "en", "3": "qu"}
LANG_LABELS = {"es": "español", "en": "English", "qu": "quechua"}

BANNER = """
============================================================
  YachAI / yachaq-01 — Tutor de Matemática y Ciencia
  Primaria (CNEB niveles 3-5) · español / English / quechua
============================================================
Comandos:  /idioma  cambiar idioma   /perfil  ver tu memoria
           /salir   terminar (guarda tu progreso)
"""


def ask(prompt):
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return "/salir"


def choose_language():
    while True:
        choice = ask("Idioma / Language / Simi  (1=español, 2=English, 3=quechua): ")
        if choice in LANG_OPTIONS:
            return LANG_OPTIONS[choice]
        if choice in LANG_OPTIONS.values():
            return choice
        print("Escribe 1, 2 o 3.")


def choose_grade():
    while True:
        raw = ask("¿En qué grado de primaria estás? (1-6): ")
        if raw.isdigit() and 1 <= int(raw) <= 6:
            return int(raw)
        print("Escribe un número del 1 al 6.")


def close_session(student, session_id, profile, turns):
    """Actualiza la memoria persistente al terminar la sesión."""
    if turns < 2:
        return
    print("\nGuardando tu progreso...")
    try:
        transcript = memory.session_transcript(student["id"], session_id)
        # Timeout moderado: si no hay señal, mejor cerrar rápido que colgarse;
        # la transcripción ya está en la BD y el perfil puede esperar.
        new_profile = agent.summarize_session(
            student, profile, transcript, timeout=30, max_retries=2
        )
        memory.update_profile(student["id"], new_profile)
        print("Listo. Te recordaré en la próxima sesión. ¡Tupananchikkama!")
    except GeminiError as exc:
        print(f"(No se pudo actualizar el perfil: {exc})")
        print("La transcripción sí quedó guardada; no se perdió nada.")


def main():
    memory.init()
    offline.init()
    print(BANNER)

    name = ""
    while not name:
        name = ask("¿Cómo te llamas? / What's your name? / ¿Imam sutiyki?: ")
    if name == "/salir":
        return

    student, is_new = memory.get_or_create_student(name)
    if is_new:
        grade = choose_grade()
        lang = choose_language()
        memory.set_grade(student["id"], grade)
        memory.set_language(student["id"], lang)
        student["grade"], student["language"] = grade, lang
        print(f"\n¡Hola {student['name']}! Pregúntame algo de matemática o ciencia.\n")
    else:
        print(
            f"\n¡Hola de nuevo, {student['name']}! "
            f"({student['grade']}° grado, {LANG_LABELS[student['language']]})"
        )
        print("Pregúntame algo de matemática o ciencia.\n")

    profile = memory.get_profile(student["id"])
    session_id = memory.new_session_id()
    history = []  # historial de ESTA sesión, se envía completo en cada llamada
    turns = 0

    while True:
        user_msg = ask(f"{student['name']}> ")
        if not user_msg:
            continue

        if user_msg.lower() in ("/salir", "/exit", "/quit"):
            close_session(student, session_id, profile, turns)
            pending = offline.pending_count()
            if pending:
                print(
                    f"\n[Docente] Hay {pending} pregunta(s) pendiente(s) sin "
                    "responder. Donde haya señal, corre:\n"
                    "    python precache.py --solo-pendientes"
                )
            return

        if user_msg.lower() == "/perfil":
            print("\n--- Tu memoria guardada ---")
            print(profile or "Todavía no tengo nada guardado sobre ti.")
            print("---------------------------\n")
            continue

        if user_msg.lower().startswith("/idioma"):
            new_lang = choose_language()
            student["language"] = new_lang
            memory.set_language(student["id"], new_lang)
            print(f"Idioma cambiado a {LANG_LABELS[new_lang]}.\n")
            continue

        if config.OFFLINE_FALLBACK:
            reply, source = agent.chat_with_fallback(student, profile, history, user_msg)
            if source == "banco":
                print("\n[Sin conexión: respuesta del banco local pre-cargado]")
            elif source == "pendiente":
                print("\n[Sin conexión: la pregunta quedó guardada como pendiente]")
        else:
            try:
                reply = agent.chat(student, profile, history, user_msg)
            except GeminiError as exc:
                print(f"\n[Error] {exc}\n")
                continue

        print(f"\nYachaq: {reply}\n")

        history.append({"role": "user", "text": user_msg})
        history.append({"role": "model", "text": reply})
        memory.save_turn(student["id"], session_id, "user", user_msg)
        memory.save_turn(student["id"], session_id, "model", reply)
        turns += 1


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nHasta pronto.")
        sys.exit(0)
