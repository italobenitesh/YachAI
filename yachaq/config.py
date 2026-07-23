"""Configuración central de yachaq-01."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv():
    """Carga un .env simple (KEY=VALUE) sin dependencias externas."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Verificado 2026-07-20 con la clave de este proyecto:
# - gemini-2.0-flash / 2.5-flash*: retirados para claves nuevas (429 limit: 0 / 404).
# - gemini-flash-latest -> gemini-3.5-flash: free tier de SOLO 20 requests/DÍA
#   (una sesión de tutoría son ~9) + 503 frecuentes por alta demanda. Inviable.
# - gemini-3.1-flash-lite: funciona y es la opción con cuota diaria utilizable.
# Cambiable vía variable de entorno YACHAQ_MODEL.
MODEL = os.environ.get("YACHAQ_MODEL", "gemini-3.1-flash-lite")

DB_PATH = ROOT / "yachaq.db"
CURRICULUM_PATH = ROOT / "CNEB_estandares_primaria_matematica_ciencia.md"

# En modo quechua, añadir una línea final [ES] con resumen para el docente.
# Útil durante el piloto para supervisión; desactivar cuando ya se confíe.
QUECHUA_GLOSS_ES = os.environ.get("YACHAQ_QU_GLOSS", "1") == "1"

REQUEST_TIMEOUT = 90  # segundos
MAX_RETRIES = 4

# --- Modo offline (contingencia para el aula sin señal) ---
# Si está activo, cada turno del tutor intenta Gemini con un timeout CORTO y
# un solo intento; si falla, cae al banco local (question_bank en SQLite).
OFFLINE_FALLBACK = os.environ.get("YACHAQ_OFFLINE", "1") == "1"
FALLBACK_TIMEOUT = float(os.environ.get("YACHAQ_FALLBACK_TIMEOUT", "12"))
FALLBACK_RETRIES = int(os.environ.get("YACHAQ_FALLBACK_RETRIES", "1"))
# Puntaje mínimo (0-1) para aceptar una coincidencia del banco local.
# Por debajo, la pregunta va a la tabla de pendientes. Ver offline.find_match.
MATCH_THRESHOLD = float(os.environ.get("YACHAQ_MATCH_THRESHOLD", "0.45"))
