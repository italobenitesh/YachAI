"""Evaluación de calidad de respuestas en QUECHUA de yachaq-01.

Qué hace:
1. Corre cada pregunta del eval set (eval/quechua_eval_set.json) contra el
   agente, con idioma configurado en quechua.
2. Un segundo pase con Gemini como "juez" produce, por respuesta:
   - back-translation al español (para que cualquiera del equipo la lea)
   - chequeos automáticos: ¿es quechua de verdad o español disfrazado?,
     ¿dio la respuesta directa (prohibido)?, ¿terminó con pregunta de
     verificación?, ¿está calibrada al nivel CNEB del ítem?
3. Genera un reporte Markdown en eval/reports/ con columnas VACÍAS de
   validación humana.

IMPORTANTE — límites de este mecanismo:
El juez es el mismo modelo que genera, así que NO puede certificar la calidad
del quechua (sesgo de auto-evaluación, y el quechua es lengua de bajos
recursos). El reporte está diseñado para entregárselo a un HABLANTE NATIVO,
que valida leyendo quechua + back-translation lado a lado. El juez sirve para
descartar fallas obvias baratas (responde en español, da la respuesta
directa) antes de gastar el tiempo del validador humano.

Uso:
  python eval_quechua.py                    # todo el set con respuesta en quechua
  python eval_quechua.py --lang-in qu       # solo los ítems escritos en quechua
  python eval_quechua.py --lang-out es      # calibración pedagógica en español
  python eval_quechua.py --competencia C26  # solo una competencia
  python eval_quechua.py --nivel 5          # solo un nivel CNEB
  python eval_quechua.py --limit 3          # solo los primeros 3 ítems

Cada ítem cuesta 2 requests (agente + juez); el script imprime el costo antes
de empezar. El set completo son 56 ítems = 112 requests: filtra por
competencia/nivel para no quemar la cuota diaria de un tirón.
"""
import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from yachaq import agent, config, curriculum
from yachaq.gemini_client import GeminiError, generate_json

ROOT = Path(__file__).resolve().parent
EVAL_SET = ROOT / "eval" / "quechua_eval_set.json"
REPORTS_DIR = ROOT / "eval" / "reports"

_JUDGE_QUECHUA_FIELDS = """  "variante_detectada": "...",        // ej. "quechua sureño (chanka)", "cusco-collao", "no determinable"
  "back_translation_es": "...",       // traducción fiel COMPLETA de la respuesta al español
"""


def _judge_system(lang_out):
    """El juez cambia según el idioma de RESPUESTA evaluado: en quechua pide
    además variante y back-translation (insumo del validador humano); en
    español o inglés esos campos no aplican y solo se evalúa la pedagogía."""
    idioma = agent.LANG_NAMES.get(lang_out, lang_out)
    extra = _JUDGE_QUECHUA_FIELDS if lang_out == "qu" else ""
    nota = ("Sé estricto con da_respuesta_directa: si el número/resultado "
            "final aparece explícito en la respuesta, es true.")
    nota += (
        " En contexto_ejemplos anota el tema del ejemplo tal cual aparece, sin "
        "juzgarlo: sirve para auditar si el tutor varía los contextos o repite "
        "siempre el mismo. No penalices ni premies que sea rural."
    )
    if lang_out == "qu":
        nota += (" Si dudas de tu propia competencia en quechua para algún "
                 'campo, dilo dentro de "problemas".')
    return f"""Eres un evaluador de calidad para un tutor escolar que \
responde en {idioma} a niños de primaria en Perú. Recibirás la pregunta del \
estudiante, la respuesta del tutor y el estándar curricular CNEB aplicable.

Devuelve SOLO un objeto JSON con estos campos:
{{
  "idioma_correcto": true/false,      // ¿la respuesta está mayormente en {idioma}?
{extra}  "da_respuesta_directa": true/false, // ¿el tutor dio el resultado final en vez de guiar? (violación grave)
  "tiene_pregunta_verificacion": true/false,
  "calibrado_al_nivel": true/false,   // ¿el contenido corresponde al estándar CNEB dado?
  "contexto_ejemplos": "...",         // 1-3 palabras: de qué trata el ejemplo usado (ej. "fútbol", "chacra", "tienda", "ninguno")
  "calidad_pedagogica_1a5": 1-5,      // claridad del paso a paso y ejemplos concretos y comprensibles
  "problemas": ["..."]                // lista de problemas concretos observados, vacía si ninguno
}}

{nota}"""


