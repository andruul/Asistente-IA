import datetime
import logging
from typing import Dict, List, Optional

import pytz
from telegram import Update
from telegram.constants import ParseMode, ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from google import genai
from google.genai import types

from config import (
    TELEGRAM_BOT_TOKEN,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GEMINI_MULTIMEDIA_MODEL,
    DEFAULT_TIMEZONE,
    BASE_SYSTEM_INSTRUCTION,
    MAX_OUTPUT_TOKENS,
)
import database

# ─────────────────────── Logging ───────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─────────────────────── Base de Datos ───────────────────────
database.init_db()

# ─────────────────────── Gemini Client ───────────────────────
client: genai.Client | None = None
if GEMINI_API_KEY:
    client = genai.Client(api_key=GEMINI_API_KEY)
else:
    logger.warning("GEMINI_API_KEY no configurada en .env")

# Historial en RAM (chat_id -> List[types.Content])
histories: Dict[int, List[types.Content]] = {}
MAX_HISTORY_TURNS = 20

# Referencia a la aplicación de Telegram para programar Jobs
app_instance = None


def get_current_time_str() -> str:
    tz = pytz.timezone(DEFAULT_TIMEZONE)
    now = datetime.datetime.now(tz)
    return now.strftime("%Y-%m-%d %H:%M:%S (%A)")


# ─────────────────────── Callback de Recordatorios ───────────────────────
async def reminder_callback(context: ContextTypes.DEFAULT_TYPE):
    """Función que se ejecuta cuando se cumple el tiempo de un recordatorio."""
    job = context.job
    data = job.data
    chat_id = data["chat_id"]
    text = data["text"]
    reminder_id = data.get("reminder_id")

    if reminder_id:
        database.mark_reminder_completed(reminder_id)

    message = (
        f"⏰ *¡RECORDATORIO!* 🔔\n\n"
        f"📌 _{text}_\n\n"
        f"_(Programado previamente con Junior)_"
    )
    try:
        await context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Error enviando recordatorio a {chat_id}: {e}")


def schedule_reminder_job(chat_id: int, text: str, target_dt: datetime.datetime, reminder_id: int):
    """Programa la alarma en el JobQueue de Telegram."""
    if not app_instance or not app_instance.job_queue:
        logger.error("JobQueue no disponible.")
        return

    now = datetime.datetime.now(target_dt.tzinfo)
    delay_seconds = (target_dt - now).total_seconds()
    if delay_seconds <= 0:
        delay_seconds = 1  # Disparar de inmediato si ya pasó

    app_instance.job_queue.run_once(
        reminder_callback,
        when=delay_seconds,
        data={"chat_id": chat_id, "text": text, "reminder_id": reminder_id},
        name=f"reminder_{reminder_id}",
    )


# ─────────────────────── Herramientas para Gemini ───────────────────────
def agendar_recordatorio(minutos_desde_ahora: int, texto_recordatorio: str, chat_id: int = 0) -> str:
    """Programa una alarma o recordatorio que se enviará automáticamente en X minutos."""
    try:
        tz = pytz.timezone(DEFAULT_TIMEZONE)
        now = datetime.datetime.now(tz)
        target = now + datetime.timedelta(minutes=int(minutos_desde_ahora))
        target_str = target.strftime("%Y-%m-%d %H:%M:%S")

        reminder_id = database.add_reminder(chat_id, texto_recordatorio, target_str)
        schedule_reminder_job(chat_id, texto_recordatorio, target, reminder_id)

        hora_aviso = target.strftime("%I:%M %p")
        return f"Recordatorio programado con éxito para las {hora_aviso} ({minutos_desde_ahora} minutos). Texto: '{texto_recordatorio}'."
    except Exception as e:
        return f"Error al programar recordatorio: {str(e)}"


# ─────────────────────── Memoria & Historial ───────────────────────
def get_system_instruction(chat_id: int) -> str:
    prefs = database.get_user_preferences(chat_id)
    time_info = f"[FECHA Y HORA ACTUAL DEL USUARIO]: {get_current_time_str()} (Zona: {DEFAULT_TIMEZONE})"
    
    prefs_text = ""
    if prefs:
        prefs_formatted = "\n".join([f"- {p}" for p in prefs])
        prefs_text = f"\n[MEMORIA PERMANENTE DEL USUARIO]:\n{prefs_formatted}\n"

    return f"""{BASE_SYSTEM_INSTRUCTION}

{time_info}
{prefs_text}
Cuando el usuario te pida recordar algo más tarde (ej: 'acuérdame de tomar agua en 30 minutos', 'en 2 horas avísame de llamar a Juan'), calcula los minutos necesarios desde la hora actual y ejecuta la herramienta adecuada.
""".strip()


def get_history(chat_id: int) -> List[types.Content]:
    if chat_id not in histories:
        saved_msgs = database.load_recent_messages(chat_id, limit=MAX_HISTORY_TURNS * 2)
        history = []
        for msg in saved_msgs:
            history.append(types.Content(
                role=msg["role"],
                parts=[types.Part.from_text(text=msg["text"])]
            ))
        histories[chat_id] = history
    return histories[chat_id]


