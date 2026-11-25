import logging
from typing import Dict, List

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
    SYSTEM_INSTRUCTION,
    MAX_OUTPUT_TOKENS,
)

# ─────────────────────── Logging ───────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─────────────────────── Gemini Client ───────────────────────
client: genai.Client | None = None
if GEMINI_API_KEY:
    client = genai.Client(api_key=GEMINI_API_KEY)
else:
    logger.warning("GEMINI_API_KEY no configurada en .env")

# Historial de conversación por chat_id
histories: Dict[int, List[types.Content]] = {}
MAX_HISTORY_TURNS = 20

# ─────────────────────── Mapeo de MIME types ───────────────────────
# Mapeo de extensiones/tipos de Telegram a MIME types que Gemini acepta
MIME_MAP = {
    # Audio
    "audio/ogg": "audio/ogg",
    "audio/oga": "audio/ogg",
    "audio/mp4": "audio/mp4",
    "audio/mpeg": "audio/mpeg",
    "audio/mp3": "audio/mp3",
    "audio/wav": "audio/wav",
    "audio/x-wav": "audio/wav",
    "audio/webm": "audio/webm",
    "audio/aac": "audio/aac",
    "audio/flac": "audio/flac",
    # Video
    "video/mp4": "video/mp4",
    "video/mpeg": "video/mpeg",
    "video/webm": "video/webm",
    "video/quicktime": "video/mp4",
    "video/x-matroska": "video/mp4",
    "video/3gpp": "video/3gpp",
    # Imágenes
    "image/jpeg": "image/jpeg",
    "image/png": "image/png",
    "image/gif": "image/gif",
    "image/webp": "image/webp",
    "image/heic": "image/heic",
    "image/heif": "image/heif",
    # Documentos
    "application/pdf": "application/pdf",
    "text/plain": "text/plain",
    "text/csv": "text/csv",
    "text/html": "text/html",
    "text/xml": "text/xml",
    "application/json": "application/json",
    "text/x-python": "text/x-python",
    "text/javascript": "text/javascript",
    "text/css": "text/css",
    "text/markdown": "text/markdown",
}

# Extensiones a MIME para cuando Telegram no reporta mime_type
EXT_TO_MIME = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".json": "application/json",
    ".py": "text/x-python",
    ".js": "text/javascript",
    ".ts": "text/javascript",
    ".html": "text/html",
    ".htm": "text/html",
    ".css": "text/css",
    ".md": "text/markdown",
    ".xml": "text/xml",
    ".java": "text/plain",
    ".c": "text/plain",
    ".cpp": "text/plain",
    ".h": "text/plain",
    ".rs": "text/plain",
    ".go": "text/plain",
    ".rb": "text/plain",
    ".php": "text/plain",
    ".sql": "text/plain",
    ".sh": "text/plain",
    ".bat": "text/plain",
    ".yaml": "text/plain",
    ".yml": "text/plain",
    ".toml": "text/plain",
    ".ini": "text/plain",
    ".cfg": "text/plain",
    ".log": "text/plain",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def resolve_mime(telegram_mime: str | None, filename: str | None) -> str | None:
    """Resuelve el MIME type correcto para Gemini."""
    if telegram_mime and telegram_mime in MIME_MAP:
        return MIME_MAP[telegram_mime]

    if filename:
        import os
        ext = os.path.splitext(filename)[1].lower()
        if ext in EXT_TO_MIME:
            return EXT_TO_MIME[ext]

    # Fallback: si Telegram nos da algo, pasarlo directamente
    if telegram_mime:
        return telegram_mime

    return None


# ─────────────────────── Helpers ───────────────────────
def get_history(chat_id: int) -> List[types.Content]:
    if chat_id not in histories:
        histories[chat_id] = []
    return histories[chat_id]


def trim_history(chat_id: int):
    history = histories.get(chat_id, [])
    max_entries = MAX_HISTORY_TURNS * 2
    if len(history) > max_entries:
        histories[chat_id] = history[-max_entries:]


def add_to_history(chat_id: int, role: str, parts: list):
    history = get_history(chat_id)
    history.append(types.Content(role=role, parts=parts))
    trim_history(chat_id)


