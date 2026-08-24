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
from urllib.parse import unquote, quote, urlparse
from collections import deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, BackgroundTasks, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

from playwright.async_api import (
    async_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

import descargar_coachings
import auto_renew_riot_key

load_dotenv()
log_queue = queue.Queue()
shutdown_event = threading.Event()
riot_api_ready_sync = threading.Event()
riot_api_ready_sync.set()
riot_api_ready_async = None

ARCHIVO_JSON = "preload.json"
NUM_PESTANAS = 5


def guardado_atomico(datos, ruta_archivo):
    ruta_temporal = f"{ruta_archivo}.tmp"
    with open(ruta_temporal, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=4)
    os.replace(ruta_temporal, ruta_archivo)


def cargar_datos_previos(ruta_archivo):
    if os.path.exists(ruta_archivo):
        try:
            with open(ruta_archivo, "r", encoding="utf-8") as f:
                datos = json.load(f)
                emitir_log(
                    f"[*] Se han cargado {len(datos)} usuarios existentes de '{ruta_archivo}'.",
                    "info",
                )
                return datos
        except json.JSONDecodeError:
            emitir_log(
                f"[!] Error leyendo '{ruta_archivo}'. Asegúrate de que es un JSON válido. Empezando de cero...",
                "error",
            )
            return []
    else:
        emitir_log(f"[*] No se encontró '{ruta_archivo}'. Se creará uno nuevo.", "info")
        return []


def cargar_checkpoint():
    load_dotenv(override=True)
    region = os.getenv("REGIONSCRAPPER", "KR").strip().lower()
    try:
        pagina = max(0, int(os.getenv("PAGESCRAPPER", "0")))
    except ValueError:
        pagina = 0

    return region, pagina


def guardar_checkpoint(region, pagina):
    ruta_env = ".env"
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


async def leer_pagina(page, region, pagina, rangos_permitidos):
    url = f"https://www.op.gg/leaderboards/tier?region={region}&page={pagina}"

    for intento in range(1, 4):
        try:
            emitir_log(f">> Leyendo pagina {pagina} de {region.upper()}...", "info")
            await page.goto(url, timeout=30000)
            await page.wait_for_selector("table tbody tr", timeout=15000)
            filas = await page.locator("table tbody tr").all()
            jugadores = []

            for fila in filas:
                try:
                    locator_nombre = fila.locator(
                        'a[href*="/summoners/"] span.whitespace-pre-wrap'
                    )
                    locator_tag = fila.locator(
                        'a[href*="/summoners/"] span.text-gray-500'
                    )
                    nombre = (
                        (await locator_nombre.inner_text()).strip()
                        if await locator_nombre.count() > 0
                        else ""
                    )
                    tag = (
                        (await locator_tag.inner_text()).strip()
                        if await locator_tag.count() > 0
                        else ""
                    )
                    jugador_completo = f"{nombre}{tag}"
                    celda_tier = (
                        (await fila.locator("td").nth(2).inner_text()).strip().lower()
                    )

                    if not any(rango in celda_tier for rango in rangos_permitidos):
                        return pagina, jugadores, True, jugador_completo, celda_tier

                    if nombre:
                        jugadores.append(jugador_completo)
                except Exception:
                    continue

            if not filas:
                return pagina, [], True, "", "sin filas"

            return pagina, jugadores, False, "", ""
        except PlaywrightTimeoutError:
            emitir_log(
                f"[!] Timeout en pagina {pagina} de {region.upper()} (intento {intento}/3)",
                "error",
            )
            if intento < 3:
                await asyncio.sleep(10)
        except Exception as e:
            emitir_log(
                f"[!] Error en pagina {pagina} de {region.upper()}: {e}", "error"
            )
            return pagina, [], True, "", "error"

    return pagina, [], True, "", "timeout"


async def scrape_opgg_async():
    regiones = [
        "kr",
        "na",
        "euw",
        "eune",
        "oce",
        "jp",
        "br",
        "las",
        "lan",
        "ru",
        "tr",
        "sg",
        "tw",
        "vn",
        "th",
        "ph",
    ]
    RANGOS_PERMITIDOS = [
        "challenger",
        "grandmaster",
        "master",
        "aspirante",
        "gran maestro",
        "maestro",
    ]
    region_inicial, pagina_guardada = cargar_checkpoint()
    indice_region = regiones.index(region_inicial) if region_inicial in regiones else 0

    todos_los_jugadores = cargar_datos_previos(ARCHIVO_JSON)
    jugadores_vistos = set(todos_los_jugadores)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        pages = [await context.new_page() for _ in range(NUM_PESTANAS)]

        for region in regiones[indice_region:]:
            emitir_log(f"--- [ INICIANDO REGIÓN: {region.upper()} ] ---", "info")
            alcanzado_limite_elo = False
            pagina_base = max(1, pagina_guardada) if region == region_inicial else 1

            while not alcanzado_limite_elo:
                if shutdown_event.is_set():
                    emitir_log(
                        "[!] Señal de apagado detectada. Deteniendo scraper de OP.GG...",
                        "info",
                    )
                    await browser.close()
                    return

                paginas_bloque = list(range(pagina_base, pagina_base + NUM_PESTANAS))
                resultados = await asyncio.gather(
                    *[
                        leer_pagina(pages[indice], region, pagina, RANGOS_PERMITIDOS)
                        for indice, pagina in enumerate(paginas_bloque)
                    ]
                )

                jugadores_nuevos_en_bloque = 0
                for (
                    pagina,
                    jugadores,
                    activa_corte,
                    jugador_corte,
                    tier_corte,
                ) in sorted(resultados):
                    if activa_corte:
                        emitir_log(
                            f"[!] Corte en pagina {pagina} de {region.upper()}: {jugador_corte} ({tier_corte}).",
                            "info",
                        )
                        alcanzado_limite_elo = True
                        break

                    for jugador_completo in jugadores:
                        if jugador_completo not in jugadores_vistos:
                            todos_los_jugadores.append(jugador_completo)
                            jugadores_vistos.add(jugador_completo)
                            jugadores_nuevos_en_bloque += 1
                            emitir_log(f"  [+] Añadido: {jugador_completo}", "success")

                resultados_ordenados = sorted(resultados)
                primer_corte = next(
                    (
                        indice
                        for indice, (_, _, activa_corte, _, _) in enumerate(
                            resultados_ordenados
                        )
                        if activa_corte
                    ),
                    len(resultados_ordenados),
                )
                paginas_completadas = [
                    pagina
                    for pagina, _, _, _, motivo in resultados_ordenados[:primer_corte]
                    if motivo != "error" and motivo != "timeout"
                ]
                if paginas_completadas:
                    guardar_checkpoint(region, max(paginas_completadas))

                if jugadores_nuevos_en_bloque > 0:
                    emitir_log(
                        f"  [GUARDADO] Bloque terminado. Total usuarios: {len(todos_los_jugadores)}",
                        "success",
                    )
                    guardado_atomico(todos_los_jugadores, ARCHIVO_JSON)

                if alcanzado_limite_elo:
                    if any(
                        motivo in ("error", "timeout")
                        for _, _, _, _, motivo in resultados
                    ):
                        emitir_log(
                            "[!] No se actualiza el checkpoint porque hubo un error de lectura.",
                            "error",
                        )
                        break

                    siguiente_indice = regiones.index(region) + 1
                    if siguiente_indice < len(regiones):
                        guardar_checkpoint(regiones[siguiente_indice], 0)
                    break

                pagina_base += NUM_PESTANAS
                if not alcanzado_limite_elo:
                    await asyncio.sleep(random.uniform(2.5, 5.5))

        await browser.close()

    emitir_log(
        f"¡Proceso completado! Archivo '{ARCHIVO_JSON}' actualizado con {len(todos_los_jugadores)} jugadores en total.",
        "success",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    emitir_log("Iniciando servidor web...", "info")

    loop = asyncio.get_running_loop()

    global riot_api_ready_async
    riot_api_ready_async = asyncio.Event()
    riot_api_ready_async.set()

    loop.run_in_executor(None, limpiar_unknowns_region_cache)
    loop.run_in_executor(None, optimizar_orden_preload)

    renovador_thread = threading.Thread(
        target=background_key_manager, args=(loop,), daemon=True
    )
    renovador_thread.start()

    app.state.scraper_task = asyncio.create_task(scrape_opgg_async())

    emitir_log(
        "Servidor listo y escuchando peticiones. Optimización y Renovador en segundo plano...",
        "success",
    )

    yield

    emitir_log("Apagando servidor... Deteniendo tareas en segundo plano.", "success")

    shutdown_event.set()

    if hasattr(app.state, "scraper_task") and not app.state.scraper_task.done():
        app.state.scraper_task.cancel()
        try:
            await app.state.scraper_task
        except asyncio.CancelledError:
            emitir_log("Scraper cancelado correctamente.", "success")


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory="."), name="static")

