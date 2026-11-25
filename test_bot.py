"""
Tests internos del Asistente de IA - Junior Bot (v2 Multimedia Completo)
"""
import asyncio
import sys
import time
import os

from dotenv import load_dotenv
load_dotenv()

PASS = "✅"
FAIL = "❌"
results = []


def report(name: str, passed: bool, detail: str = ""):
    status = PASS if passed else FAIL
    results.append((name, passed))
    msg = f"  {status} {name}"
    if detail:
        msg += f" — {detail}"
    print(msg)


async def run_all_tests():
    # ═══════════════════════════════════════════════════════
    print("\n🧪 TEST 1: Importaciones y módulos")
    # ═══════════════════════════════════════════════════════
    try:
        from config import (TELEGRAM_BOT_TOKEN, GEMINI_API_KEY, GEMINI_MODEL,
                            GEMINI_MULTIMEDIA_MODEL, SYSTEM_INSTRUCTION, MAX_OUTPUT_TOKENS)
        report("config.py", True)
    except Exception as e:
        report("config.py", False, str(e))
        return

    try:
        from google import genai
        from google.genai import types
        report("google-genai", True)
    except Exception as e:
        report("google-genai", False, str(e))
        return

    try:
        import telegram
        report("python-telegram-bot", True, f"v{telegram.__version__}")
    except Exception as e:
        report("python-telegram-bot", False, str(e))

    try:
        from bot import (get_history, add_to_history, trim_history, histories,
                         generate_response, resolve_mime, MIME_MAP, EXT_TO_MIME,
                         handle_message, handle_photo, handle_voice, handle_audio,
                         handle_video, handle_video_note, handle_document,
                         handle_sticker, handle_location, handle_contact)
        report("bot.py (todos los handlers)", True)
    except Exception as e:
        report("bot.py handlers", False, str(e))
        return

    # ═══════════════════════════════════════════════════════
    print("\n🧪 TEST 2: Configuración")
    # ═══════════════════════════════════════════════════════
    report("TELEGRAM_BOT_TOKEN", bool(TELEGRAM_BOT_TOKEN))
    report("GEMINI_API_KEY", bool(GEMINI_API_KEY))
    report("GEMINI_MODEL", bool(GEMINI_MODEL), GEMINI_MODEL)
    report("GEMINI_MULTIMEDIA_MODEL", bool(GEMINI_MULTIMEDIA_MODEL), GEMINI_MULTIMEDIA_MODEL)
    report("MAX_OUTPUT_TOKENS", isinstance(MAX_OUTPUT_TOKENS, int) and MAX_OUTPUT_TOKENS > 0)

    # ═══════════════════════════════════════════════════════
    print("\n🧪 TEST 3: Token de Telegram")
    # ═══════════════════════════════════════════════════════
    try:
        import requests
        res = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe", timeout=10).json()
        report("Token válido", res.get("ok", False), f"@{res['result']['username']}")
    except Exception as e:
        report("Token válido", False, str(e)[:100])

    # ═══════════════════════════════════════════════════════
    print("\n🧪 TEST 4: MIME resolver")
    # ═══════════════════════════════════════════════════════
    report("audio/ogg → audio/ogg", resolve_mime("audio/ogg", None) == "audio/ogg")
    report("audio/mpeg → audio/mpeg", resolve_mime("audio/mpeg", None) == "audio/mpeg")
    report("video/mp4 → video/mp4", resolve_mime("video/mp4", None) == "video/mp4")
    report("image/webp → image/webp", resolve_mime("image/webp", None) == "image/webp")
    report("application/pdf → application/pdf", resolve_mime("application/pdf", None) == "application/pdf")
    report(".py → text/x-python", resolve_mime(None, "script.py") == "text/x-python")
    report(".pdf → application/pdf", resolve_mime(None, "doc.pdf") == "application/pdf")
    report(".mp3 → audio/mpeg", resolve_mime(None, "song.mp3") == "audio/mpeg")
    report(".csv → text/csv", resolve_mime(None, "data.csv") == "text/csv")
    report("Desconocido con mime raw", resolve_mime("application/octet-stream", None) == "application/octet-stream")

    # ═══════════════════════════════════════════════════════
    print("\n🧪 TEST 5: Historial y memoria")
    # ═══════════════════════════════════════════════════════
    test_id = -999
    histories.pop(test_id, None)

    h = get_history(test_id)
    report("get_history vacío", isinstance(h, list) and len(h) == 0)

    add_to_history(test_id, "user", [types.Part.from_text(text="hola")])
    report("add_to_history +1", len(get_history(test_id)) == 1)

    add_to_history(test_id, "model", [types.Part.from_text(text="hola!")])
    report("add user+model = 2", len(get_history(test_id)) == 2)

    # Probar trim
    for i in range(50):
        add_to_history(test_id, "user", [types.Part.from_text(text=f"msg {i}")])
        add_to_history(test_id, "model", [types.Part.from_text(text=f"reply {i}")])
    report("Trim a MAX_HISTORY_TURNS*2", len(get_history(test_id)) <= 40)

    histories.pop(test_id, None)

    # ═══════════════════════════════════════════════════════
    print("\n🧪 TEST 6: Gemini - Generación async (texto)")
    # ═══════════════════════════════════════════════════════
    client = genai.Client(api_key=GEMINI_API_KEY)

    try:
        t0 = time.time()
        config = types.GenerateContentConfig(
            system_instruction="Responde brevemente en español.",
            temperature=0.5, max_output_tokens=100,
        )
        res = await client.aio.models.generate_content(
            model=GEMINI_MODEL, contents="Di hola.", config=config,
        )
        elapsed = time.time() - t0
        text = (res.text or "").strip()
        report(f"Texto con {GEMINI_MODEL}", bool(text), f"'{text[:60]}' ({elapsed:.1f}s)")
    except Exception as e:
        report(f"Texto con {GEMINI_MODEL}", False, str(e)[:120])

    # ═══════════════════════════════════════════════════════
    print("\n🧪 TEST 7: Gemini - Modelo multimedia")
    # ═══════════════════════════════════════════════════════
    try:
        t0 = time.time()
        config = types.GenerateContentConfig(
            system_instruction="Responde brevemente.",
            temperature=0.5, max_output_tokens=100,
        )
        res = await client.aio.models.generate_content(
            model=GEMINI_MULTIMEDIA_MODEL, contents="Di 'multimedia ok'.", config=config,
        )
        elapsed = time.time() - t0
        text = (res.text or "").strip()
        report(f"Multimedia con {GEMINI_MULTIMEDIA_MODEL}", bool(text), f"'{text[:60]}' ({elapsed:.1f}s)")
    except Exception as e:
        report(f"Multimedia con {GEMINI_MULTIMEDIA_MODEL}", False, str(e)[:120])

    # ═══════════════════════════════════════════════════════
    print("\n🧪 TEST 8: Memoria multi-turno via Gemini")
    # ═══════════════════════════════════════════════════════
    try:
        config = types.GenerateContentConfig(
            system_instruction="Recuerda todo. Sé breve.",
            temperature=0.3, max_output_tokens=200,
        )
        history = [
            types.Content(role="user", parts=[types.Part.from_text(text="Me llamo Andrés.")]),
            types.Content(role="model", parts=[types.Part.from_text(text="Hola Andrés.")]),
            types.Content(role="user", parts=[types.Part.from_text(text="¿Cómo me llamo?")]),
        ]
        response = await client.aio.models.generate_content(
            model=GEMINI_MODEL, contents=history, config=config,
        )
        text = (response.text or "").lower()
        report("Recuerda nombre", "andrés" in text or "andres" in text, f"'{response.text.strip()[:60]}'")
    except Exception as e:
        report("Memoria multi-turno", False, str(e)[:120])

    # ═══════════════════════════════════════════════════════
    print("\n🧪 TEST 9: generate_response end-to-end")
    # ═══════════════════════════════════════════════════════
    try:
        test_id = -998
        histories.pop(test_id, None)

        t0 = time.time()
        reply = await generate_response(test_id, [types.Part.from_text(text="Hola")])
        elapsed = time.time() - t0
        report("Responde correctamente", bool(reply) and "error" not in reply.lower()[:20], f"({elapsed:.1f}s) '{reply[:80]}'")
        report("Historial tiene 2 entries", len(histories.get(test_id, [])) == 2)

        # Segundo turno
        reply2 = await generate_response(test_id, [types.Part.from_text(text="¿Qué te dije antes?")])
        report("Segundo turno funciona", bool(reply2), f"'{reply2[:60]}'")
        report("Historial tiene 4 entries", len(histories.get(test_id, [])) == 4)

        # Con modelo multimedia
        reply3 = await generate_response(-997, [types.Part.from_text(text="Di ok")], use_multimedia_model=True)
        report("generate_response multimedia", bool(reply3) and "error" not in reply3.lower()[:20], f"'{reply3[:60]}'")

        histories.pop(test_id, None)
        histories.pop(-997, None)
    except Exception as e:
        report("generate_response", False, str(e)[:120])

    # ═══════════════════════════════════════════════════════
    print("\n🧪 TEST 10: Concisión del system prompt")
    # ═══════════════════════════════════════════════════════
    try:
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            temperature=0.5, max_output_tokens=MAX_OUTPUT_TOKENS,
        )
        contents = [types.Content(role="user", parts=[types.Part.from_text(text="Hola")])]
        response = await client.aio.models.generate_content(
            model=GEMINI_MODEL, contents=contents, config=config,
        )
        text = (response.text or "").strip()
        word_count = len(text.split())
        report("Saludo conciso (<=25 palabras)", word_count <= 25, f"{word_count} palabras: '{text[:80]}'")
    except Exception as e:
        report("Concisión", False, str(e)[:120])

    # ═══════════════════════════════════════════════════════
    print("\n🧪 TEST 11: Manejo de errores")
    # ═══════════════════════════════════════════════════════
    try:
        bad_client = genai.Client(api_key="INVALID_KEY_12345")
        await bad_client.aio.models.generate_content(model=GEMINI_MODEL, contents="test")
        report("API key inválida → excepción", False)
    except Exception:
        report("API key inválida → excepción", True)

    # ═══════════════════════════════════════════════════════
    print("\n🧪 TEST 12: Handlers registrados")
    # ═══════════════════════════════════════════════════════
    handlers = {
        "handle_message": handle_message,
        "handle_photo": handle_photo,
        "handle_voice": handle_voice,
        "handle_audio": handle_audio,
        "handle_video": handle_video,
        "handle_video_note": handle_video_note,
        "handle_document": handle_document,
        "handle_sticker": handle_sticker,
        "handle_location": handle_location,
        "handle_contact": handle_contact,
    }
    for name, handler in handlers.items():
        report(f"{name} definido", callable(handler))


# ═══════════════════════════════════════════════════════
asyncio.run(run_all_tests())

# Resumen
print("\n" + "=" * 60)
total = len(results)
passed = sum(1 for _, p in results if p)
failed = total - passed

if failed == 0:
    print(f"  🎉 TODOS LOS TESTS PASARON: {passed}/{total}")
    print("  El bot está listo.")
else:
    print(f"  ⚠️  Resultado: {passed}/{total} pasaron, {failed} fallaron")
    for name, p in results:
        if not p:
            print(f"     {FAIL} {name}")

print("=" * 60 + "\n")
sys.exit(0 if failed == 0 else 1)