async def generate_response(chat_id: int, user_parts: list, use_multimedia_model: bool = False) -> str:
    """Envía mensaje a Gemini con historial y devuelve la respuesta."""
    if not client:
        return "⚠️ Error: GEMINI_API_KEY no configurada."

    add_to_history(chat_id, "user", user_parts)

    model = GEMINI_MULTIMEDIA_MODEL if use_multimedia_model else GEMINI_MODEL

    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
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
        add_to_history(chat_id, "model", [types.Part.from_text(text=reply)])
        return reply

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error Gemini: {error_msg}", exc_info=True)

        # Quitar el mensaje fallido del historial
        history = get_history(chat_id)
        if history and history[-1].role == "user":
            history.pop()

        if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
            return "⏳ Muchas solicitudes. Intenta en unos segundos."
        elif "503" in error_msg or "UNAVAILABLE" in error_msg:
            return "🔄 Servicio saturado temporalmente. Intenta en un momento."
        elif "400" in error_msg:
            return "⚠️ No pude procesar este contenido. Puede que el formato no sea compatible."
        else:
            return f"❌ Error: {error_msg[:200]}"


async def send_reply(update: Update, text: str):
    """Envía respuesta con fallback de formato."""
    # Telegram tiene un límite de 4096 caracteres por mensaje
    if len(text) > 4096:
        # Dividir en chunks
        for i in range(0, len(text), 4096):
            chunk = text[i:i + 4096]
            try:
                await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
            except Exception:
                try:
                    await update.message.reply_text(chunk)
                except Exception as e:
                    logger.error(f"Error enviando chunk: {e}")
    else:
        try:
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            try:
                await update.message.reply_text(text)
            except Exception as e:
                logger.error(f"Error enviando mensaje: {e}")


# ─────────────────────── Command Handlers ───────────────────────
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name if update.effective_user else "Amigo"
    chat_id = update.effective_chat.id
    histories.pop(chat_id, None)
    await update.message.reply_text(
        f"👋 ¡Hola, {user_name}! Soy *Junior*, tu asistente de IA.\n\n"
        "Puedo procesar *todo* lo que me envíes:\n"
        "💬 Texto • 📸 Fotos • 🎤 Audios y notas de voz\n"
        "🎬 Videos • 📄 Documentos/PDFs • 📍 Ubicaciones\n\n"
        "🧹 /clear → reiniciar conversación",
        parse_mode=ParseMode.MARKDOWN,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💡 *Comandos:*\n"
        "• /start → Reiniciar y ver bienvenida\n"
        "• /clear → Borrar memoria\n"
        "• /help → Este mensaje\n\n"
        "*Medios soportados:*\n"
        "📝 Texto y preguntas\n"
        "📸 Fotos (con o sin descripción)\n"
        "🎤 Notas de voz y audios\n"
        "🎬 Videos y video-notas (círculos)\n"
        "📄 Documentos: PDF, código, texto, CSV\n"
        "📍 Ubicaciones\n"
        "😄 Stickers",
        parse_mode=ParseMode.MARKDOWN,
    )


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    histories.pop(chat_id, None)
    await update.message.reply_text("🧹 Memoria reiniciada.")


