import sqlite3
import json
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

DB_PATH = "assistant_memory.db"


def init_db():
    """Inicializa la base de datos SQLite con las tablas necesarias."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        # Tabla para preferencias / memoria a largo plazo del usuario
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                chat_id INTEGER PRIMARY KEY,
                user_name TEXT,
                preferences TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Tabla para historial de mensajes persistente
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS message_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                role TEXT,
                text_content TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Índice para búsquedas rápidas de historial por chat_id
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_chat_history ON message_history (chat_id, id)
        """)
        
        conn.commit()


def save_user_preference(chat_id: int, new_fact: str, user_name: Optional[str] = None):
    """Guarda o actualiza preferencias aprendidas del usuario."""
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
    """Obtiene la lista de preferencias conocidas del usuario."""
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


def save_message(chat_id: int, role: str, text: str):
    """Guarda un mensaje en la base de datos permanente."""
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
    """Carga los mensajes más recientes de la base de datos."""
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
    """Borra el historial de mensajes de la conversación actual (mantiene las preferencias aprendidas)."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM message_history WHERE chat_id = ?", (chat_id,))
        conn.commit()


def clear_all(chat_id: int):
    """Borra absolutamente todo: historial y preferencias del usuario."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM message_history WHERE chat_id = ?", (chat_id,))
        cursor.execute("DELETE FROM user_preferences WHERE chat_id = ?", (chat_id,))
        conn.commit()
