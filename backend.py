"""
LEAGUE OF LEGENDS DATA MINER - Backend Unificado
Fusión de: backend.py + descargar_coachings.py + auto_renew_riot_key.py + export_friends.py

Optimizaciones incluidas:
- LRU Cache para búsquedas JSON (índices en memoria)
- Caching PUUID/región con TTL de 24h
- Endpoints start/stop/pause para controlar scraper y preload
- Notificaciones SSE para caducidad de APIs
- Anti-bot mejorado en Playwright (User-Agent aleatorio, delays humanos)
"""

# =====================================================================
# 1. IMPORTS - Organizados por categoría
# =====================================================================

import asyncio
import json
import time
import os
import random
import subprocess
import shutil
import requests
import re
import urllib.parse
import itertools
import queue
import threading
import base64
import ctypes
import ctypes.util
import sys
import psutil
import urllib3
from urllib.parse import unquote, quote, urlparse
from collections import deque, defaultdict
from contextlib import asynccontextmanager
from functools import lru_cache
from datetime import datetime, timedelta
from enum import Enum

# FastAPI & Web
from fastapi import FastAPI, BackgroundTasks, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Env & Dotenv
from dotenv import load_dotenv, set_key

# Playwright (Scraping)
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

# YouTube & Audio
import yt_dlp
import whisper
import imageio_ffmpeg

# IA - Google Gemini
from google import genai
from google.genai import types
from youtube_transcript_api import YouTubeTranscriptApi

# NLP - Compresión de texto
import nltk
from sumy.parsers.plaintext import PlaintextParser
from sumy.nlp.tokenizers import Tokenizer
from sumy.summarizers.lsa import LsaSummarizer

# Desactivar advertencias de SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Parche Whisper en Windows
_original_find_library = ctypes.util.find_library
def _patched_find_library(name):
    if name == "c" and os.name == "nt":
        return "msvcrt"
    return _original_find_library(name)
ctypes.util.find_library = _patched_find_library

# Descargar dependencias NLP silenciosamente
for pkg in ["punkt", "punkt_tab"]:
    try:
        nltk.data.find(f"tokenizers/{pkg}")
    except LookupError:
        nltk.download(pkg, quiet=True)

load_dotenv()

# =====================================================================
# 2. CONSTANTES GLOBALES
# =====================================================================

# FastAPI
ARCHIVO_JSON = "preload.json"
NUM_PESTANAS = 5
MAX_JUGADORES_POR_TIER = 50
MATCHUPS_FILE = "matchups_stats.json"
ARCHIVO_HISTORIAL = "historial_coachings.json"
ARCHIVO_CACHE_LEIDOS = "cache_jugadores_leidos.json"

# Rutas
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
CHROME_PROFILE_PATH = os.path.join(os.path.expanduser("~"), "riot_bot_profile")
LOCAL_FFMPEG = os.path.join(os.getcwd(), "ffmpeg.exe")
FFMPEG_PATH = LOCAL_FFMPEG if os.path.exists(LOCAL_FFMPEG) else imageio_ffmpeg.get_ffmpeg_exe()

# APIs & Keys
RIOT_API_KEY = os.getenv("RIOT_API_KEY", "")
GEMINI_API_KEYS = [
    os.getenv("GEMINI_KEY_1"),
    os.getenv("GEMINI_KEY_2"),
    os.getenv("GEMINI_KEY_3"),
]
GEMINI_API_KEYS = [key for key in GEMINI_API_KEYS if key]

# User Agents
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# Regiones Riot
SUB_TO_MACRO = {
    "euw1": "europe", "eun1": "europe", "tr1": "europe", "ru": "europe", "me1": "europe",
    "na1": "americas", "la1": "americas", "la2": "americas", "br1": "americas",
    "kr": "asia", "jp1": "asia", "oc1": "asia", "ph2": "asia", "sg2": "asia", "tw2": "asia", "vn2": "asia",
}

# Campeones LoL
CAMPEONES_BASE = [
    "Aatrox", "Ahri", "Akali", "Akshan", "Alistar", "Ambessa", "Amumu", "Anivia", "Annie", "Aphelios",
    "Ashe", "Aurelion Sol", "Aurora", "Azir", "Bard", "Bel'Veth", "Blitzcrank", "Brand", "Braum", "Briar",
    "Caitlyn", "Camille", "Cassiopeia", "Cho'Gath", "Corki", "Darius", "Diana", "Dr. Mundo", "Draven", "Ekko",
    "Elise", "Evelynn", "Ezreal", "Fiddlesticks", "Fiora", "Fizz", "Galio", "Gangplank", "Garen", "Gnar",
    "Gragas", "Graves", "Gwen", "Hecarim", "Heimerdinger", "Hwei", "Illaoi", "Irelia", "Ivern", "Janna",
    "Jarvan IV", "Jax", "Jayce", "Jhin", "Jinx", "K'Sante", "Kai'Sa", "Kalista", "Karma", "Karthus",
    "Kassadin", "Katarina", "Kayle", "Kayn", "Kennen", "KhaZix", "Kindred", "Kled", "Kog'Maw", "LeBlanc",
    "Lee Sin", "Leona", "Lillia", "Lissandra", "Locke", "Lucian", "Lulu", "Lux", "Malphite", "Malzahar",
    "Maokai", "Master Yi", "Mel", "Milio", "Miss Fortune", "Mordekaiser", "Morgana", "Naafiri", "Nami", "Nasus",
    "Nautilus", "Neeko", "Nidalee", "Nilah", "Nocturne", "Nunu & Willump", "Olaf", "Orianna", "Ornn", "Pantheon",
    "Poppy", "Pyke", "Qiyana", "Quinn", "Rakan", "Rammus", "Rek'Sai", "Rell", "Renata Glasc", "Renekton",
    "Rengar", "Riven", "Rumble", "Ryze", "Samira", "Sejuani", "Senna", "Seraphine", "Sett", "Shaco",
    "Shen", "Shyvana", "Singed", "Sion", "Sivir", "Skarner", "Smolder", "Sona", "Soraka", "Swain",
    "Sylas", "Syndra", "Tahm Kench", "Taliyah", "Talon", "Taric", "Teemo", "Thresh", "Tristana", "Trundle",
    "Tryndamere", "Twisted Fate", "Twitch", "Udyr", "Urgot", "Varus", "Vayne", "Veigar", "Vel'Koz", "Vex",
    "Vi", "Viego", "Viktor", "Vladimir", "Volibear", "Warwick", "Wukong", "Xayah", "Xerath", "Xin Zhao",
    "Yasuo", "Yone", "Yorick", "Yunara", "Yuumi", "Zaahen", "Zac", "Zed", "Zeri", "Ziggs", "Zilean", "Zoe", "Zyra",
]

CHAMPION_DISPLAY_MAP = {re.sub(r"[^a-zA-Z0-9]", "", c).lower(): c for c in CAMPEONES_BASE}

ESTANDARIZAR_NOMBRES = {
    "monkeyking": "wukong", "wukong": "wukong",
    "nunuwillump": "nunu", "nunu": "nunu",
    "renata": "renata",
}

CAMPEONES_LOL_LOWER = [c.lower() for c in CAMPEONES_BASE]
CHAMPS_SORTED = sorted(CAMPEONES_LOL_LOWER, key=len, reverse=True)
CHAMPS_REGEX = re.compile(r"\b(" + "|".join(re.escape(c) for c in CHAMPS_SORTED) + r")\b", re.IGNORECASE)

# =====================================================================
# 3. EVENTOS Y SINCRONIZACIÓN GLOBAL
# =====================================================================

log_queue = queue.Queue()
shutdown_event = threading.Event()
riot_api_ready_sync = threading.Event()
riot_api_ready_sync.set()
riot_api_ready_async = None

# Estado del Scraper
SCRAPER_STATE = {
    "status": "idle",  # idle, running, paused, stopping
    "region": "",
    "page": 0,
    "players_processed": 0,
}

# Event loop global para lanzar tareas async desde endpoints sync
EVENT_LOOP = None

# Estado del Preload
PRELOAD_STATUS = {
    "status": "idle",  # idle, running, paused, stopping
    "current_player": "",
    "processed_matches": 0,
    "total_matches": 0,
    "message": "",
}

# Estado de APIs
API_STATUS = {
    "riot": {"status": "ok", "expiry": None},
    "gemini": {"status": "ok", "expiry": None},
}

# Rate Limiting
RIOT_RATE_LIMITS = {}
REGION_LOCKS = {}
GLOBAL_LOCK = threading.Lock()

# Cache con TTL (24h)
PUUID_CACHE = {}  # {riot_id: (puuid, timestamp)}
REGION_CACHE_TTL = 86400  # 24 horas en segundos

# =====================================================================
# 4. MODELOS PYDANTIC
# =====================================================================

class RolLoL(str, Enum):
    TOP = "Top"
    JUNGLE = "Jungle"
    MID = "Mid"
    ADC = "ADC"
    SUPPORT = "Support"
    GENERAL = "General"

class RespuestaCoaching(BaseModel):
    rol: RolLoL = Field(description="Rol principal del coaching")
    campeon_principal: str = Field(description="Campeón jugado (formato exacto)")
    campeon_enemigo: str = Field(description="Campeón rival")
    dificultad_matchup: int = Field(description="Dificultad 1-10", default=5)
    guia: str = Field(description="Guía estructurada en EARLY/MID/LATE GAME")

class UrlRequest(BaseModel):
    url: str

class DraftRequest(BaseModel):
    top_aliado: str = ""
    top_enemigo: str = ""
    jungle_aliado: str = ""
    jungle_enemigo: str = ""
    mid_aliado: str = ""
    mid_enemigo: str = ""
    adc_aliado: str = ""
    adc_enemigo: str = ""
    support_aliado: str = ""
    support_enemigo: str = ""

class GuiaRequest(BaseModel):
    rol: str
    champ_main: str
    champ_enemy: str

# =====================================================================
# 5. FUNCIONES UTILITARIAS - LOGGING & EMIT
# =====================================================================

def emitir_log(mensaje: str, tipo: str):
    """Emite un log a la cola para streaming SSE"""
    timestamp = datetime.now().isoformat()
    log_entry = {"mensaje": mensaje, "tipo": tipo, "timestamp": timestamp}
    try:
        print(f"[{tipo.upper()}] {mensaje}")
    except UnicodeEncodeError:
        print(f"[{tipo.upper()}] {mensaje.encode('ascii', 'replace').decode()}")
    log_queue.put(log_entry)

async def log_generator():
    """Generador SSE para streaming de logs"""
    while True:
        if not log_queue.empty():
            log = log_queue.get()
            yield f"data: {json.dumps(log)}\n\n"
        else:
            await asyncio.sleep(0.1)

def emit_api_status(api_name: str, status: str, expiry: str = None):
    """Notifica cambio de estado de API"""
    if api_name in API_STATUS:
        API_STATUS[api_name] = {"status": status, "expiry": expiry}
        emitir_log(f"🔔 API {api_name} cambió a {status}", "warning" if status != "ok" else "info")

# =====================================================================
# 6. FUNCIONES UTILITARIAS - NORMALIZACIÓN & MAPEO
# =====================================================================

@lru_cache(maxsize=1024)
def normalizar(texto: str) -> str:
    """Normaliza un nombre de campeón para búsquedas"""
    if not texto:
        return ""
    return re.sub(r"[^a-zA-Z0-9]", "", str(texto)).lower()

def formatear_nombre_campeon(norm_champ: str) -> str:
    """Convierte nombre normalizado a formato display"""
    norm = normalizar(norm_champ)
    if norm in CHAMPION_DISPLAY_MAP:
        return CHAMPION_DISPLAY_MAP[norm]
    if norm in ESTANDARIZAR_NOMBRES and ESTANDARIZAR_NOMBRES[norm] in CHAMPION_DISPLAY_MAP:
        return CHAMPION_DISPLAY_MAP[ESTANDARIZAR_NOMBRES[norm]]
    return norm_champ.capitalize()

def obtener_variantes_campeon(campeon: str) -> set:
    """Obtiene todas las variantes de un campeón"""
    norm = normalizar(campeon)
    variantes = {norm}
    if norm in ESTANDARIZAR_NOMBRES:
        variantes.add(ESTANDARIZAR_NOMBRES[norm])
    return variantes

def normalizar_rol_canonico(rol: str) -> str:
    """Normaliza nombre de rol a formato canonical"""
    r = str(rol).upper().strip()
    if r in ["MIDDLE", "MID", "MEDIO"]:
        return "MID"
    if r in ["UTILITY", "SUPPORT", "SUPP", "SUP", "SOPORTE"]:
        return "SUPPORT"
    if r in ["BOTTOM", "BOT", "ADC"]:
        return "ADC"
    if r in ["JUNGLE", "JUG", "JUNGLA"]:
        return "JUNGLE"
    if r in ["TOP"]:
        return "TOP"
    return r

def mapear_rol(rol: str) -> list:
    """Mapea rol a lista de variantes válidas"""
    r = normalizar_rol_canonico(rol)
    mapeo = {
        "MID": ["mid", "middle"],
        "SUPPORT": ["support", "utility", "sup"],
        "ADC": ["bot", "bottom", "adc"],
        "JUNGLE": ["jungle"],
        "TOP": ["top"],
    }
    return mapeo.get(r, [normalizar(rol)])

