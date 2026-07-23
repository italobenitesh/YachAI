"""Memory store persistente en SQLite.

Reemplaza el Foundry Memory Store. Dos niveles de memoria:
- interactions: transcripción cruda de cada turno (auditoría / re-análisis).
- profiles: resumen compacto por estudiante (errores recurrentes, temas
  dominados, nivel estimado) que se actualiza al cerrar cada sesión y se
  inyecta en el system prompt de la sesión siguiente.
"""
import sqlite3
import uuid
from contextlib import contextmanager

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE COLLATE NOCASE NOT NULL,
    language TEXT NOT NULL DEFAULT 'es',
    grade INTEGER NOT NULL DEFAULT 4,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id),
    session_id TEXT NOT NULL,
    ts TEXT NOT NULL DEFAULT (datetime('now')),
    role TEXT NOT NULL CHECK (role IN ('user', 'model')),
    content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_interactions_student
    ON interactions(student_id, ts);
CREATE TABLE IF NOT EXISTS profiles (
    student_id INTEGER PRIMARY KEY REFERENCES students(id),
    summary TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@contextmanager
def _db():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init():
    with _db() as conn:
        conn.executescript(_SCHEMA)


def get_or_create_student(name, language="es", grade=4):
    """Devuelve (student_dict, es_nuevo)."""
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM students WHERE name = ?", (name,)
        ).fetchone()
        if row:
            return dict(row), False
        cur = conn.execute(
            "INSERT INTO students (name, language, grade) VALUES (?, ?, ?)",
            (name, language, grade),
        )
        row = conn.execute(
            "SELECT * FROM students WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return dict(row), True


def set_language(student_id, language):
    with _db() as conn:
        conn.execute(
            "UPDATE students SET language = ? WHERE id = ?", (language, student_id)
        )


def set_grade(student_id, grade):
    with _db() as conn:
        conn.execute(
            "UPDATE students SET grade = ? WHERE id = ?", (grade, student_id)
        )


def new_session_id():
    return uuid.uuid4().hex


def save_turn(student_id, session_id, role, content):
    with _db() as conn:
        conn.execute(
            "INSERT INTO interactions (student_id, session_id, role, content) "
            "VALUES (?, ?, ?, ?)",
            (student_id, session_id, role, content),
        )


def get_profile(student_id):
    with _db() as conn:
        row = conn.execute(
            "SELECT summary FROM profiles WHERE student_id = ?", (student_id,)
        ).fetchone()
        return row["summary"] if row else None


def update_profile(student_id, summary):
    with _db() as conn:
        conn.execute(
            "INSERT INTO profiles (student_id, summary, updated_at) "
            "VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(student_id) DO UPDATE SET "
            "summary = excluded.summary, updated_at = excluded.updated_at",
            (student_id, summary),
        )


def session_transcript(student_id, session_id):
    with _db() as conn:
        rows = conn.execute(
            "SELECT role, content FROM interactions "
            "WHERE student_id = ? AND session_id = ? ORDER BY id",
            (student_id, session_id),
        ).fetchall()
        return [dict(r) for r in rows]
