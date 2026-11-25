import asyncio
import datetime
import io
import logging
import os
from typing import Dict, List, Optional

import pytz
from google import genai
from google.genai import types
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import database
import tools
from config import (
    BASE_SYSTEM_INSTRUCTION,
    DEFAULT_TIMEZONE,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GEMINI_MULTIMEDIA_MODEL,
    MAX_OUTPUT_TOKENS,
    TELEGRAM_BOT_TOKEN,
    TTS_VOICE,
)

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

histories: Dict[int, List[types.Content]] = {}
MAX_HISTORY_TURNS = 20

app_instance: Application | None = None

# ─────────────────────── MIME Mapping ───────────────────────
MIME_MAP = {
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
    "video/mp4": "video/mp4",
    "video/mpeg": "video/mpeg",
    "video/webm": "video/webm",
    "video/quicktime": "video/mp4",
    "video/x-matroska": "video/mp4",
    "video/3gpp": "video/3gpp",
    "image/jpeg": "image/jpeg",
    "image/png": "image/png",
    "image/gif": "image/gif",
    "image/webp": "image/webp",
    "image/heic": "image/heic",
    "image/heif": "image/heif",
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
    ".log": "text/plain",
    ".yaml": "text/plain",
    ".yml": "text/plain",
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


def resolve_mime(telegram_mime: Optional[str], filename: Optional[str]) -> Optional[str]:
    if telegram_mime and telegram_mime in MIME_MAP:
        return MIME_MAP[telegram_mime]
    if filename:
        ext = os.path.splitext(filename)[1].lower()
        if ext in EXT_TO_MIME:
            return EXT_TO_MIME[ext]
    return telegram_mime


def get_user_tz(chat_id: int) -> pytz.BaseTzInfo:
    tz_name = database.get_user_timezone(chat_id, DEFAULT_TIMEZONE)
    try:
        return pytz.timezone(tz_name)
    except Exception:
        return pytz.timezone(DEFAULT_TIMEZONE)


def get_current_time_str(chat_id: int) -> str:
    tz = get_user_tz(chat_id)
    now = datetime.datetime.now(tz)
    return now.strftime("%Y-%m-%d %H:%M:%S (%A)")


# ─────────────────────── Recordatorios (JobQueue) ───────────────────────
async def reminder_callback(context: ContextTypes.DEFAULT_TYPE):
    """Se ejecuta al cumplirse un recordatorio; reprograma si es recurrente."""
    data = context.job.data
    chat_id = data["chat_id"]
    text = data["text"]
    reminder_id = data.get("reminder_id")
    recurrence = data.get("recurrence")

    if reminder_id:
        if recurrence in ("diario", "semanal"):
            tz = get_user_tz(chat_id)
            delta = datetime.timedelta(days=1 if recurrence == "diario" else 7)
            next_dt = datetime.datetime.now(tz) + delta
            database.update_reminder_time(reminder_id, next_dt.strftime("%Y-%m-%d %H:%M:%S"))
            schedule_reminder_job(chat_id, text, next_dt, reminder_id, recurrence)
        else:
            database.mark_reminder_completed(reminder_id)

    rec_tag = f"\n🔁 _Repetición: {recurrence}_" if recurrence else ""
    message = f"⏰ *¡RECORDATORIO!* 🔔\n\n📌 *{text}*{rec_tag}"
    try:
        await context.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        try:
            await context.bot.send_message(chat_id=chat_id, text=f"⏰ ¡RECORDATORIO!\n\n📌 {text}")
        except Exception as e:
            logger.error(f"Error enviando recordatorio a {chat_id}: {e}")


def schedule_reminder_job(
    chat_id: int,
    text: str,
    target_dt: datetime.datetime,
    reminder_id: int,
    recurrence: Optional[str] = None,
):
    if not app_instance or not app_instance.job_queue:
        logger.warning("JobQueue no disponible para agendar en memoria.")
        return

    now = datetime.datetime.now(target_dt.tzinfo)
    delay_seconds = (target_dt - now).total_seconds()
    if delay_seconds <= 0:
        delay_seconds = 1

    app_instance.job_queue.run_once(
        reminder_callback,
        when=delay_seconds,
        data={
            "chat_id": chat_id,
            "text": text,
            "reminder_id": reminder_id,
            "recurrence": recurrence,
        },
        name=f"reminder_{reminder_id}",
    )


def restore_pending_reminders():
    pending = database.get_all_pending_reminders()
    count = 0
    for r in pending:
        try:
            tz = get_user_tz(r["chat_id"])
            target_dt = datetime.datetime.strptime(r["remind_at"], "%Y-%m-%d %H:%M:%S")
            target_dt = tz.localize(target_dt)
            schedule_reminder_job(r["chat_id"], r["text"], target_dt, r["id"], r.get("recurrence"))
            count += 1
        except Exception as e:
            logger.error(f"Error restaurando recordatorio #{r['id']}: {e}")
    if count > 0:
        logger.info(f"⏰ Restaurados {count} recordatorios pendientes.")


# ─────────────────────── Herramientas por Usuario ───────────────────────
def build_user_tools(chat_id: int) -> list:
    """Construye el conjunto de herramientas conectadas al chat_id del usuario."""

    def programar_recordatorio(
        texto_recordatorio: str,
        minutos_desde_ahora: int = 0,
        fecha_hora_exacta: str = "",
        recurrencia: str = "",
    ) -> str:
        """Agenda un recordatorio. Usa minutos_desde_ahora (ej: 15) O fecha_hora_exacta en formato 'YYYY-MM-DD HH:MM'. recurrencia puede ser '', 'diario' o 'semanal'."""
        try:
            tz = get_user_tz(chat_id)
            now = datetime.datetime.now(tz)

            if fecha_hora_exacta.strip():
                naive = datetime.datetime.strptime(fecha_hora_exacta.strip(), "%Y-%m-%d %H:%M")
                target = tz.localize(naive)
                if target <= now:
                    return "La fecha/hora indicada ya pasó. Indica una hora futura."
            elif minutos_desde_ahora > 0:
                target = now + datetime.timedelta(minutes=int(minutos_desde_ahora))
            else:
                return "Debes indicar minutos_desde_ahora (>0) o fecha_hora_exacta ('YYYY-MM-DD HH:MM')."

            rec_clean = recurrencia.strip().lower()
            if rec_clean not in ("diario", "semanal"):
                rec_clean = None

            target_str = target.strftime("%Y-%m-%d %H:%M:%S")
            rem_id = database.add_reminder(chat_id, texto_recordatorio, target_str, rec_clean)
            schedule_reminder_job(chat_id, texto_recordatorio, target, rem_id, rec_clean)

            hora_str = target.strftime("%d/%m %I:%M %p")
            extra = f" (repetición: {rec_clean})" if rec_clean else ""
            return f"Recordatorio #{rem_id} agendado para {hora_str}{extra}: '{texto_recordatorio}'."
        except Exception as e:
            return f"Error al agendar recordatorio: {str(e)}"

    def listar_recordatorios() -> str:
        """Lista todos los recordatorios pendientes del usuario."""
        active = database.get_active_reminders(chat_id)
        if not active:
            return "No tienes recordatorios pendientes."
        lines = []
        for r in active:
            rec = f" [{r['recurrence']}]" if r.get("recurrence") else ""
            lines.append(f"#{r['id']}: {r['text']} -> {r['remind_at']}{rec}")
        return "\n".join(lines)

    def cancelar_recordatorio(id_recordatorio: int) -> str:
        """Cancela un recordatorio pendiente por su ID numérico."""
        if database.delete_reminder(int(id_recordatorio), chat_id):
            if app_instance and app_instance.job_queue:
                for j in app_instance.job_queue.get_jobs_by_name(f"reminder_{id_recordatorio}"):
                    j.schedule_removal()
            return f"Recordatorio #{id_recordatorio} cancelado."
        return f"No existe ningún recordatorio pendiente con ID #{id_recordatorio}."

    def agregar_tarea(titulo: str) -> str:
        """Agrega una tarea o pendiente a la lista personal del usuario."""
        task_id = database.add_task(chat_id, titulo)
        return f"Tarea #{task_id} agregada: '{titulo}'."

    def listar_tareas() -> str:
        """Muestra todas las tareas pendientes del usuario."""
        pending = database.get_tasks(chat_id, include_done=False)
        if not pending:
            return "No tienes tareas pendientes."
        return "\n".join([f"#{t['id']}: {t['title']}" for t in pending])

    def completar_tarea(id_tarea: int) -> str:
        """Marca como completada una tarea pendiente usando su ID."""
        if database.complete_task(int(id_tarea), chat_id):
            return f"Tarea #{id_tarea} marcada como completada."
        return f"No encontré la tarea #{id_tarea}."

    def guardar_dato_memoria(dato: str) -> str:
        """Guarda un hecho, preferencia o dato importante del usuario en su memoria permanente."""
        database.save_user_preference(chat_id, dato)
        return f"Guardado en memoria permanente: '{dato}'."

    def olvidar_dato_memoria(palabra_clave: str) -> str:
        """Elimina datos guardados en la memoria permanente que contengan la palabra clave."""
        count = database.remove_user_preference(chat_id, palabra_clave)
        return f"Se eliminaron {count} recuerdos relacionados con '{palabra_clave}'."

    return [
        programar_recordatorio,
        listar_recordatorios,
        cancelar_recordatorio,
        agregar_tarea,
        listar_tareas,
        completar_tarea,
        guardar_dato_memoria,
        olvidar_dato_memoria,
        tools.buscar_en_internet,
        tools.leer_pagina_web,
        tools.obtener_clima,
        tools.convertir_divisa,
        tools.calcular_expresion,
    ]


# ─────────────────────── Memoria e Historial ───────────────────────
def get_system_instruction(chat_id: int) -> str:
    prefs = database.get_user_preferences(chat_id)
    tz_name = database.get_user_timezone(chat_id, DEFAULT_TIMEZONE)
    time_info = f"[FECHA Y HORA ACTUAL]: {get_current_time_str(chat_id)} (Zona: {tz_name})"

    prefs_text = ""
    if prefs:
        prefs_formatted = "\n".join([f"- {p}" for p in prefs])
        prefs_text = f"\n[MEMORIA PERMANENTE DEL USUARIO]:\n{prefs_formatted}\n"

    return f"{BASE_SYSTEM_INSTRUCTION}\n\n{time_info}{prefs_text}".strip()


def get_history(chat_id: int) -> List[types.Content]:
    if chat_id not in histories:
        saved_msgs = database.load_recent_messages(chat_id, limit=MAX_HISTORY_TURNS * 2)
        histories[chat_id] = [
            types.Content(role=m["role"], parts=[types.Part.from_text(text=m["text"])])
            for m in saved_msgs
        ]
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


async def generate_response(
    chat_id: int,
    user_parts: list,
    raw_user_text: str = "",
    use_multimedia_model: bool = False,
) -> str:
    if not client:
        return "⚠️ Error: GEMINI_API_KEY no configurada."

    add_to_history(chat_id, "user", user_parts, text_to_save=raw_user_text)
    model = GEMINI_MULTIMEDIA_MODEL if use_multimedia_model else GEMINI_MODEL

    user_tools = build_user_tools(chat_id) if not use_multimedia_model else None

    config = types.GenerateContentConfig(
        system_instruction=get_system_instruction(chat_id),
        temperature=0.6,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        tools=user_tools,
    )

    last_error = ""
    for attempt in range(2):
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
            last_error = str(e)
            if ("503" in last_error or "UNAVAILABLE" in last_error) and attempt == 0:
                await asyncio.sleep(1.5)
                continue
            break

    logger.error(f"Error Gemini: {last_error}")
    history = get_history(chat_id)
    if history and history[-1].role == "user":
        history.pop()

    if "429" in last_error or "RESOURCE_EXHAUSTED" in last_error:
        return "⏳ Estoy recibiendo muchas solicitudes. Intenta de nuevo en unos segundos."
    if "503" in last_error or "UNAVAILABLE" in last_error:
        return "🔄 El servicio de IA está temporalmente saturado. Intenta en un momento."
    if "400" in last_error:
        return "⚠️ No pude procesar ese archivo o formato."
    return f"❌ Error: {last_error[:180]}"


async def send_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, force_voice: bool = False):
    """Envía texto (dividido si supera 4096 chars) y opcionalmente audio hablado."""
    chat_id = update.effective_chat.id

    for i in range(0, max(len(text), 1), 4096):
        chunk = text[i:i + 4096]
        try:
            await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await update.message.reply_text(chunk)

    if force_voice or database.get_voice_mode(chat_id):
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)
        audio_bytes = await tools.generate_voice_audio(text, voice=TTS_VOICE)
        if audio_bytes:
            bio = io.BytesIO(audio_bytes)
            bio.name = "junior_respuesta.mp3"
            await update.message.reply_voice(voice=bio)