def extraer_rol_riot(participant: dict) -> str:
    """Extrae rol del participante de Riot API"""
    rol = participant.get("teamPosition", "")
    if not rol or rol == "Invalid":
        rol = participant.get("individualPosition", "")
    return normalizar_rol_canonico(rol)

# =====================================================================
# 7. FUNCIONES UTILITARIAS - ARCHIVOS & PERSISTENCIA
# =====================================================================

def guardado_atomico(datos, ruta_archivo):
    """Guarda datos JSON de forma atómica"""
    ruta_temporal = f"{ruta_archivo}.tmp"
    with open(ruta_temporal, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=4)
    os.replace(ruta_temporal, ruta_archivo)

def cargar_datos_previos(ruta_archivo):
    """Carga datos previos de un JSON"""
    if os.path.exists(ruta_archivo):
        try:
            with open(ruta_archivo, "r", encoding="utf-8") as f:
                datos = json.load(f)
                emitir_log(f"✓ Cargados {len(datos)} registros de '{ruta_archivo}'", "success")
                return datos
        except json.JSONDecodeError:
            emitir_log(f"✗ Error leyendo '{ruta_archivo}'", "error")
            return []
    else:
        emitir_log(f"✓ Archivo '{ruta_archivo}' no existe. Se creará nuevo.", "info")
        return []

def cargar_checkpoint():
    """Carga checkpoint de scraper desde .env"""
    load_dotenv(override=True)
    region = os.getenv("REGIONSCRAPPER", "KR").strip().lower()
    try:
        pagina = max(0, int(os.getenv("PAGESCRAPPER", "0")))
    except ValueError:
        pagina = 0
    return region, pagina

def guardar_checkpoint(region, pagina):
    """Guarda checkpoint de scraper en .env"""
    ruta_env = ".env"
    lineas = []
    if os.path.exists(ruta_env):
        with open(ruta_env, "r", encoding="utf-8") as archivo:
            lineas = archivo.readlines()

    valores = {"REGIONSCRAPPER": region.upper(), "PAGESCRAPPER": str(pagina)}
    claves_actualizadas = set()
    nuevas_lineas = []
    
    for linea in lineas:
        clave = linea.split("=", 1)[0].strip() if "=" in linea else ""
        if clave in valores:
            nuevas_lineas.append(f"{clave}={valores[clave]}\n")
            claves_actualizadas.add(clave)
        else:
            nuevas_lineas.append(linea)

    for clave, valor in valores.items():
        if clave not in claves_actualizadas:
            nuevas_lineas.append(f"{clave}={valor}\n")

    ruta_temporal = f"{ruta_env}.tmp"
    with open(ruta_temporal, "w", encoding="utf-8") as archivo:
        archivo.writelines(nuevas_lineas)
    os.replace(ruta_temporal, ruta_env)
    os.environ.update(valores)

# =====================================================================
# 8. FUNCIONES PARA MATCHUPS & ESTADÍSTICAS
# =====================================================================

