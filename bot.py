import logging
from typing import Dict, List, Optional

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

# ─────────────────────── MIME Mapping ───────────────────────
MIME_MAP = {
    "audio/ogg": "audio/ogg",
    "audio/oga": "audio/ogg",
    "audio/mp4": "audio/mp4",
    "audio/mpeg": "audio/mpeg",
    "audio/mp3": "audio/mp3",
    "audio/wav": "audio/wav",
    "video/mp4": "video/mp4",
    "image/jpeg": "image/jpeg",
    "image/png": "image/png",
    "image/webp": "image/webp",
    "application/pdf": "application/pdf",
    "text/plain": "text/plain",
    "application/json": "application/json",
}

EXT_TO_MIME = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".json": "application/json",
    ".py": "text/x-python",
    ".js": "text/javascript",
    ".html": "text/html",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".mp4": "video/mp4",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def resolve_mime(telegram_mime: Optional[str], filename: Optional[str]) -> Optional[str]:
    if telegram_mime and telegram_mime in MIME_MAP:
        return MIME_MAP[telegram_mime]
    if filename:
        import os
        ext = os.path.splitext(filename)[1].lower()
        if ext in EXT_TO_MIME:
            return EXT_TO_MIME[ext]
    return telegram_mime


# ─────────────────────── Gestión de Memoria e Historial ───────────────────────
def get_system_instruction(chat_id: int) -> str:
    """Construye las instrucciones del sistema inyectando el perfil y preferencias del usuario."""
    prefs = database.get_user_preferences(chat_id)
    if not prefs:
        return BASE_SYSTEM_INSTRUCTION
    
    prefs_formatted = "\n".join([f"- {p}" for p in prefs])
    return f"""{BASE_SYSTEM_INSTRUCTION}

[MEMORIA PERMANENTE DEL USUARIO]:
{prefs_formatted}
Usa esta información para personalizar tus respuestas sin necesidad de volver a preguntarla.
""".strip()


def get_history(chat_id: int) -> List[types.Content]:
    """Recupera el historial activo o lo carga de la base de datos persistente."""
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
    
    # Guardar en SQLite permanente
    if text_to_save:
        database.save_message(chat_id, role, text_to_save)


