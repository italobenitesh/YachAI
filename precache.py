"""Pre-carga del banco offline de yachaq-01 — CORRER DONDE SÍ HAY SEÑAL.

Hace dos cosas, en este orden:
1. Procesa las preguntas PENDIENTES que quedaron guardadas en sesiones sin
   conexión (genera su respuesta y la agrega al banco: la próxima vez que el
   estudiante pregunte algo parecido offline, el banco sí la tendrá).
2. Genera el banco de preguntas/respuestas: 7 competencias CNEB (C20-C26)
   x 3 niveles (3-5) x N variantes por combinación, y lo guarda en la tabla
   question_bank de yachaq.db (INSERT OR IGNORE: re-correrlo no duplica).

Presupuesto de API: 1 request por combinación (7x3 = 21 por idioma con los
filtros por defecto) + 1 request por pregunta pendiente.

Uso:
  python precache.py                     # banco en español (21 requests)
  python precache.py --lang qu           # banco en quechua
  python precache.py --variantes 6       # más variaciones por combinación
  python precache.py --competencia C23   # solo una competencia
  python precache.py --nivel 4           # solo un nivel
  python precache.py --solo-pendientes   # solo procesa pendientes
  python precache.py --dry-run           # muestra el plan sin llamar a la API
"""
import argparse
import re
import sys
import time

from yachaq import agent, config, curriculum, memory, offline
from yachaq.gemini_client import GeminiError, generate_json

# Los títulos "### C## — Nombre" del documento CNEB son la fuente de verdad
# de las competencias; si el documento cambia, esto se actualiza solo.
_COMP_RE = re.compile(r"^### (C\d+) — (.+)$", re.MULTILINE)

NIVELES = (3, 4, 5)
PAUSA = 2  # segundos entre requests, respeto al rate limit del free tier


def _gen_system(lang):
    extra = ""
    if lang == "qu":
        extra = agent._QUECHUA_RULES
        if config.QUECHUA_GLOSS_ES:
            extra += agent._QUECHUA_GLOSS
    return f"""Eres el generador del banco de ejercicios OFFLINE del tutor Yachaq \
(matemática y ciencia, primaria rural andina del Perú). Los pares \
pregunta-respuesta que generes se entregarán TAL CUAL a estudiantes cuando no \
haya internet, así que cada respuesta debe entenderse sola, sin conversación \
previa.

Cada RESPUESTA debe cumplir las reglas del tutor:
- NUNCA dar el resultado final: guiar paso a paso; el estudiante calcula el
  último paso.
- Explicar el razonamiento de cada paso (qué se hace y por qué).
- VARIAR los contextos entre las variantes que generes: juegos, deportes,
  familia, escuela y recreo, tienda y mercado, música, comida, animales,
  objetos cotidianos. El campo (chacra, cosecha, crianza) es UNA opción más,
  no el default de todas las preguntas: los estudiantes rurales son niños con
  intereses diversos, no solo agricultores en miniatura. Si generas varias
  variantes, que NO compartan el mismo contexto.
- Terminar con UNA sola pregunta corta de verificación.
- Frases cortas, máximo ~200 palabras, calibrada al nivel CNEB pedido (nada
  de contenido de secundaria).
{extra}
=== BASE DE CONOCIMIENTO: ESTÁNDARES CNEB (PRIMARIA, NIVELES 3-5) ===
{curriculum.full_text()}"""


def _gen_prompt(comp_id, comp_name, nivel, variantes, lang):
    kw_extra = ""
    if lang != "es":
        kw_extra = (
            "\nEn palabras_clave incluye TAMBIÉN los equivalentes en ESPAÑOL "
            "de cada término: los estudiantes bilingües suelen escribir sus "
            "preguntas en español."
        )
    return f"""Genera {variantes} variantes DISTINTAS entre sí (situaciones y \
sub-temas diferentes dentro de la competencia) para:
- Competencia: {comp_id} — {comp_name}
- Nivel CNEB: {nivel}
- Idioma de pregunta y respuesta: {agent.LANG_NAMES[lang]}

Devuelve SOLO un array JSON con {variantes} objetos:
[{{"pregunta": "tal como la escribiría un estudiante de ese nivel",
   "palabras_clave": ["6 a 10 términos del tema: sustantivos y verbos clave"],
   "respuesta": "respuesta del tutor cumpliendo TODAS las reglas"}}]{kw_extra}"""