def cargar_matchups():
    """Carga base de datos de matchups desde JSON"""
    data = {
        "processed_matches": [],
        "stats": {"TOP": {}, "JUNGLE": {}, "MID": {}, "ADC": {}, "SUPPORT": {}},
    }
    if os.path.exists(MATCHUPS_FILE):
        try:
            with open(MATCHUPS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            emitir_log(f"✓ {MATCHUPS_FILE} cargado", "success")
        except Exception as e:
            emitir_log(f"✗ Error cargando {MATCHUPS_FILE}: {e}", "error")
            return data
    
    return limpiar_base_de_datos(data)

def guardar_matchups(data):
    """Guarda base de datos de matchups"""
    temp_file = f"{MATCHUPS_FILE}.tmp"
    try:
        if os.path.exists(MATCHUPS_FILE):
            with open(MATCHUPS_FILE, "r", encoding="utf-8") as existing_file:
                json.load(existing_file)
            backup_file = f"{MATCHUPS_FILE}.bak"
            shutil.copy2(MATCHUPS_FILE, backup_file)

        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(temp_file, MATCHUPS_FILE)
    except Exception as e:
        emitir_log(f"✗ Error guardando {MATCHUPS_FILE}: {e}", "error")
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except:
                pass

def limpiar_base_de_datos(data):
    """Limpia y normaliza base de datos de matchups"""
    stats = data.get("stats", {})
    nuevos_stats = {"TOP": {}, "JUNGLE": {}, "MID": {}, "ADC": {}, "SUPPORT": {}}
    hubo_cambios = False

    for rol, matchups in stats.items():
        rol_canon = normalizar_rol_canonico(rol)
        if rol_canon not in nuevos_stats:
            nuevos_stats[rol_canon] = {}

        for key, valores in matchups.items():
            if "_vs_" not in key:
                nuevos_stats[rol_canon][key] = valores
                continue

            c1, c2 = key.split("_vs_", 1)
            c1_norm = normalizar(c1)
            c2_norm = normalizar(c2)
            c1_limpio = ESTANDARIZAR_NOMBRES.get(c1_norm, c1_norm)
            c2_limpio = ESTANDARIZAR_NOMBRES.get(c2_norm, c2_norm)
            nueva_key = f"{c1_limpio}_vs_{c2_limpio}"

            if nueva_key != key:
                hubo_cambios = True

            if nueva_key not in nuevos_stats[rol_canon]:
                nuevos_stats[rol_canon][nueva_key] = {"wins": 0, "losses": 0, "elos": {}}

            nuevos_stats[rol_canon][nueva_key]["wins"] += valores.get("wins", 0)
            nuevos_stats[rol_canon][nueva_key]["losses"] += valores.get("losses", 0)

            elos_existentes = valores.get("elos", {})
            for elo_name, elo_stats in elos_existentes.items():
                if elo_name not in nuevos_stats[rol_canon][nueva_key]["elos"]:
                    nuevos_stats[rol_canon][nueva_key]["elos"][elo_name] = {"wins": 0, "losses": 0}

                nuevos_stats[rol_canon][nueva_key]["elos"][elo_name]["wins"] += elo_stats.get("wins", 0)
                nuevos_stats[rol_canon][nueva_key]["elos"][elo_name]["losses"] += elo_stats.get("losses", 0)

    if hubo_cambios:
        data["stats"] = nuevos_stats
        guardar_matchups(data)
        emitir_log("✓ BD limpiada y normalizada", "success")
    else:
        emitir_log("✓ BD no requiere cambios", "info")

    return data

def registrar_resultado_matchup(stats_dict, rol, champ1, champ2, gano_champ_1, elo="UNRANKED"):
    """Registra resultado de un matchup en las estadísticas"""
    c1_norm = normalizar(champ1)
    c2_norm = normalizar(champ2)
    c1_limpio = ESTANDARIZAR_NOMBRES.get(c1_norm, c1_norm)
    c2_limpio = ESTANDARIZAR_NOMBRES.get(c2_norm, c2_norm)

    if c1_limpio < c2_limpio:
        key = f"{c1_limpio}_vs_{c2_limpio}"
        c1_gana = gano_champ_1
    else:
        key = f"{c2_limpio}_vs_{c1_limpio}"
        c1_gana = not gano_champ_1

    rol_canon = normalizar_rol_canonico(rol)

    if rol_canon not in stats_dict:
        stats_dict[rol_canon] = {}
    if key not in stats_dict[rol_canon]:
        stats_dict[rol_canon][key] = {"wins": 0, "losses": 0, "elos": {}}
    if "elos" not in stats_dict[rol_canon][key]:
        stats_dict[rol_canon][key]["elos"] = {}
    if elo not in stats_dict[rol_canon][key]["elos"]:
        stats_dict[rol_canon][key]["elos"][elo] = {"wins": 0, "losses": 0}

    if c1_gana:
        stats_dict[rol_canon][key]["wins"] += 1
        stats_dict[rol_canon][key]["elos"][elo]["wins"] += 1
    else:
        stats_dict[rol_canon][key]["losses"] += 1
        stats_dict[rol_canon][key]["elos"][elo]["losses"] += 1

def extraer_ids_procesados(registros) -> set:
    """Extrae IDs de partidas procesadas"""
    ids = set()
    for registro in registros or []:
        if isinstance(registro, str):
            ids.add(registro)
        elif isinstance(registro, dict) and registro.get("id"):
            ids.add(registro["id"])
    return ids

def extraer_metadata_procesados(registros) -> dict:
    """Extrae metadata de partidas procesadas"""
    metadata = {}
    for registro in registros or []:
        if isinstance(registro, dict) and registro.get("id"):
            metadata[registro["id"]] = {k: v for k, v in registro.items() if k != "id"}
    return metadata

def serializar_procesados(ids: set, metadata: dict) -> list:
    """Serializa IDs procesados a lista"""
    registros = []
    for match_id in sorted(ids):
        registros.append({"id": match_id, **metadata.get(match_id, {})})
    return registros

def verificar_matchup_existe(rol: str, champ1: str, champ2: str):
    """Verifica si existe guía para un matchup"""
    ruta_base = "coachings"
    if not os.path.exists(ruta_base):
        return None

    c1_vars = obtener_variantes_campeon(champ1)
    c2_vars = obtener_variantes_campeon(champ2)
    roles_validos = mapear_rol(rol)

    try:
        for rol_folder in os.listdir(ruta_base):
            rol_path = os.path.join(ruta_base, rol_folder)
            if not os.path.isdir(rol_path):
                continue
            if normalizar(rol_folder) not in roles_validos:
                continue

            for champ_folder in os.listdir(rol_path):
                champ_path = os.path.join(rol_path, champ_folder)
                if not os.path.isdir(champ_path):
                    continue

                norm_champ_folder = normalizar(champ_folder)
                if norm_champ_folder in c1_vars or norm_champ_folder in c2_vars:
                    for archivo in os.listdir(champ_path):
                        if not archivo.endswith(".txt"):
                            continue
                        norm_archivo = normalizar(archivo)
                        tiene_c1 = any(v in norm_archivo for v in c1_vars)
                        tiene_c2 = any(v in norm_archivo for v in c2_vars)
                        if tiene_c1 and tiene_c2:
                            return os.path.join(champ_path, archivo)

        for root, _, files in os.walk(ruta_base):
            for archivo in files:
                if not archivo.endswith(".txt"):
                    continue
                norm_archivo = normalizar(archivo)
                if any(v in norm_archivo for v in c1_vars) and any(v in norm_archivo for v in c2_vars):
                    return os.path.join(root, archivo)

    except Exception as e:
        emitir_log(f"✗ Error en verificación de matchup: {e}", "error")

    return None

def obtener_campeones_por_rol(ruta_json):
    """Obtiene lista de campeones disponibles por rol"""
    data = cargar_matchups()
    stats = data.get("stats", {})
    campeones_por_rol = {}

    for rol, matchups in stats.items():
        campeones_set = set()
        for matchup_key in matchups.keys():
            if "_vs_" in matchup_key:
                c1, c2 = matchup_key.split("_vs_")
                campeones_set.add(formatear_nombre_campeon(c1))
                campeones_set.add(formatear_nombre_campeon(c2))
        campeones_por_rol[rol] = sorted(list(campeones_set))

    emitir_log("✓ Campeones clasificados por rol", "success")
    return campeones_por_rol

# =====================================================================
# 9. FUNCIONES RIOT API
# =====================================================================

def hacer_peticion_riot(url: str, headers: dict, max_reintentos_red: int = 1, riot_id: str = None, timeout: int = 10):
    """Realiza petición a Riot API con rate limiting y manejo de errores"""
    if shutdown_event.is_set():
        return None

    dominio = urlparse(url).netloc
    region = dominio.split(".")[0]

    with GLOBAL_LOCK:
        if region not in RIOT_RATE_LIMITS:
            RIOT_RATE_LIMITS[region] = deque()
            REGION_LOCKS[region] = threading.Lock()

    historial = RIOT_RATE_LIMITS[region]
    lock = REGION_LOCKS[region]
    intentos = 0

    while intentos < max_reintentos_red:
        if shutdown_event.is_set():
            return None

        ahora = time.time()
        espera = 0

        with lock:
            while historial and ahora - historial[0] > 120:
                historial.popleft()

            if len(historial) >= 98:
                espera = 120.0 - (ahora - historial[0]) + 0.5
            elif sum(1 for t in historial if ahora - t <= 1.0) >= 18:
                espera = 1.0
            else:
                historial.append(time.time())

        if espera > 0:
            if shutdown_event.wait(espera):
                return None
            continue

        try:
            r = requests.get(url, headers=headers, timeout=timeout)

            if r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", 10))
                emitir_log(f"⚠️ Rate limit (429). Esperando {retry_after}s...", "warning")
                if shutdown_event.wait(retry_after):
                    return None
                continue

            if r.status_code >= 500:
                intentos += 1
                emitir_log(f"✗ Error 5xx ({r.status_code}). Reintentando ({intentos}/{max_reintentos_red})", "error")
                if intentos >= max_reintentos_red:
                    return r
                if shutdown_event.wait(5):
                    return None
                continue

            return r

        except requests.exceptions.RequestException as e:
            intentos += 1
            if intentos >= max_reintentos_red:
                return None

            if shutdown_event.wait(5):
                return None

    return None

def obtener_rango_riot(puuid: str, macro_region: str, headers: dict, request_fn=None, timeout=10) -> dict:
    """Obtiene rango actual del jugador desde Riot API"""
    plataformas = {
        "europe": ["euw1", "eun1", "tr1", "ru"],
        "americas": ["na1", "br1", "la1", "la2"],
        "asia": ["kr", "jp1", "oc1", "ph2", "sg2", "tw2", "vn2"],
    }
    resultado = {"tier": "UNRANKED", "division": "", "lp": 0, "wins": 0, "losses": 0, "plataforma": ""}

    if isinstance(request_fn, int):
        timeout = request_fn
        request_fn = None

    if request_fn is None:
        request_fn = hacer_peticion_riot

    emitir_log(f"🔍 Buscando rango para PUUID: {puuid[:10]}...", "api")

    for plataforma in plataformas.get(macro_region, plataformas["europe"]):
        if shutdown_event.is_set():
            return resultado

        try:
            league_url = f"https://{plataforma}.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}"
            league_response = request_fn(league_url, headers=headers, timeout=timeout)

            if not league_response or league_response.status_code == 404:
                continue

            if league_response.status_code != 200:
                emitir_log(f"   ⚠️ [{plataforma}] HTTP {league_response.status_code}", "error")
                continue

            data_ligas = league_response.json()
            if not data_ligas:
                resultado["plataforma"] = plataforma
                continue

            solo_queue = next(
                (entry for entry in data_ligas if entry.get("queueType") == "RANKED_SOLO_5x5"),
                None,
            )

            if solo_queue:
                resultado.update({
                    "tier": solo_queue.get("tier", "UNRANKED").upper(),
                    "division": solo_queue.get("rank", ""),
                    "lp": solo_queue.get("leaguePoints", 0),
                    "wins": solo_queue.get("wins", 0),
                    "losses": solo_queue.get("losses", 0),
                    "plataforma": plataforma,
                })
                emitir_log(f"   ✅ {resultado['tier']} {resultado['division']} en {plataforma}", "success")
                return resultado
            else:
                resultado["plataforma"] = plataforma

        except requests.exceptions.RequestException as e:
            emitir_log(f"[API] Error de red en {plataforma}: {e}", "api")
            continue
        except Exception as e:
            emitir_log(f"   ✗ Error en {plataforma}: {e}", "error")
            continue

    emitir_log("✗ No se encontró rango. Devolviendo UNRANKED", "api")
    return resultado

def mandar_a_corregir(riot_id: str):
    """Envía ID problemático a archivo de corrección"""
    if not riot_id:
        return

    archivo_json = "nombres_a_corregir.json"
    try:
        datos = []
        if os.path.exists(archivo_json):
            with open(archivo_json, "r", encoding="utf-8") as f_c:
                try:
                    datos = json.load(f_c)
                except json.JSONDecodeError:
                    pass

        if riot_id not in datos:
            datos.append(riot_id)
            with open(archivo_json, "w", encoding="utf-8") as f:
                json.dump(datos, f, indent=4, ensure_ascii=False)
            emitir_log(f"📝 Jugador {riot_id} añadido a correcciones", "success")
    except Exception as ex:
        emitir_log(f"⚠️ Error escribiendo correcciones: {ex}", "error")

# Cache de PUUID con TTL
def obtener_puuid_cached(game_name: str, tag_line: str, headers: dict) -> str:
    """Obtiene PUUID con caché de 24h"""
    riot_id = f"{game_name.strip()}#{tag_line.strip()}"
    
    if riot_id in PUUID_CACHE:
        puuid, timestamp = PUUID_CACHE[riot_id]
        if time.time() - timestamp < REGION_CACHE_TTL:
            emitir_log(f"✓ PUUID desde caché: {riot_id}", "success")
            return puuid
    
    game_name_encoded = urllib.parse.quote(game_name.strip())
    tag_line_encoded = urllib.parse.quote(tag_line.strip())
    
    for reg_acc in ["europe", "americas", "asia"]:
        url_account = f"https://{reg_acc}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name_encoded}/{tag_line_encoded}"
        riot_api_ready_sync.wait()
        headers["X-Riot-Token"] = os.environ.get("RIOT_API_KEY")
        
        r_account = hacer_peticion_riot(url_account, headers)
        
        if r_account and r_account.status_code == 200:
            puuid = r_account.json().get("puuid")
            if puuid:
                PUUID_CACHE[riot_id] = (puuid, time.time())
                emitir_log(f"✓ PUUID resuelto en {reg_acc}", "success")
                return puuid
            break

    emitir_log(f"✗ Invocador '{riot_id}' no encontrado", "error")
    return None

def descubrir_macro_region_real(nick: str, headers: dict) -> str:
    """Descubre la macro región de un jugador"""
    if shutdown_event.is_set() or "#" not in nick:
        return "unknown"

    nombre, tag = nick.rsplit("#", 1)
    url_account = f"https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{quote(nombre)}/{quote(tag)}"
    headers["X-Riot-Token"] = os.environ.get("RIOT_API_KEY")

    resp_acc = hacer_peticion_riot(url_account, headers, riot_id=nick)
    if not resp_acc or resp_acc.status_code != 200:
        return "unknown"

    puuid = resp_acc.json().get("puuid")
    sub_regiones = list(SUB_TO_MACRO.keys())

    for reg in sub_regiones:
        if shutdown_event.is_set():
            return "unknown"

        url_summoner = f"https://{reg}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}"
        headers["X-Riot-Token"] = os.environ.get("RIOT_API_KEY")
        r = hacer_peticion_riot(url_summoner, headers)

        if r and r.status_code == 200:
            return SUB_TO_MACRO[reg]

    return "unknown"

# =====================================================================
# 10. FUNCIONES SCRAPER OPGG
# =====================================================================

def normalizar_tier(texto_raw):
    """Normaliza tier de OP.GG a formato estándar"""
    text = texto_raw.lower()
    if "challenger" in text or "aspirante" in text:
        return "challenger"
    if "grandmaster" in text or "gran maestro" in text:
        return "grandmaster"
    if "master" in text or "maestro" in text:
        return "master"

    patron = r"(diamond|diamante|emerald|esmeralda|platinum|platino|gold|oro|silver|plata|bronze|bronce|iron|hierro)\s*([1-4])"
    match = re.search(patron, text)
    if match:
        tier, div = match.groups()
        traducciones = {
            "diamante": "diamond", "esmeralda": "emerald", "platino": "platinum",
            "oro": "gold", "plata": "silver", "bronce": "bronze", "hierro": "iron",
        }
        tier_clean = traducciones.get(tier, tier)
        return f"{tier_clean} {div}"

    return text.strip()

async def leer_pagina(page, region, pagina):
    """Lee una página de OP.GG"""
    url = f"https://www.op.gg/leaderboards/tier?region={region}&page={pagina}"

    for intento in range(1, 4):
        try:
            await page.goto(url, timeout=30000)
            await page.wait_for_selector("table tbody tr", timeout=15000)

            jugadores_raw = await page.evaluate("""() => {
                const filas = document.querySelectorAll('table tbody tr');
                const data = [];
                for (const fila of filas) {
                    const link = fila.querySelector('a[href*="/summoners/"]');
                    if (!link) continue;
                    const nameSpan = link.querySelector('span.whitespace-pre-wrap');
                    const tagSpan = link.querySelector('span.text-gray-500');
                    const tierCell = fila.querySelectorAll('td')[2];
                    const nombre = nameSpan ? nameSpan.innerText.trim() : '';
                    const tag = tagSpan ? tagSpan.innerText.trim() : '';
                    const tier = tierCell ? tierCell.innerText.trim() : '';
                    if (nombre) {
                        data.push({jugador: nombre + tag, tier: tier});
                    }
                }
                return data;
            }""")

            if not jugadores_raw:
                return pagina, [], True, "sin filas"

            jugadores_con_tier = [(d["jugador"], normalizar_tier(d["tier"])) for d in jugadores_raw]
            return pagina, jugadores_con_tier, False, ""

        except PlaywrightTimeoutError:
            if intento == 3:
                emitir_log(f"✗ Timeout persistente en {region.upper()} pág {pagina}", "error")
            await asyncio.sleep(2)
        except Exception as e:
            emitir_log(f"✗ Error en {region.upper()} pág {pagina}: {e}", "error")
            return pagina, [], True, "error"

    return pagina, [], True, "timeout"

async def scrape_opgg_async():
    """Scraper principal de OP.GG"""
    regiones = [
        "kr", "na", "euw", "eune", "oce", "jp", "br", "las", "lan", "ru", "tr", "sg", "tw", "vn", "th", "ph",
    ]
    region_inicial, pagina_guardada = cargar_checkpoint()
    indice_region = regiones.index(region_inicial) if region_inicial in regiones else 0

    todos_los_jugadores = cargar_datos_previos(ARCHIVO_JSON)
    jugadores_vistos = set(todos_los_jugadores)

    tiers_sin_limite = {"challenger", "grandmaster", "master"}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent=random.choice(USER_AGENTS)  # 🔥 User-Agent aleatorio
        )

        await context.route(
            "**/*",
            lambda route: (
                route.abort()
                if route.request.resource_type in ["image", "stylesheet", "font", "media"]
                else route.continue_()
            ),
        )

        pages = [await context.new_page() for _ in range(NUM_PESTANAS)]

        for region in regiones[indice_region:]:
            if shutdown_event.is_set():
                emitir_log("⚠️ Señal de apagado detectada", "success")
                await browser.close()
                SCRAPER_STATE["status"] = "idle"
                return

            if SCRAPER_STATE["status"] == "stopping":
                emitir_log("⚠️ Scraper detenido por usuario", "success")
                await browser.close()
                SCRAPER_STATE["status"] = "idle"
                return

            if SCRAPER_STATE["status"] == "paused":
                emitir_log("⏸️ Scraper pausado", "info")
                while SCRAPER_STATE["status"] == "paused" and not shutdown_event.is_set():
                    await asyncio.sleep(1)

            SCRAPER_STATE["region"] = region
            emitir_log(f"--- [ {region.upper()} ] ---", "info")
            alcanzado_limite_elo = False
            pagina_base = max(1, pagina_guardada) if region == region_inicial else 1
            conteo_tier_region = defaultdict(int)

            while not alcanzado_limite_elo:
                if shutdown_event.is_set() or SCRAPER_STATE["status"] == "stopping":
                    emitir_log("⚠️ Scraper detenido por usuario", "success")
                    await browser.close()
                    SCRAPER_STATE["status"] = "idle"
                    return

                paginas_bloque = list(range(pagina_base, pagina_base + NUM_PESTANAS))

                emitir_log(f"⏩ [{region.upper()}] Bloques {paginas_bloque[0]}-{paginas_bloque[-1]}...", "info")

                try:
                    resultados = await asyncio.wait_for(
                        asyncio.gather(
                            *[leer_pagina(pages[i], region, pag) for i, pag in enumerate(paginas_bloque)]
                        ),
                        timeout=60
                    )
                except asyncio.TimeoutError:
                    emitir_log(f"⚠️ [{region.upper()}] Timeout leyendo páginas, saltando bloque", "warning")
                    if SCRAPER_STATE["status"] == "stopping":
                        await browser.close()
                        SCRAPER_STATE["status"] = "idle"
                        return
                    pagina_base += NUM_PESTANAS
                    await asyncio.sleep(random.uniform(1.5, 3.0))
                    continue

                jugadores_nuevos_en_bloque = 0
                for pagina, jugadores_tier, es_fin, motivo in sorted(resultados):
                    if es_fin and motivo == "sin filas":
                        alcanzado_limite_elo = True
                        break

                    for jugador_completo, tier in jugadores_tier:
                        if (tier in tiers_sin_limite or conteo_tier_region[tier] < MAX_JUGADORES_POR_TIER):
                            if jugador_completo not in jugadores_vistos:
                                todos_los_jugadores.append(jugador_completo)
                                jugadores_vistos.add(jugador_completo)
                                conteo_tier_region[tier] += 1
                                jugadores_nuevos_en_bloque += 1

                        if conteo_tier_region["iron 4"] >= MAX_JUGADORES_POR_TIER:
                            alcanzado_limite_elo = True
                            break

                    if alcanzado_limite_elo:
                        break

                paginas_completadas = [
                    pag for pag, _, es_fin, motivo in sorted(resultados)
                    if motivo not in ("error", "timeout")
                ]

                if paginas_completadas:
                    guardar_checkpoint(region, max(paginas_completadas))

                if SCRAPER_STATE["status"] == "stopping":
                    emitir_log("⚠️ Scraper detenido por usuario", "success")
                    await browser.close()
                    SCRAPER_STATE["status"] = "idle"
                    return

                if jugadores_nuevos_en_bloque > 0:
                    SCRAPER_STATE["players_processed"] = len(todos_los_jugadores)
                    SCRAPER_STATE["page"] = pagina_base
                    emitir_log(
                        f"💾 [{region.upper()}] +{jugadores_nuevos_en_bloque} jugadores (Total: {len(todos_los_jugadores)})",
                        "success",
                    )
                    guardado_atomico(todos_los_jugadores, ARCHIVO_JSON)
                else:
                    emitir_log(f"⏭️ [{region.upper()}] Límite alcanzado", "info")

                if alcanzado_limite_elo:
                    siguiente_indice = regiones.index(region) + 1
                    if siguiente_indice < len(regiones):
                        guardar_checkpoint(regiones[siguiente_indice], 0)
                    break

                pagina_base += NUM_PESTANAS
                await asyncio.sleep(random.uniform(1.5, 3.0))  # 🔥 Delays humanos

        await browser.close()

    emitir_log(f"✓ Scraper completado: {len(todos_los_jugadores)} jugadores", "success")
    SCRAPER_STATE["status"] = "idle"