# ─────────────────────── Message Handlers ───────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mensajes de texto."""
    if not update.message or not update.message.text:
        return
    chat_id = update.effective_chat.id
    user_text = update.message.text.strip()
    if not user_text:
        return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    reply = await generate_response(chat_id, [types.Part.from_text(text=user_text)])
    await send_reply(update, reply)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fotos e imágenes."""
    if not update.message or not update.message.photo:
        return
    chat_id = update.effective_chat.id
    caption = update.message.caption or "Describe brevemente qué hay en esta imagen."

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    try:
        photo_file = await update.message.photo[-1].get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        parts = [
            types.Part.from_bytes(data=bytes(photo_bytes), mime_type="image/jpeg"),
            types.Part.from_text(text=caption),
        ]
        reply = await generate_response(chat_id, parts)
        await send_reply(update, reply)
    except Exception as e:
        logger.error(f"Error foto: {e}", exc_info=True)
        await update.message.reply_text(f"❌ No pude procesar la foto: {str(e)[:150]}")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Notas de voz."""
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
            types.Part.from_text(text="El usuario envió una nota de voz. Escúchala, interpreta lo que dice y responde de forma natural a lo que pide o pregunta."),
        ]
        reply = await generate_response(chat_id, parts, use_multimedia_model=True)
        await send_reply(update, reply)
    except Exception as e:
        logger.error(f"Error voz: {e}", exc_info=True)
        await update.message.reply_text(f"❌ No pude procesar el audio: {str(e)[:150]}")


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Archivos de audio (mp3, música, podcasts, etc.)."""
    if not update.message or not update.message.audio:
        return
    chat_id = update.effective_chat.id
    caption = update.message.caption or "El usuario envió un archivo de audio. Escúchalo y describe su contenido o responde a lo que dice."

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    try:
        audio = update.message.audio
        audio_file = await audio.get_file()
        audio_bytes = await audio_file.download_as_bytearray()

        mime = resolve_mime(audio.mime_type, audio.file_name) or "audio/mpeg"

        # Info adicional del audio
        info_parts = []
        if audio.title:
            info_parts.append(f"Título: {audio.title}")
        if audio.performer:
            info_parts.append(f"Artista: {audio.performer}")
        if audio.duration:
            info_parts.append(f"Duración: {audio.duration}s")
        extra_info = " | ".join(info_parts) if info_parts else ""

        prompt = caption
        if extra_info:
            prompt = f"{caption}\n[Info: {extra_info}]"

        parts = [
            types.Part.from_bytes(data=bytes(audio_bytes), mime_type=mime),
            types.Part.from_text(text=prompt),
        ]
        reply = await generate_response(chat_id, parts, use_multimedia_model=True)
        await send_reply(update, reply)
    except Exception as e:
        logger.error(f"Error audio: {e}", exc_info=True)
        await update.message.reply_text(f"❌ No pude procesar el audio: {str(e)[:150]}")


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Videos."""
    if not update.message or not update.message.video:
        return
    chat_id = update.effective_chat.id
    caption = update.message.caption or "El usuario envió un video. Describe brevemente lo que ves y escuchas."

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    try:
        video = update.message.video
        # Verificar que no sea demasiado grande (límite 20MB para API gratuita de Telegram)
        if video.file_size and video.file_size > 20 * 1024 * 1024:
            await update.message.reply_text("⚠️ El video es demasiado grande. El límite es ~20MB.")
            return

        video_file = await video.get_file()
        video_bytes = await video_file.download_as_bytearray()

        mime = resolve_mime(video.mime_type, video.file_name) or "video/mp4"

        parts = [
            types.Part.from_bytes(data=bytes(video_bytes), mime_type=mime),
            types.Part.from_text(text=caption),
        ]
        reply = await generate_response(chat_id, parts, use_multimedia_model=True)
        await send_reply(update, reply)
    except Exception as e:
        logger.error(f"Error video: {e}", exc_info=True)
        await update.message.reply_text(f"❌ No pude procesar el video: {str(e)[:150]}")


async def handle_video_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Video-notas (mensajes de video circulares de Telegram)."""
    if not update.message or not update.message.video_note:
        return
    chat_id = update.effective_chat.id

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    try:
        vn = update.message.video_note
        vn_file = await vn.get_file()
        vn_bytes = await vn_file.download_as_bytearray()

        parts = [
            types.Part.from_bytes(data=bytes(vn_bytes), mime_type="video/mp4"),
            types.Part.from_text(text="El usuario envió un video circular. Mira y escucha lo que dice o muestra, y responde de forma natural."),
        ]
        reply = await generate_response(chat_id, parts, use_multimedia_model=True)
        await send_reply(update, reply)
    except Exception as e:
        logger.error(f"Error video-nota: {e}", exc_info=True)
        await update.message.reply_text(f"❌ No pude procesar el video circular: {str(e)[:150]}")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Documentos: PDFs, código fuente, texto, CSV, imágenes como archivo, etc."""
    if not update.message or not update.message.document:
        return
    chat_id = update.effective_chat.id
    doc = update.message.document
    caption = update.message.caption or ""

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    # Verificar tamaño (Telegram limita a 20MB para bots)
    if doc.file_size and doc.file_size > 20 * 1024 * 1024:
        await update.message.reply_text("⚠️ El archivo es demasiado grande (>20MB).")
        return

    try:
        doc_file = await doc.get_file()
        doc_bytes = await doc_file.download_as_bytearray()

        mime = resolve_mime(doc.mime_type, doc.file_name)

        if not mime:
            # Intentar leer como texto plano
            try:
                text_content = bytes(doc_bytes).decode("utf-8", errors="replace")
                if len(text_content) > 30000:
                    text_content = text_content[:30000] + "\n\n[... truncado por longitud ...]"

                prompt = f"El usuario envió un archivo"
                if doc.file_name:
                    prompt += f" llamado '{doc.file_name}'"
                prompt += f". Contenido:\n\n```\n{text_content}\n```"
                if caption:
                    prompt += f"\n\nInstrucción del usuario: {caption}"
                else:
                    prompt += "\n\nAnaliza y resume su contenido brevemente."

                parts = [types.Part.from_text(text=prompt)]
                reply = await generate_response(chat_id, parts)
                await send_reply(update, reply)
                return
            except Exception:
                await update.message.reply_text("⚠️ No reconozco el formato de este archivo.")
                return

        # Determinar si es multimedia (audio/video/imagen) o documento
        is_multimedia = mime.startswith(("audio/", "video/", "image/"))

        file_info = f" ({doc.file_name})" if doc.file_name else ""
        default_prompt = f"El usuario envió un archivo{file_info}. "
        if mime == "application/pdf":
            default_prompt += "Analiza el PDF y resume su contenido."
        elif mime.startswith("text/") or mime == "application/json":
            default_prompt += "Analiza el contenido del archivo."
        elif mime.startswith("image/"):
            default_prompt += "Describe la imagen."
        elif mime.startswith("audio/"):
            default_prompt += "Escucha y describe el contenido del audio."
        elif mime.startswith("video/"):
            default_prompt += "Mira el video y describe lo que ves."
        else:
            default_prompt += "Analiza su contenido."

        prompt = caption if caption else default_prompt

        parts = [
            types.Part.from_bytes(data=bytes(doc_bytes), mime_type=mime),
            types.Part.from_text(text=prompt),
        ]
        reply = await generate_response(chat_id, parts, use_multimedia_model=is_multimedia)
        await send_reply(update, reply)

    except Exception as e:
        logger.error(f"Error documento: {e}", exc_info=True)
        await update.message.reply_text(f"❌ No pude procesar el archivo: {str(e)[:150]}")