# ─────────────────────── Comandos ───────────────────────
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name if update.effective_user else "Amigo"
    chat_id = update.effective_chat.id
    database.save_user_preference(chat_id, f"El nombre del usuario es {user_name}", user_name)

    await update.message.reply_text(
        f"👋 ¡Hola, *{user_name}*! Soy *Junior*, tu asistente personal de alto nivel.\n\n"
        "🚀 *Qué puedo hacer por ti:*\n"
        "• 🔍 Buscar en internet y leer enlaces web\n"
        "• 🌤 Clima en tiempo real y conversión de divisas\n"
        "• ⏰ Recordatorios únicos o recurrentes (por voz o texto)\n"
        "• ✅ Lista de tareas y notas rápidas\n"
        "• 🧠 Memoria permanente automática\n"
        "• 🎧 Multimedio total: fotos, audios, videos, PDFs y código\n"
        "• 🗣 Respuestas habladas (/voz on)\n\n"
        "Usa /help para ver todos los comandos.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛠 *Comandos disponibles:*\n\n"
        "• /tasks — Ver y completar tus tareas pendientes\n"
        "• /reminders — Ver y cancelar tus recordatorios\n"
        "• /memory — Ver qué recuerda Junior sobre ti\n"
        "• /remember `<dato>` — Guardar una preferencia fija\n"
        "• /voz `on/off` — Activar o desactivar respuestas por audio\n"
        "• /zona `<ciudad/zona>` — Cambiar zona horaria (ej: `America/Mexico_City`)\n"
        "• /clear — Limpiar conversación actual (conserva memoria y tareas)\n"
        "• /forget\\_all — Borrar todo desde cero\n\n"
        "💡 _No necesitas usar comandos para casi nada: pídemelo hablando natural._",
        parse_mode=ParseMode.MARKDOWN,
    )


