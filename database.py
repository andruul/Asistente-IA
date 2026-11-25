import json
import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DATA_DIR = os.getenv("DATA_DIR", ".").strip() or "."
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "assistant_memory.db")


def _get_conn() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def init_db():
    """Inicializa la base de datos SQLite con memoria, preferencias, recordatorios y tareas."""
    with _get_conn() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                chat_id INTEGER PRIMARY KEY,
                user_name TEXT,
                preferences TEXT DEFAULT '[]',
                voice_mode INTEGER DEFAULT 0,
                timezone TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Migración segura si la tabla ya existía sin voice_mode o timezone
        cursor.execute("PRAGMA table_info(user_preferences)")
        cols = {row[1] for row in cursor.fetchall()}
        if "voice_mode" not in cols:
            cursor.execute("ALTER TABLE user_preferences ADD COLUMN voice_mode INTEGER DEFAULT 0")
        if "timezone" not in cols:
            cursor.execute("ALTER TABLE user_preferences ADD COLUMN timezone TEXT")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS message_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                role TEXT,
                text_content TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                reminder_text TEXT,
                remind_at TIMESTAMP,
                recurrence TEXT DEFAULT NULL,
                is_completed INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("PRAGMA table_info(reminders)")
        rem_cols = {row[1] for row in cursor.fetchall()}
        if "recurrence" not in rem_cols:
            cursor.execute("ALTER TABLE reminders ADD COLUMN recurrence TEXT DEFAULT NULL")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                title TEXT NOT NULL,
                is_done INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_history ON message_history (chat_id, id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_active_reminders ON reminders (chat_id, is_completed)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_tasks ON tasks (chat_id, is_done)")

        conn.commit()


# ─────────────────────── Preferencias y Perfil ───────────────────────
def save_user_preference(chat_id: int, new_fact: str, user_name: Optional[str] = None):
    fact = new_fact.strip()
    if not fact:
        return
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT preferences FROM user_preferences WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()

        prefs = json.loads(row[0]) if row and row[0] else []
        if fact not in prefs:
            prefs.append(fact)

        cursor.execute("""
            INSERT INTO user_preferences (chat_id, user_name, preferences, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id) DO UPDATE SET
                user_name = COALESCE(?, user_name),
                preferences = excluded.preferences,
                updated_at = CURRENT_TIMESTAMP
        """, (chat_id, user_name, json.dumps(prefs, ensure_ascii=False), user_name))
        conn.commit()


def remove_user_preference(chat_id: int, keyword: str) -> int:
    """Elimina preferencias que coincidan con una palabra clave. Devuelve cuántas borró."""
    kw = keyword.strip().lower()
    if not kw:
        return 0
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT preferences FROM user_preferences WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()
        if not row or not row[0]:
            return 0
        prefs = json.loads(row[0])
        new_prefs = [p for p in prefs if kw not in p.lower()]
        removed = len(prefs) - len(new_prefs)
        if removed > 0:
            cursor.execute(
                "UPDATE user_preferences SET preferences = ?, updated_at = CURRENT_TIMESTAMP WHERE chat_id = ?",
                (json.dumps(new_prefs, ensure_ascii=False), chat_id),
            )
            conn.commit()
        return removed


def get_user_preferences(chat_id: int) -> List[str]:
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT preferences FROM user_preferences WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()
        if row and row[0]:
            try:
                return json.loads(row[0])
            except Exception:
                return []
    return []


def set_voice_mode(chat_id: int, enabled: bool):
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO user_preferences (chat_id, voice_mode, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id) DO UPDATE SET
                voice_mode = excluded.voice_mode,
                updated_at = CURRENT_TIMESTAMP
        """, (chat_id, 1 if enabled else 0))
        conn.commit()


def get_voice_mode(chat_id: int) -> bool:
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT voice_mode FROM user_preferences WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()
        return bool(row and row[0])


def set_user_timezone(chat_id: int, timezone: str):
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO user_preferences (chat_id, timezone, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id) DO UPDATE SET
                timezone = excluded.timezone,
                updated_at = CURRENT_TIMESTAMP
        """, (chat_id, timezone))
        conn.commit()


def get_user_timezone(chat_id: int, default_tz: str = "America/Bogota") -> str:
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT timezone FROM user_preferences WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()
        if row and row[0]:
            return row[0]
    return default_tz