def _idioma_ok(verdict):
    """Los reportes anteriores usaban la clave es_quechua; se acepta ambas."""
    if "idioma_correcto" in verdict:
        return verdict["idioma_correcto"]
    return verdict.get("es_quechua")


def run_item(item, lang_out="qu"):
    student = {
        "id": 0,
        "name": "EvalRunner",
        "grade": item["grado"],
        "language": lang_out,
    }
    reply = agent.chat(student, None, [], item["question"])

    nivel = item["nivel"]
    judge_input = f"""PREGUNTA DEL ESTUDIANTE ({item['lang_in']}): {item['question']}

COMPETENCIA CNEB: {item['competencia']} — nivel {nivel} (grado {item['grado']}° de primaria)

RESPUESTA DEL TUTOR (a evaluar):
{reply}"""
    verdict = generate_json(
        _judge_system(lang_out), [{"role": "user", "text": judge_input}],
        temperature=0.0,
    )
    return reply, verdict


def flag(ok):
    return "OK" if ok else "**FALLA**"


def build_report(results, started_at, lang_out="qu"):
    es_qu = lang_out == "qu"
    idioma = agent.LANG_NAMES.get(lang_out, lang_out)
    lines = [
        f"# Reporte de evaluación — respuestas en {idioma} (yachaq-01)",
        "",
        f"Fecha: {started_at}  ·  Ítems: {len(results)}",
        "",
    ]
    if es_qu:
        lines += [
            "> **Este reporte NO certifica calidad del quechua por sí solo.**",
            "> Los chequeos automáticos filtran fallas obvias; la columna",
            "> 'Validación humana' debe llenarla un hablante nativo de quechua",
            "> (idealmente de la variante local de los estudiantes del piloto).",
            "",
        ]
    else:
        lines += [
            f"> Evaluación de calibración pedagógica en {idioma}: verifica que el",
            "> tutor guíe sin dar el resultado, cierre con pregunta de verificación",
            "> y se ajuste al nivel CNEB del ítem. El juez es el mismo modelo que",
            "> genera, así que estos números son un filtro, no una certificación.",
            "",
        ]
    lines += [
        "## Resumen automático",
        "",
        f"| Ítem | Competencia | Área | Nivel | ¿{idioma.capitalize()}? "
        "| ¿Guía sin dar respuesta? | ¿Verifica? | ¿Nivel OK? | Calidad 1-5 |",
        "|------|-------------|------|-------|-----------|"
        "--------------------------|------------|------------|-------------|",
    ]
    for r in results:
        it, v = r["item"], r["verdict"]
        comp = it["competencia"].split(" ")[0]
        if v is None:
            lines.append(f"| {it['id']} | {comp} | {it['area']} | {it['nivel']} "
                         f"| ERROR: {r['error']} | | | | |")
            continue
        lines.append(
            f"| {it['id']} | {comp} | {it['area']} | {it['nivel']} "
            f"| {flag(_idioma_ok(v))} "
            f"| {flag(not v.get('da_respuesta_directa'))} "
            f"| {flag(v.get('tiene_pregunta_verificacion'))} "
            f"| {flag(v.get('calibrado_al_nivel'))} "
            f"| {v.get('calidad_pedagogica_1a5', '?')} |"
        )

    contextos = Counter(
        (r["verdict"].get("contexto_ejemplos") or "?").strip().lower()
        for r in results if r["verdict"]
    )
    if contextos:
        lines += [
            "",
            "### Variedad de contextos en los ejemplos",
            "",
            "Los estudiantes rurales son niños con intereses diversos; el campo "
            "debe ser un contexto más, no el único. Si un solo tema domina esta "
            "lista, el tutor está estereotipando.",
            "",
        ]
        lines += [f"- {ctx}: {n}" for ctx, n in contextos.most_common()]

    titulo = ("## Detalle por ítem (para validación humana)" if es_qu
              else "## Detalle por ítem")
    lines += ["", "---", "", titulo, ""]
    for r in results:
        it, v = r["item"], r["verdict"]
        lines += [
            f"### {it['id']} — {it['competencia']} (nivel {it['nivel']}, {it['grado']}° grado)",
            "",
            f"**Pregunta ({it['lang_in']}):** {it['question']}",
            "",
        ]
        if v is None:
            lines += [f"**ERROR al evaluar:** {r['error']}", ""]
            continue
        lines += [
            f"**Respuesta del tutor ({idioma}):**",
            "",
            "```",
            r["reply"],
            "```",
            "",
        ]
        if es_qu:
            lines += [
                f"**Back-translation al español (automática, verificar):** "
                f"{v.get('back_translation_es', '?')}",
                "",
                f"**Variante detectada:** {v.get('variante_detectada', '?')}",
            ]
        lines += [
            f"**Contexto del ejemplo:** {v.get('contexto_ejemplos', '?')}",
            f"**Problemas detectados automáticamente:** "
            f"{', '.join(v.get('problemas') or ['ninguno'])}",
            "",
        ]
        if es_qu:
            lines += [
                "**Validación humana** (llenar a mano):",
                "- [ ] El quechua es correcto y natural para la zona del piloto",
                "- [ ] Un niño de este grado lo entendería",
                "- [ ] La back-translation es fiel (el juez no inventó)",
                "- Observaciones: ______________________________________",
                "",
            ]
        lines += ["---", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--limit", type=int, default=0, help="correr solo N ítems")
    parser.add_argument(
        "--lang-in",
        choices=["es", "qu"],
        default=None,
        help="correr solo los ítems cuya pregunta está en este idioma",
    )
    parser.add_argument(
        "--lang-out",
        choices=["es", "en", "qu"],
        default="qu",
        help="idioma en que debe RESPONDER el tutor (default: qu)",
    )
    parser.add_argument("--competencia", default=None,
                        help="correr solo esta competencia (ej. C26)")
    parser.add_argument("--nivel", type=int, choices=[3, 4, 5], default=None,
                        help="correr solo este nivel CNEB")
    args = parser.parse_args()

    data = json.loads(EVAL_SET.read_text(encoding="utf-8"))
    items = data["items"]
    if args.lang_in:
        items = [i for i in items if i["lang_in"] == args.lang_in]
    if args.competencia:
        items = [i for i in items
                 if i["competencia"].upper().startswith(args.competencia.upper())]
    if args.nivel:
        items = [i for i in items if i["nivel"] == args.nivel]
    if args.limit:
        items = items[: args.limit]
    if not items:
        sys.exit("Ningún ítem coincide con los filtros.")
    curriculum.full_text()  # falla temprano si no está el documento

    idioma = agent.LANG_NAMES.get(args.lang_out, args.lang_out)
    print(f"{len(items)} ítems · respuesta en {idioma} · "
          f"{len(items) * 2} requests a {config.MODEL}\n")

    started_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    results = []
    for i, item in enumerate(items, 1):
        print(f"[{i}/{len(items)}] {item['id']} ({item['competencia']})...", end=" ", flush=True)
        try:
            reply, verdict = run_item(item, args.lang_out)
            results.append({"item": item, "reply": reply, "verdict": verdict, "error": None})
            ok = _idioma_ok(verdict) and not verdict.get("da_respuesta_directa")
            print("ok" if ok else "REVISAR")
        except GeminiError as exc:
            results.append({"item": item, "reply": "", "verdict": None, "error": str(exc)})
            print(f"ERROR: {exc}")
        time.sleep(2)  # respeto al rate limit del free tier

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = REPORTS_DIR / f"eval_{args.lang_out}_{stamp}.md"
    out.write_text(build_report(results, started_at, args.lang_out), encoding="utf-8")

    evaluated = [r for r in results if r["verdict"]]
    n_lang = sum(1 for r in evaluated if _idioma_ok(r["verdict"]))
    n_direct = sum(1 for r in evaluated if r["verdict"].get("da_respuesta_directa"))
    n_verify = sum(1 for r in evaluated if r["verdict"].get("tiene_pregunta_verificacion"))
    n_nivel = sum(1 for r in evaluated if r["verdict"].get("calibrado_al_nivel"))
    total = len(evaluated) or 1
    print(f"""
Resultados automáticos ({len(evaluated)}/{len(items)} ítems evaluados):
  - Respondió en {idioma}:{' ' * max(1, 20 - len(idioma))}{n_lang}/{len(evaluated)}
  - Dio respuesta directa (falla):    {n_direct}/{len(evaluated)}
  - Incluyó pregunta de verificación: {n_verify}/{len(evaluated)}
  - Calibrado al nivel CNEB:          {n_nivel}/{len(evaluated)}

Contextos usados en los ejemplos (si uno domina, hay estereotipo):
{chr(10).join(f'  - {c}: {n}' for c, n in Counter(
    (r['verdict'].get('contexto_ejemplos') or '?').strip().lower()
    for r in evaluated).most_common()) or '  (sin datos)'}

Reporte: {out}""")
    if args.lang_out == "qu":
        print("Siguiente paso: entregar ese archivo a un hablante nativo de quechua.")


if __name__ == "__main__":
    main()