async def generate_response(chat_id: int, user_parts: list, raw_user_text: str = "", use_multimedia_model: bool = False) -> str:
    """Envía mensaje a Gemini con memoria persistente y devuelve respuesta."""
    if not client:
        return "⚠️ Error: GEMINI_API_KEY no configurada."

    add_to_history(chat_id, "user", user_parts, text_to_save=raw_user_text)

    model = GEMINI_MULTIMEDIA_MODEL if use_multimedia_model else GEMINI_MODEL

    config = types.GenerateContentConfig(
        system_instruction=get_system_instruction(chat_id),
        temperature=0.6,
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )

    try:
        response = await client.aio.models.generate_content(
            model=model,
            contents=get_history(chat_id),
            config=config,
        )

        reply = response.text or "No pude generar una respuesta."
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
    """Envía la respuesta al usuario dividiendo mensajes largos si exceden el límite de Telegram."""
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
    
    # Auto-recordar el nombre del usuario
    database.save_user_preference(chat_id, f"El nombre del usuario es {user_name}", user_name)

    await update.message.reply_text(
        f"👋 ¡Hola, {user_name}! Soy *Junior*, tu asistente de IA.\n\n"
        "🧠 *Memoria activa:* Ahora recuerdo nuestras conversaciones, tus gustos y preferencias para siempre, incluso si el bot se reinicia.\n\n"
        "Comandos útiles:\n"
        "• /remember <dato> - Guárdame un dato o preferencia explícita.\n"
        "• /memory - Ver lo que sé sobre ti.\n"
        "• /clear - Limpiar la charla actual (mantiene tus preferencias).\n"
        "• /forget_all - Borrar absolutamente toda mi memoria sobre ti.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def remember_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Permite al usuario guardar un dato o preferencia explícita."""
    chat_id = update.effective_chat.id
    text = " ".join(context.args).strip() if context.args else ""
    if not text:
        await update.message.reply_text("💡 Uso: `/remember Me gusta que me hables de usted` o `/remember Trabajo en desarrollo web`", parse_mode=ParseMode.MARKDOWN)
        return
    
    database.save_user_preference(chat_id, text)
    await update.message.reply_text(f"✅ Anotado en mi memoria permanente:\n_\"{text}\"_", parse_mode=ParseMode.MARKDOWN)


async def memory_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra lo que el bot recuerda del usuario."""
    chat_id = update.effective_chat.id
    prefs = database.get_user_preferences(chat_id)
    if not prefs:
        await update.message.reply_text("🤔 Aún no tengo preferencias registradas sobre ti. Cuéntame sobre ti o usa `/remember <dato>`.", parse_mode=ParseMode.MARKDOWN)
        return
    
    formatted = "\n".join([f"• {p}" for p in prefs])
    await update.message.reply_text(f"🧠 *Esto es lo que recuerdo sobre ti:*\n\n{formatted}", parse_mode=ParseMode.MARKDOWN)


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    histories.pop(chat_id, None)
    database.clear_history(chat_id)
    await update.message.reply_text("🧹 Charla actual limpiada. (Tus preferencias y perfil se conservan).")


async def forget_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    histories.pop(chat_id, None)
    database.clear_all(chat_id)
    await update.message.reply_text("🗑️ He borrado toda mi memoria y preferencias sobre ti. Comenzamos desde cero.")


# ─────────────────────── Manejadores de Mensajes ───────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    chat_id = update.effective_chat.id
    user_text = update.message.text.strip()
    if not user_text:
        return

    # Auto-detección de frases clave como "recuerda que..." o "mi ... es ..."
    lower = user_text.lower()
    if lower.startswith(("recuerda que", "ten en cuenta que", "anota que", "mi nombre es", "me llamo")):
        fact = user_text
        for prefix in ["recuerda que", "ten en cuenta que", "anota que"]:
            if lower.startswith(prefix):
                fact = user_text[len(prefix):].strip(": ,")
                break
        database.save_user_preference(chat_id, fact)

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    reply = await generate_response(
        chat_id,
        [types.Part.from_text(text=user_text)],
        raw_user_text=user_text,
    )
    await send_reply(update, reply)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.photo:
        return
    chat_id = update.effective_chat.id
    caption = update.message.caption or "Analiza esta imagen detalladamente."

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    try:
        photo_file = await update.message.photo[-1].get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        parts = [
            types.Part.from_bytes(data=bytes(photo_bytes), mime_type="image/jpeg"),
            types.Part.from_text(text=caption),
        ]
        reply = await generate_response(chat_id, parts, raw_user_text=f"[Foto enviada]: {caption}")
        await send_reply(update, reply)
    except Exception as e:
        logger.error(f"Error foto: {e}", exc_info=True)
        await update.message.reply_text(f"❌ No pude procesar la foto: {str(e)[:150]}")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.voice:
        return
    chat_id = update.effective_chat.id

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    try:
        voice = update.message.voice
        voice_file = await voice.get_file()
        voice_bytes = await voice_file.download_as_bytearray()
        mime = resolve_mime(voice.mime_type, None) or "audio/ogg"

        parts = [
            types.Part.from_bytes(data=bytes(voice_bytes), mime_type=mime),
            types.Part.from_text(text="Escucha la nota de voz, responde a lo que pide o comenta de forma natural."),
        ]
        reply = await generate_response(chat_id, parts, raw_user_text="[Nota de voz]", use_multimedia_model=True)
        await send_reply(update, reply)
    except Exception as e:
        logger.error(f"Error voz: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Error en audio: {str(e)[:150]}")


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.audio:
        return
    chat_id = update.effective_chat.id
    caption = update.message.caption or "Escucha este audio y resume o responde según su contenido."

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    try:
        audio = update.message.audio
        audio_file = await audio.get_file()
        audio_bytes = await audio_file.download_as_bytearray()
        mime = resolve_mime(audio.mime_type, audio.file_name) or "audio/mpeg"

        parts = [
            types.Part.from_bytes(data=bytes(audio_bytes), mime_type=mime),
            types.Part.from_text(text=caption),
        ]
        reply = await generate_response(chat_id, parts, raw_user_text=f"[Audio]: {caption}", use_multimedia_model=True)
        await send_reply(update, reply)
    except Exception as e:
        logger.error(f"Error audio: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Error en audio: {str(e)[:150]}")


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.video:
        return
    chat_id = update.effective_chat.id
    caption = update.message.caption or "Describe lo que ves y escuchas en el video."

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    try:
        video = update.message.video
        if video.file_size and video.file_size > 20 * 1024 * 1024:
            await update.message.reply_text("⚠️ El video supera el límite de 20MB.")
            return

        video_file = await video.get_file()
        video_bytes = await video_file.download_as_bytearray()
        mime = resolve_mime(video.mime_type, video.file_name) or "video/mp4"

        parts = [
            types.Part.from_bytes(data=bytes(video_bytes), mime_type=mime),
            types.Part.from_text(text=caption),
        ]
        reply = await generate_response(chat_id, parts, raw_user_text=f"[Video]: {caption}", use_multimedia_model=True)
        await send_reply(update, reply)
    except Exception as e:
        logger.error(f"Error video: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Error en video: {str(e)[:150]}")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.document:
        return
    chat_id = update.effective_chat.id
    doc = update.message.document
    caption = update.message.caption or f"Analiza el documento adjunto: {doc.file_name or 'archivo'}."

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    try:
        doc_file = await doc.get_file()
        doc_bytes = await doc_file.download_as_bytearray()
        mime = resolve_mime(doc.mime_type, doc.file_name) or "application/pdf"

        parts = [
            types.Part.from_bytes(data=bytes(doc_bytes), mime_type=mime),
            types.Part.from_text(text=caption),
        ]
        reply = await generate_response(chat_id, parts, raw_user_text=f"[Doc: {doc.file_name}]: {caption}", use_multimedia_model=True)
        await send_reply(update, reply)
    except Exception as e:
        logger.error(f"Error documento: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Error en documento: {str(e)[:150]}")


# ─────────────────────── Main ───────────────────────
def main():
    if not TELEGRAM_BOT_TOKEN:
        print("⚠️ Configura TELEGRAM_BOT_TOKEN en el archivo .env")
        return

    print("🚀 Iniciando Asistente Junior con Memoria Persistente SQLite...")
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Comandos
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("remember", remember_command))
    app.add_handler(CommandHandler("memory", memory_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("forget_all", forget_all_command))

    # Multimedia
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # Texto
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ Bot activo con memoria a largo plazo.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
