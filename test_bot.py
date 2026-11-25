"""
Tests internos de Junior Pro (Alto Nivel)
Verifica: módulos, DB (memoria, tareas, recordatorios), herramientas (clima, divisas,
calculadora, búsqueda web, TTS), Gemini Function Calling y handlers de Telegram.
"""
import asyncio
import sys
import time

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
    print("\n🧪 TEST 1: Importaciones y Módulos")
    # ═══════════════════════════════════════════════════════
    try:
        import config
        import database
        import tools
        import bot
        from google.genai import types
        report("Importar config, database, tools, bot", True)
    except Exception as e:
        report("Importar módulos", False, str(e))
        return

    # ═══════════════════════════════════════════════════════
    print("\n🧪 TEST 2: Base de Datos SQLite (Memoria, Tareas, Recordatorios)")
    # ═══════════════════════════════════════════════════════
    test_id = -77777
    database.init_db()
    database.clear_all(test_id)

    database.save_user_preference(test_id, "Prefiere respuestas directas", "Tester")
    prefs = database.get_user_preferences(test_id)
    report("Guardar y leer preferencia", "Prefiere respuestas directas" in prefs)

    removed = database.remove_user_preference(test_id, "directas")
    report("Eliminar preferencia por palabra clave", removed == 1 and len(database.get_user_preferences(test_id)) == 0)

    database.set_voice_mode(test_id, True)
    report("Activar voice_mode", database.get_voice_mode(test_id) is True)
    database.set_voice_mode(test_id, False)
    report("Desactivar voice_mode", database.get_voice_mode(test_id) is False)

    t_id = database.add_task(test_id, "Revisar servidor")
    tasks = database.get_tasks(test_id)
    report("Agregar y listar tarea", len(tasks) == 1 and tasks[0]["title"] == "Revisar servidor")
    report("Completar tarea", database.complete_task(t_id, test_id) and len(database.get_tasks(test_id)) == 0)

    rem_id = database.add_reminder(test_id, "Tomar café", "2099-01-01 08:00:00", "diario")
    rems = database.get_active_reminders(test_id)
    report("Agregar recordatorio recurrente", len(rems) == 1 and rems[0]["recurrence"] == "diario")
    report("Eliminar recordatorio", database.delete_reminder(rem_id, test_id) and len(database.get_active_reminders(test_id)) == 0)

    database.clear_all(test_id)

    # ═══════════════════════════════════════════════════════
    print("\n🧪 TEST 3: Herramientas Externas (Calculadora, Clima, Divisas, Búsqueda, TTS)")
    # ═══════════════════════════════════════════════════════
    calc_res = tools.calcular_expresion("(150 * 2) + sqrt(144)")
    report("Calculadora AST segura", "312" in calc_res, calc_res)

    bad_calc = tools.calcular_expresion("__import__('os').system('echo hack')")
    report("Calculadora bloquea inyección", "No pude calcular" in bad_calc)

    weather_res = tools.obtener_clima("Bogota")
    report("Clima Open-Meteo (Bogotá)", "°C" in weather_res, weather_res.splitlines()[0])

    fx_res = tools.convertir_divisa(10, "USD", "COP")
    report("Conversor de divisas USD->COP", "COP" in fx_res and "=" in fx_res, fx_res)

    search_res = tools.buscar_en_internet("Python programming language")
    report("Búsqueda DuckDuckGo", "1." in search_res and len(search_res) > 40)

    audio_bytes = await tools.generate_voice_audio("Hola, prueba de voz completada.")
    report("Síntesis de voz Edge-TTS", audio_bytes is not None and len(audio_bytes) > 1000, f"{len(audio_bytes or b'')} bytes")

    # ═══════════════════════════════════════════════════════
    print("\n🧪 TEST 4: Gemini + Tool Calling End-to-End")
    # ═══════════════════════════════════════════════════════
    chat_e2e = -88888
    database.clear_all(chat_e2e)
    bot.histories.pop(chat_e2e, None)

    try:
        t0 = time.time()
        reply_calc = await bot.generate_response(
            chat_e2e,
            [types.Part.from_text(text="Usa tu calculadora para decirme cuánto es 1234 * 5678")],
            raw_user_text="Usa tu calculadora para decirme cuánto es 1234 * 5678",
        )
        elapsed = time.time() - t0
        ok_calc = "7006652" in reply_calc.replace(",", "").replace(".", "")
        report("Gemini invoca calculadora", ok_calc, f"({elapsed:.1f}s) '{reply_calc[:90]}'")

        reply_task = await bot.generate_response(
            chat_e2e,
            [types.Part.from_text(text="Agrega a mis tareas pendientes: Comprar café")],
            raw_user_text="Agrega a mis tareas pendientes: Comprar café",
        )
        tasks_e2e = database.get_tasks(chat_e2e)
        report("Gemini agrega tarea automáticamente", len(tasks_e2e) >= 1, f"Tareas: {[t['title'] for t in tasks_e2e]}")
    except Exception as e:
        report("Gemini Tool Calling", False, str(e)[:150])
    finally:
        database.clear_all(chat_e2e)
        bot.histories.pop(chat_e2e, None)

    # ═══════════════════════════════════════════════════════
    print("\n🧪 TEST 5: Cobertura de Handlers")
    # ═══════════════════════════════════════════════════════
    required = [
        "start_command", "help_command", "tasks_command", "reminders_command",
        "voz_command", "zona_command", "handle_callback", "handle_message",
        "handle_photo", "handle_voice", "handle_audio", "handle_video",
        "handle_video_note", "handle_document", "handle_sticker",
        "handle_location", "handle_contact",
    ]
    for fn in required:
        report(f"Handler {fn}", callable(getattr(bot, fn, None)))


asyncio.run(run_all_tests())

print("\n" + "=" * 60)
total = len(results)
passed = sum(1 for _, p in results if p)
failed = total - passed
if failed == 0:
    print(f"  🎉 TODOS LOS TESTS PASARON: {passed}/{total}")
else:
    print(f"  ⚠️  Resultado: {passed}/{total} pasaron, {failed} fallaron")
    for name, p in results:
        if not p:
            print(f"     {FAIL} {name}")
print("=" * 60 + "\n")
sys.exit(0 if failed == 0 else 1)