def limpiar_unknowns_region_cache():
    """Limpia cacheregional de jugadores unknown"""
    archivo_cache = "region_cache.json"
    archivo_corregir = "nombres_a_corregir.json"

    if not os.path.exists(archivo_cache) or shutdown_event.is_set():
        return

    try:
        with open(archivo_cache, "r", encoding="utf-8") as f:
            cache_data = json.load(f)
    except:
        return

    usuarios_unknown = [jug for jug, region in cache_data.items() if region == "unknown"]

    if not usuarios_unknown or shutdown_event.is_set():
        return

    for jugador in usuarios_unknown:
        del cache_data[jugador]

    if not shutdown_event.is_set():
        try:
            with open(archivo_cache, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, indent=4, ensure_ascii=False)
            emitir_log(f"🧹 Eliminados {len(usuarios_unknown)} 'unknown'", "info")
        except:
            pass

    datos_corregir = []
    if os.path.exists(archivo_corregir):
        try:
            with open(archivo_corregir, "r", encoding="utf-8") as f:
                datos_corregir = json.load(f)
        except:
            pass

    nuevos_añadidos = 0
    for jugador in usuarios_unknown:
        if shutdown_event.is_set():
            return
        if jugador not in datos_corregir:
            datos_corregir.append(jugador)
            nuevos_añadidos += 1

    if nuevos_añadidos > 0 and not shutdown_event.is_set():
        try:
            with open(archivo_corregir, "w", encoding="utf-8") as f:
                json.dump(datos_corregir, f, indent=4, ensure_ascii=False)
            emitir_log(f"✅ {nuevos_añadidos} trasladados a correcciones", "success")
        except:
            pass

def optimizar_orden_preload(ruta_json: str = "preload.json", ruta_cache: str = "region_cache.json", reintentar_unknown: bool = False):
    """Optimiza orden de preload intercalando regiones"""
    if not os.path.exists(ruta_json):
        return []

    cache = {}
    if os.path.exists(ruta_cache):
        try:
            with open(ruta_cache, "r", encoding="utf-8") as f:
                cache = json.load(f)
            emitir_log(f"✓ Caché cargada con {len(cache)} registros", "success")
        except:
            pass

    api_key = os.getenv("RIOT_API_KEY")
    headers = {"X-Riot-Token": api_key} if api_key else {}

    try:
        with open(ruta_json, "r", encoding="utf-8") as f:
            nicks = json.load(f)

        buckets = {"europe": [], "americas": [], "asia": [], "unknown": []}
        total_cuentas = len(nicks)
        emitir_log(f"🔍 Optimizando {total_cuentas} cuentas", "info")

        nuevos_procesados = 0

        for index, nick in enumerate(nicks):
            if shutdown_event.is_set():
                if nuevos_procesados > 0:
                    with open(ruta_cache, "w", encoding="utf-8") as fc:
                        json.dump(cache, fc, indent=4, ensure_ascii=False)
                return []

            en_cache = nick in cache
            es_unknown = en_cache and cache[nick] == "unknown"

            if en_cache and (not es_unknown or not reintentar_unknown):
                region = cache.get(nick, "unknown")
                if region not in buckets:
                    region = "unknown"
                buckets[region].append(nick)
            else:
                emitir_log(f"📡 [{index + 1}/{total_cuentas}] Consultando: {nick}", "info")
                region = descubrir_macro_region_real(nick, headers)
                if shutdown_event.is_set():
                    return []
                cache[nick] = region
                buckets[region].append(nick)
                nuevos_procesados += 1

                if nuevos_procesados > 0 and nuevos_procesados % 25 == 0:
                    with open(ruta_cache, "w", encoding="utf-8") as fc:
                        json.dump(cache, fc, indent=4, ensure_ascii=False)

        if nuevos_procesados > 0:
            with open(ruta_cache, "w", encoding="utf-8") as fc:
                json.dump(cache, fc, indent=4, ensure_ascii=False)

        lista_intercalada = []
        for eu, am, asi in itertools.zip_longest(
            buckets["europe"], buckets["americas"], buckets["asia"]
        ):
            if eu:
                lista_intercalada.append(eu)
            if am:
                lista_intercalada.append(am)
            if asi:
                lista_intercalada.append(asi)

        lista_intercalada.extend(buckets["unknown"])

        with open(ruta_json, "w", encoding="utf-8") as f:
            json.dump(lista_intercalada, f, indent=4, ensure_ascii=False)

        emitir_log(f"✅ Preload optimizado: {len(lista_intercalada)} cuentas", "success")
        return lista_intercalada

    except Exception as e:
        emitir_log(f"✗ Error en optimizar_orden_preload: {e}", "error")
        return []

# =====================================================================
# 11. FUNCIONES GENERACIÓN DE GUÍAS CON IA
# =====================================================================

def obtener_top_matchups(limite=10):
    """Obtiene top matchups con guías disponibles"""
    if not os.path.exists(MATCHUPS_FILE):
        return {}

    try:
        with open(MATCHUPS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        stats = data.get("stats", {})
        matchups_agrupados = {
            "Top": [], "Jungle": [], "Mid": [], "Adc": [], "Support": [],
        }
        matchups_dict = {}

        for rol, matchups in stats.items():
            rol_cap = rol.capitalize()
            if rol_cap not in matchups_agrupados:
                matchups_agrupados[rol_cap] = []

            for match_key, resultados in matchups.items():
                if "_vs_" not in match_key:
                    continue

                champ_main, champ_enemy = match_key.split("_vs_")
                c_main_title = champ_main.title()
                c_enemy_title = champ_enemy.title()

                if c_main_title > c_enemy_title:
                    c_main_title, c_enemy_title = c_enemy_title, c_main_title
                    wins = resultados.get("losses", 0)
                    losses = resultados.get("wins", 0)
                else:
                    wins = resultados.get("wins", 0)
                    losses = resultados.get("losses", 0)

                clave_unica = (rol_cap, c_main_title, c_enemy_title)

                ruta_guia_1 = os.path.join("coachings", rol_cap, c_main_title, f"{c_main_title}_vs_{c_enemy_title}_IA.txt")
                ruta_guia_yt_1 = os.path.join("coachings", rol_cap, c_main_title, f"{c_main_title}_vs_{c_enemy_title}.txt")
                ruta_guia_2 = os.path.join("coachings", rol_cap, c_enemy_title, f"{c_enemy_title}_vs_{c_main_title}_IA.txt")
                ruta_guia_yt_2 = os.path.join("coachings", rol_cap, c_enemy_title, f"{c_enemy_title}_vs_{c_main_title}.txt")

                tiene_guia = os.path.exists(ruta_guia_1) or os.path.exists(ruta_guia_yt_1) or os.path.exists(ruta_guia_2) or os.path.exists(ruta_guia_yt_2)

                if clave_unica in matchups_dict:
                    matchups_dict[clave_unica]["total"] += wins + losses
                    matchups_dict[clave_unica]["wins"] += wins
                    matchups_dict[clave_unica]["losses"] += losses
                    matchups_dict[clave_unica]["tiene_guia"] = matchups_dict[clave_unica]["tiene_guia"] or tiene_guia
                else:
                    matchups_dict[clave_unica] = {
                        "rol": rol_cap, "champ_main": c_main_title, "champ_enemy": c_enemy_title,
                        "total": wins + losses, "wins": wins, "losses": losses, "tiene_guia": tiene_guia,
                    }

        for item in matchups_dict.values():
            matchups_agrupados[item["rol"]].append(item)

        for rol in matchups_agrupados:
            matchups_agrupados[rol].sort(key=lambda x: x["total"], reverse=True)
            matchups_agrupados[rol] = matchups_agrupados[rol][:limite]

        return matchups_agrupados

    except Exception as e:
        emitir_log(f"✗ Error leyendo stats: {e}", "error")
        return {}

def obtener_top_matchups_sin_guia(limite=10):
    """Obtiene top matchups SIN guías disponibles"""
    if not os.path.exists(MATCHUPS_FILE):
        return {}

    try:
        with open(MATCHUPS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        stats = data.get("stats", {})
        matchups_agrupados = {
            "Top": [], "Jungle": [], "Mid": [], "Adc": [], "Support": [],
        }
        matchups_dict = {}

        for rol, matchups in stats.items():
            rol_cap = rol.capitalize()
            if rol_cap not in matchups_agrupados:
                matchups_agrupados[rol_cap] = []

            for match_key, resultados in matchups.items():
                if "_vs_" not in match_key:
                    continue

                champ_main, champ_enemy = match_key.split("_vs_")
                c_main_title = champ_main.title()
                c_enemy_title = champ_enemy.title()

                if c_main_title > c_enemy_title:
                    c_main_title, c_enemy_title = c_enemy_title, c_main_title
                    wins = resultados.get("losses", 0)
                    losses = resultados.get("wins", 0)
                else:
                    wins = resultados.get("wins", 0)
                    losses = resultados.get("losses", 0)

                clave_unica = (rol_cap, c_main_title, c_enemy_title)

                ruta_guia_1 = os.path.join("coachings", rol_cap, c_main_title, f"{c_main_title}_vs_{c_enemy_title}_IA.txt")
                ruta_guia_yt_1 = os.path.join("coachings", rol_cap, c_main_title, f"{c_main_title}_vs_{c_enemy_title}.txt")
                ruta_guia_2 = os.path.join("coachings", rol_cap, c_enemy_title, f"{c_enemy_title}_vs_{c_main_title}_IA.txt")
                ruta_guia_yt_2 = os.path.join("coachings", rol_cap, c_enemy_title, f"{c_enemy_title}_vs_{c_main_title}.txt")

                tiene_guia = os.path.exists(ruta_guia_1) or os.path.exists(ruta_guia_yt_1) or os.path.exists(ruta_guia_2) or os.path.exists(ruta_guia_yt_2)

                if not tiene_guia:
                    if clave_unica in matchups_dict:
                        matchups_dict[clave_unica]["total"] += wins + losses
                        matchups_dict[clave_unica]["wins"] += wins
                        matchups_dict[clave_unica]["losses"] += losses
                    else:
                        matchups_dict[clave_unica] = {
                            "rol": rol_cap, "champ_main": c_main_title, "champ_enemy": c_enemy_title,
                            "total": wins + losses, "wins": wins, "losses": losses, "tiene_guia": False,
                        }

        for item in matchups_dict.values():
            matchups_agrupados[item["rol"]].append(item)

        for rol in matchups_agrupados:
            matchups_agrupados[rol].sort(key=lambda x: x["total"], reverse=True)
            matchups_agrupados[rol] = matchups_agrupados[rol][:limite]

        return matchups_agrupados

    except Exception as e:
        emitir_log(f"✗ Error leyendo stats: {e}", "error")
        return {}

def generar_guia_ia_directa(rol, champ_main, champ_enemy):
    """Genera guía IA directa sin vídeo"""
    if not GEMINI_API_KEYS:
        emitir_log("✗ No hay API Keys de Gemini configuradas", "error")
        return False

    prompt = f"""Eres un Coach profesional y analista de League of Legends (nivel Challenger). 
Genera una guía MUY CLARA Y DIRECTA para el siguiente matchup.

Matchup: {champ_main} (tu campeón) contra {champ_enemy} (campeón rival).
Rol: {rol}

El campo "guia" DEBE estar en ESPAÑOL, en texto claro, con estructura estricta:

DIFICULTAD: [Nota del 1 al 10 justificada en 1 línea]

EARLY GAME:
- [Puntos clave: trades nivel 1-3, control de oleada, gankeos, prioridades]

MID GAME:
- [Puntos clave: power spikes, control de visión, rotaciones, peleas por objetivos]

LATE GAME:
- [Puntos clave: rol en teamfights, win condition, posicionamiento]
"""

    for modelo in ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]:
        for api_key in GEMINI_API_KEYS:
            try:
                client = genai.Client(api_key=api_key)
                res = client.models.generate_content(
                    model=modelo,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=RespuestaCoaching,
                    ),
                )
                datos = json.loads(res.text)

                ruta_carpeta = os.path.join("coachings", rol.capitalize(), champ_main.title())
                os.makedirs(ruta_carpeta, exist_ok=True)
                ruta_completa = os.path.join(ruta_carpeta, f"{champ_main.title()}_vs_{champ_enemy.title()}_IA.txt")

                dificultad = datos.get("dificultad_matchup", 5)
                guia = datos.get("guia", "Sin guía disponible.")

                contenido_archivo = f"""FUENTE: Generado por IA Experta sin vídeo (Gemini {modelo})
MATCHUP: {champ_main.title()} vs {champ_enemy.title()}
ROL: {rol.capitalize()}
DIFICULTAD APROX: {dificultad}/10
{'='*50}

{guia}"""

                with open(ruta_completa, "w", encoding="utf-8") as f:
                    f.write(contenido_archivo)

                emitir_log(f"✓ Guía IA generada: {ruta_completa}", "success")
                return True

            except Exception as e:
                error_text = str(e)
                if "429" in error_text or "RESOURCE_EXHAUSTED" in error_text or "quota" in error_text.lower():
                    emit_api_status("gemini", "quota_exceeded")
                continue

    emitir_log("✗ Fallo al generar guía con IA", "error")
    return False

# =====================================================================
# 12. FUNCIONES LCU - EXPORTAR AMIGOS
# =====================================================================

