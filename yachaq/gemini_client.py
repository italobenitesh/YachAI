"""Cliente mínimo para la API REST de Gemini (generateContent).

Se usa REST directo con `requests` en lugar del SDK oficial para mantener
las dependencias al mínimo (relevante para máquinas con mala conectividad
donde instalar paquetes es costoso).
"""
import json
import re
import time

import requests

from . import config

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Errores HTTP que ameritan reintento (rate limit del free tier / servidor).
_RETRYABLE = {429, 500, 502, 503, 504}

# Espera base y tope del backoff exponencial: 3s, 6s, 12s, 24s... máx 60s.
_BACKOFF_BASE = 3.0
_BACKOFF_CAP = 60.0


class GeminiError(Exception):
    pass


def _backoff_delay(attempt, resp=None):
    """Espera antes del reintento N: exponencial, y si el servidor indica
    cuánto esperar (RetryInfo en el cuerpo del 429, o header Retry-After),
    se respeta ese valor cuando es mayor."""
    delay = min(_BACKOFF_BASE * (2 ** (attempt - 1)), _BACKOFF_CAP)
    if resp is None:
        return delay
    hint = 0.0
    retry_after = resp.headers.get("retry-after", "")
    if retry_after.replace(".", "", 1).isdigit():
        hint = float(retry_after)
    m = re.search(r'"retryDelay":\s*"([\d.]+)s"', resp.text) or re.search(
        r"retry in ([\d.]+)s", resp.text, re.IGNORECASE
    )
    if m:
        hint = max(hint, float(m.group(1)) + 1.0)  # +1s de margen
    return min(max(delay, hint), 90.0)


def _quota_is_zero(body_text):
    """429 con 'limit: 0' = el modelo NO tiene free tier para esta clave.
    Reintentar jamás lo arregla; hay que cambiar de modelo."""
    return "RESOURCE_EXHAUSTED" in body_text and re.search(
        r"limit:\s*0[,\s]", body_text
    )


def _daily_quota_exhausted(body_text):
    """429 por cuota DIARIA (quotaId ...PerDay...) = no se recupera en
    segundos sino a medianoche (hora del Pacífico). Reintentar solo quema
    más requests; hay que abortar con mensaje claro."""
    return "RESOURCE_EXHAUSTED" in body_text and "PerDay" in body_text


def generate(system_instruction, messages, temperature=0.6, json_mode=False,
             timeout=None, max_retries=None):
    """Llama a Gemini y devuelve el texto de la respuesta.

    messages: lista de {"role": "user" | "model", "text": str}
    timeout / max_retries: si se pasan, sobreescriben los de config. El modo
    offline los usa para fallar RÁPIDO (12s, 1 intento) y caer al banco local
    en vez de colgarse 4 x 90s esperando una señal que no existe.
    """
    if not config.GEMINI_API_KEY:
        raise GeminiError(
            "Falta la variable GEMINI_API_KEY.\n"
            "1. Consigue una clave gratis en https://aistudio.google.com/apikey\n"
            "2. Crea un archivo .env en la carpeta del proyecto con:\n"
            "   GEMINI_API_KEY=tu_clave"
        )

    gen_config = {"temperature": temperature}
    if json_mode:
        gen_config["response_mime_type"] = "application/json"

    payload = {
        "system_instruction": {"parts": [{"text": system_instruction}]},
        "contents": [
            {"role": m["role"], "parts": [{"text": m["text"]}]} for m in messages
        ],
        "generationConfig": gen_config,
    }
    url = API_URL.format(model=config.MODEL)
    headers = {"x-goog-api-key": config.GEMINI_API_KEY}
    timeout = timeout or config.REQUEST_TIMEOUT
    retries = max_retries or config.MAX_RETRIES

    last_error = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            last_error = f"Error de red: {exc}"
            if attempt < retries:
                time.sleep(_backoff_delay(attempt))
            continue

        if resp.status_code == 200:
            return _extract_text(resp.json())

        if resp.status_code == 429 and _quota_is_zero(resp.text):
            raise GeminiError(
                f"El modelo '{config.MODEL}' no tiene cuota free tier para esta "
                "clave (429 con limit: 0). Reintentar no sirve: cambia de modelo "
                "con YACHAQ_MODEL en .env (p. ej. YACHAQ_MODEL=gemini-3.1-flash-lite)."
            )

        if resp.status_code == 429 and _daily_quota_exhausted(resp.text):
            raise GeminiError(
                f"Cuota DIARIA del free tier agotada para '{config.MODEL}' "
                "(se resetea a medianoche, hora del Pacífico). Reintentar hoy "
                "no sirve. Opciones: cambiar de modelo con YACHAQ_MODEL en .env "
                "o esperar al día siguiente."
            )

        if resp.status_code in _RETRYABLE:
            last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            if attempt < retries:
                time.sleep(_backoff_delay(attempt, resp))
            continue

        raise GeminiError(f"HTTP {resp.status_code}: {resp.text[:500]}")

    raise GeminiError(
        f"Sin respuesta tras {retries} intentos. Último error: {last_error}\n"
        "Si es HTTP 429, se agotó la cuota del free tier (espera unos minutos "
        "o revisa el límite diario)."
    )


def _extract_text(data):
    try:
        candidate = data["candidates"][0]
        parts = candidate["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
        if not text:
            raise KeyError("respuesta vacía")
        return text
    except (KeyError, IndexError):
        reason = ""
        try:
            reason = data["candidates"][0].get("finishReason", "")
        except (KeyError, IndexError):
            pass
        raise GeminiError(
            f"Respuesta sin texto (finishReason={reason or 'desconocido'}): "
            f"{json.dumps(data)[:300]}"
        )


def generate_json(system_instruction, messages, temperature=0.0,
                  timeout=None, max_retries=None):
    """Como generate() pero fuerza salida JSON y la parsea."""
    raw = generate(system_instruction, messages, temperature, json_mode=True,
                   timeout=timeout, max_retries=max_retries)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GeminiError(f"El modelo no devolvió JSON válido: {exc}\n{raw[:300]}")
