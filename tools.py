import ast
import io
import logging
import math
import operator
import re
from typing import Optional

import edge_tts
import requests
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
}

WMO_WEATHER_CODES = {
    0: "Despejado",
    1: "Mayormente despejado",
    2: "Parcialmente nublado",
    3: "Nublado",
    45: "Niebla",
    48: "Niebla con escarcha",
    51: "Llovizna ligera",
    53: "Llovizna moderada",
    55: "Llovizna densa",
    61: "Lluvia ligera",
    63: "Lluvia moderada",
    65: "Lluvia fuerte",
    71: "Nieve ligera",
    73: "Nieve moderada",
    75: "Nieve fuerte",
    80: "Chubascos ligeros",
    81: "Chubascos moderados",
    82: "Chubascos violentos",
    95: "Tormenta eléctrica",
    96: "Tormenta con granizo",
    99: "Tormenta fuerte con granizo",
}


# ─────────────────────── Búsqueda Web ───────────────────────
def buscar_en_internet(consulta: str) -> str:
    """Busca información actualizada en internet (noticias, datos recientes, precios, hechos)."""
    try:
        results = DDGS().text(consulta, max_results=5)
        if not results:
            return "No se encontraron resultados relevantes en la web."
        lines = []
        for idx, item in enumerate(results, 1):
            title = item.get("title", "").strip()
            body = item.get("body", "").strip()
            href = item.get("href", "").strip()
            lines.append(f"{idx}. {title}\n   {body}\n   Fuente: {href}")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Error en búsqueda web: {e}")
        return f"No pude completar la búsqueda web: {str(e)[:120]}"


# ─────────────────────── Lector de Enlaces ───────────────────────
def leer_pagina_web(url: str) -> str:
    """Descarga y extrae el texto principal de una página web o artículo a partir de su URL."""
    try:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        resp = requests.get(url, headers=HEADERS, timeout=12)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside"]):
            tag.decompose()

        title = soup.title.string.strip() if soup.title and soup.title.string else "Sin título"
        text = soup.get_text(separator="\n")
        clean_lines = [line.strip() for line in text.splitlines() if line.strip()]
        content = "\n".join(clean_lines)

        if len(content) > 6000:
            content = content[:6000] + "\n\n[... contenido recortado ...]"

        return f"Título: {title}\nURL: {url}\n\nContenido:\n{content}"
    except Exception as e:
        logger.error(f"Error leyendo URL {url}: {e}")
        return f"No pude leer la página web ({url}): {str(e)[:120]}"


# ─────────────────────── Clima en Tiempo Real ───────────────────────
def obtener_clima(ciudad: str) -> str:
    """Consulta el clima actual y el pronóstico del día para cualquier ciudad del mundo."""
    try:
        geo_resp = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": ciudad, "count": 1, "language": "es", "format": "json"},
            timeout=10,
        ).json()

        results = geo_resp.get("results")
        if not results:
            return f"No encontré la ciudad '{ciudad}'."

        place = results[0]
        lat, lon = place["latitude"], place["longitude"]
        nombre = place.get("name", ciudad)
        pais = place.get("country", "")

        weather_resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "timezone": "auto",
                "forecast_days": 1,
            },
            timeout=10,
        ).json()

        cur = weather_resp.get("current", {})
        daily = weather_resp.get("daily", {})

        code = cur.get("weather_code", 0)
        desc = WMO_WEATHER_CODES.get(code, "Condición variable")
        temp = cur.get("temperature_2m", "?")
        feels = cur.get("apparent_temperature", "?")
        hum = cur.get("relative_humidity_2m", "?")
        wind = cur.get("wind_speed_10m", "?")
        tmax = daily.get("temperature_2m_max", ["?"])[0]
        tmin = daily.get("temperature_2m_min", ["?"])[0]
        rain_prob = daily.get("precipitation_probability_max", ["?"])[0]

        return (
            f"Clima en {nombre}, {pais}: {desc}.\n"
            f"Temperatura actual: {temp}°C (sensación {feels}°C).\n"
            f"Mínima: {tmin}°C / Máxima: {tmax}°C.\n"
            f"Humedad: {hum}% | Viento: {wind} km/h | Prob. lluvia: {rain_prob}%."
        )
    except Exception as e:
        logger.error(f"Error clima {ciudad}: {e}")
        return f"No pude obtener el clima de {ciudad}: {str(e)[:120]}"