def get_lcu_credentials():
    """Extrae puerto y token del cliente de LoL"""
    for proc in psutil.process_iter(["name", "cmdline"]):
        if proc.info["name"] in ["LeagueClientUx.exe", "LeagueClient"]:
            cmdline = proc.info["cmdline"]
            port = None
            token = None
            for arg in cmdline:
                if arg.startswith("--app-port="):
                    port = arg.split("=")[1]
                elif arg.startswith("--remoting-auth-token="):
                    token = arg.split("=")[1]
            if port and token:
                return port, token
    return None, None

def exportar_amigos_lcu():
    """Exporta lista de amigos desde LCU API"""
    port, token = get_lcu_credentials()

    if not port or not token:
        emitir_log("✗ Cliente de LoL no encontrado", "error")
        return []

    credentials = base64.b64encode(f"riot:{token}".encode("utf-8")).decode("utf-8")
    headers = {"Authorization": f"Basic {credentials}", "Accept": "application/json"}

    url = f"https://127.0.0.1:{port}/lol-chat/v1/friends"

    try:
        response = requests.get(url, headers=headers, verify=False)

        if response.status_code == 200:
            friends_data = response.json()
            amigos_filtrados = []
            for amigo in friends_data:
                nombre = amigo.get("gameName", "")
                tag = amigo.get("gameTag", "")
                if nombre and tag:
                    amigos_filtrados.append(f"{nombre}#{tag}")
                elif nombre:
                    amigos_filtrados.append(nombre)

            with open("lol_friends.json", "w", encoding="utf-8") as f:
                json.dump(amigos_filtrados, f, indent=4, ensure_ascii=False)

            emitir_log(f"✓ {len(amigos_filtrados)} amigos exportados", "success")
            return amigos_filtrados

    except Exception as e:
        emitir_log(f"✗ Error accediendo LCU: {e}", "error")
        return []

# =====================================================================
# 13. FUNCIONES BOT RIOT DEVELOPER
# =====================================================================

def parse_remaining_minutes(text):
    """Parsea minutos restantes del sitio de Riot"""
    match = re.search(r"in\s+(?:(\d+)\s+hours?\s+and\s+)?(\d+)\s+minutes", text, re.IGNORECASE)
    if match:
        hours = int(match.group(1)) if match.group(1) else 0
        minutes = int(match.group(2))
        return (hours * 60) + minutes
    if "expired" in text.lower() or "0 minutes" in text.lower():
        return 0
    return 9999

def renovar_api_key_riot(api_event=None):
    """Renueva API Key de Riot automáticamente"""
    load_dotenv(ENV_PATH)

    try:
        with sync_playwright() as p:
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=CHROME_PROFILE_PATH,
                    channel="chrome",
                    headless=False,
                    no_viewport=True,
                    ignore_default_args=["--enable-automation"],
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--start-maximized",
                    ],
                    permissions=["clipboard-read", "clipboard-write"],
                )
            except Exception as ctx_err:
                error_str = str(ctx_err).lower()
                if "lock" in error_str or "in use" in error_str:
                    emitir_log("✗ Chrome profile bloqueado (ventana abierta o OneDrive)", "error")
                raise ctx_err

            page = context.pages[0] if context.pages else context.new_page()
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            page.goto("https://developer.riotgames.com/", wait_until="networkidle")

            logged_in = False
            try:
                page.wait_for_selector("p:has-text('Expire')", timeout=5000)
                logged_in = True
            except:
                logged_in = False

            if not logged_in:
                emitir_log("⚠️ Iniciando sesión...", "info")
                try:
                    login_btn = page.locator("a:has-text('LOG IN'), a:has-text('Log In')").first
                    if login_btn.is_visible(timeout=2000):
                        login_btn.click()
                except:
                    pass
                page.wait_for_selector("p:has-text('Expire')", timeout=120000)

            expiration_locator = page.locator("p:has-text('Expire')").first
            expiration_text = expiration_locator.inner_text()
            minutes_left = parse_remaining_minutes(expiration_text)

            emitir_log(f"⏳ API Key expira en {minutes_left} minutos", "api")

            if minutes_left < 30:
                emitir_log("⚠️ Renovando API Key...", "warning")

                if api_event:
                    api_event.clear()

                try:
                    show_btn = page.locator("button:has-text('Show')").first
                    if show_btn.is_visible(timeout=2000):
                        show_btn.scroll_into_view_if_needed()
                        page.mouse.wheel(0, -250)
                        page.wait_for_timeout(600)
                        show_btn.click()
                        page.wait_for_timeout(500)
                    clave_vieja = page.locator("#apikey").get_attribute("value")
                except:
                    clave_vieja = None

                captcha_frame = page.frame_locator('iframe[title="reCAPTCHA"]').first
                captcha_checkbox = captcha_frame.locator("#recaptcha-anchor")
                iframe_container = page.locator('iframe[title="reCAPTCHA"]').first

                if iframe_container.is_visible(timeout=5000):
                    iframe_container.scroll_into_view_if_needed()
                    page.mouse.wheel(0, -250)
                    page.wait_for_timeout(500)

                if captcha_checkbox.is_visible(timeout=5000):
                    if captcha_checkbox.get_attribute("aria-checked") != "true":
                        captcha_checkbox.click()
                        emitir_log("🤖 Resolviendo reCAPTCHA...", "info")

                        try:
                            captcha_frame.locator('#recaptcha-anchor[aria-checked="true"]').wait_for(timeout=5000)
                            emitir_log("✓ reCAPTCHA superado", "success")
                        except:
                            emitir_log("⚠️ Tienes 60s para resolver manualmente", "warning")
                            captcha_frame.locator('#recaptcha-anchor[aria-checked="true"]').wait_for(timeout=60000)
                            emitir_log("✓ reCAPTCHA resuelto", "success")

                print("[RENOVADOR] 🔄 Buscando botón de Regenerar...")
                boton_regenerar = page.locator("[name='confirm_action']").first
                boton_regenerar.scroll_into_view_if_needed()
                page.mouse.wheel(0, 150)
                page.wait_for_timeout(1000)
                boton_regenerar.click()

                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(4000)

                if page.locator("text=Invalid captcha!").is_visible() or page.locator("text=Invalid captcha").is_visible():
                    emitir_log("✗ Riot rechazó el captcha", "error")
                    emit_api_status("riot", "captcha_failed")
                    page.wait_for_timeout(30000)
                    context.close()
                    return None, 10

                emitir_log("📋 Extrayendo nueva API Key...", "info")
                page.wait_for_selector("#apikey", state="visible", timeout=15000)

                try:
                    show_btn_new = page.locator("button:has-text('Show')").first
                    if show_btn_new.is_visible(timeout=2000):
                        show_btn_new.scroll_into_view_if_needed()
                        page.mouse.wheel(0, -250)
                        page.wait_for_timeout(500)
                        show_btn_new.click()
                        page.locator("#apikey").wait_for(state="visible", timeout=5000)
                except:
                    pass

                new_key = page.locator("#apikey").get_attribute("value")

                if not new_key:
                    emitir_log("✗ No se pudo extraer la API Key", "error")
                    emit_api_status("riot", "extraction_failed")
                    context.close()
                    return None, minutes_left

                emitir_log(f"✓ Nueva API Key obtenida", "success")
                emit_api_status("riot", "ok", datetime.now().isoformat())

                set_key(".env", "RIOT_API_KEY", new_key)
                os.environ["RIOT_API_KEY"] = new_key
                global RIOT_API_KEY
                RIOT_API_KEY = new_key

                if api_event:
                    api_event.set()

                context.close()
                return new_key, minutes_left

            if api_event:
                api_event.set()

            context.close()
            return None, minutes_left

    except Exception as e:
        emitir_log(f"✗ Error en renovación de API Key: {e}", "error")
        if api_event:
            api_event.set()
        return None, 600

def background_key_manager(loop_principal):
    """Gestor de renovación de API Key en background"""
    llave_actual = os.getenv("RIOT_API_KEY", "")

    # Verificar si la key actual funciona ANTES de abrir Chrome
    if llave_actual:
        try:
            test_url = "https://europe.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Element/EUW"
            test_r = requests.get(test_url, headers={"X-Riot-Token": llave_actual}, timeout=10)
            if test_r.status_code == 200:
                emitir_log("✓ API Key válida. No se necesita renovar aún.", "api")
                riot_api_ready_sync.set()
                # Esperar 4 horas antes de verificar (la key dura 24h)
                shutdown_event.wait(4 * 3600)
            else:
                emitir_log(f"⚠️ API Key devuelve HTTP {test_r.status_code}. Intentando renovar...", "warning")
        except requests.exceptions.RequestException:
            emitir_log("⚠️ No se pudo verificar API Key (sin red). Esperando...", "warning")
            shutdown_event.wait(3600)

    while not shutdown_event.is_set():
        try:
            nueva_llave, minutes_left = renovar_api_key_riot(api_event=riot_api_ready_sync)

            if nueva_llave and nueva_llave != llave_actual:
                llave_actual = nueva_llave
                wait_seconds = 6 * 3600
                emitir_log("⏳ Siguiente revisión en 6 horas...", "api")
            else:
                wait_minutes = max(minutes_left - 32, 5)
                wait_minutes = min(wait_minutes, 360)
                wait_seconds = wait_minutes * 60
                emitir_log(f"⏳ Siguiente revisión en {wait_minutes} min...", "api")

        except Exception as e:
            emitir_log(f"✗ Error en gestor de renovación: {e}", "error")
            riot_api_ready_sync.set()
            wait_seconds = 600

        shutdown_event.wait(wait_seconds)

# =====================================================================
# 14. LIFESPAN & APLICACIÓN FASTAPI
# =====================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan management para startup y shutdown"""
    emitir_log("🚀 Iniciando servidor web...", "info")

    loop = asyncio.get_running_loop()
    global EVENT_LOOP
    EVENT_LOOP = loop

    global riot_api_ready_async
    riot_api_ready_async = asyncio.Event()
    riot_api_ready_async.set()

    loop.run_in_executor(None, limpiar_unknowns_region_cache)

    await asyncio.sleep(1)

    renovador_thread = threading.Thread(
        target=background_key_manager, args=(loop,), daemon=True
    )
    renovador_thread.start()

    emitir_log("✅ Servidor listo. El scraper se inicia cuando el usuario lo solicita.", "success")

    yield

    emitir_log("🛑 Apagando servidor...", "success")

    shutdown_event.set()
    SCRAPER_STATE["status"] = "stopping"

    if hasattr(app.state, "scraper_task") and not app.state.scraper_task.done():
        app.state.scraper_task.cancel()
        try:
            await app.state.scraper_task
        except asyncio.CancelledError:
            emitir_log("✓ Scraper cancelado", "success")

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory="."), name="static")

CAMPEONES_POR_ROL = obtener_campeones_por_rol(MATCHUPS_FILE)

# =====================================================================
# 15. ENDPOINTS - SERVICIOS GENERALES
# =====================================================================

@app.get("/favicon.ico", include_in_schema=False)
@app.get("/favicon.svg", include_in_schema=False)
async def favicon():
    return FileResponse("favicon.svg", media_type="image/svg+xml")

@app.get("/")
def serve_index():
    return FileResponse("index.html")

@app.get("/api/stream-logs")
async def stream_logs():
    """SSE para streaming de logs"""
    return StreamingResponse(log_generator(), media_type="text/event-stream")

@app.get("/api/status")
def get_status():
    """Obtiene estado del sistema"""
    return {
        "scraper": SCRAPER_STATE,
        "preload": PRELOAD_STATUS,
        "api_status": API_STATUS,
    }

# =====================================================================
# 16b. FUNCIONES AUXILIARES DE PRELOAD (restauradas)
# =====================================================================

def limpiar_duplicados_preload(ruta_archivo: str = "preload.json") -> list:
    emitir_log(
        f"[DUPLICADOS] Iniciando limpieza de duplicados en {ruta_archivo}", "info"
    )
    if not os.path.exists(ruta_archivo):
        emitir_log(
            f"[DUPLICADOS] Archivo {ruta_archivo} no encontrado. Devolviendo lista vacía.",
            "error",
        )
        return []

    try:
        with open(ruta_archivo, "r", encoding="utf-8") as f:
            datos = json.load(f)

        emitir_log(
            f"[DUPLICADOS] Archivo cargado. Total de registros en bruto: {len(datos)}",
            "info",
        )

        vistos = set()
        lista_limpia = []

        for item in datos:
            identificador = None
            if isinstance(item, str):
                identificador = item.strip().lower()
            elif isinstance(item, dict):
                nick = item.get("nick") or item.get("game_name", "")
                tag = item.get("tag") or item.get("tag_line", "")
                if nick and tag:
                    identificador = f"{nick.strip().lower()}#{tag.strip().lower()}"

            if identificador:
                if identificador not in vistos:
                    vistos.add(identificador)
                    lista_limpia.append(item)
                else:
                    emitir_log(
                        f"[DUPLICADOS] Elemento duplicado detectado y omitido: {identificador}",
                        "info",
                    )

        eliminados = len(datos) - len(lista_limpia)

        with open(ruta_archivo, "w", encoding="utf-8") as f:
            json.dump(lista_limpia, f, indent=4, ensure_ascii=False)

        emitir_log(
            f"[DUPLICADOS] Limpieza terminada. Registros finales: {len(lista_limpia)} (Eliminados: {eliminados})",
            "success",
        )
        return lista_limpia

    except Exception as e:
        emitir_log(
            f"[DUPLICADOS] Error crítico procesando el archivo: {str(e)}", "error"
        )
        return []