def limpiar_unknowns_region_cache():

    archivo_cache = "region_cache.json"
    archivo_corregir = "nombres_a_corregir.json"

    if not os.path.exists(archivo_cache):
        return

    try:
        with open(archivo_cache, "r", encoding="utf-8") as f:
            cache_data = json.load(f)
    except Exception as e:
        emitir_log(f"⚠️ Error leyendo {archivo_cache}: {e}", "error")
        return

    # Extraemos todos los jugadores que tengan valor "unknown"
    usuarios_unknown = [
        jugador for jugador, region in cache_data.items() if region == "unknown"
    ]

    if not usuarios_unknown:
        return  # No hay nada que hacer

    # --- 1. Eliminar de region_cache.json ---
    for jugador in usuarios_unknown:
        del cache_data[jugador]

    try:
        with open(archivo_cache, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, indent=4, ensure_ascii=False)
        emitir_log(
            f"🧹 [LIMPIEZA] Eliminados {len(usuarios_unknown)} usuarios 'unknown' de {archivo_cache}.",
            "info",
        )
    except Exception as e:
        emitir_log(f"⚠️ Error limpiando {archivo_cache}: {e}", "error")

    # --- 2. Añadir a nombres_a_corregir.json ---
    datos_corregir = []
    if os.path.exists(archivo_corregir):
        try:
            with open(archivo_corregir, "r", encoding="utf-8") as f:
                datos_corregir = json.load(f)
        except json.JSONDecodeError:
            pass  # Si falla, asumimos lista vacía

    nuevos_añadidos = 0
    for jugador in usuarios_unknown:
        if jugador not in datos_corregir:
            datos_corregir.append(jugador)
            nuevos_añadidos += 1

    if nuevos_añadidos > 0:
        try:
            with open(archivo_corregir, "w", encoding="utf-8") as f:
                json.dump(datos_corregir, f, indent=4, ensure_ascii=False)
            emitir_log(
                f"✅ [LIMPIEZA] Traspasados {nuevos_añadidos} usuarios a {archivo_corregir}.",
                "success",
            )
        except Exception as e:
            emitir_log(f"⚠️ Error actualizando {archivo_corregir}: {e}", "error")


def wait_for_key_activation(new_key):
    """Espera hasta que Riot acepte la llave nueva (devuelva 200 OK)."""
    url = "https://euw1.api.riotgames.com/lol/status/v4/platform-data"
    headers = {"X-Riot-Token": new_key}

    emitir_log("⏳ [GESTOR] Esperando a que Riot active la nueva API Key...", "info")
    while not shutdown_event.is_set():
        try:
            resp = requests.get(url, headers=headers)
            if resp.status_code == 200:
                emitir_log(
                    "✅ [GESTOR] ¡Nueva API Key totalmente operativa!", "success"
                )
                break
            emitir_log(
                f"⏳ [GESTOR] Llave aún no activa (Código {resp.status_code}). Reintentando en 10s...",
                "error",
            )
        except Exception as e:
            emitir_log(f"⚠️ [GESTOR] Error de conexión comprobando llave: {e}", "error")
        time.sleep(10)


def background_key_manager(loop_principal):
    """Hilo secundario que revisa y renueva la API Key de forma eficiente."""
    while not shutdown_event.is_set():
        wait_seconds = 600  # Tiempo por defecto de seguridad (10 minutos)
        try:
            resultado = auto_renew_riot_key.run(api_event=riot_api_ready_sync)

            if isinstance(resultado, tuple):
                nueva_llave, minutes_left = resultado
            else:
                nueva_llave = resultado
                minutes_left = 60

            if nueva_llave:
                if not riot_api_ready_sync.is_set():
                    loop_principal.call_soon_threadsafe(riot_api_ready_async.clear)

                os.environ["RIOT_API_KEY"] = nueva_llave
                global RIOT_API_KEY
                RIOT_API_KEY = nueva_llave

                wait_for_key_activation(nueva_llave)

                emitir_log(
                    "🟢 [GESTOR] Reanudando peticiones de la API de Riot.", "success"
                )
                riot_api_ready_sync.set()
                loop_principal.call_soon_threadsafe(riot_api_ready_async.set)

                # La nueva llave dura 24 horas; dormimos 23.5 horas
                wait_seconds = int(23.5 * 3600)
                emitir_log(
                    "⏳ [GESTOR] Nueva API Key lista. Hibernando durante 23.5 horas...",
                    "info",
                )
            else:
                # Calcula cuánto esperar para despertar justo cuando queden 32 minutos para caducar
                wait_minutes = max(minutes_left - 32, 5)
                wait_seconds = wait_minutes * 60
                emitir_log(
                    f"⏳ [GESTOR] Faltan {minutes_left} min para caducar. Hibernando por {wait_minutes} min...",
                    "info",
                )

        except Exception as e:
            emitir_log(f"❌ [GESTOR] Error en el hilo de renovación: {e}", "error")
            # Forzado de seguridad para desbloquear peticiones en caso de fallo crítico
            riot_api_ready_sync.set()
            if loop_principal and riot_api_ready_async:
                loop_principal.call_soon_threadsafe(riot_api_ready_async.set)

            wait_seconds = 600
            emitir_log(
                "⏳ [GESTOR] Reintentando en 10 minutos por fallo de conexión...",
                "error",
            )

        shutdown_event.wait(wait_seconds)


RIOT_RATE_LIMITS = {}

RIOT_API_KEY = os.getenv("RIOT_API_KEY", "")
MATCHUPS_FILE = "matchups_stats.json"
CAMPEONES_BASE = [
    "Aatrox",
    "Ahri",
    "Akali",
    "Akshan",
    "Alistar",
    "Ambessa",
    "Amumu",
    "Anivia",
    "Annie",
    "Aphelios",
    "Ashe",
    "Aurelion Sol",
    "Aurora",
    "Azir",
    "Bard",
    "Bel'Veth",
    "Blitzcrank",
    "Brand",
    "Braum",
    "Briar",
    "Caitlyn",
    "Camille",
    "Cassiopeia",
    "Cho'Gath",
    "Corki",
    "Darius",
    "Diana",
    "Dr. Mundo",
    "Draven",
    "Ekko",
    "Elise",
    "Evelynn",
    "Ezreal",
    "Fiddlesticks",
    "Fiora",
    "Fizz",
    "Galio",
    "Gangplank",
    "Garen",
    "Gnar",
    "Gragas",
    "Graves",
    "Gwen",
    "Hecarim",
    "Heimerdinger",
    "Hwei",
    "Illaoi",
    "Irelia",
    "Ivern",
    "Janna",
    "Jarvan IV",
    "Jax",
    "Jayce",
    "Jhin",
    "Jinx",
    "K'Sante",
    "Kai'Sa",
    "Kalista",
    "Karma",
    "Karthus",
    "Kassadin",
    "Katarina",
    "Kayle",
    "Kayn",
    "Kennen",
    "KhaZix",
    "Kindred",
    "Kled",
    "Kog'Maw",
    "LeBlanc",
    "Lee Sin",
    "Leona",
    "Lillia",
    "Lissandra",
    "Locke",
    "Lucian",
    "Lulu",
    "Lux",
    "Malphite",
    "Malzahar",
    "Maokai",
    "Master Yi",
    "Mel",
    "Milio",
    "Miss Fortune",
    "Mordekaiser",
    "Morgana",
    "Naafiri",
    "Nami",
    "Nasus",
    "Nautilus",
    "Neeko",
    "Nidalee",
    "Nilah",
    "Nocturne",
    "Nunu & Willump",
    "Olaf",
    "Orianna",
    "Ornn",
    "Pantheon",
    "Poppy",
    "Pyke",
    "Qiyana",
    "Quinn",
    "Rakan",
    "Rammus",
    "Rek'Sai",
    "Rell",
    "Renata Glasc",
    "Renekton",
    "Rengar",
    "Riven",
    "Rumble",
    "Ryze",
    "Samira",
    "Sejuani",
    "Senna",
    "Seraphine",
    "Sett",
    "Shaco",
    "Shen",
    "Shyvana",
    "Singed",
    "Sion",
    "Sivir",
    "Skarner",
    "Smolder",
    "Sona",
    "Soraka",
    "Swain",
    "Sylas",
    "Syndra",
    "Tahm Kench",
    "Taliyah",
    "Talon",
    "Taric",
    "Teemo",
    "Thresh",
    "Tristana",
    "Trundle",
    "Tryndamere",
    "Twisted Fate",
    "Twitch",
    "Udyr",
    "Urgot",
    "Varus",
    "Vayne",
    "Veigar",
    "Vel'Koz",
    "Vex",
    "Vi",
    "Viego",
    "Viktor",
    "Vladimir",
    "Volibear",
    "Warwick",
    "Wukong",
    "Xayah",
    "Xerath",
    "Xin Zhao",
    "Yasuo",
    "Yone",
    "Yorick",
    "Yunara",
    "Yuumi",
    "Zaahen",
    "Zac",
    "Zed",
    "Zeri",
    "Ziggs",
    "Zilean",
    "Zoe",
    "Zyra",
]


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


def normalizar(texto: str) -> str:
    if not texto:
        return ""
    return re.sub(r"[^a-zA-Z0-9]", "", str(texto)).lower()


CHAMPION_DISPLAY_MAP = {normalizar(c): c for c in CAMPEONES_BASE}

ESTANDARIZAR_NOMBRES = {
    "monkeyking": "wukong",
    "wukong": "wukong",
    "nunuwillump": "nunu",
    "nunu": "nunu",
    "renata": "renata",
}


