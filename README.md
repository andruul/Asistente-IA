# 🤖 Asistente de IA en Telegram con Google Gemini

Un bot inteligente de Telegram con memoria de contexto, capacidades multimodales (análisis de imágenes) y herramientas integradas como búsqueda web en tiempo real (Google Search Grounding).

---

## 🚀 Requisitos Previos

1. **Python 3.10 o superior** (Detectado en tu sistema: Python 3.12).
2. **Token de Bot de Telegram**:
   - Abre Telegram y busca al bot oficial `@BotFather`.
   - Envía `/newbot`, ponle un nombre y un usuario que termine en `bot` (ejemplo: `MiAsistenteSuperBot`).
   - Copia el token HTTP API que te entregue.
3. **API Key de Google Gemini**:
   - Ingresa a [Google AI Studio](https://aistudio.google.com/).
   - Inicia sesión y haz clic en **"Get API key"** -> **"Create API key"**.
   - Copia la clave generada.

---

## ⚙️ Configuración

1. En la carpeta del proyecto, crea un archivo llamado `.env` copiando el ejemplo:
   - Puedes duplicar el archivo `.env.example` y renombrarlo a `.env`.
2. Llena tus credenciales en `.env`:
   ```env
   TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ
   GEMINI_API_KEY=AIzaSy...
   GEMINI_MODEL=gemini-2.5-flash
   ```

---

## ▶️ Ejecución

Para iniciar el bot:

```powershell
.\venv\Scripts\python.exe bot.py
```

Una vez que veas el mensaje:
```
✅ Bot en ejecución. Esperando mensajes...
```
Abre tu bot en Telegram y haz clic en **Iniciar** o escribe `/start`.

---

## ✨ Funcionalidades y Comandos

| Comando / Acción | Descripción |
| :--- | :--- |
| `/start` | Mensaje de bienvenida con menú de opciones. |
| `/help` | Guía de ayuda y capacidades del asistente. |
| `/clear` o `/reset` | Reinicia la memoria de la conversación para comenzar un tema nuevo. |
| **Cualquier mensaje de texto** | Chatea con el bot con memoria contextual activa. |
| **Búsqueda web automática** | Pregunta sobre eventos actuales o datos recientes; Gemini consultará Google Search. |
| **Envío de Fotos / Imágenes** | Envía una imagen (con o sin pie de foto); el modelo multimodal la analizará al instante. |