def purgar_nombres_a_corregir(
    ruta_preload: str = "preload.json", ruta_corregir: str = "nombres_a_corregir.json"
) -> list:
    emitir_log(
        f"[PURGA] Iniciando verificación de purga (Preload: '{ruta_preload}', Corregir: '{ruta_corregir}')",
        "info",
    )

    if not os.path.exists(ruta_preload):
        emitir_log(f"[PURGA] No se encontró el archivo {ruta_preload}.", "error")
        return []

    if not os.path.exists(ruta_corregir):
        emitir_log(
            f"[PURGA] No se encontró {ruta_corregir}. No hay nada que purgar.", "error"
        )
        return []

    try:
        emitir_log(
            f"[PURGA] Leyendo archivo de correcciones: {ruta_corregir}...", "info"
        )
        with open(ruta_corregir, "r", encoding="utf-8") as f_corr:
            nombres_corregir = json.load(f_corr)
        emitir_log(
            f"[PURGA] Archivo {ruta_corregir} leído correctamente ({len(nombres_corregir)} elementos).",
            "info",
        )
    except Exception as e:
        emitir_log(f"[PURGA] Error leyendo {ruta_corregir}: {e}", "error")
        return []

    if not isinstance(nombres_corregir, list) or not nombres_corregir:
        emitir_log(
            "[PURGA] El archivo de corrección está vacío o no es una lista válida.",
            "info",
        )
        return []

    set_a_corregir = {
        nombre.strip().lower() for nombre in nombres_corregir if isinstance(nombre, str)
    }
    emitir_log(
        f"[PURGA] Set de purga normalizado creado con {len(set_a_corregir)} cuentas únicas.",
        "info",
    )

    try:
        emitir_log(f"[PURGA] Cargar lista de jugadores desde {ruta_preload}...", "info")
        with open(ruta_preload, "r", encoding="utf-8") as f_pre:
            jugadores_preload = json.load(f_pre)
        emitir_log(
            f"[PURGA] {ruta_preload} cargado con éxito ({len(jugadores_preload)} jugadores).",
            "info",
        )
    except Exception as e:
        emitir_log(f"[PURGA] Error leyendo {ruta_preload}: {e}", "error")
        return []

    jugadores_filtrados = []
    eliminados = 0

    for jug in jugadores_preload:
        riot_id = None

        if isinstance(jug, str) and "#" in jug:
            riot_id = jug.strip()
        elif isinstance(jug, dict):
            game_name = jug.get("nick") or jug.get("game_name")
            tag_line = jug.get("tag") or jug.get("tag_line")
            if game_name and tag_line:
                riot_id = f"{game_name.strip()}#{tag_line.strip()}"

        if riot_id and riot_id.lower() in set_a_corregir:
            eliminados += 1
            emitir_log(
                f"[PURGA] Coincidencia encontrada. Purgando cuenta: {riot_id}", "info"
            )
        else:
            jugadores_filtrados.append(jug)

    if eliminados > 0:
        try:
            emitir_log(
                f"[PURGA] Escribiendo cambios en {ruta_preload} (Se conservan {len(jugadores_filtrados)} registros)...",
                "info",
            )
            with open(ruta_preload, "w", encoding="utf-8") as f_pre:
                json.dump(jugadores_filtrados, f_pre, indent=4, ensure_ascii=False)
            emitir_log(
                f"[PURGA] Se eliminaron {eliminados} jugadores problemáticos de {ruta_preload}.",
                "success",
            )
        except Exception as e:
            emitir_log(f"[PURGA] Error guardando {ruta_preload}: {e}", "error")
    else:
        emitir_log("[PURGA] No se encontraron coincidencias para eliminar.", "info")

    emitir_log(
        f"[PURGA] Proceso de purga completado. Retornando {len(jugadores_filtrados)} jugadores.",
        "success",
    )
    return jugadores_filtrados


def ejecutar_preload_task(region: str = "europe"):
    global PRELOAD_STATUS
    emitir_log(
        f"[PRELOAD_TASK] Arrancando hilo principal de extracción en macro-región base: {region}",
        "info",
    )

    ARCHIVO_CACHE_LEIDOS = "cache_jugadores_leidos.json"
    jugadores_leidos = set()

    try:
        emitir_log("[PRELOAD_TASK] Fase 1: Limpieza e inicialización...", "info")
        limpiar_duplicados_preload("preload.json")
        jugadores = purgar_nombres_a_corregir("preload.json", "nombres_a_corregir.json")
        emitir_log(
            f"[PRELOAD_TASK] Lista de jugadores obtenida para esta sesión: {len(jugadores)} en total.",
            "info",
        )

        if os.path.exists(ARCHIVO_CACHE_LEIDOS):
            with open(ARCHIVO_CACHE_LEIDOS, "r", encoding="utf-8") as f_c:
                jugadores_leidos = set(json.load(f_c))
            emitir_log(
                f"[PRELOAD_TASK] Caché de leídos detectada. {len(jugadores_leidos)} jugadores ya procesados.",
                "success",
            )

    except FileNotFoundError:
        emitir_log(
            "[PRELOAD_TASK] Falla de inicio: No se encontró el archivo preload.json",
            "error",
        )
        PRELOAD_STATUS["status"] = "error"
        PRELOAD_STATUS["message"] = "No se encontró el archivo preload.json"
        return
    except Exception as e:
        emitir_log(
            f"[PRELOAD_TASK] Excepción crítica al preparar archivos: {e}", "error"
        )
        PRELOAD_STATUS["status"] = "error"
        PRELOAD_STATUS["message"] = f"Error leyendo preload.json: {e}"
        return

    if not RIOT_API_KEY:
        emitir_log(
            "[PRELOAD_TASK] Falla de inicio: RIOT_API_KEY vacía en entorno.", "error"
        )
        PRELOAD_STATUS["status"] = "error"
        PRELOAD_STATUS["message"] = "No se ha configurado RIOT_API_KEY en .env"
        return

    # Validar que la API key es válida antes de procesar
    headers = {"X-Riot-Token": RIOT_API_KEY}
    try:
        test_r = requests.get(
            "https://europe.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Element/EUW",
            headers=headers, timeout=10
        )
        if test_r.status_code == 401:
            emitir_log("[PRELOAD_TASK] RIOT_API_KEY expirada o inválida (HTTP 401). Renueva la key.", "error")
            PRELOAD_STATUS["status"] = "error"
            PRELOAD_STATUS["message"] = "RIOT_API_KEY expirada. Renueva en https://developer.riotgames.com/"
            return
        elif test_r.status_code != 200:
            emitir_log(f"[PRELOAD_TASK] API responde HTTP {test_r.status_code}. Verificando...", "warning")
    except requests.exceptions.RequestException:
        emitir_log("[PRELOAD_TASK] No se pudo verificar la API key (error de red). Continuando...", "warning")

    matchup_db = cargar_matchups()

    processed_matches = extraer_ids_procesados(matchup_db.get("processed_matches", []))
    processed_metadata = extraer_metadata_procesados(
        matchup_db.get("processed_matches", [])
    )
    stats_dict = matchup_db.get(
        "stats", {"TOP": {}, "JUNGLE": {}, "MID": {}, "ADC": {}, "SUPPORT": {}}
    )

    emitir_log(
        f"[PRELOAD_TASK] DB Matchups cargada. Partidas históricas totales procesadas: {len(processed_matches)}",
        "info",
    )

    PRELOAD_STATUS["status"] = "running"
    PRELOAD_STATUS["message"] = "Iniciando precarga..."

    for index_jugador, jug in enumerate(jugadores):
        if PRELOAD_STATUS.get("status") == "stopping":
            emitir_log(
                "[PRELOAD_TASK] Señal de parada ('stopping') detectada. Rompiendo bucle principal de jugadores.",
                "info",
            )
            break

        if isinstance(jug, str):
            if "#" not in jug:
                continue
            game_name, tag_line = jug.split("#", 1)
        elif isinstance(jug, dict):
            game_name = jug.get("nick") or jug.get("game_name")
            tag_line = jug.get("tag") or jug.get("tag_line")
            if not game_name or not tag_line:
                continue
        else:
            continue

        game_name_encoded = urllib.parse.quote(game_name.strip())
        tag_line_encoded = urllib.parse.quote(tag_line.strip())
        riot_id = f"{game_name.strip()}#{tag_line.strip()}"

        if riot_id in jugadores_leidos:
            continue
        PRELOAD_STATUS["current_player"] = riot_id
        PRELOAD_STATUS["message"] = f"Buscando PUUID de {riot_id}..."

        puuid = None
        error_api = None
        for reg_acc in ["europe", "americas", "asia"]:
            url_account = f"https://{reg_acc}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name_encoded}/{tag_line_encoded}"

            riot_api_ready_sync.wait()
            headers["X-Riot-Token"] = os.environ.get("RIOT_API_KEY")

            r_account = hacer_peticion_riot(url_account, headers, max_reintentos_red=3)

            if r_account and r_account.status_code == 200:
                puuid = r_account.json().get("puuid")
                if not puuid:
                    error_api = f"Respuesta inválida de la API al buscar {riot_id}"
                    emitir_log(f"[PRELOAD_TASK] Error API: {error_api}", "error")
                else:
                    emitir_log(f"[PRELOAD_TASK] PUUID encontrado: {puuid}", "success")
                break
            elif not r_account or r_account.status_code != 404:
                status_code = r_account.status_code if r_account else "sin respuesta"
                error_api = f"Error de la API al buscar {riot_id} (HTTP {status_code})"
                emitir_log(
                    f"[PRELOAD_TASK] Rompiendo búsqueda de cuenta por error no-404: {error_api}",
                    "error",
                )
                break

        if error_api:
            es_error_api_key = "401" in str(error_api)
            es_error_red = "sin respuesta" in str(error_api) or "ConnectionError" in str(error_api)

            if es_error_api_key:
                emitir_log(
                    f"[PRELOAD] {error_api}. API Key inválida/expirada.",
                    "error",
                )
                PRELOAD_STATUS["message"] = error_api
                break
            elif es_error_red or "5" in str(error_api)[:20]:
                # Errores de red o 5xx → marcar jugador para limpieza
                emitir_log(
                    f"[PRELOAD] {error_api}. Marcando para limpieza futura.",
                    "warning",
                )
                mandar_a_corregir(riot_id)
                PRELOAD_STATUS["message"] = error_api
                continue
            else:
                emitir_log(
                    f"[PRELOAD] {error_api}. Saltando sin purgar.",
                    "info",
                )
                PRELOAD_STATUS["message"] = error_api
                continue

        if not puuid:
            emitir_log(
                f"[PRELOAD] Jugador {riot_id} no encontrado (HTTP 404 en todas). Iniciando purga en tiempo real.",
                "error",
            )
            nombres_a_corregir = []
            if os.path.exists("nombres_a_corregir.json"):
                try:
                    with open("nombres_a_corregir.json", "r", encoding="utf-8") as f_err:
                        nombres_a_corregir = json.load(f_err)
                except Exception:
                    pass

            if riot_id not in nombres_a_corregir:
                nombres_a_corregir.append(riot_id)
                with open("nombres_a_corregir.json", "w", encoding="utf-8") as f_err:
                    json.dump(nombres_a_corregir, f_err, indent=4, ensure_ascii=False)
                emitir_log(
                    f"[PURGA] {riot_id} añadido a nombres_a_corregir.json", "success"
                )

            try:
                with open("preload.json", "r", encoding="utf-8") as f_pre:
                    datos_preload = json.load(f_pre)

                datos_filtrados = []
                for item in datos_preload:
                    item_id = None
                    if isinstance(item, str) and "#" in item:
                        item_id = item.strip()
                    elif isinstance(item, dict):
                        g_n = item.get("nick") or item.get("game_name")
                        t_l = item.get("tag") or item.get("tag_line")
                        if g_n and t_l:
                            item_id = f"{g_n.strip()}#{t_l.strip()}"

                    if item_id and item_id.lower() != riot_id.lower():
                        datos_filtrados.append(item)

                with open("preload.json", "w", encoding="utf-8") as f_pre:
                    json.dump(datos_filtrados, f_pre, indent=4, ensure_ascii=False)
                emitir_log(
                    f"[PURGA] {riot_id} borrado de preload.json al instante.", "success"
                )
            except Exception as e:
                emitir_log(
                    f"[PURGA] Error borrando {riot_id} al instante: {e}", "error"
                )

            continue

        match_ids = []
        region_activa = None
        regiones_a_probar = [region] + [
            r for r in ["europe", "americas", "asia"] if r != region
        ]
        emitir_log(
            f"[PRELOAD_TASK] Buscando Match IDs. Orden de regiones: {regiones_a_probar}",
            "info",
        )

        for match_reg in regiones_a_probar:
            url_matches = f"https://{match_reg}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?start=0&count=100"

            riot_api_ready_sync.wait()
            headers["X-Riot-Token"] = os.environ.get("RIOT_API_KEY")

            emitir_log(f"[PRELOAD_TASK] GET MatchList ({match_reg})", "api")
            r_matches = hacer_peticion_riot(url_matches, headers)

            if r_matches and r_matches.status_code == 200:
                data_matches = r_matches.json()
                if len(data_matches) > 0:
                    match_ids = data_matches
                    region_activa = match_reg
                    emitir_log(
                        f"[PRELOAD_TASK] Encontradas {len(match_ids)} partidas en {match_reg}",
                        "success",
                    )
                    break
                else:
                    emitir_log(
                        f"[PRELOAD_TASK] Respuesta 200 pero lista vacía en {match_reg}",
                        "info",
                    )

        if not match_ids:
            emitir_log(
                f"[PRELOAD] Jugador {riot_id} no tiene partidas recientes en ninguna región. Marcando como leído.",
                "info",
            )
            jugadores_leidos.add(riot_id)
            with open(ARCHIVO_CACHE_LEIDOS, "w", encoding="utf-8") as f_c:
                json.dump(list(jugadores_leidos), f_c, indent=4, ensure_ascii=False)
            continue

        emitir_log(
            f"[PRELOAD] Detectada región activa: {region_activa} para {riot_id}", "info"
        )
        rango_actual = obtener_rango_riot(puuid, region_activa, headers)
        emitir_log(
            f"[PRELOAD_TASK] Rango obtenido para el usuario: Tier={rango_actual.get('tier')}, Div={rango_actual.get('division')}",
            "info",
        )

        total_jugador = len(match_ids)
        hubo_cambios = False

        for idx, match_id in enumerate(match_ids, start=1):
            if PRELOAD_STATUS.get("status") == "stopping":
                break

            PRELOAD_STATUS["processed_matches"] = idx
            PRELOAD_STATUS["total_matches"] = total_jugador
            PRELOAD_STATUS["message"] = (
                f"Jugador {riot_id} ({idx}/{total_jugador} partidas)"
            )

            if match_id in processed_matches:
                continue

            url_detail = f"https://{region_activa}.api.riotgames.com/lol/match/v5/matches/{match_id}"

            riot_api_ready_sync.wait()
            headers["X-Riot-Token"] = os.environ.get("RIOT_API_KEY")

            r_detail = hacer_peticion_riot(url_detail, headers)

            if r_detail and r_detail.status_code == 200:
                data = r_detail.json()
                info = data.get("info", {})
                queue_name = {420: "RANKED_SOLO", 440: "RANKED_FLEX"}.get(
                    info.get("queueId")
                )
                participants = info.get("participants", [])

                roles_map = {}
                for p in participants:
                    pos = extraer_rol_riot(p)
                    team = p.get("teamId")
                    if pos in ["TOP", "JUNGLE", "MID", "ADC", "SUPPORT"]:
                        if pos not in roles_map:
                            roles_map[pos] = {}
                        if team not in roles_map[pos]:
                            roles_map[pos][team] = p

                for pos, equipos in roles_map.items():
                    if 100 in equipos and 200 in equipos:
                        p1, p2 = equipos[100], equipos[200]
                        c1 = p1["championName"]
                        c2 = p2["championName"]
                        victoria_1 = p1.get("win", False)

                        registrar_resultado_matchup(
                            stats_dict,
                            pos,
                            c1,
                            c2,
                            gano_champ_1=victoria_1,
                            elo=rango_actual["tier"],
                        )

                processed_matches.add(match_id)
                if queue_name:
                    processed_metadata[match_id] = {
                        "queue": queue_name,
                        "elo": rango_actual["tier"],
                        "division": rango_actual["division"],
                        "lp": rango_actual["lp"],
                        "platform": rango_actual["plataforma"] or region_activa,
                    }
                hubo_cambios = True

                if len(processed_matches) % 10 == 0:
                    matchup_db["processed_matches"] = serializar_procesados(
                        processed_matches, processed_metadata
                    )
                    matchup_db["stats"] = stats_dict
                    guardar_matchups(matchup_db)

        if hubo_cambios:
            matchup_db["processed_matches"] = serializar_procesados(
                processed_matches, processed_metadata
            )
            matchup_db["stats"] = stats_dict
            guardar_matchups(matchup_db)

        if PRELOAD_STATUS.get("status") == "stopping":
            break

        jugadores_leidos.add(riot_id)
        with open(ARCHIVO_CACHE_LEIDOS, "w", encoding="utf-8") as f_c:
            json.dump(list(jugadores_leidos), f_c, indent=4, ensure_ascii=False)

    emitir_log(
        "[PRELOAD_TASK] Purgando residuos de nombres_a_corregir.json al final del ciclo...",
        "info",
    )
    purgar_nombres_a_corregir("preload.json", "nombres_a_corregir.json")

    if PRELOAD_STATUS.get("status") == "stopping":
        PRELOAD_STATUS["status"] = "idle"
        PRELOAD_STATUS["message"] = (
            "Precarga detenida. Se guardó el progreso de los jugadores leídos."
        )
        emitir_log(
            "[PRELOAD_TASK] 🛑 Finalizado forzosamente por orden del sistema (status=stopping).",
            "info",
        )
    else:
        PRELOAD_STATUS["status"] = "completed"
        PRELOAD_STATUS["message"] = "¡Precarga 100% finalizada! Reiniciando ciclo..."
        emitir_log(
            "[PRELOAD_TASK] ✅ Ciclo de lectura 100% completado naturalmente.",
            "success",
        )

        if os.path.exists(ARCHIVO_CACHE_LEIDOS):
            try:
                os.remove(ARCHIVO_CACHE_LEIDOS)
                emitir_log(
                    "[PRELOAD] Ciclo de lectura completado. Caché limpiado para el siguiente ciclo.",
                    "success",
                )
            except Exception as e:
                emitir_log(f"[PRELOAD] Error al limpiar la caché final: {e}", "error")