# ─────────────────────── Mensajes ───────────────────────
def save_message(chat_id: int, role: str, text: str):
    if not text:
        return
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO message_history (chat_id, role, text_content) VALUES (?, ?, ?)",
            (chat_id, role, text),
        )
        conn.commit()


def load_recent_messages(chat_id: int, limit: int = 20) -> List[Dict[str, str]]:
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT role, text_content FROM (
                SELECT id, role, text_content
                FROM message_history
                WHERE chat_id = ?
                ORDER BY id DESC
                LIMIT ?
            ) ORDER BY id ASC
        """, (chat_id, limit))
        rows = cursor.fetchall()
        return [{"role": row[0], "text": row[1]} for row in rows]


def clear_history(chat_id: int):
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM message_history WHERE chat_id = ?", (chat_id,))
        conn.commit()


def clear_all(chat_id: int):
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM message_history WHERE chat_id = ?", (chat_id,))
        cursor.execute("DELETE FROM user_preferences WHERE chat_id = ?", (chat_id,))
        cursor.execute("DELETE FROM reminders WHERE chat_id = ?", (chat_id,))
        cursor.execute("DELETE FROM tasks WHERE chat_id = ?", (chat_id,))
        conn.commit()


# ─────────────────────── Recordatorios ───────────────────────
def add_reminder(chat_id: int, text: str, remind_at_str: str, recurrence: Optional[str] = None) -> int:
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO reminders (chat_id, reminder_text, remind_at, recurrence, is_completed)
            VALUES (?, ?, ?, ?, 0)
        """, (chat_id, text, remind_at_str, recurrence))
        conn.commit()
        return cursor.lastrowid


def update_reminder_time(reminder_id: int, new_remind_at_str: str):
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE reminders SET remind_at = ? WHERE id = ?",
            (new_remind_at_str, reminder_id),
        )
        conn.commit()


def get_active_reminders(chat_id: int) -> List[Dict[str, Any]]:
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, reminder_text, remind_at, recurrence
            FROM reminders
            WHERE chat_id = ? AND is_completed = 0
            ORDER BY remind_at ASC
        """, (chat_id,))
        rows = cursor.fetchall()
        return [
            {"id": r[0], "text": r[1], "remind_at": r[2], "recurrence": r[3]}
            for r in rows
        ]


def get_all_pending_reminders() -> List[Dict[str, Any]]:
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, chat_id, reminder_text, remind_at, recurrence
            FROM reminders
            WHERE is_completed = 0
        """)
        rows = cursor.fetchall()
        return [
            {"id": r[0], "chat_id": r[1], "text": r[2], "remind_at": r[3], "recurrence": r[4]}
            for r in rows
        ]


def mark_reminder_completed(reminder_id: int):
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE reminders SET is_completed = 1 WHERE id = ?", (reminder_id,))
        conn.commit()


def delete_reminder(reminder_id: int, chat_id: int) -> bool:
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM reminders WHERE id = ? AND chat_id = ?", (reminder_id, chat_id))
        conn.commit()
        return cursor.rowcount > 0


# ─────────────────────── Tareas / Pendientes ───────────────────────
def add_task(chat_id: int, title: str) -> int:
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO tasks (chat_id, title, is_done) VALUES (?, ?, 0)",
            (chat_id, title.strip()),
        )
        conn.commit()
        return cursor.lastrowid


def get_tasks(chat_id: int, include_done: bool = False) -> List[Dict[str, Any]]:
    with _get_conn() as conn:
        cursor = conn.cursor()
        if include_done:
            cursor.execute(
                "SELECT id, title, is_done, created_at FROM tasks WHERE chat_id = ? ORDER BY is_done ASC, id DESC",
                (chat_id,),
            )
        else:
            cursor.execute(
                "SELECT id, title, is_done, created_at FROM tasks WHERE chat_id = ? AND is_done = 0 ORDER BY id ASC",
                (chat_id,),
            )
        rows = cursor.fetchall()
        return [{"id": r[0], "title": r[1], "is_done": bool(r[2]), "created_at": r[3]} for r in rows]


def complete_task(task_id: int, chat_id: int) -> bool:
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE tasks SET is_done = 1 WHERE id = ? AND chat_id = ?", (task_id, chat_id))
        conn.commit()
        return cursor.rowcount > 0


def delete_task(task_id: int, chat_id: int) -> bool:
    with _get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM tasks WHERE id = ? AND chat_id = ?", (task_id, chat_id))
        conn.commit()
        return cursor.rowcount > 0