def emitir_log(mensaje: str, tipo: str):
    print(mensaje)
    log_queue.put({"mensaje": mensaje, "tipo": tipo})


async def log_generator():
    while True:
        if not log_queue.empty():
            log = log_queue.get()
            yield f"data: {json.dumps(log)}\n\n"
        else:
            await asyncio.sleep(0.1)


@app.get("/api/stream-logs")
async def stream_logs():
    return StreamingResponse(log_generator(), media_type="text/event-stream")


def formatear_nombre_campeon(norm_champ: str) -> str:
    norm = normalizar(norm_champ)
    if norm in CHAMPION_DISPLAY_MAP:
        return CHAMPION_DISPLAY_MAP[norm]
    if (
        norm in ESTANDARIZAR_NOMBRES
        and ESTANDARIZAR_NOMBRES[norm] in CHAMPION_DISPLAY_MAP
    ):
        return CHAMPION_DISPLAY_MAP[ESTANDARIZAR_NOMBRES[norm]]
    return norm_champ.capitalize()


def obtener_variantes_campeon(campeon: str) -> set:
    norm = normalizar(campeon)
    variantes = {norm}
    if norm in ESTANDARIZAR_NOMBRES:
        variantes.add(ESTANDARIZAR_NOMBRES[norm])
    return variantes


def normalizar_rol_canonico(rol: str) -> str:
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
    r = normalizar_rol_canonico(rol)
    if r == "MID":
        return ["mid", "middle"]
    if r == "SUPPORT":
        return ["support", "utility", "sup"]
    if r == "ADC":
        return ["bot", "bottom", "adc"]
    if r == "JUNGLE":
        return ["jungle"]
    if r == "TOP":
        return ["top"]
    return [normalizar(rol)]


def extraer_rol_riot(participant: dict) -> str:
    rol = participant.get("teamPosition", "")
    if not rol or rol == "Invalid":
        rol = participant.get("individualPosition", "")
    return normalizar_rol_canonico(rol)


def obtener_rango_riot(
    puuid: str, macro_region: str, headers: dict, request_fn=None
) -> dict:
    plataformas = {
        "europe": ["euw1", "eun1", "tr1", "ru"],
        "americas": ["na1", "br1", "la1", "la2"],
        "asia": ["kr", "jp1", "oc1", "ph2", "sg2", "tw2", "vn2"],
    }
    resultado = {
        "tier": "UNRANKED",
        "division": "",
        "lp": 0,
        "wins": 0,
        "losses": 0,
        "plataforma": "",
    }

    if request_fn is None:
        if "hacer_peticion_riot" in globals():
            request_fn = globals()["hacer_peticion_riot"]
        else:
            request_fn = requests.get

    emitir_log(
        f"🔍 [RANGO] Buscando rango para PUUID: {puuid[:10]}... en macro-región: {macro_region}",
        "info",
    )

    for plataforma in plataformas.get(macro_region, plataformas["europe"]):
        try:
            league_url = f"https://{plataforma}.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}"
            league_response = request_fn(league_url, headers=headers)

            if not league_response:
                continue

            if league_response.status_code == 404:
                continue

            if league_response.status_code != 200:
                emitir_log(
                    f"   ⚠️ [{plataforma}] Error HTTP {league_response.status_code} al buscar ligas por puuid.",
                    "error",
                )
                continue

            data_ligas = league_response.json()

            if not data_ligas:
                resultado["plataforma"] = plataforma
                continue

            solo_queue = next(
                (
                    entry
                    for entry in data_ligas
                    if entry.get("queueType") == "RANKED_SOLO_5x5"
                ),
                None,
            )

            if solo_queue:
                resultado.update(
                    {
                        "tier": solo_queue.get("tier", "UNRANKED").upper(),
                        "division": solo_queue.get("rank", ""),
                        "lp": solo_queue.get("leaguePoints", 0),
                        "wins": solo_queue.get("wins", 0),
                        "losses": solo_queue.get("losses", 0),
                        "plataforma": plataforma,
                    }
                )
                emitir_log(
                    f"   ✅ ¡Encontrado! Rango: {resultado['tier']} {resultado['division']} en {plataforma}",
                    "success",
                )
                return resultado
            else:
                resultado["plataforma"] = plataforma

        except Exception as e:
            emitir_log(
                f"   ❌ [EXCEPCIÓN] Error procesando en {plataforma}: {e}", "error"
            )
            continue

    emitir_log(
        f"❌ [RANGO] No se pudo encontrar ningún rango para este jugador. Se devuelve UNRANKED.",
        "error",
    )
    return resultado


def verificar_matchup_existe(rol: str, champ1: str, champ2: str):
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
                if any(v in norm_archivo for v in c1_vars) and any(
                    v in norm_archivo for v in c2_vars
                ):
                    return os.path.join(root, archivo)

    except Exception as e:
        emitir_log(f"Error en verificación de matchup: {e}", "error")

    return None


def guardar_matchups(data):

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
        emitir_log(f"Error al guardar {MATCHUPS_FILE}: {e}", "error")
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except Exception:
                pass


def limpiar_base_de_datos(data):
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

                nuevos_stats[rol_canon][nueva_key] = {
                    "wins": 0,
                    "losses": 0,
                    "elos": {},
                }

            nuevos_stats[rol_canon][nueva_key]["wins"] += valores.get("wins", 0)
            nuevos_stats[rol_canon][nueva_key]["losses"] += valores.get("losses", 0)

            elos_existentes = valores.get("elos", {})
            for elo_name, elo_stats in elos_existentes.items():
                if elo_name not in nuevos_stats[rol_canon][nueva_key]["elos"]:
                    nuevos_stats[rol_canon][nueva_key]["elos"][elo_name] = {
                        "wins": 0,
                        "losses": 0,
                    }

                nuevos_stats[rol_canon][nueva_key]["elos"][elo_name][
                    "wins"
                ] += elo_stats.get("wins", 0)
                nuevos_stats[rol_canon][nueva_key]["elos"][elo_name][
                    "losses"
                ] += elo_stats.get("losses", 0)

    if hubo_cambios:
        data["stats"] = nuevos_stats
        guardar_matchups(data)
        emitir_log(
            "✅ Base de datos limpiada: Claves convertidas a minúsculas y Elos migrados.",
            "success",
        )

    return data