# =====================================================================
# 16c. ENDPOINTS - PRELOAD BASE DE DATOS (restaurados)
# =====================================================================

@app.post("/api/preload")
def iniciar_preload(background_tasks: BackgroundTasks):
    global PRELOAD_STATUS
    emitir_log(
        f"[API_PRELOAD] Recibida solicitud para iniciar precarga. Estado actual: '{PRELOAD_STATUS['status']}'",
        "info",
    )

    if PRELOAD_STATUS["status"] == "running":
        emitir_log(
            "[API_PRELOAD] Operación rechazada: La precarga ya está en ejecución.",
            "error",
        )
        return JSONResponse(
            {"message": "La precarga ya está ejecutándose.", "status": PRELOAD_STATUS}
        )

    emitir_log(
        "[API_PRELOAD] Programando 'ejecutar_preload_task' en background...", "success"
    )
    background_tasks.add_task(ejecutar_preload_task)
    return {"message": "Precarga iniciada en segundo plano."}


@app.post("/api/preload/stop")
def detener_preload():
    global PRELOAD_STATUS
    emitir_log(
        f"[API_PRELOAD_STOP] Recibida solicitud para detener precarga. Estado actual: '{PRELOAD_STATUS['status']}'",
        "info",
    )

    if PRELOAD_STATUS["status"] == "running":
        PRELOAD_STATUS["status"] = "stopping"
        PRELOAD_STATUS["message"] = (
            "Deteniendo de forma segura tras terminar la partida actual..."
        )
        emitir_log(
            "[API_PRELOAD_STOP] Flag cambiado a 'stopping'. Se detendrá al finalizar la iteración en curso.",
            "success",
        )
        return {"message": "Petición de parada recibida."}

    emitir_log("[API_PRELOAD_STOP] No hay ninguna precarga activa que detener.", "info")
    return {"message": "No hay ninguna precarga en ejecución."}


@app.get("/api/preload/status")
def obtener_estado_preload():
    return PRELOAD_STATUS


# =====================================================================
# 17. ENDPOINTS - CARGA DE JUGADORES (Scrapeo de nombres)
# =====================================================================

