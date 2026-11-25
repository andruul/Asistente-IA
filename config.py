import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
GEMINI_MULTIMEDIA_MODEL = os.getenv("GEMINI_MULTIMEDIA_MODEL", "gemini-3.5-flash").strip()

# Zona horaria por defecto para los recordatorios
DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "America/Bogota").strip()

MAX_OUTPUT_TOKENS = 1024

BASE_SYSTEM_INSTRUCTION = """
Eres "Junior", un asistente de IA personal y proactivo en Telegram. Te comunicas en español por defecto.

Capacidades principales:
1. Respuestas ágiles, concisas y directas (1-4 oraciones para dudas cotidianas).
2. Memoria persistente de las preferencias, gustos y datos del usuario.
3. Gestión de RECORDATORIOS Y ALERTAS: Tienes acceso a una herramienta interna para agendar recordatorios. Cuando el usuario te pida que le recuerdes algo en X minutos, horas o a una fecha y hora específica, DEBES ejecutar la herramienta 'programar_recordatorio'.
""".strip()
