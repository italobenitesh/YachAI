"""Base de conocimiento curricular (CNEB primaria, matemática y ciencia).

DECISIÓN RAG: el documento filtrado pesa ~10 KB (~3K tokens), así que se
inyecta COMPLETO en el system prompt de cada llamada en vez de indexarlo
con embeddings. Ver justificación en IMPLEMENTACION.md.

Este módulo es la "costura" donde se cambiaría a retrieval con embeddings
si la base de conocimiento creciera (p. ej. el PDF completo del CNEB de
224 páginas, o múltiples documentos MINEDU).
"""
from functools import lru_cache

from . import config


@lru_cache(maxsize=1)
def full_text():
    if not config.CURRICULUM_PATH.exists():
        raise FileNotFoundError(
            f"No se encuentra el documento curricular: {config.CURRICULUM_PATH}"
        )
    return config.CURRICULUM_PATH.read_text(encoding="utf-8")


def level_for_grade(grade):
    """Mapea grado de primaria (1-6) al nivel de estándar CNEB (3-5)."""
    if grade <= 2:
        return 3
    if grade <= 4:
        return 4
    return 5