def trim_history(chat_id: int):
    history = histories.get(chat_id, [])
    max_entries = MAX_HISTORY_TURNS * 2
    if len(history) > max_entries:
        histories[chat_id] = history[-max_entries:]


def add_to_history(chat_id: int, role: str, parts: list, text_to_save: Optional[str] = None):
    history = get_history(chat_id)
    history.append(types.Content(role=role, parts=parts))
    trim_history(chat_id)
    if text_to_save:
        database.save_message(chat_id, role, text_to_save)


async def generate_response(chat_id: int, user_parts: list, raw_user_text: str = "", use_multimedia_model: bool = False) -> str:
    if not client:
        return "⚠️ Error: GEMINI_API_KEY no configurada."

    add_to_history(chat_id, "user", user_parts, text_to_save=raw_user_text)
    model = GEMINI_MULTIMEDIA_MODEL if use_multimedia_model else GEMINI_MODEL

    # Wrapper que inyecta automáticamente el chat_id
    def programar_recordatorio(minutos_desde_ahora: int, texto_recordatorio: str) -> str:
        return agendar_recordatorio(minutos_desde_ahora, texto_recordatorio, chat_id=chat_id)

    tools = [programar_recordatorio] if not use_multimedia_model else None

    config = types.GenerateContentConfig(
        system_instruction=get_system_instruction(chat_id),
        temperature=0.6,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        tools=tools,
    )

    try:
        response = await client.aio.models.generate_content(
            model=model,
            contents=get_history(chat_id),
            config=config,
        )

        reply = response.text or "Entendido."
        add_to_history(chat_id, "model", [types.Part.from_text(text=reply)], text_to_save=reply)
        return reply

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error Gemini: {error_msg}", exc_info=True)

        history = get_history(chat_id)
        if history and history[-1].role == "user":
            history.pop()

        if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
            return "⏳ Muchas solicitudes. Intenta en unos segundos."
        elif "503" in error_msg or "UNAVAILABLE" in error_msg:
            return "🔄 Servicio saturado temporalmente. Intenta en un momento."
        else:
            return f"❌ Error: {error_msg[:200]}"


async def send_reply(update: Update, text: str):
    if len(text) > 4096:
        for i in range(0, len(text), 4096):
            chunk = text[i:i + 4096]
            try:
                await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
            except Exception:
                await update.message.reply_text(chunk)
    else:
        try:
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await update.message.reply_text(text)