# ─────────────────────── Conversor de Divisas ───────────────────────
def convertir_divisa(monto: float, moneda_origen: str, moneda_destino: str) -> str:
    """Convierte un monto entre dos monedas usando tasas de cambio actuales (ej. USD a COP, EUR a MXN)."""
    try:
        origen = moneda_origen.strip().upper()
        destino = moneda_destino.strip().upper()
        resp = requests.get(f"https://open.er-api.com/v6/latest/{origen}", timeout=10).json()
        if resp.get("result") != "success":
            return f"No encontré tasas de cambio para '{origen}'."

        rates = resp.get("rates", {})
        if destino not in rates:
            return f"La moneda destino '{destino}' no está disponible."

        rate = rates[destino]
        converted = float(monto) * rate
        return (
            f"{monto:,.2f} {origen} = {converted:,.2f} {destino} "
            f"(Tasa: 1 {origen} = {rate:,.4f} {destino})"
        )
    except Exception as e:
        logger.error(f"Error divisa: {e}")
        return f"No pude convertir la divisa: {str(e)[:120]}"


# ─────────────────────── Calculadora Segura ───────────────────────
_ALLOWED_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_ALLOWED_FUNCS = {
    "sqrt": math.sqrt,
    "abs": abs,
    "round": round,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "ceil": math.ceil,
    "floor": math.floor,
}

_ALLOWED_CONSTS = {"pi": math.pi, "e": math.e}


def _eval_ast(node):
    if isinstance(node, ast.Expression):
        return _eval_ast(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name) and node.id in _ALLOWED_CONSTS:
        return _ALLOWED_CONSTS[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPS:
        left = _eval_ast(node.left)
        right = _eval_ast(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ValueError("Exponente demasiado grande.")
        return _ALLOWED_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPS:
        return _ALLOWED_OPS[type(node.op)](_eval_ast(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _ALLOWED_FUNCS:
        args = [_eval_ast(a) for a in node.args]
        return _ALLOWED_FUNCS[node.func.id](*args)
    raise ValueError("Operación no permitida.")


def calcular_expresion(expresion: str) -> str:
    """Evalúa de forma exacta expresiones matemáticas (ej: '(150000 * 0.19) + 5000', 'sqrt(144)')."""
    try:
        clean_expr = expresion.replace("^", "**").replace(",", "").strip()
        tree = ast.parse(clean_expr, mode="eval")
        result = _eval_ast(tree)
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        return f"{expresion} = {result}"
    except Exception as e:
        return f"No pude calcular '{expresion}': {str(e)}"


# ─────────────────────── Síntesis de Voz (TTS) ───────────────────────
def _clean_text_for_tts(text: str) -> str:
    """Elimina símbolos Markdown y emojis complejos para que la lectura por voz suene natural."""
    cleaned = re.sub(r"```[\s\S]*?```", " (revisa el bloque de código en pantalla) ", text)
    cleaned = re.sub(r"[*_`~>#\[\]]", "", cleaned)
    cleaned = re.sub(r"https?://\S+", "un enlace", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:1500]


async def generate_voice_audio(text: str, voice: str = "es-CO-GonzaloNeural") -> Optional[bytes]:
    """Genera audio MP3 en memoria a partir de texto usando Edge TTS."""
    try:
        clean_text = _clean_text_for_tts(text)
        if not clean_text:
            return None
        communicate = edge_tts.Communicate(clean_text, voice=voice)
        buf = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
        audio_bytes = buf.getvalue()
        return audio_bytes if audio_bytes else None
    except Exception as e:
        logger.error(f"Error generando voz TTS: {e}")
        return None