def cargar_matchups():
    data = {
        "processed_matches": [],
        "stats": {"TOP": {}, "JUNGLE": {}, "MID": {}, "ADC": {}, "SUPPORT": {}},
    }
    if os.path.exists(MATCHUPS_FILE):
        try:
            with open(MATCHUPS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            emitir_log(f"Error al cargar {MATCHUPS_FILE}: {e}", "error")
            return data

    return limpiar_base_de_datos(data)


def extraer_ids_procesados(registros) -> set:
    ids = set()
    for registro in registros or []:
        if isinstance(registro, str):
            ids.add(registro)
        elif isinstance(registro, dict) and registro.get("id"):
            ids.add(registro["id"])
    return ids


def extraer_metadata_procesados(registros) -> dict:
    metadata = {}
    for registro in registros or []:
        if isinstance(registro, dict) and registro.get("id"):
            metadata[registro["id"]] = {k: v for k, v in registro.items() if k != "id"}
    return metadata


def serializar_procesados(ids: set, metadata: dict) -> list:
    registros = []
    for match_id in sorted(ids):
        registros.append({"id": match_id, **metadata.get(match_id, {})})
    return registros


def obtener_campeones_por_rol(ruta_json):
    data = cargar_matchups()

    stats = data.get("stats", {})
    campeones_por_rol = {}

    # Recorremos cada posición (TOP, JUNGLE, MID, ADC, SUPPORT, etc.)
    for rol, matchups in stats.items():
        campeones_set = set()

        for matchup_key in matchups.keys():
            if "_vs_" in matchup_key:
                c1, c2 = matchup_key.split("_vs_")

                # Aplicamos el formateador para devolver el nombre original con tildes y espacios
                campeones_set.add(formatear_nombre_campeon(c1))
                campeones_set.add(formatear_nombre_campeon(c2))

        # Guardar lista ordenada de campeones para este rol
        campeones_por_rol[rol] = sorted(list(campeones_set))

    return campeones_por_rol


def registrar_resultado_matchup(
    stats_dict, rol, champ1, champ2, gano_champ_1, elo="UNRANKED"
):
    c1_norm = normalizar(champ1)
    c2_norm = normalizar(champ2)

    c1_limpio = ESTANDARIZAR_NOMBRES.get(c1_norm, c1_norm)
    c2_limpio = ESTANDARIZAR_NOMBRES.get(c2_norm, c2_norm)

    # Ordenar alfabéticamente para evitar duplicados (Ej: Ahri vs Zed == Zed vs Ahri)
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

    # Parche de seguridad: por si hay datos viejos que no tienen la clave "elos"
    if "elos" not in stats_dict[rol_canon][key]:
        stats_dict[rol_canon][key]["elos"] = {}

    # Inicializamos el Elo específico si no existe
    if elo not in stats_dict[rol_canon][key]["elos"]:
        stats_dict[rol_canon][key]["elos"][elo] = {"wins": 0, "losses": 0}

    # Sumamos las victorias y derrotas globales y las del Elo específico
    if c1_gana:
        stats_dict[rol_canon][key]["wins"] += 1
        stats_dict[rol_canon][key]["elos"][elo]["wins"] += 1
    else:
        stats_dict[rol_canon][key]["losses"] += 1
        stats_dict[rol_canon][key]["elos"][elo]["losses"] += 1


@app.get("/api/top-matchups")
def top_matchups():
    # Llama a tu función de scraping/IA para sacar el top
    tops = descargar_coachings.obtener_top_matchups(15)
    return tops


@app.get("/api/top-matchups-sin-guia")
def top_matchups_sin_guia():
    # Asumiendo que la función está en descargar_coachings como en tu código original
    tops = descargar_coachings.obtener_top_matchups_sin_guia(10)
    return tops


@app.post("/api/generate-guide")
def gen_guide(datos: GuiaRequest):
    # Llama a la función de generar guía pasándole los datos del frontend
    exito = descargar_coachings.generar_guia_ia_directa(
        datos.rol, datos.champ_main, datos.champ_enemy
    )

    if exito:
        return {"success": True}

    error_tipo = getattr(descargar_coachings, "ULTIMO_ERROR_GUIA", "error")
    mensajes_error = {
        "missing_keys": "No hay API Keys configuradas.",
        "quota": "Cuota diaria o tokens agotados en las API Keys.",
        "error": "Fallo de generación con IA.",
    }
    return {
        "success": False,
        "error": mensajes_error.get(error_tipo, mensajes_error["error"]),
        "error_type": error_tipo,
    }


@app.get("/")
def serve_index():
    return FileResponse("index.html")


@app.post("/api/process")
def process_url(req: UrlRequest, background_tasks: BackgroundTasks):
    if not req.url:
        return JSONResponse({"error": "No se proporcionó URL"}, status_code=400)
    background_tasks.add_task(run_scraper, req.url)
    return {"message": "✅ Procesamiento iniciado."}


def run_scraper(url: str):
    subprocess.run(["python", "descargar_coachings.py", url])


@app.get("/api/campeones")
def get_campeones(
    rol: str = Query(
        None, description="Filtrar campeones por rol (TOP, JUNGLE, MID, ADC, SUPPORT)"
    )
):
    """
    Devuelve la lista de campeones. Si se especifica un rol, devuelve solo los meta de ese rol.
    Si no, intenta devolver todos desde Data Dragon o la lista base.
    """
    if rol:
        rol_canon = normalizar_rol_canonico(rol)
        if rol_canon in CAMPEONES_POR_ROL:
            return {"campeones": sorted(CAMPEONES_POR_ROL[rol_canon])}

    # Si no hay rol, devolver todos (intentando DDragon primero)
    try:
        resp = requests.get(
            "https://ddragon.leagueoflegends.com/cdn/14.15.1/data/es_ES/champion.json",
            timeout=3,
        )
        if resp.status_code == 200:
            champs = list(resp.json()["data"].keys())
            champs.sort()
            return {"campeones": champs}
    except Exception:
        pass

    return {"campeones": sorted(CAMPEONES_BASE)}


CAMPEONES_POR_ROL = obtener_campeones_por_rol("matchups_stats.json")


@app.get("/api/guias")
def get_guias():
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
                        archivos = [
                            f for f in os.listdir(champ_path) if f.endswith(".txt")
                        ]
                        if archivos:
                            estructura[rol][champ] = archivos
    except Exception as e:
        emitir_log(f"Error leyendo guías: {e}", "error")
    return {"guias": estructura}


@app.get("/api/guia_matchup/{rol}/{campeon1}/{campeon2}")
def get_guia_matchup(rol: str, campeon1: str, campeon2: str):
    ruta_archivo = verificar_matchup_existe(rol, campeon1, campeon2)
    if not ruta_archivo:
        raise HTTPException(status_code=404, detail="Guía de matchup no encontrada")
    try:
        with open(ruta_archivo, "r", encoding="utf-8") as f:
            contenido = f.read()
        return {"contenido": contenido}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sugerencias_matchup")
def get_sugerencias_matchup(
    rol: str = Query(..., description="TOP, JUNGLE, MID, ADC, SUPPORT"),
    campeon: str = Query(..., description="Nombre del campeón"),
    side: str = Query("aliado", description="'aliado' o 'enemigo'"),
):
    rol_canon = normalizar_rol_canonico(rol)

    # CORRECCIÓN 1: Normalizar Y ESTANDARIZAR
    norm_c = normalizar(campeon)
    norm_c = ESTANDARIZAR_NOMBRES.get(norm_c, norm_c)

    if not norm_c:
        return {"campeon": campeon, "rol": rol_canon, "mejores": [], "peores": []}

    matchup_db = cargar_matchups()
    stats_rol = matchup_db.get("stats", {}).get(rol_canon, {})

    lista_enfrentamientos = []

    for key, data in stats_rol.items():
        if "_vs_" not in key:
            continue

        c1, c2 = key.split("_vs_", 1)

        # 🔥 AQUÍ ESTÁ LA MAGIA: Comprobamos ambos lados e invertimos si es necesario
        if norm_c == c1:
            rival = c2
            w = data.get("wins", 0)
            l = data.get("losses", 0)
        elif norm_c == c2:
            rival = c1
            # INVERTIMOS: Las derrotas de c1 son las victorias de c2
            w = data.get("losses", 0)
            l = data.get("wins", 0)
        else:
            # Si nuestro campeón no es ni c1 ni c2, pasamos al siguiente
            continue

        total = w + l
        if total <= 0:
            continue

        # CORRECCIÓN 2: Lógica de perspectiva
        if side == "aliado":
            # YO juego norm_c. Quiero ver a quién le gano. Mis victorias son 'w'.
            wins_reales = w
            losses_reales = l
        else:  # side == "enemigo"
            # EL ENEMIGO juega norm_c. Yo jugaré rival. Quiero ver si rival le gana.
            # Mis victorias (con rival) son las derrotas del enemigo ('l').
            wins_reales = l
            losses_reales = w

        wr_real = round((wins_reales / total) * 100, 1)

        # 🔥 IMPORTANTE: Ahora usamos 'rival' en vez de c2 para mostrar el nombre
        rival_display = formatear_nombre_campeon(rival)
        coaching = bool(verificar_matchup_existe(rol_canon, norm_c, rival))

        lista_enfrentamientos.append(
            {
                "campeon": rival_display,
                "winrate": wr_real,
                "partidas": total,
                "wins": wins_reales,
                "losses": losses_reales,
                "tipo": "favorable" if wr_real >= 50 else "desfavorable",
                "coaching_disponible": coaching,
            }
        )

    # CORRECCIÓN 3: Estandarizar también al buscar en las carpetas si no hay datos
    if not lista_enfrentamientos:
        ruta_base = "coachings"
        roles_validos = mapear_rol(rol_canon)
        if os.path.exists(ruta_base):
            for r_folder in os.listdir(ruta_base):
                if normalizar(r_folder) in roles_validos:
                    rol_p = os.path.join(ruta_base, r_folder)
                    for c_folder in os.listdir(rol_p):

                        # Estandarizar carpeta
                        c_folder_norm = normalizar(c_folder)
                        c_folder_estan = ESTANDARIZAR_NOMBRES.get(
                            c_folder_norm, c_folder_norm
                        )

                        if c_folder_estan == norm_c:
                            champ_p = os.path.join(rol_p, c_folder)
                            for arch in os.listdir(champ_p):
                                if arch.endswith(".txt"):
                                    nombre_limpio = arch.replace(".txt", "")
                                    partes = re.split(
                                        r"_vs_|-vs-|vs",
                                        nombre_limpio,
                                        flags=re.IGNORECASE,
                                    )
                                    if len(partes) >= 2:

                                        # Estandarizar nombre extraído del archivo
                                        p0_norm = normalizar(partes[0])
                                        p0_estan = ESTANDARIZAR_NOMBRES.get(
                                            p0_norm, p0_norm
                                        )

                                        otro_champ = (
                                            partes[1]
                                            if p0_estan == norm_c
                                            else partes[0]
                                        )
                                        otro_display = formatear_nombre_campeon(
                                            otro_champ
                                        )

                                        lista_enfrentamientos.append(
                                            {
                                                "campeon": otro_display,
                                                "winrate": 50.0,
                                                "partidas": 0,
                                                "wins": 0,
                                                "losses": 0,
                                                "tipo": "neutral",
                                                "coaching_disponible": True,
                                            }
                                        )

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


@app.get("/api/partidas")
def get_partidas_cuenta(
    riot_id: str = Query("FNX Tempuro#FNIX", description="Formato: Nombre#TAG"),
    region: str = "europe",
):
    if "#" not in riot_id:
        raise HTTPException(
            status_code=400, detail="Formato de Riot ID inválido. Usa Nombre#TAG"
        )

    game_name, tag_line = riot_id.split("#", 1)

    matchup_db = cargar_matchups()
    processed_matches = extraer_ids_procesados(matchup_db.get("processed_matches", []))
    processed_metadata = extraer_metadata_procesados(
        matchup_db.get("processed_matches", [])
    )
    stats_dict = matchup_db.get(
        "stats", {"TOP": {}, "JUNGLE": {}, "MID": {}, "ADC": {}, "SUPPORT": {}}
    )

    if not RIOT_API_KEY:
        mock_matches = [
            # ... (Tus mock_matches se quedan igual) ...
            {
                "id": "EUW1_MOCK_101",
                "campeon": "Lee Sin",
                "enemigo": "KhaZix",
                "rol": "Jungle",
                "victoria": True,
                "kills": 10,
                "deaths": 0,
                "assists": 13,
                "duracion": "28m",
                "modo": "Clasificatoria Solo/Duo",
                "elo": "DIAMOND",
                "division": "II",
                "lp": 74,
                "coaching_disponible": True,
            },
            {
                "id": "EUW1_MOCK_102",
                "campeon": "Ahri",
                "enemigo": "Yasuo",
                "rol": "Mid",
                "victoria": False,
                "kills": 4,
                "deaths": 7,
                "assists": 5,
                "duracion": "32m",
                "modo": "Clasificatoria Solo/Duo",
                "elo": "DIAMOND",
                "division": "II",
                "lp": 74,
                "coaching_disponible": False,
            },
        ]
        return {"invocador": f"{game_name}#{tag_line}", "partidas": mock_matches}

    headers = {"X-Riot-Token": RIOT_API_KEY}

    # 1. CODIFICACIÓN URL (Soluciona espacios y caracteres especiales/coreanos)
    game_name_encoded = urllib.parse.quote(game_name.strip())
    tag_line_encoded = urllib.parse.quote(tag_line.strip())

    try:
        # 2. OBTENER PUUID (Account API es Global - basta con probar en europe y americas como fallback)
        puuid = None
        for reg_acc in ["europe", "americas", "asia"]:
            url_account = f"https://{reg_acc}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name_encoded}/{tag_line_encoded}"

            # 🔴 ESPERA DEL SEMÁFORO Y ACTUALIZACIÓN DE KEY
            riot_api_ready_sync.wait()
            headers["X-Riot-Token"] = os.environ.get("RIOT_API_KEY")

            r_account = requests.get(url_account, headers=headers)

            if r_account.status_code == 200:
                puuid = r_account.json()["puuid"]
                break
            elif r_account.status_code == 429:
                time.sleep(int(r_account.headers.get("Retry-After", 10)))

        if not puuid:
            raise HTTPException(
                status_code=404,
                detail="Invocador no encontrado en la base de datos de Riot",
            )

        # 3. BUSCAR PARTIDAS (Match API es Regional - descubrimos dónde juega realmente)
        regiones_a_probar = [region] + [
            r for r in ["europe", "americas", "asia"] if r != region
        ]
        match_ids = []
        region_activa = None

        for match_reg in regiones_a_probar:
            url_matches = f"https://{match_reg}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?start=0&count=20"

            # 🔴 ESPERA DEL SEMÁFORO Y ACTUALIZACIÓN DE KEY
            riot_api_ready_sync.wait()
            headers["X-Riot-Token"] = os.environ.get("RIOT_API_KEY")

            r_matches = requests.get(url_matches, headers=headers)

            if r_matches.status_code == 200:
                datos = r_matches.json()
                if len(datos) > 0:
                    match_ids = datos
                    region_activa = match_reg
                    break
            elif r_matches.status_code == 429:
                time.sleep(int(r_matches.headers.get("Retry-After", 10)))

        # Si lo encontramos pero no tiene partidas, devolvemos array vacío limpio
        if not match_ids:
            return {"invocador": f"{game_name}#{tag_line}", "partidas": []}

        # obtener_rango_riot ya manejará su propia pausa si lo implementaste como te sugerí en la respuesta anterior
        rango_actual = obtener_rango_riot(
            puuid, region_activa, headers, request_fn=hacer_peticion_riot
        )
        partidas_resultado = []
        hubo_cambios = False

        # 4. PROCESAR PARTIDAS (Usando la region_activa correcta descubierta en el paso 3)
        for match_id in match_ids:
            url_detail = f"https://{region_activa}.api.riotgames.com/lol/match/v5/matches/{match_id}"
            time.sleep(1.2)  # Mantienes el rate-limit manual, está bien.

            # 🔴 ESPERA DEL SEMÁFORO Y ACTUALIZACIÓN DE KEY (Útil si justo expira a mitad de la iteración)
            riot_api_ready_sync.wait()
            headers["X-Riot-Token"] = os.environ.get("RIOT_API_KEY")

            r_detail = requests.get(url_detail, headers=headers)

            if r_detail.status_code == 200:
                data = r_detail.json()
                info = data["info"]
                queue_id = info.get("queueId")
                queue_name = {420: "RANKED_SOLO", 440: "RANKED_FLEX"}.get(queue_id)
                match_metadata = processed_metadata.get(match_id, {})

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

                p_data = next(
                    (p for p in info["participants"] if p["puuid"] == puuid), None
                )
                if p_data:
                    rol = extraer_rol_riot(p_data)
                    equipo = p_data["teamId"]
                    campeon = p_data["championName"]

                    enemigo_data = next(
                        (
                            p
                            for p in info["participants"]
                            if extraer_rol_riot(p) == rol and p["teamId"] != equipo
                        ),
                        None,
                    )

                    if not enemigo_data:
                        enemigos_huerfanos = [
                            p
                            for p in info["participants"]
                            if p["teamId"] != equipo
                            and extraer_rol_riot(p)
                            not in ["TOP", "JUNGLE", "MID", "ADC", "SUPPORT"]
                        ]
                        if len(enemigos_huerfanos) == 1:
                            enemigo_data = enemigos_huerfanos[0]

                    campeon_enemigo = (
                        enemigo_data["championName"] if enemigo_data else "Desconocido"
                    )
                    rol_mostrar = (
                        rol.title()
                        if rol in ["TOP", "JUNGLE", "MID", "ADC", "SUPPORT"]
                        else "General"
                    )

                    ruta_coaching = verificar_matchup_existe(
                        rol, campeon, campeon_enemigo
                    )
                    minutos = info.get("gameDuration", 0) // 60

                    partidas_resultado.append(
                        {
                            "id": match_id,
                            "campeon": campeon,
                            "enemigo": campeon_enemigo,
                            "rol": rol_mostrar,
                            "victoria": p_data["win"],
                            "kills": p_data["kills"],
                            "deaths": p_data["deaths"],
                            "assists": p_data["assists"],
                            "duracion": f"{minutos}m",
                            "modo": info.get("gameMode", "Clásica"),
                            "elo": (
                                rango_actual["tier"]
                                if rango_actual["tier"] != "UNRANKED"
                                else match_metadata.get("elo", "UNRANKED")
                            ),
                            "division": (
                                rango_actual["division"]
                                if rango_actual["tier"] != "UNRANKED"
                                else match_metadata.get("division", "")
                            ),
                            "lp": (
                                rango_actual["lp"]
                                if rango_actual["tier"] != "UNRANKED"
                                else match_metadata.get("lp", 0)
                            ),
                            "rank_wins": rango_actual["wins"],
                            "rank_losses": rango_actual["losses"],
                            "region": rango_actual["plataforma"] or region_activa,
                            "queue": match_metadata.get(
                                "queue", queue_name or "NORMAL"
                            ),
                            "fecha": info.get("gameCreation"),
                            "parche": info.get("gameVersion", "").split(".", 2)[0:2],
                            "nivel": p_data.get("champLevel", 0),
                            "cs": p_data.get("totalMinionsKilled", 0)
                            + p_data.get("neutralMinionsKilled", 0),
                            "vision_score": p_data.get("visionScore", 0),
                            "wards_placed": p_data.get("wardsPlaced", 0),
                            "wards_killed": p_data.get("wardsKilled", 0),
                            "oro": p_data.get("goldEarned", 0),
                            "daño_campeones": p_data.get(
                                "totalDamageDealtToChampions", 0
                            ),
                            "objetos": [
                                p_data.get(f"item{i}", 0)
                                for i in range(7)
                                if p_data.get(f"item{i}", 0)
                            ],
                            "hechizos": [
                                p_data.get("summoner1Id", 0),
                                p_data.get("summoner2Id", 0),
                            ],
                            "coaching_disponible": bool(ruta_coaching),
                        }
                    )

        if hubo_cambios:
            matchup_db["processed_matches"] = serializar_procesados(
                processed_matches, processed_metadata
            )
            matchup_db["stats"] = stats_dict
            guardar_matchups(matchup_db)

        return {"invocador": f"{game_name}#{tag_line}", "partidas": partidas_resultado}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/analizar_draft")
def analizar_draft(req: DraftRequest):
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
            resultados.append(
                {
                    "rol": rol,
                    "aliado": c_aliado or "-",
                    "enemigo": c_enemigo or "-",
                    "estado": "sin_datos",
                    "texto_estado": "Sin datos",
                    "winrate": 0,
                    "wins": 0,
                    "losses": 0,
                    "desglose_elo": [],  # Añadimos el array vacío por seguridad
                }
            )
            continue

        rol_stats = stats_dict.get(rol, {})
        aliado_norm = normalizar(c_aliado)
        enemigo_norm = normalizar(c_enemigo)

        # --- NUEVA LÓGICA: BIDIRECCIONALIDAD ---
        key_directa = f"{aliado_norm}_vs_{enemigo_norm}"
        key_inversa = f"{enemigo_norm}_vs_{aliado_norm}"

        matchup_data = None
        es_inverso = False

        # Comprobamos en qué dirección existe el matchup
        if key_directa in rol_stats:
            matchup_data = rol_stats[key_directa]
        elif key_inversa in rol_stats:
            matchup_data = rol_stats[key_inversa]
            es_inverso = True

        if matchup_data:
            # Si es inverso, las victorias del JSON son nuestras derrotas
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

            # --- PROCESAR EL DESGLOSE DE ELO (También con inversión) ---
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
                    desglose_elo.append(
                        {
                            "nombre": elo_nombre,
                            "winrate": elo_wr,
                            "wins": elo_w,
                            "losses": elo_l,
                        }
                    )

            # Ordenamos por total de partidas jugadas en ese Elo
            desglose_elo.sort(key=lambda x: x["wins"] + x["losses"], reverse=True)
            # -----------------------------------------------------------

            resultados.append(
                {
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
                }
            )
        else:
            # Si no existe ni directa ni inversa, devuelve sin datos
            resultados.append(
                {
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
                }
            )

    return {"resultados": resultados}


SUB_TO_MACRO = {
    # Europe
    "euw1": "europe",
    "eun1": "europe",
    "tr1": "europe",
    "ru": "europe",
    "me1": "europe",
    # Americas
    "na1": "americas",
    "la1": "americas",
    "la2": "americas",
    "br1": "americas",
    # Asia
    "kr": "asia",
    "jp1": "asia",
    "oc1": "asia",
    "ph2": "asia",
    "sg2": "asia",
    "tw2": "asia",
    "vn2": "asia",
}


def optimizar_orden_preload(
    ruta_json: str = "preload.json",
    ruta_cache: str = "region_cache.json",
    reintentar_unknown: bool = False,
):
    if not os.path.exists(ruta_json):
        emitir_log(f"[PRELOAD] Archivo {ruta_json} no encontrado.", "error")
        return []

    # 1. Cargar la caché si existe
    cache = {}
    if os.path.exists(ruta_cache):
        try:
            with open(ruta_cache, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except json.JSONDecodeError:
            emitir_log(
                "[PRELOAD] Archivo de caché corrupto, se creará uno nuevo.", "info"
            )

    api_key = os.getenv("RIOT_API_KEY")
    headers = {"X-Riot-Token": api_key} if api_key else {}

    try:
        with open(ruta_json, "r", encoding="utf-8") as f:
            nicks = json.load(f)

        buckets = {"europe": [], "americas": [], "asia": [], "unknown": []}

        emitir_log(
            f"[PRELOAD] 🔍 Iniciando optimización. {len(nicks)} cuentas en la lista.",
            "info",
        )

        nuevos_procesados = 0

        for index, nick in enumerate(nicks):

            # if shutdown_event.is_set():
            #     emitir_log("[PRELOAD] Optimización abortada por el usuario.", "error")
            #     break

            en_cache = nick in cache
            es_unknown = en_cache and cache[nick] == "unknown"

            if en_cache and (not es_unknown or not reintentar_unknown):
                region = cache.get(nick, "unknown")
                # Seguro por si hay basura en la caché
                if region not in buckets:
                    region = "unknown"
                buckets[region].append(nick)

            else:
                region = descubrir_macro_region_real(nick, headers)
                cache[nick] = region
                buckets[region].append(nick)
                nuevos_procesados += 1

                emitir_log(
                    f"  -> [API] [{index+1}/{len(nicks)}] {nick}: Localizado en {region.upper()}",
                    "api",
                )

                # 3. AUTOGUARDADO: Guardar la caché cada 25 usuarios nuevos
                if nuevos_procesados > 0 and nuevos_procesados % 25 == 0:
                    with open(ruta_cache, "w", encoding="utf-8") as fc:
                        json.dump(cache, fc, indent=4, ensure_ascii=False)

        # 4. Guardado final de la caché al terminar el bucle
        if nuevos_procesados > 0:
            with open(ruta_cache, "w", encoding="utf-8") as fc:
                json.dump(cache, fc, indent=4, ensure_ascii=False)

        # 5. Intercalamos 1 de cada región
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

        # Añadimos los "unknown" al final
        lista_intercalada.extend(buckets["unknown"])

        # 6. Sobreescribimos el JSON original
        with open(ruta_json, "w", encoding="utf-8") as f:
            json.dump(lista_intercalada, f, indent=4, ensure_ascii=False)

        emitir_log("\n" + "=" * 50, "info")
        emitir_log(
            f"✅ [PRELOAD] ¡Finalizado! {len(nicks)} cuentas procesadas.", "success"
        )
        emitir_log(
            f"⚡ Peticiones ahorradas por caché: {len(nicks) - nuevos_procesados}",
            "info",
        )
        emitir_log(f"📡 Nuevas peticiones a la API: {nuevos_procesados}", "info")
        emitir_log(f"📊 Distribución final:", "info")
        emitir_log(f"   - 🇪🇺 Europe:   {len(buckets['europe'])}", "info")
        emitir_log(f"   - 🌎 Americas: {len(buckets['americas'])}", "info")
        emitir_log(f"   - 🌏 Asia:     {len(buckets['asia'])}", "info")
        emitir_log(f"   - ❓ Unknown:  {len(buckets['unknown'])}", "info")
        emitir_log("=" * 50 + "\n", "info")

        return lista_intercalada

    except Exception as e:
        emitir_log(f"[ERROR] Fallo al optimizar el orden del preload: {e}", "error")
        return []


def descubrir_macro_region_real(nick: str, headers: dict) -> str:
    if "#" not in nick:
        return "unknown"

    nombre, tag = nick.rsplit("#", 1)

    url_account = f"https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{quote(nombre)}/{quote(tag)}"

    # riot_api_ready_sync.wait() # Asegúrate de que esta variable existe globalmente en tu código
    headers["X-Riot-Token"] = os.environ.get("RIOT_API_KEY")

    # --- PASAMOS EL NICK (RIOT_ID) A LA PETICIÓN ---
    resp_acc = hacer_peticion_riot(url_account, headers, riot_id=nick)

    if not resp_acc:
        emitir_log(f"⚠️ {nick}: La petición falló por completo (es None).", "error")
        return "unknown"

    if resp_acc.status_code != 200:
        emitir_log(
            f"⚠️ {nick}: Riot rechazó la cuenta. Código: {resp_acc.status_code} - Respuesta: {resp_acc.text}",
            "error",
        )
        return "unknown"

    puuid = resp_acc.json().get("puuid")

    sub_regiones = list(SUB_TO_MACRO.keys())
    tag_lower = tag.lower()

    if "euw" in tag_lower:
        sub_regiones.insert(0, sub_regiones.pop(sub_regiones.index("euw1")))
    elif "na" in tag_lower:
        sub_regiones.insert(0, sub_regiones.pop(sub_regiones.index("na1")))
    elif "kr" in tag_lower:
        sub_regiones.insert(0, sub_regiones.pop(sub_regiones.index("kr")))
    elif "lan" in tag_lower or "la1" in tag_lower:
        sub_regiones.insert(0, sub_regiones.pop(sub_regiones.index("la1")))
    elif "las" in tag_lower or "la2" in tag_lower:
        sub_regiones.insert(0, sub_regiones.pop(sub_regiones.index("la2")))

    for reg in sub_regiones:
        url_summoner = f"https://{reg}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}"

        # riot_api_ready_sync.wait()
        headers["X-Riot-Token"] = os.environ.get("RIOT_API_KEY")

        # --- PASAMOS EL NICK (RIOT_ID) TAMBIÉN AQUÍ ---
        r = hacer_peticion_riot(url_summoner, headers)

        if r and r.status_code == 200:
            # ¡Lo encontramos! Devolvemos la macro-región
            return SUB_TO_MACRO[reg]

        # Si devuelve 404 (No existe), el bucle continúa probando el siguiente servidor

    return "unknown"


PRELOAD_STATUS = {
    "status": "idle",
    "current_player": "",
    "processed_matches": 0,
    "total_matches": 0,
    "message": "",
}

RIOT_RATE_LIMITS = {}
REGION_LOCKS = {}
GLOBAL_LOCK = threading.Lock()


def mandar_a_corregir(riot_id: str):
    if not riot_id:
        return

    archivo_json = "nombres_a_corregir.json"

    try:
        datos = []
        if os.path.exists(archivo_json):
            with open(archivo_json, "r", encoding="utf-8") as f:
                try:
                    datos = json.load(f)
                except json.JSONDecodeError:
                    pass

        if riot_id not in datos:
            datos.append(riot_id)
            with open(archivo_json, "w", encoding="utf-8") as f:
                json.dump(datos, f, indent=4, ensure_ascii=False)
            emitir_log(
                f"📝 [CORRECCIÓN] Jugador {riot_id} añadido a {archivo_json} tras error de red.",
                "success",
            )

    except Exception as ex:
        emitir_log(f"⚠️ Error al escribir en {archivo_json}: {ex}", "error")


def hacer_peticion_riot(
    url: str, headers: dict, max_reintentos_red: int = 1, riot_id: str = None
):
    dominio = urlparse(url).netloc
    region = dominio.split(".")[0]

    # 1. Inicialización Thread-Safe
    with GLOBAL_LOCK:
        if region not in RIOT_RATE_LIMITS:
            RIOT_RATE_LIMITS[region] = deque()
            REGION_LOCKS[region] = threading.Lock()

    historial = RIOT_RATE_LIMITS[region]
    lock = REGION_LOCKS[region]
    intentos = 0

    while intentos < max_reintentos_red:
        ahora = time.time()
        espera = 0

        # 2. Control de Rate Limit con Lock (evita colisiones entre hilos)
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
            time.sleep(espera)
            continue

        # 3. Petición HTTP
        try:
            r = requests.get(url, headers=headers, timeout=10)

            # Fallback por límite de Riot (429)
            if r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", 10))
                emitir_log(
                    f"[API RIOT] Límite superado en {region}. Esperando {retry_after}s...",
                    "api",
                )
                time.sleep(retry_after)
                continue

            # Fallback por caídas del servidor de Riot (500, 503)
            if r.status_code >= 500:
                intentos += 1
                emitir_log(
                    f"[API RIOT] Error 5xx del servidor ({intentos}/{max_reintentos_red})",
                    "api",
                )
                if intentos >= max_reintentos_red:
                    return r
                time.sleep(5)
                continue

            return r

        except requests.exceptions.RequestException as e:
            intentos += 1
            emitir_log(
                f"[API RIOT] Error de red ({intentos}/{max_reintentos_red}): {e}", "api"
            )

            if intentos >= max_reintentos_red:
                emitir_log(
                    f"[API RIOT] Abortando petición. Dominio inalcanzable: {url}", "api"
                )

                # --- LLAMADA LIMPIA A LA FUNCIÓN DE GUARDADO ---
                if riot_id:
                    mandar_a_corregir(riot_id)
                else:
                    # Fallback por si en algún momento no se pasa el parámetro y es la URL inicial
                    match = re.search(r"/by-riot-id/([^/]+)/([^/?]+)", url)
                    if match:
                        game_name = unquote(match.group(1))
                        tag_line = unquote(match.group(2))
                        mandar_a_corregir(f"{game_name}#{tag_line}")

                return None

            time.sleep(5)

    return None


def ejecutar_preload_task(region: str = "europe"):
    global PRELOAD_STATUS

    # 🔥 CACHÉ: Archivo donde guardaremos por dónde vamos
    ARCHIVO_CACHE_LEIDOS = "cache_jugadores_leidos.json"
    jugadores_leidos = set()

    # 1. CARGAR, LIMPIAR DUPLICADOS Y PURGAR AL INICIO
    try:
        # Limpiamos duplicados
        limpiar_duplicados_preload("preload.json")
        # Purgamos la basura acumulada de sesiones anteriores ANTES de empezar
        jugadores = purgar_nombres_a_corregir("preload.json", "nombres_a_corregir.json")

        # 🔥 CACHÉ: Cargamos los jugadores que ya hemos leído en ciclos anteriores
        if os.path.exists(ARCHIVO_CACHE_LEIDOS):
            with open(ARCHIVO_CACHE_LEIDOS, "r", encoding="utf-8") as f_c:
                jugadores_leidos = set(json.load(f_c))

    except FileNotFoundError:
        PRELOAD_STATUS["status"] = "error"
        PRELOAD_STATUS["message"] = "No se encontró el archivo preload.json"
        return
    except Exception as e:
        PRELOAD_STATUS["status"] = "error"
        PRELOAD_STATUS["message"] = f"Error leyendo preload.json: {e}"
        return

    if not RIOT_API_KEY:
        PRELOAD_STATUS["status"] = "error"
        PRELOAD_STATUS["message"] = "No se ha configurado RIOT_API_KEY en .env"
        return

    headers = {"X-Riot-Token": RIOT_API_KEY}
    matchup_db = cargar_matchups()
    processed_matches = extraer_ids_procesados(matchup_db.get("processed_matches", []))
    processed_metadata = extraer_metadata_procesados(
        matchup_db.get("processed_matches", [])
    )
    stats_dict = matchup_db.get(
        "stats", {"TOP": {}, "JUNGLE": {}, "MID": {}, "ADC": {}, "SUPPORT": {}}
    )

    PRELOAD_STATUS["status"] = "running"
    PRELOAD_STATUS["message"] = "Iniciando precarga..."

    for jug in jugadores:
        # 🔥 Comprobar si nos han pedido parar antes de empezar un nuevo jugador
        if PRELOAD_STATUS.get("status") == "stopping":
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

        # 1. CODIFICACIÓN URL
        game_name_encoded = urllib.parse.quote(game_name.strip())
        tag_line_encoded = urllib.parse.quote(tag_line.strip())

        riot_id = f"{game_name.strip()}#{tag_line.strip()}"

        # 🔥 CACHÉ: Si este jugador ya lo leímos en este ciclo, nos lo saltamos
        if riot_id in jugadores_leidos:
            continue

        PRELOAD_STATUS["current_player"] = riot_id
        PRELOAD_STATUS["message"] = f"Buscando PUUID de {riot_id}..."

        # 2. OBTENER PUUID (Base Global de Riot)
        puuid = None
        error_api = None
        for reg_acc in ["europe", "americas", "asia"]:
            url_account = f"https://{reg_acc}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name_encoded}/{tag_line_encoded}"

            # 🔴 ESPERA DEL SEMÁFORO Y ACTUALIZACIÓN DE KEY (Protege la busqueda de cuentas)
            riot_api_ready_sync.wait()
            headers["X-Riot-Token"] = os.environ.get("RIOT_API_KEY")

            r_account = hacer_peticion_riot(url_account, headers)

            if r_account and r_account.status_code == 200:
                puuid = r_account.json().get("puuid")
                if not puuid:
                    error_api = f"Respuesta inválida de la API al buscar {riot_id}"
                break
            elif not r_account or r_account.status_code != 404:
                status_code = r_account.status_code if r_account else "sin respuesta"
                error_api = f"Error de la API al buscar {riot_id} (HTTP {status_code})"
                break

        # Solo un 404 en todas las regiones confirma que el nombre no existe.
        if error_api:
            emitir_log(
                f"[PRELOAD] {error_api}. Se detiene la precarga sin purgar el jugador.",
                "info",
            )
            PRELOAD_STATUS["message"] = error_api
            continue

        # --- PURGADO EN TIEMPO REAL ---
        if not puuid:
            emitir_log(
                f"[PRELOAD] Jugador {riot_id} no encontrado en ninguna región.", "info"
            )

            # A) Guardar en nombres_a_corregir.json
            nombres_a_corregir = []
            if os.path.exists("nombres_a_corregir.json"):
                try:
                    with open(
                        "nombres_a_corregir.json", "r", encoding="utf-8"
                    ) as f_err:
                        nombres_a_corregir = json.load(f_err)
                except Exception:
                    pass

            if riot_id not in nombres_a_corregir:
                nombres_a_corregir.append(riot_id)
                with open("nombres_a_corregir.json", "w", encoding="utf-8") as f_err:
                    json.dump(nombres_a_corregir, f_err, indent=4, ensure_ascii=False)

            # B) Eliminar de preload.json INMEDIATAMENTE
            try:
                with open("preload.json", "r", encoding="utf-8") as f_pre:
                    datos_preload = json.load(f_pre)

                # Nos quedamos con todos EXCEPTO el que acaba de fallar
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
                    f"[PURGA] {riot_id} borrado de preload.json al instante.", "info"
                )
            except Exception as e:
                emitir_log(f"[PURGA] Error borrando {riot_id} al instante: {e}", "info")

            continue
        # ---------------------------------------------------

        # 3. BUSCAR PARTIDAS (Match API Regional)
        match_ids = []
        region_activa = None
        regiones_a_probar = [region] + [
            r for r in ["europe", "americas", "asia"] if r != region
        ]

        for match_reg in regiones_a_probar:
            url_matches = f"https://{match_reg}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?start=0&count=100"

            # 🔴 ESPERA DEL SEMÁFORO Y ACTUALIZACIÓN DE KEY (Protege la búsqueda de IDs de partidas)
            riot_api_ready_sync.wait()
            headers["X-Riot-Token"] = os.environ.get("RIOT_API_KEY")

            r_matches = hacer_peticion_riot(url_matches, headers)

            if r_matches and r_matches.status_code == 200:
                data_matches = r_matches.json()
                if len(data_matches) > 0:
                    match_ids = data_matches
                    region_activa = match_reg
                    break

        if not match_ids:
            emitir_log(
                f"[PRELOAD] Jugador {riot_id} no tiene partidas recientes en ninguna región.",
                "info",
            )

            # 🔥 CACHÉ: Lo guardamos formateado en JSON limpio
            jugadores_leidos.add(riot_id)
            with open(ARCHIVO_CACHE_LEIDOS, "w", encoding="utf-8") as f_c:
                json.dump(list(jugadores_leidos), f_c, indent=4, ensure_ascii=False)

            continue

        emitir_log(
            f"[PRELOAD] Detectada región activa: {region_activa} para {riot_id}", "info"
        )
        rango_actual = obtener_rango_riot(puuid, region_activa, headers)
        total_jugador = len(match_ids)
        hubo_cambios = False

        # 4. PROCESAR PARTIDAS
        for idx, match_id in enumerate(match_ids, start=1):
            # 🔥 Comprobar si nos han pedido parar en medio del procesado de partidas
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

            # 🔴 ESPERA DEL SEMÁFORO Y ACTUALIZACIÓN DE KEY (Protege el bucle masivo de detalles de partidas)
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
                        c1, k1, d1, a1 = (
                            p1["championName"],
                            p1["kills"],
                            p1["deaths"],
                            p1["assists"],
                        )
                        c2, k2, d2, a2 = (
                            p2["championName"],
                            p2["kills"],
                            p2["deaths"],
                            p2["assists"],
                        )

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

        # 🔥 Si estamos deteniendo, rompemos también el bucle general de jugadores
        if PRELOAD_STATUS.get("status") == "stopping":
            break

        # 🔥 CACHÉ: Jugador procesado entero, lo guardamos formateado en JSON limpio
        jugadores_leidos.add(riot_id)
        with open(ARCHIVO_CACHE_LEIDOS, "w", encoding="utf-8") as f_c:
            json.dump(list(jugadores_leidos), f_c, indent=4, ensure_ascii=False)

    # Por si queda algo residual (aunque ya se ha borrado al vuelo)
    purgar_nombres_a_corregir("preload.json", "nombres_a_corregir.json")

    # 🔥 Actualizamos el mensaje final dependiendo de si terminó natural o forzado
    if PRELOAD_STATUS.get("status") == "stopping":
        PRELOAD_STATUS["status"] = "idle"
        PRELOAD_STATUS["message"] = (
            "Precarga detenida. Se guardó el progreso de los jugadores leídos."
        )
    else:
        PRELOAD_STATUS["status"] = "completed"
        PRELOAD_STATUS["message"] = "¡Precarga 100% finalizada! Reiniciando ciclo..."

        # 🔥 CACHÉ: Si terminó todo el preload.json, borramos la caché para empezar de nuevo el ciclo
        if os.path.exists(ARCHIVO_CACHE_LEIDOS):
            try:
                os.remove(ARCHIVO_CACHE_LEIDOS)
                emitir_log(
                    "[PRELOAD] Ciclo de lectura completado. Caché limpiado para el siguiente ciclo.",
                    "success",
                )
            except Exception as e:
                emitir_log(f"[PRELOAD] Error al limpiar la caché final: {e}", "error")


def limpiar_duplicados_preload(ruta_archivo: str = "preload.json") -> list:
    """
    Lee el JSON de jugadores, elimina los duplicados basándose en el Riot ID
    y sobrescribe el archivo si encuentra repetidos.
    """
    if not os.path.exists(ruta_archivo):
        raise FileNotFoundError(f"No se encontró el archivo {ruta_archivo}")

    with open(ruta_archivo, "r", encoding="utf-8") as f:
        jugadores = json.load(f)

    jugadores_unicos = []
    vistos = set()
    hubo_duplicados = False

    for jug in jugadores:
        riot_id = None

        # Extraer el Riot ID dependiendo de si es string o diccionario (igual que en tu código original)
        if isinstance(jug, str) and "#" in jug:
            riot_id = jug.strip()
        elif isinstance(jug, dict):
            game_name = jug.get("nick") or jug.get("game_name")
            tag_line = jug.get("tag") or jug.get("tag_line")
            if game_name and tag_line:
                riot_id = f"{game_name.strip()}#{tag_line.strip()}"

        if riot_id:
            # Convertimos a minúsculas solo para comparar y evitar que "Name#TAG" y "name#tag" pasen como distintos
            riot_id_lower = riot_id.lower()
            if riot_id_lower not in vistos:
                vistos.add(riot_id_lower)
                jugadores_unicos.append(jug)  # Guardamos el formato ORIGINAL
            else:
                hubo_duplicados = True
        else:
            # Si tiene un formato que no es válido, lo mantenemos para no perder la data por error
            jugadores_unicos.append(jug)

    # Si encontramos repetidos, actualizamos el JSON
    if hubo_duplicados:
        with open(ruta_archivo, "w", encoding="utf-8") as f:

            json.dump(jugadores_unicos, f, indent=4, ensure_ascii=False)
        emitir_log(
            f"[PRELOAD] Se eliminaron duplicados. Quedan {len(jugadores_unicos)} jugadores únicos.",
            "success",
        )

    return jugadores_unicos


def purgar_nombres_a_corregir(
    ruta_preload: str = "preload.json", ruta_corregir: str = "nombres_a_corregir.json"
) -> list:

    if not os.path.exists(ruta_preload):
        emitir_log(f"[PURGA] No se encontró el archivo {ruta_preload}.", "error")
        return []

    if not os.path.exists(ruta_corregir):
        emitir_log(
            f"[PURGA] No se encontró {ruta_corregir}. No hay nada que purgar.", "error"
        )
        return []

    try:
        with open(ruta_corregir, "r", encoding="utf-8") as f_corr:
            nombres_corregir = json.load(f_corr)
    except Exception as e:
        emitir_log(f"[PURGA] Error leyendo {ruta_corregir}: {e}", "error")
        return []

    if not isinstance(nombres_corregir, list) or not nombres_corregir:
        emitir_log(
            "[PURGA] El archivo de corrección está vacío o no es una lista válida.",
            "info",
        )
        return []

    # Crear un set en minúsculas para búsquedas rápidas (case-insensitive)
    set_a_corregir = {
        nombre.strip().lower() for nombre in nombres_corregir if isinstance(nombre, str)
    }

    # 3. Cargar el preload.json
    try:
        with open(ruta_preload, "r", encoding="utf-8") as f_pre:
            jugadores_preload = json.load(f_pre)
    except Exception as e:
        emitir_log(f"[PURGA] Error leyendo {ruta_preload}: {e}", "error")
        return []

    jugadores_filtrados = []
    eliminados = 0

    # 4. Filtrar jugadores
    for jug in jugadores_preload:
        riot_id = None

        # Extraer el Riot ID usando la misma lógica que en limpiar_duplicados_preload
        if isinstance(jug, str) and "#" in jug:
            riot_id = jug.strip()
        elif isinstance(jug, dict):
            game_name = jug.get("nick") or jug.get("game_name")
            tag_line = jug.get("tag") or jug.get("tag_line")
            if game_name and tag_line:
                riot_id = f"{game_name.strip()}#{tag_line.strip()}"

        # Comprobar si está en la lista de elementos a corregir
        if riot_id and riot_id.lower() in set_a_corregir:
            eliminados += 1
        else:
            jugadores_filtrados.append(jug)

    # 5. Guardar los cambios si hubo eliminaciones
    if eliminados > 0:
        try:
            with open(ruta_preload, "w", encoding="utf-8") as f_pre:
                json.dump(jugadores_filtrados, f_pre, indent=4, ensure_ascii=False)
            emitir_log(
                f"[PURGA] Se eliminaron {eliminados} jugadores problemáticos de {ruta_preload}.",
                "success",
            )

            # Opcional: Vaciar el archivo nombres_a_corregir.json tras purgar
            # with open(ruta_corregir, "w", encoding="utf-8") as f_corr:
            #     json.dump([], f_corr)
        except Exception as e:
            emitir_log(f"[PURGA] Error guardando {ruta_preload}: {e}", "error")
    else:
        emitir_log("[PURGA] No se encontraron coincidencias para eliminar.", "info")

    return jugadores_filtrados


@app.get("/ranks/{nombre_imagen}")
def servir_imagen_rango(nombre_imagen: str):
    ruta_archivo = os.path.join("ranks", nombre_imagen)
    emitir_log(
        f"[DEBUG LOG] El navegador solicita imagen de rango: '{nombre_imagen}' -> Buscando en: '{ruta_archivo}'",
        "info",
    )

    if os.path.exists(ruta_archivo):
        emitir_log(
            f"[DEBUG LOG] ✅ Imagen encontrada: {ruta_archivo}. Sirviéndola...",
            "success",
        )
        return FileResponse(ruta_archivo)

    emitir_log(
        f"[DEBUG LOG] ❌ ERROR: La imagen NO existe en la ruta: {ruta_archivo}", "error"
    )
    return {"error": "No encontrada"}, 404


@app.post("/api/preload")
def iniciar_preload(background_tasks: BackgroundTasks):
    global PRELOAD_STATUS
    if PRELOAD_STATUS["status"] == "running":
        return JSONResponse(
            {"message": "La precarga ya está ejecutándose.", "status": PRELOAD_STATUS}
        )

    background_tasks.add_task(ejecutar_preload_task)
    return {"message": "Precarga iniciada en segundo plano."}


@app.post("/api/preload/stop")
def detener_preload():
    global PRELOAD_STATUS
    if PRELOAD_STATUS["status"] == "running":
        PRELOAD_STATUS["status"] = "stopping"
        PRELOAD_STATUS["message"] = (
            "Deteniendo de forma segura tras terminar la partida actual..."
        )
        return {"message": "Petición de parada recibida."}
    return {"message": "No hay ninguna precarga en ejecución."}


@app.get("/api/preload/status")
def obtener_estado_preload():
    return PRELOAD_STATUS


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, access_log=False)
