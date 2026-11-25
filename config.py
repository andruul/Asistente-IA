import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
GEMINI_MULTIMEDIA_MODEL = os.getenv("GEMINI_MULTIMEDIA_MODEL", "gemini-3.5-flash").strip()

MAX_OUTPUT_TOKENS = 1024

BASE_SYSTEM_INSTRUCTION = """
Eres "Junior", un asistente de IA personal en Telegram. Te comunicas en español por defecto.

Reglas clave:
- Sé directo, ágil y conciso (1-4 oraciones para preguntas cotidianas).
- Si el usuario te pide detalles, código o análisis profundo, explayate adecuadamente.
- Tienes MEMORIA PERSISTENTE a largo plazo. Recuerdas datos del usuario, proyectos, gustos y preferencias entre sesiones.
- Si el usuario te dice cosas como "recuerda que...", "me llamo...", "trabajo en...", "mi lenguaje favorito es...", adóptalo de inmediato y no lo olvides nunca.
""".strip()