async def handle_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stickers (se envían como imagen a Gemini)."""
    if not update.message or not update.message.sticker:
        return
    chat_id = update.effective_chat.id
    sticker = update.message.sticker

    # Stickers animados/video no se pueden procesar fácilmente
    if sticker.is_animated:
        await update.message.reply_text("😄 ¡Buen sticker animado! No puedo analizarlos aún, pero envíame una foto o imagen y la reviso.")
        return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    try:
        sticker_file = await sticker.get_file()
        sticker_bytes = await sticker_file.download_as_bytearray()

        # Los stickers estáticos de Telegram son WebP
        mime = "image/webp"
        if sticker.is_video:
            mime = "video/webm"

        emoji_text = f" (emoji: {sticker.emoji})" if sticker.emoji else ""
        set_text = f" del pack '{sticker.set_name}'" if sticker.set_name else ""

        parts = [
            types.Part.from_bytes(data=bytes(sticker_bytes), mime_type=mime),
            types.Part.from_text(text=f"El usuario envió un sticker{emoji_text}{set_text}. Describe brevemente qué muestra y reacciona de forma divertida."),
        ]
        reply = await generate_response(chat_id, parts, use_multimedia_model=sticker.is_video)
        await send_reply(update, reply)
    except Exception as e:
        logger.error(f"Error sticker: {e}", exc_info=True)
        await update.message.reply_text("😄 ¡Buen sticker!")


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ubicaciones GPS."""
    if not update.message or not update.message.location:
        return
    chat_id = update.effective_chat.id
    loc = update.message.location

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    prompt = f"El usuario compartió su ubicación GPS: latitud {loc.latitude}, longitud {loc.longitude}. Dime qué zona/lugar corresponde aproximadamente y ofrece información útil sobre ese lugar (clima general, datos interesantes). Sé breve."

    parts = [types.Part.from_text(text=prompt)]
    reply = await generate_response(chat_id, parts)
    await send_reply(update, reply)


async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Contactos compartidos."""
    if not update.message or not update.message.contact:
        return
    chat_id = update.effective_chat.id
    contact = update.message.contact

    info = f"Nombre: {contact.first_name or ''} {contact.last_name or ''}".strip()
    if contact.phone_number:
        info += f"\nTeléfono: {contact.phone_number}"

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    prompt = f"El usuario compartió un contacto:\n{info}\n\nConfirma que recibiste el contacto y pregunta si necesita algo con esa información."
    parts = [types.Part.from_text(text=prompt)]
    reply = await generate_response(chat_id, parts)
    await send_reply(update, reply)


async def handle_unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cualquier tipo de mensaje no soportado."""
    if update.message:
        await update.message.reply_text("🤔 No pude procesar ese tipo de mensaje. Intenta con texto, foto, audio, video, documento o ubicación.")


# ─────────────────────── Main ───────────────────────
def main():
    if not TELEGRAM_BOT_TOKEN:
        print("⚠️ Configura TELEGRAM_BOT_TOKEN en el archivo .env")
        return

    print(f"🚀 Iniciando bot Junior")
    print(f"   Texto: {GEMINI_MODEL}")
    print(f"   Multimedia: {GEMINI_MULTIMEDIA_MODEL}")

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Comandos
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("reset", clear_command))

    # Mensajes multimedia (orden importa: más específico primero)
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.VIDEO_NOTE, handle_video_note))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.Sticker.ALL, handle_sticker))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))

    # Texto plano (al final)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ Bot activo. Esperando mensajes...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
