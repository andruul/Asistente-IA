import sqlite3
import json
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

DB_PATH = "assistant_memory.db"


def init_db():
    """Inicializa la base de datos SQLite con tablas de memoria y recordatorios."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        # Preferencias / hechos a largo plazo del usuario
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                chat_id INTEGER PRIMARY KEY,
                user_name TEXT,
                preferences TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Historial de mensajes persistente
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS message_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                role TEXT,
                text_content TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Tabla de recordatorios programados
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                reminder_text TEXT,
                remind_at TIMESTAMP,
                is_completed INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_history ON message_history (chat_id, id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_active_reminders ON reminders (chat_id, is_completed)")
        
        conn.commit()


# ─────────────────────── Preferencias ───────────────────────
def save_user_preference(chat_id: int, new_fact: str, user_name: Optional[str] = None):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT preferences FROM user_preferences WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()
        
        if row and row[0]:
            prefs = json.loads(row[0])
            if new_fact not in prefs:
                prefs.append(new_fact)
        else:
            prefs = [new_fact]
            
        cursor.execute("""
            INSERT INTO user_preferences (chat_id, user_name, preferences, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id) DO UPDATE SET
                user_name = COALESCE(?, user_name),
                preferences = excluded.preferences,
                updated_at = CURRENT_TIMESTAMP
        """, (chat_id, user_name, json.dumps(prefs, ensure_ascii=False), user_name))
        conn.commit()


def get_user_preferences(chat_id: int) -> List[str]:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT preferences FROM user_preferences WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()
        if row and row[0]:
            try:
                return json.loads(row[0])
            except Exception:
                return []
    return []


# ─────────────────────── Mensajes ───────────────────────
def save_message(chat_id: int, role: str, text: str):
    if not text:
        return
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO message_history (chat_id, role, text_content)
            VALUES (?, ?, ?)
        """, (chat_id, role, text))
        conn.commit()


def load_recent_messages(chat_id: int, limit: int = 20) -> List[Dict[str, str]]:
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM message_history WHERE chat_id = ?", (chat_id,))
        conn.commit()


def clear_all(chat_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM message_history WHERE chat_id = ?", (chat_id,))
        cursor.execute("DELETE FROM user_preferences WHERE chat_id = ?", (chat_id,))
        cursor.execute("DELETE FROM reminders WHERE chat_id = ?", (chat_id,))
        conn.commit()


# ─────────────────────── Recordatorios ───────────────────────
def add_reminder(chat_id: int, text: str, remind_at_str: str) -> int:
    """Guarda un recordatorio y retorna su ID."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO reminders (chat_id, reminder_text, remind_at, is_completed)
            VALUES (?, ?, ?, 0)
        """, (chat_id, text, remind_at_str))
        conn.commit()
        return cursor.lastrowid


def get_active_reminders(chat_id: int) -> List[Dict[str, Any]]:
    """Obtiene los recordatorios pendientes de un usuario."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, reminder_text, remind_at 
            FROM reminders 
            WHERE chat_id = ? AND is_completed = 0
            ORDER BY remind_at ASC
        """, (chat_id,))
        rows = cursor.fetchall()
        return [{"id": r[0], "text": r[1], "remind_at": r[2]} for r in rows]


def get_all_pending_reminders() -> List[Dict[str, Any]]:
    """Obtiene todos los recordatorios pendientes para cargarlos en JobQueue al iniciar."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, chat_id, reminder_text, remind_at 
            FROM reminders 
            WHERE is_completed = 0
        """)
        rows = cursor.fetchall()
        return [{"id": r[0], "chat_id": r[1], "text": r[2], "remind_at": r[3]} for r in rows]


def mark_reminder_completed(reminder_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE reminders SET is_completed = 1 WHERE id = ?", (reminder_id,))
        conn.commit()


def delete_reminder(reminder_id: int, chat_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM reminders WHERE id = ? AND chat_id = ?", (reminder_id, chat_id))
        conn.commit()
        return cursor.rowcount > 0