# ─────────────────────── Comandos ───────────────────────
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name if update.effective_user else "Amigo"
    chat_id = update.effective_chat.id
    database.save_user_preference(chat_id, f"El nombre del usuario es {user_name}", user_name)

    await update.message.reply_text(
        f"👋 ¡Hola, {user_name}! Soy *Junior*, tu asistente de IA personal.\n\n"
        "✨ *Capacidades nuevas:*\n"
        "⏰ *Recordatorios automáticos:* Pídeme en lenguaje natural:\n"
        "_\"Recuérdame sacar la basura en 20 minutos\"_\n"
        "_\"Avísame a las 5:30 pm de llamar al médico\"_\n\n"
        "Comandos útiles:\n"
        "• /reminders - Ver tus recordatorios pendientes.\n"
        "• /memory - Ver lo que recuerdo sobre ti.\n"
        "• /clear - Limpiar la charla actual.\n"
        "• /forget_all - Borrar toda mi memoria.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def reminders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra la lista de recordatorios activos del usuario."""
    chat_id = update.effective_chat.id
    active = database.get_active_reminders(chat_id)
    if not active:
        await update.message.reply_text("🔔 No tienes recordatorios pendientes por ahora.", parse_mode=ParseMode.MARKDOWN)
        return

    lines = []
    for r in active:
        lines.append(f"• ID `{r['id']}`: *{r['text']}* ➔ _{r['remind_at']}_")

    msg = "⏰ *Tus Recordatorios Pendientes:*\n\n" + "\n".join(lines) + "\n\n_Para cancelar uno, escribe /delete_reminder <id>_"
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


async def delete_reminder_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancela un recordatorio por su ID."""
    chat_id = update.effective_chat.id
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("💡 Uso: `/delete_reminder <id>` (usa `/reminders` para ver los IDs)", parse_mode=ParseMode.MARKDOWN)
        return

    rem_id = int(args[0])
    if database.delete_reminder(rem_id, chat_id):
        # Cancelar el job en cola si existe
        if app_instance and app_instance.job_queue:
            jobs = app_instance.job_queue.get_jobs_by_name(f"reminder_{rem_id}")
            for j in jobs:
                j.schedule_removal()
        await update.message.reply_text(f"✅ Recordatorio #{rem_id} cancelado con éxito.")
    else:
        await update.message.reply_text(f"❌ No encontré ningún recordatorio pendiente con el ID #{rem_id}.")


async def memory_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    prefs = database.get_user_preferences(chat_id)
    if not prefs:
        await update.message.reply_text("🤔 Aún no tengo datos guardados sobre ti.", parse_mode=ParseMode.MARKDOWN)
        return
    formatted = "\n".join([f"• {p}" for p in prefs])
    await update.message.reply_text(f"🧠 *Esto es lo que recuerdo sobre ti:*\n\n{formatted}", parse_mode=ParseMode.MARKDOWN)


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    histories.pop(chat_id, None)
    database.clear_history(chat_id)
    await update.message.reply_text("🧹 Charla actual limpiada. (Tus preferencias y recordatorios se conservan).")


async def forget_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    histories.pop(chat_id, None)
    database.clear_all(chat_id)
    await update.message.reply_text("🗑️ He borrado toda mi memoria, preferencias y recordatorios.")


# ─────────────────────── Manejadores de Mensajes ───────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    chat_id = update.effective_chat.id
    user_text = update.message.text.strip()
    if not user_text:
        return

    lower = user_text.lower()
    if lower.startswith(("recuerda que", "ten en cuenta que", "anota que", "mi nombre es", "me llamo")):
        fact = user_text
        for prefix in ["recuerda que", "ten en cuenta que", "anota que"]:
            if lower.startswith(prefix):
                fact = user_text[len(prefix):].strip(": ,")
                break
        database.save_user_preference(chat_id, fact)

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    reply = await generate_response(chat_id, [types.Part.from_text(text=user_text)], raw_user_text=user_text)
    await send_reply(update, reply)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.photo:
        return
    chat_id = update.effective_chat.id
    caption = update.message.caption or "Analiza esta imagen."
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    try:
        photo_file = await update.message.photo[-1].get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        parts = [
            types.Part.from_bytes(data=bytes(photo_bytes), mime_type="image/jpeg"),
            types.Part.from_text(text=caption),
        ]
        reply = await generate_response(chat_id, parts, raw_user_text=f"[Foto]: {caption}")
        await send_reply(update, reply)
    except Exception as e:
        logger.error(f"Error foto: {e}")
        await update.message.reply_text(f"❌ Error al procesar imagen: {str(e)[:150]}")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.voice:
        return
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    try:
        voice = update.message.voice
        voice_file = await voice.get_file()
        voice_bytes = await voice_file.download_as_bytearray()
        parts = [
            types.Part.from_bytes(data=bytes(voice_bytes), mime_type="audio/ogg"),
            types.Part.from_text(text="Escucha la nota de voz y responde a lo que dice o pide de forma natural."),
        ]
        reply = await generate_response(chat_id, parts, raw_user_text="[Nota de voz]", use_multimedia_model=True)
        await send_reply(update, reply)
    except Exception as e:
        logger.error(f"Error voz: {e}")
        await update.message.reply_text(f"❌ Error en audio: {str(e)[:150]}")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.document:
        return
    chat_id = update.effective_chat.id
    doc = update.message.document
    caption = update.message.caption or f"Analiza: {doc.file_name or 'archivo'}."
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    try:
        doc_file = await doc.get_file()
        doc_bytes = await doc_file.download_as_bytearray()
        parts = [
            types.Part.from_bytes(data=bytes(doc_bytes), mime_type=doc.mime_type or "application/pdf"),
            types.Part.from_text(text=caption),
        ]
        reply = await generate_response(chat_id, parts, raw_user_text=f"[Doc]: {caption}", use_multimedia_model=True)
        await send_reply(update, reply)
    except Exception as e:
        logger.error(f"Error doc: {e}")
        await update.message.reply_text(f"❌ Error en documento: {str(e)[:150]}")


# ─────────────────────── Restaurar Recordatorios ───────────────────────
def restore_pending_reminders():
    """Restaura en JobQueue los recordatorios pendientes tras un reinicio del bot."""
    tz = pytz.timezone(DEFAULT_TIMEZONE)
    pending = database.get_all_pending_reminders()
    count = 0
    for r in pending:
        try:
            target_dt = datetime.datetime.strptime(r["remind_at"], "%Y-%m-%d %H:%M:%S")
            target_dt = tz.localize(target_dt)
            schedule_reminder_job(r["chat_id"], r["text"], target_dt, r["id"])
            count += 1
        except Exception as e:
            logger.error(f"Error restaurando recordatorio #{r['id']}: {e}")
    if count > 0:
        print(f"⏰ Restaurados {count} recordatorios pendientes desde SQLite.")


# ─────────────────────── Main ───────────────────────
def main():
    global app_instance

    if not TELEGRAM_BOT_TOKEN:
        print("⚠️ Configura TELEGRAM_BOT_TOKEN en el archivo .env")
        return

    print("🚀 Iniciando Asistente Junior con Recordatorios y Memoria...")
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app_instance = app

    # Comandos
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("reminders", reminders_command))
    app.add_handler(CommandHandler("delete_reminder", delete_reminder_command))
    app.add_handler(CommandHandler("memory", memory_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("forget_all", forget_all_command))

    # Multimedia
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # Texto
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Restaurar recordatorios pendientes de la BD
    restore_pending_reminders()

    print("✅ Bot activo con Alarmas y Recordatorios Automáticos.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