async def tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    pending = database.get_tasks(chat_id, include_done=False)
    if not pending:
        await update.message.reply_text("✅ No tienes tareas pendientes. Pídeme anotar una cuando quieras.")
        return

    lines = [f"• `#{t['id']}` {t['title']}" for t in pending]
    keyboard = [
        [InlineKeyboardButton(f"✅ Completar #{t['id']}: {t['title'][:25]}", callback_data=f"done_task:{t['id']}")]
        for t in pending[:8]
    ]
    await update.message.reply_text(
        "📋 *Tus Tareas Pendientes:*\n\n" + "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def reminders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    active = database.get_active_reminders(chat_id)
    if not active:
        await update.message.reply_text("🔔 No tienes recordatorios pendientes.")
        return

    lines = []
    keyboard = []
    for r in active:
        rec = f" 🔁({r['recurrence']})" if r.get("recurrence") else ""
        lines.append(f"• `#{r['id']}` *{r['text']}* → _{r['remind_at']}_{rec}")
        if len(keyboard) < 8:
            keyboard.append([
                InlineKeyboardButton(f"🗑 Cancelar #{r['id']}: {r['text'][:22]}", callback_data=f"del_rem:{r['id']}")
            ])

    await update.message.reply_text(
        "⏰ *Tus Recordatorios:*\n\n" + "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def delete_reminder_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("💡 Uso: `/delete_reminder <id>`", parse_mode=ParseMode.MARKDOWN)
        return
    rem_id = int(context.args[0])
    if database.delete_reminder(rem_id, chat_id):
        if app_instance and app_instance.job_queue:
            for j in app_instance.job_queue.get_jobs_by_name(f"reminder_{rem_id}"):
                j.schedule_removal()
        await update.message.reply_text(f"✅ Recordatorio #{rem_id} cancelado.")
    else:
        await update.message.reply_text(f"❌ No encontré el recordatorio #{rem_id}.")


async def remember_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = " ".join(context.args).strip() if context.args else ""
    if not text:
        await update.message.reply_text("💡 Uso: `/remember <dato o preferencia>`", parse_mode=ParseMode.MARKDOWN)
        return
    database.save_user_preference(chat_id, text)
    await update.message.reply_text(f"✅ Guardado en memoria permanente:\n_{text}_", parse_mode=ParseMode.MARKDOWN)


async def memory_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    prefs = database.get_user_preferences(chat_id)
    if not prefs:
        await update.message.reply_text("🤔 Aún no tengo preferencias registradas sobre ti.")
        return
    formatted = "\n".join([f"• {p}" for p in prefs])
    await update.message.reply_text(f"🧠 *Memoria sobre ti:*\n\n{formatted}", parse_mode=ParseMode.MARKDOWN)


async def voz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    arg = context.args[0].lower() if context.args else ""
    if arg in ("on", "si", "sí", "activar", "1"):
        database.set_voice_mode(chat_id, True)
        await update.message.reply_text("🔊 *Modo voz activado.* Ahora también te responderé con audio.", parse_mode=ParseMode.MARKDOWN)
    elif arg in ("off", "no", "desactivar", "0"):
        database.set_voice_mode(chat_id, False)
        await update.message.reply_text("🔇 *Modo voz desactivado.* Responderé solo con texto.", parse_mode=ParseMode.MARKDOWN)
    else:
        current = not database.get_voice_mode(chat_id)
        database.set_voice_mode(chat_id, current)
        estado = "activado 🔊" if current else "desactivado 🔇"
        await update.message.reply_text(f"Modo voz {estado}.")


async def zona_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        actual = database.get_user_timezone(chat_id, DEFAULT_TIMEZONE)
        await update.message.reply_text(f"🕒 Tu zona horaria actual es `{actual}`.\nCámbiala con `/zona America/Mexico_City`", parse_mode=ParseMode.MARKDOWN)
        return
    tz_input = context.args[0].strip()
    try:
        pytz.timezone(tz_input)
        database.set_user_timezone(chat_id, tz_input)
        await update.message.reply_text(f"✅ Zona horaria actualizada a `{tz_input}`.", parse_mode=ParseMode.MARKDOWN)
    except Exception:
        await update.message.reply_text("❌ Zona horaria inválida. Ejemplo válido: `America/Bogota`, `America/Mexico_City`, `Europe/Madrid`.", parse_mode=ParseMode.MARKDOWN)


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    histories.pop(chat_id, None)
    database.clear_history(chat_id)
    await update.message.reply_text("🧹 Charla actual limpiada. (Tus preferencias, tareas y recordatorios siguen intactos).")


async def forget_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    histories.pop(chat_id, None)
    database.clear_all(chat_id)
    await update.message.reply_text("🗑️ Memoria, tareas y recordatorios borrados por completo.")


# ─────────────────────── Botones Inline (Callbacks) ───────────────────────
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    data = query.data or ""

    if data.startswith("done_task:"):
        task_id = int(data.split(":")[1])
        if database.complete_task(task_id, chat_id):
            await query.edit_message_text(f"✅ Tarea #{task_id} completada.")
        else:
            await query.edit_message_text("⚠️ Esa tarea ya no estaba pendiente.")
    elif data.startswith("del_rem:"):
        rem_id = int(data.split(":")[1])
        if database.delete_reminder(rem_id, chat_id):
            if app_instance and app_instance.job_queue:
                for j in app_instance.job_queue.get_jobs_by_name(f"reminder_{rem_id}"):
                    j.schedule_removal()
            await query.edit_message_text(f"🗑 Recordatorio #{rem_id} cancelado.")
        else:
            await query.edit_message_text("⚠️ Ese recordatorio ya no estaba activo.")


# ─────────────────────── Manejadores de Mensajes ───────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    chat_id = update.effective_chat.id
    user_text = update.message.text.strip()
    if not user_text:
        return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    want_voice = any(p in user_text.lower() for p in ("respóndeme con voz", "respondeme con voz", "en audio", "dímelo en voz"))
    reply = await generate_response(chat_id, [types.Part.from_text(text=user_text)], raw_user_text=user_text)
    await send_reply(update, context, reply, force_voice=want_voice)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.photo:
        return
    chat_id = update.effective_chat.id
    caption = update.message.caption or "Analiza esta imagen y describe lo relevante de forma concisa."
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    try:
        photo_file = await update.message.photo[-1].get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        parts = [
            types.Part.from_bytes(data=bytes(photo_bytes), mime_type="image/jpeg"),
            types.Part.from_text(text=caption),
        ]
        reply = await generate_response(chat_id, parts, raw_user_text=f"[Foto]: {caption}", use_multimedia_model=True)
        await send_reply(update, context, reply)
    except Exception as e:
        logger.error(f"Error foto: {e}")
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
            types.Part.from_text(text="Escucha la nota de voz del usuario y responde de forma natural y concisa a lo que dice o pide."),
        ]
        reply = await generate_response(chat_id, parts, raw_user_text="[Nota de voz]", use_multimedia_model=True)
        await send_reply(update, context, reply)
    except Exception as e:
        logger.error(f"Error voz: {e}")
        await update.message.reply_text(f"❌ Error en nota de voz: {str(e)[:150]}")


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.audio:
        return
    chat_id = update.effective_chat.id
    audio = update.message.audio
    caption = update.message.caption or "Escucha este audio y resume o responde según su contenido."
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    try:
        audio_file = await audio.get_file()
        audio_bytes = await audio_file.download_as_bytearray()
        mime = resolve_mime(audio.mime_type, audio.file_name) or "audio/mpeg"
        parts = [
            types.Part.from_bytes(data=bytes(audio_bytes), mime_type=mime),
            types.Part.from_text(text=caption),
        ]
        reply = await generate_response(chat_id, parts, raw_user_text=f"[Audio]: {caption}", use_multimedia_model=True)
        await send_reply(update, context, reply)
    except Exception as e:
        logger.error(f"Error audio: {e}")
        await update.message.reply_text(f"❌ Error en audio: {str(e)[:150]}")


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.video:
        return
    chat_id = update.effective_chat.id
    video = update.message.video
    if video.file_size and video.file_size > 20 * 1024 * 1024:
        await update.message.reply_text("⚠️ El video supera el límite de 20MB de Telegram.")
        return
    caption = update.message.caption or "Describe brevemente lo que ves y escuchas en el video."
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    try:
        video_file = await video.get_file()
        video_bytes = await video_file.download_as_bytearray()
        mime = resolve_mime(video.mime_type, video.file_name) or "video/mp4"
        parts = [
            types.Part.from_bytes(data=bytes(video_bytes), mime_type=mime),
            types.Part.from_text(text=caption),
        ]
        reply = await generate_response(chat_id, parts, raw_user_text=f"[Video]: {caption}", use_multimedia_model=True)
        await send_reply(update, context, reply)
    except Exception as e:
        logger.error(f"Error video: {e}")
        await update.message.reply_text(f"❌ Error en video: {str(e)[:150]}")


async def handle_video_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.video_note:
        return
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    try:
        vn_file = await update.message.video_note.get_file()
        vn_bytes = await vn_file.download_as_bytearray()
        parts = [
            types.Part.from_bytes(data=bytes(vn_bytes), mime_type="video/mp4"),
            types.Part.from_text(text="Mira y escucha esta video-nota circular y responde de forma natural."),
        ]
        reply = await generate_response(chat_id, parts, raw_user_text="[Video-nota]", use_multimedia_model=True)
        await send_reply(update, context, reply)
    except Exception as e:
        logger.error(f"Error video-nota: {e}")
        await update.message.reply_text(f"❌ Error en video circular: {str(e)[:150]}")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.document:
        return
    chat_id = update.effective_chat.id
    doc = update.message.document
    if doc.file_size and doc.file_size > 20 * 1024 * 1024:
        await update.message.reply_text("⚠️ El archivo supera el límite de 20MB.")
        return

    caption = update.message.caption or f"Analiza este archivo ({doc.file_name or 'documento'}) y resume lo más importante."
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    try:
        doc_file = await doc.get_file()
        doc_bytes = await doc_file.download_as_bytearray()
        mime = resolve_mime(doc.mime_type, doc.file_name)

        if not mime:
            text_content = bytes(doc_bytes).decode("utf-8", errors="replace")[:30000]
            prompt = f"Archivo '{doc.file_name}':\n```\n{text_content}\n```\n\n{caption}"
            reply = await generate_response(chat_id, [types.Part.from_text(text=prompt)], raw_user_text=f"[Doc: {doc.file_name}]")
            await send_reply(update, context, reply)
            return

        parts = [
            types.Part.from_bytes(data=bytes(doc_bytes), mime_type=mime),
            types.Part.from_text(text=caption),
        ]
        reply = await generate_response(chat_id, parts, raw_user_text=f"[Doc: {doc.file_name}]: {caption}", use_multimedia_model=True)
        await send_reply(update, context, reply)
    except Exception as e:
        logger.error(f"Error documento: {e}")
        await update.message.reply_text(f"❌ Error procesando archivo: {str(e)[:150]}")


async def handle_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.sticker:
        return
    chat_id = update.effective_chat.id
    sticker = update.message.sticker
    if sticker.is_animated:
        await update.message.reply_text("😄 ¡Buen sticker animado!")
        return
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    try:
        sticker_file = await sticker.get_file()
        sticker_bytes = await sticker_file.download_as_bytearray()
        mime = "video/webm" if sticker.is_video else "image/webp"
        parts = [
            types.Part.from_bytes(data=bytes(sticker_bytes), mime_type=mime),
            types.Part.from_text(text=f"El usuario envió este sticker ({sticker.emoji or ''}). Reacciona breve y naturalmente."),
        ]
        reply = await generate_response(chat_id, parts, raw_user_text="[Sticker]", use_multimedia_model=True)
        await send_reply(update, context, reply)
    except Exception:
        await update.message.reply_text("😄 ¡Buen sticker!")


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.location:
        return
    chat_id = update.effective_chat.id
    loc = update.message.location
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    prompt = (
        f"El usuario compartió su ubicación GPS: latitud {loc.latitude}, longitud {loc.longitude}. "
        "Indica brevemente qué zona/ciudad aproximada es y ofrece ayuda útil."
    )
    reply = await generate_response(chat_id, [types.Part.from_text(text=prompt)], raw_user_text="[Ubicación GPS]")
    await send_reply(update, context, reply)


async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.contact:
        return
    chat_id = update.effective_chat.id
    c = update.message.contact
    info = f"Nombre: {c.first_name or ''} {c.last_name or ''} | Tel: {c.phone_number or 'N/A'}".strip()
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    prompt = f"El usuario compartió un contacto ({info}). Confirma recepción brevemente."
    reply = await generate_response(chat_id, [types.Part.from_text(text=prompt)], raw_user_text=f"[Contacto: {info}]")
    await send_reply(update, context, reply)


# ─────────────────────── Post Init & Main ───────────────────────
async def post_init(application: Application):
    """Registra el menú de comandos en Telegram y restaura recordatorios."""
    commands = [
        BotCommand("start", "Iniciar asistente y ver bienvenida"),
        BotCommand("tasks", "Ver y completar tareas pendientes"),
        BotCommand("reminders", "Ver y cancelar recordatorios"),
        BotCommand("memory", "Ver lo que Junior recuerda sobre ti"),
        BotCommand("remember", "Guardar un dato o preferencia"),
        BotCommand("voz", "Activar/desactivar respuestas con voz (on/off)"),
        BotCommand("zona", "Configurar tu zona horaria"),
        BotCommand("clear", "Limpiar conversación actual"),
        BotCommand("help", "Guía rápida de uso"),
    ]
    try:
        await application.bot.set_my_commands(commands)
    except Exception as e:
        logger.warning(f"No se pudo registrar menú de comandos: {e}")

    restore_pending_reminders()


def main():
    global app_instance

    if not TELEGRAM_BOT_TOKEN:
        print("⚠️ Configura TELEGRAM_BOT_TOKEN en el archivo .env")
        return

    print(f"🚀 Iniciando Junior Pro ({GEMINI_MODEL} / {GEMINI_MULTIMEDIA_MODEL})...")
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    app_instance = app

    # Comandos
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("tasks", tasks_command))
    app.add_handler(CommandHandler("reminders", reminders_command))
    app.add_handler(CommandHandler("delete_reminder", delete_reminder_command))
    app.add_handler(CommandHandler("remember", remember_command))
    app.add_handler(CommandHandler("memory", memory_command))
    app.add_handler(CommandHandler("voz", voz_command))
    app.add_handler(CommandHandler("zona", zona_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("forget_all", forget_all_command))

    # Botones inline
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Multimedia
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.VIDEO_NOTE, handle_video_note))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.Sticker.ALL, handle_sticker))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))

    # Texto
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ Bot de alto nivel activo.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