@app.get("/api/scraper/status")
def scraper_status():
    """Estado actual del scrapeo de nombres"""
    total_jugadores = 0
    try:
        if os.path.exists("preload.json"):
            with open("preload.json", "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    total_jugadores = len(data)
    except Exception:
        pass

    status = SCRAPER_STATE["status"]
    region = SCRAPER_STATE.get("region", "")
    page = SCRAPER_STATE.get("page", 0)
    players = SCRAPER_STATE.get("players_processed", 0)

    if status == "running":
        message = f"Descargando [{region.upper()}] pág. {page} — {players} jugadores"
    elif status == "paused":
        message = f"Pausado en [{region.upper()}] — {players} jugadores"
    elif status == "stopping":
        message = "Deteniendo..."
    else:
        message = f"Inactivo — {total_jugadores} jugadores totales"

    return {
        "status": status,
        "region": region,
        "page": page,
        "players": players,
        "total_players": total_jugadores,
        "message": message,
    }

@app.post("/api/scraper/start")
def start_scraper_carga():
    """Inicia la carga de jugadores (scrapeo)"""
    if SCRAPER_STATE["status"] == "running":
        return {"error": "La carga ya está en curso"}, 400

    was_idle = SCRAPER_STATE["status"] == "idle"
    SCRAPER_STATE["status"] = "running"

    if was_idle and EVENT_LOOP:
        asyncio.run_coroutine_threadsafe(scrape_opgg_async(), EVENT_LOOP)

    emitir_log("▶️ Carga de jugadores reanudada", "info")
    return {"message": "Carga de jugadores iniciada"}

@app.post("/api/scraper/stop")
def stop_scraper_carga():
    """Detiene la carga de jugadores (scrapeo)"""
    if SCRAPER_STATE["status"] == "idle":
        return {"error": "La carga no está activa"}, 400

    SCRAPER_STATE["status"] = "stopping"
    emitir_log("⏹️ Carga de jugadores detenida por usuario", "info")
    return {"message": "Carga detenida"}

# =====================================================================
# 17. ENDPOINTS - CAMPEONES & GUÍAS
# =====================================================================

@app.get("/api/campeones")
def get_campeones(rol: str = Query(None, description="TOP, JUNGLE, MID, ADC, SUPPORT")):
    """Obtiene lista de campeones"""
    if rol:
        rol_canon = normalizar_rol_canonico(rol)
        if rol_canon in CAMPEONES_POR_ROL:
            return {"campeones": sorted(CAMPEONES_POR_ROL[rol_canon])}

    try:
        resp = requests.get(
            "https://ddragon.leagueoflegends.com/cdn/14.15.1/data/es_ES/champion.json",
            timeout=3,
        )
        if resp.status_code == 200:
            champs = list(resp.json()["data"].keys())
            champs.sort()
            return {"campeones": champs}
    except:
        pass

    return {"campeones": sorted(CAMPEONES_BASE)}

@app.get("/api/guias")
def get_guias():
    """Obtiene estructura de guías locales"""
    base_dir = "coachings"
    estructura = {}
    if not os.path.exists(base_dir):
        return {"guias": estructura}
    try:
        for rol in os.listdir(base_dir):
            rol_path = os.path.join(base_dir, rol)
            if os.path.isdir(rol_path):
                estructura[rol] = {}
                for champ in os.listdir(rol_path):
                    champ_path = os.path.join(rol_path, champ)
                    if os.path.isdir(champ_path):
                        archivos = [f for f in os.listdir(champ_path) if f.endswith(".txt")]
                        if archivos:
                            estructura[rol][champ] = archivos
    except Exception as e:
        emitir_log(f"✗ Error leyendo guías: {e}", "error")
    return {"guias": estructura}

@app.get("/api/guia_matchup/{rol}/{campeon1}/{campeon2}")
def get_guia_matchup(rol: str, campeon1: str, campeon2: str):
    """Obtiene guía específica de matchup"""
    emitir_log(f"📖 Guía solicitada: {rol} | {campeon1} vs {campeon2}", "info")
    ruta_archivo = verificar_matchup_existe(rol, campeon1, campeon2)

    if not ruta_archivo:
        raise HTTPException(status_code=404, detail="Guía no encontrada")

    try:
        with open(ruta_archivo, "r", encoding="utf-8") as f:
            contenido = f.read()
        emitir_log(f"✓ Guía cargada: {ruta_archivo}", "success")
        return {"contenido": contenido}
    except Exception as e:
        emitir_log(f"✗ Error leyendo guía: {e}", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/generate-guide")
def gen_guide(datos: GuiaRequest):
    """Genera guía IA"""
    emitir_log(
        f"🤖 Generando guía IA: {datos.rol} | {datos.champ_main} vs {datos.champ_enemy}",
        "info",
    )
    exito = generar_guia_ia_directa(datos.rol, datos.champ_main, datos.champ_enemy)

    if exito:
        emitir_log(f"✓ Guía generada", "success")
        return {"success": True}

    emitir_log(f"✗ Error generando guía", "error")
    return {
        "success": False,
        "error": "Fallo en generación de guía IA",
    }

@app.get("/api/top-matchups")
def top_matchups():
    """Top 15 matchups con guías"""
    emitir_log("📊 Top matchups solicitado", "info")
    return obtener_top_matchups(15)

@app.get("/api/top-matchups-sin-guia")
def top_matchups_sin_guia():
    """Top 10 matchups sin guías"""
    emitir_log("📊 Top matchups sin guía solicitado", "info")
    return obtener_top_matchups_sin_guia(10)

# =====================================================================
# 18. ENDPOINTS - ANÁLISIS DE DRAFT
# =====================================================================

@app.get("/api/sugerencias_matchup")
def get_sugerencias_matchup(
    rol: str = Query(..., description="TOP, JUNGLE, MID, ADC, SUPPORT"),
    campeon: str = Query(..., description="Nombre del campeón"),
    side: str = Query("aliado", description="'aliado' o 'enemigo'"),
):
    """Obtiene sugerencias de matchup"""
    emitir_log(f"💡 Sugerencias para {campeon} en {rol}", "info")
    rol_canon = normalizar_rol_canonico(rol)

    norm_c = normalizar(campeon)
    norm_c = ESTANDARIZAR_NOMBRES.get(norm_c, norm_c)

    if not norm_c:
        return {
            "campeon": campeon, "rol": rol_canon, "mejores": [], "peores": []
        }

    matchup_db = cargar_matchups()
    stats_rol = matchup_db.get("stats", {}).get(rol_canon, {})

    lista_enfrentamientos = []

    for key, data in stats_rol.items():
        if "_vs_" not in key:
            continue

        c1, c2 = key.split("_vs_", 1)

        if norm_c == c1:
            rival = c2
            w = data.get("wins", 0)
            l = data.get("losses", 0)
        elif norm_c == c2:
            rival = c1
            w = data.get("losses", 0)
            l = data.get("wins", 0)
        else:
            continue

        total = w + l
        if total <= 0:
            continue

        if side == "aliado":
            wins_reales = w
            losses_reales = l
        else:
            wins_reales = l
            losses_reales = w

        wr_real = round((wins_reales / total) * 100, 1)
        rival_display = formatear_nombre_campeon(rival)
        coaching = bool(verificar_matchup_existe(rol_canon, norm_c, rival))

        lista_enfrentamientos.append({
            "campeon": rival_display,
            "winrate": wr_real,
            "partidas": total,
            "wins": wins_reales,
            "losses": losses_reales,
            "tipo": "favorable" if wr_real >= 50 else "desfavorable",
            "coaching_disponible": coaching,
        })

    mejores = sorted(
        [item for item in lista_enfrentamientos if item["winrate"] >= 50],
        key=lambda x: (x["winrate"], x["partidas"]),
        reverse=True,
    )

    peores = sorted(
        [item for item in lista_enfrentamientos if item["winrate"] < 50],
        key=lambda x: (x["winrate"], -x["partidas"]),
        reverse=False,
    )

    return {
        "campeon": formatear_nombre_campeon(norm_c),
        "rol": rol_canon,
        "side": side,
        "mejores": mejores,
        "peores": peores,
    }

@app.post("/api/analizar_draft")
def analizar_draft(req: DraftRequest):
    """Analiza draft completo"""
    emitir_log("[DRAFT] Iniciando análisis...", "info")
    matchup_db = cargar_matchups()
    stats_dict = matchup_db.get("stats", {})

    roles_input = [
        ("TOP", req.top_aliado, req.top_enemigo),
        ("JUNGLE", req.jungle_aliado, req.jungle_enemigo),
        ("MID", req.mid_aliado, req.mid_enemigo),
        ("ADC", req.adc_aliado, req.adc_enemigo),
        ("SUPPORT", req.support_aliado, req.support_enemigo),
    ]

    resultados = []

    for rol, c_aliado, c_enemigo in roles_input:
        if not c_aliado or not c_enemigo:
            resultados.append({
                "rol": rol, "aliado": c_aliado or "-", "enemigo": c_enemigo or "-",
                "estado": "sin_datos", "texto_estado": "Sin datos",
                "winrate": 0, "wins": 0, "losses": 0, "desglose_elo": [],
            })
            continue

        rol_stats = stats_dict.get(rol, {})
        aliado_norm = normalizar(c_aliado)
        enemigo_norm = normalizar(c_enemigo)

        key_directa = f"{aliado_norm}_vs_{enemigo_norm}"
        key_inversa = f"{enemigo_norm}_vs_{aliado_norm}"

        matchup_data = None
        es_inverso = False

        if key_directa in rol_stats:
            matchup_data = rol_stats[key_directa]
        elif key_inversa in rol_stats:
            matchup_data = rol_stats[key_inversa]
            es_inverso = True

        if matchup_data:
            if not es_inverso:
                w = matchup_data.get("wins", 0)
                l = matchup_data.get("losses", 0)
            else:
                w = matchup_data.get("losses", 0)
                l = matchup_data.get("wins", 0)

            total = w + l
            wr = round((w / total) * 100, 1) if total > 0 else 0

            if wr > 50:
                texto_estado = "Gana línea"
                estado = "gana"
            elif wr < 50:
                texto_estado = "Pierde línea"
                estado = "pierde"
            else:
                texto_estado = "Empate (50%)"
                estado = "empate"

            desglose_elo = []
            elos_dict = matchup_data.get("elos", {})

            for elo_nombre, elo_stats in elos_dict.items():
                if not es_inverso:
                    elo_w = elo_stats.get("wins", 0)
                    elo_l = elo_stats.get("losses", 0)
                else:
                    elo_w = elo_stats.get("losses", 0)
                    elo_l = elo_stats.get("wins", 0)

                elo_total = elo_w + elo_l

                if elo_total > 0:
                    elo_wr = round((elo_w / elo_total) * 100, 1)
                    desglose_elo.append({
                        "nombre": elo_nombre,
                        "winrate": elo_wr,
                        "wins": elo_w,
                        "losses": elo_l,
                    })

            desglose_elo.sort(key=lambda x: x["wins"] + x["losses"], reverse=True)

            resultados.append({
                "rol": rol,
                "aliado": c_aliado,
                "enemigo": c_enemigo,
                "wins": w,
                "losses": l,
                "total": total,
                "winrate": wr,
                "estado": estado,
                "texto_estado": texto_estado,
                "desglose_elo": desglose_elo,
            })
        else:
            resultados.append({
                "rol": rol,
                "aliado": c_aliado,
                "enemigo": c_enemigo,
                "wins": 0,
                "losses": 0,
                "total": 0,
                "winrate": 0,
                "estado": "sin_datos",
                "texto_estado": "Sin datos",
                "desglose_elo": [],
            })

    emitir_log("[DRAFT] Análisis completado", "success")
    return {"resultados": resultados}

# =====================================================================
# 19. ENDPOINTS - HISTORIAL DE PARTIDAS
# =====================================================================

@app.get("/api/partidas")
def get_partidas_cuenta(
    riot_id: str = Query("FNX Tempuro#FNIX", description="Formato: Nombre#TAG"),
    region: str = "europe",
):
    """Obtiene historial de partidas"""
    emitir_log(f"📜 Partidas solicitadas: {riot_id} en {region}", "info")

    if "#" not in riot_id:
        raise HTTPException(status_code=400, detail="Formato inválido. Usa Nombre#TAG")

    game_name, tag_line = riot_id.split("#", 1)

    matchup_db = cargar_matchups()
    processed_matches = extraer_ids_procesados(matchup_db.get("processed_matches", []))
    processed_metadata = extraer_metadata_procesados(matchup_db.get("processed_matches", []))
    stats_dict = matchup_db.get(
        "stats", {"TOP": {}, "JUNGLE": {}, "MID": {}, "ADC": {}, "SUPPORT": {}}
    )

    if not RIOT_API_KEY:
        emitir_log("⚠️ RIOT_API_KEY no configurada. Devolviendo datos mock", "warning")
        mock_matches = [
            {
                "id": "EUW1_MOCK_101", "campeon": "Lee Sin", "enemigo": "KhaZix", "rol": "Jungle",
                "victoria": True, "kills": 10, "deaths": 0, "assists": 13, "duracion": "28m",
                "modo": "Clasificatoria Solo/Duo", "elo": "DIAMOND", "division": "II", "lp": 74,
                "coaching_disponible": True,
            },
        ]
        return {"invocador": f"{game_name}#{tag_line}", "partidas": mock_matches}

    headers = {"X-Riot-Token": RIOT_API_KEY}

    game_name_encoded = urllib.parse.quote(game_name.strip())
    tag_line_encoded = urllib.parse.quote(tag_line.strip())

    try:
        puuid = obtener_puuid_cached(game_name, tag_line, headers)

        if not puuid:
            raise HTTPException(status_code=404, detail="Invocador no encontrado")

        regiones_a_probar = [region] + [r for r in ["europe", "americas", "asia"] if r != region]
        match_ids = []
        region_activa = None

        for match_reg in regiones_a_probar:
            url_matches = f"https://{match_reg}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?start=0&count=20"

            riot_api_ready_sync.wait()
            headers["X-Riot-Token"] = os.environ.get("RIOT_API_KEY")

            r_matches = hacer_peticion_riot(url_matches, headers)

            if r_matches and r_matches.status_code == 200:
                datos = r_matches.json()
                if len(datos) > 0:
                    match_ids = datos
                    region_activa = match_reg
                    emitir_log(f"✓ {len(match_ids)} partidas encontradas en {region_activa}", "success")
                    break

        if not match_ids:
            return {"invocador": f"{game_name}#{tag_line}", "partidas": []}

        rango_actual = obtener_rango_riot(puuid, region_activa, headers)
        partidas_resultado = []
        hubo_cambios = False

        emitir_log(f"📊 Procesando {len(match_ids)} partidas...", "info")

        for match_id in match_ids:
            url_detail = f"https://{region_activa}.api.riotgames.com/lol/match/v5/matches/{match_id}"
            time.sleep(1.2)

            riot_api_ready_sync.wait()
            headers["X-Riot-Token"] = os.environ.get("RIOT_API_KEY")

            r_detail = hacer_peticion_riot(url_detail, headers)

            if r_detail and r_detail.status_code == 200:
                data = r_detail.json()
                info = data["info"]

                if match_id not in processed_matches:
                    participants = info.get("participants", [])
                    roles_map = {}

                    for p in participants:
                        pos = extraer_rol_riot(p)
                        team = p.get("teamId")
                        if pos in ["TOP", "JUNGLE", "MID", "ADC", "SUPPORT"]:
                            if pos not in roles_map:
                                roles_map[pos] = {}
                            if team not in roles_map[pos]:
                                roles_map[pos][team] = p

                    for pos, equipos in roles_map.items():
                        if 100 in equipos and 200 in equipos:
                            p1 = equipos[100]
                            p2 = equipos[200]

                            c1 = p1["championName"]
                            c2 = p2["championName"]
                            victoria_1 = p1["win"]

                            registrar_resultado_matchup(stats_dict, pos, c1, c2, gano_champ_1=victoria_1, elo=rango_actual["tier"])

                    processed_matches.add(match_id)
                    processed_metadata[match_id] = {
                        "queue": "RANKED",
                        "elo": rango_actual["tier"],
                        "division": rango_actual["division"],
                        "lp": rango_actual["lp"],
                        "platform": rango_actual["plataforma"] or region_activa,
                    }
                    hubo_cambios = True

                p_data = next((p for p in info["participants"] if p["puuid"] == puuid), None)
                if p_data:
                    rol = extraer_rol_riot(p_data)
                    equipo = p_data["teamId"]
                    campeon = p_data["championName"]

                    enemigo_data = next(
                        (p for p in info["participants"] if extraer_rol_riot(p) == rol and p["teamId"] != equipo),
                        None,
                    )

                    campeon_enemigo = enemigo_data["championName"] if enemigo_data else "Desconocido"
                    minutos = info.get("gameDuration", 0) // 60

                    partidas_resultado.append({
                        "id": match_id,
                        "campeon": campeon,
                        "enemigo": campeon_enemigo,
                        "rol": rol.title(),
                        "victoria": p_data["win"],
                        "kills": p_data["kills"],
                        "deaths": p_data["deaths"],
                        "assists": p_data["assists"],
                        "duracion": f"{minutos}m",
                        "modo": info.get("gameMode", "Clásica"),
                        "elo": rango_actual["tier"],
                        "division": rango_actual["division"],
                        "lp": rango_actual["lp"],
                        "coaching_disponible": bool(verificar_matchup_existe(rol, campeon, campeon_enemigo)),
                    })

        if hubo_cambios:
            matchup_db["processed_matches"] = serializar_procesados(processed_matches, processed_metadata)
            matchup_db["stats"] = stats_dict
            guardar_matchups(matchup_db)

        return {"invocador": f"{game_name}#{tag_line}", "partidas": partidas_resultado}

    except HTTPException:
        raise
    except Exception as e:
        emitir_log(f"✗ Error obteniendo historial: {e}", "error")
        raise HTTPException(status_code=500, detail=str(e))

# =====================================================================
# 20. ENDPOINTS - PROCESOS & CONTROL
# =====================================================================

@app.post("/api/process")
def process_url(req: UrlRequest, background_tasks: BackgroundTasks):
    """Procesa URL en segundo plano"""
    if not req.url:
        return JSONResponse({"error": "No se proporcionó URL"}, status_code=400)

    emitir_log(f"🔗 Procesando URL: {req.url}", "info")
    background_tasks.add_task(run_scraper, req.url)
    return {"message": "✓ Procesamiento iniciado"}

def run_scraper(url: str):
    """Ejecuta scraper en subprocess"""
    try:
        subprocess.run(["python", "descargar_coachings.py", url])
        emitir_log(f"✓ Scraper finalizado: {url}", "success")
    except Exception as e:
        emitir_log(f"✗ Error en scraper: {e}", "error")

@app.post("/api/export-friends")
def export_lcu_friends():
    """Exporta amigos desde LCU"""
    emitir_log("👥 Exportando amigos de LCU...", "info")
    amigos = exportar_amigos_lcu()
    return {"amigos": amigos, "total": len(amigos)}

# =====================================================================
# INICIO DEL SERVIDOR
# =====================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)