def generar_banco(items_plan, variantes, lang):
    nuevos, duplicados, errores = 0, 0, 0
    for i, (comp_id, comp_name, nivel) in enumerate(items_plan, 1):
        print(f"[{i}/{len(items_plan)}] {comp_id} nivel {nivel} ({lang})...",
              end=" ", flush=True)
        try:
            data = generate_json(
                _gen_system(lang),
                [{"role": "user",
                  "text": _gen_prompt(comp_id, comp_name, nivel, variantes, lang)}],
                temperature=0.8,
            )
        except GeminiError as exc:
            errores += 1
            print(f"ERROR: {exc}")
            time.sleep(PAUSA)
            continue

        if isinstance(data, dict):  # a veces el modelo envuelve el array
            data = next((v for v in data.values() if isinstance(v, list)), [])
        agregados = 0
        for item in data:
            try:
                agregados += offline.add_bank_item(
                    comp_id, nivel, lang,
                    item["pregunta"].strip(),
                    offline.normalize_keywords(item.get("palabras_clave", [])),
                    item["respuesta"].strip(),
                )
            except (KeyError, AttributeError):
                continue  # ítem malformado: se descarta, no vale otro request
        duplicados += max(0, len(data) - agregados)
        nuevos += agregados
        print(f"{agregados} nuevas")
        time.sleep(PAUSA)
    return nuevos, duplicados, errores


def procesar_pendientes():
    rows = offline.pending_unprocessed()
    if not rows:
        print("No hay preguntas pendientes.")
        return 0, 0
    print(f"Procesando {len(rows)} pregunta(s) pendiente(s)...")
    ok, errores = 0, 0
    for row in rows:
        student = {
            "name": row.get("student_name") or "estudiante",
            "grade": row["grade"],
            "language": row["lang"],
        }
        system = agent.build_system_prompt(student, None) + """

TAREA ESPECIAL (procesamiento en lote, sin estudiante presente): responde la
pregunta cumpliendo TODAS las reglas y devuelve SOLO un objeto JSON:
{"competencia": "la más pertinente entre C20 y C26",
 "palabras_clave": ["6 a 10 términos del tema"],
 "respuesta": "la respuesta del tutor"}"""
        print(f"  - [{row['lang']}] {row['question'][:60]}...", end=" ", flush=True)
        try:
            data = generate_json(
                system, [{"role": "user", "text": row["question"]}], temperature=0.6
            )
            offline.add_bank_item(
                data.get("competencia", "?"),
                curriculum.level_for_grade(row["grade"]),
                row["lang"],
                row["question"].strip(),
                offline.normalize_keywords(data.get("palabras_clave", [])),
                data["respuesta"].strip(),
                source="pendiente",
            )
            offline.mark_processed(row["id"])
            ok += 1
            print("ok")
        except (GeminiError, KeyError) as exc:
            errores += 1
            print(f"ERROR: {exc}")
        time.sleep(PAUSA)
    return ok, errores


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--lang", choices=["es", "en", "qu"], default="es",
                        help="idioma del banco a generar (default: es)")
    parser.add_argument("--variantes", type=int, default=4,
                        help="variantes por (competencia, nivel); default 4")
    parser.add_argument("--competencia", default=None,
                        help="generar solo esta competencia (ej. C23)")
    parser.add_argument("--nivel", type=int, choices=NIVELES, default=None,
                        help="generar solo este nivel CNEB")
    parser.add_argument("--solo-pendientes", action="store_true",
                        help="solo procesar pendientes, no generar banco")
    parser.add_argument("--dry-run", action="store_true",
                        help="mostrar el plan y el costo en requests, sin llamar a la API")
    args = parser.parse_args()

    memory.init()
    offline.init()

    competencias = _COMP_RE.findall(curriculum.full_text())
    if not competencias:
        sys.exit("No se encontraron competencias (### C## — ...) en el documento CNEB.")

    plan = [
        (cid, cname, nivel)
        for cid, cname in competencias
        for nivel in NIVELES
        if (args.competencia is None or cid == args.competencia)
        and (args.nivel is None or nivel == args.nivel)
    ]
    if args.solo_pendientes:
        plan = []

    n_pend = offline.pending_count()
    print(f"Plan: {len(plan)} combinaciones (competencia x nivel) en "
          f"'{args.lang}' x {args.variantes} variantes + {n_pend} pendiente(s)")
    print(f"Costo estimado: {len(plan) + n_pend} requests a {config.MODEL}\n")
    if args.dry_run:
        for cid, cname, nivel in plan:
            print(f"  {cid} nivel {nivel} — {cname}")
        return

    pend_ok, pend_err = procesar_pendientes()
    nuevos = dups = gen_err = 0
    if plan:
        print()
        nuevos, dups, gen_err = generar_banco(plan, args.variantes, args.lang)

    print("\n=== Resumen ===")
    print(f"Pendientes procesadas: {pend_ok} (errores: {pend_err})")
    print(f"Entradas nuevas en el banco: {nuevos} (ya existentes: {dups}, "
          f"errores: {gen_err})")
    print("\nCobertura del banco (idioma, competencia, nivel, entradas):")
    for lang, comp, nivel, n in offline.bank_stats():
        print(f"  {lang}  {comp}  nivel {nivel}: {n}")
    restantes = offline.pending_count()
    if restantes:
        print(f"\nOJO: quedan {restantes} pendiente(s) sin procesar (hubo errores).")


if __name__ == "__main__":
    main()
