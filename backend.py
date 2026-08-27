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
from collections import defaultdict
from dotenv import set_key

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


@app.get("/favicon.ico", include_in_schema=False)
@app.get("/favicon.svg", include_in_schema=False)
async def favicon():
    return FileResponse("favicon.svg", media_type="image/svg+xml")


ARCHIVO_JSON = "preload.json"
NUM_PESTANAS = 5
MAX_JUGADORES_POR_TIER = 50


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


def normalizar_tier(texto_raw):
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
            "diamante": "diamond",
            "esmeralda": "emerald",
            "platino": "platinum",
            "oro": "gold",
            "plata": "silver",
            "bronce": "bronze",
            "hierro": "iron",
        }
        tier_clean = traducciones.get(tier, tier)
        return f"{tier_clean} {div}"

    return text.strip()


"""
async def leer_pagina(page, region, pagina):
    url = f"https://www.op.gg/leaderboards/tier?region={region}&page={pagina}"

    for intento in range(1, 4):
        try:
            emitir_log(f">> Leyendo pagina {pagina} de {region.upper()}...", "info")
            await page.goto(url, timeout=30000)
            await page.wait_for_selector("table tbody tr", timeout=15000)
            filas = await page.locator("table tbody tr").all()
            jugadores_con_tier = []

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

                    celda_tier_raw = (
                        await fila.locator("td").nth(2).inner_text()
                    ).strip()
                    tier_norm = normalizar_tier(celda_tier_raw)

                    if nombre:
                        jugadores_con_tier.append((jugador_completo, tier_norm))
                except Exception:
                    continue

            if not filas:
                return pagina, [], True, "sin filas"

            return pagina, jugadores_con_tier, False, ""
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
            return pagina, [], True, "error"

    return pagina, [], True, "timeout"

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
    region_inicial, pagina_guardada = cargar_checkpoint()
    indice_region = regiones.index(region_inicial) if region_inicial in regiones else 0

    todos_los_jugadores = cargar_datos_previos(ARCHIVO_JSON)
    jugadores_vistos = set(todos_los_jugadores)

    # 💡 Ligas a las que NO se les aplicará el límite de 50
    tiers_sin_limite = {"challenger", "grandmaster", "master"}

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
            conteo_tier_region = defaultdict(int)

            while not alcanzado_limite_elo:
                if shutdown_event.is_set():
                    emitir_log(
                        "[!] Señal de apagado detectada. Deteniendo scraper...", "info"
                    )
                    await browser.close()
                    return

                paginas_bloque = list(range(pagina_base, pagina_base + NUM_PESTANAS))
                resultados = await asyncio.gather(
                    *[
                        leer_pagina(pages[i], region, pag)
                        for i, pag in enumerate(paginas_bloque)
                    ]
                )

                jugadores_nuevos_en_bloque = 0
                for pagina, jugadores_tier, es_fin, motivo in sorted(resultados):
                    if es_fin and motivo == "sin filas":
                        alcanzado_limite_elo = True
                        break

                    for jugador_completo, tier in jugadores_tier:

                        # 💡 AHORA: Lo guardamos si está en una liga sin límite (Master+)
                        # O si todavía no hemos llegado al máximo (50) para esa liga
                        if (
                            tier in tiers_sin_limite
                            or conteo_tier_region[tier] < MAX_JUGADORES_POR_TIER
                        ):
                            if jugador_completo not in jugadores_vistos:
                                todos_los_jugadores.append(jugador_completo)
                                jugadores_vistos.add(jugador_completo)
                                conteo_tier_region[tier] += 1
                                jugadores_nuevos_en_bloque += 1

                        # Seguimos comprobando si ya se terminó de recolectar Iron 4
                        if conteo_tier_region["iron 4"] >= MAX_JUGADORES_POR_TIER:
                            emitir_log(
                                f"[!] Se alcanzaron {MAX_JUGADORES_POR_TIER} jugadores en Iron 4 para {region.upper()}. Avanzando de región...",
                                "info",
                            )
                            alcanzado_limite_elo = True
                            break  # Rompe el bucle for de jugadores

                    # Si llegamos al final del pozo, rompemos el bucle de páginas
                    if alcanzado_limite_elo:
                        break

                paginas_completadas = [
                    pag
                    for pag, _, es_fin, motivo in sorted(resultados)
                    if motivo not in ("error", "timeout")
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
                    siguiente_indice = regiones.index(region) + 1
                    if siguiente_indice < len(regiones):
                        guardar_checkpoint(regiones[siguiente_indice], 0)
                    break  # Pasa a la siguiente región

                pagina_base += NUM_PESTANAS
                await asyncio.sleep(random.uniform(2.5, 5.5))

        await browser.close()

    emitir_log(
        f"¡Proceso completado! Archivo '{ARCHIVO_JSON}' actualizado con {len(todos_los_jugadores)} jugadores.",
        "success",
    )
"""


async def leer_pagina(page, region, pagina):
    url = f"https://www.op.gg/leaderboards/tier?region={region}&page={pagina}"

    for intento in range(1, 4):
        try:
            await page.goto(url, timeout=30000)
            await page.wait_for_selector("table tbody tr", timeout=15000)

            # 🔥 OPTIMIZACIÓN EXTREMA: Extraemos todo en 1 sola llamada de JS en lugar de iterar con Playwright
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

            # Normalizamos los datos en Python
            jugadores_con_tier = [
                (d["jugador"], normalizar_tier(d["tier"])) for d in jugadores_raw
            ]

            return pagina, jugadores_con_tier, False, ""

        except PlaywrightTimeoutError:
            if intento == 3:
                # Solo logueamos si falla definitivamente para no petar la consola
                emitir_log(
                    f"[!] Timeout persistente en {region.upper()} pág {pagina}", "error"
                )
            await asyncio.sleep(2)
        except Exception as e:
            emitir_log(f"[!] Error raro en {region.upper()} pág {pagina}: {e}", "error")
            return pagina, [], True, "error"

    return pagina, [], True, "timeout"


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
    region_inicial, pagina_guardada = cargar_checkpoint()
    indice_region = regiones.index(region_inicial) if region_inicial in regiones else 0

    todos_los_jugadores = cargar_datos_previos(ARCHIVO_JSON)
    jugadores_vistos = set(todos_los_jugadores)

    tiers_sin_limite = {"challenger", "grandmaster", "master"}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        # 🔥 OPTIMIZACIÓN DE RED: Bloquear imágenes y CSS para que la página cargue instantáneamente
        await context.route(
            "**/*",
            lambda route: (
                route.abort()
                if route.request.resource_type
                in ["image", "stylesheet", "font", "media"]
                else route.continue_()
            ),
        )

        pages = [await context.new_page() for _ in range(NUM_PESTANAS)]

        for region in regiones[indice_region:]:
            emitir_log(f"--- [ INICIANDO REGIÓN: {region.upper()} ] ---", "info")
            alcanzado_limite_elo = False
            pagina_base = max(1, pagina_guardada) if region == region_inicial else 1
            conteo_tier_region = defaultdict(int)

            while not alcanzado_limite_elo:
                if shutdown_event.is_set():
                    emitir_log(
                        "[!] Señal de apagado detectada. Deteniendo scraper...", "success"
                    )
                    await browser.close()
                    return

                paginas_bloque = list(range(pagina_base, pagina_base + NUM_PESTANAS))

                # 📝 LOG GENÉRICO DE INICIO DE BLOQUE (Solo 1 log por cada X páginas)
                emitir_log(
                    f"⏩ [{region.upper()}] Analizando bloque de páginas {paginas_bloque[0]} a {paginas_bloque[-1]}...",
                    "info",
                )

                resultados = await asyncio.gather(
                    *[
                        leer_pagina(pages[i], region, pag)
                        for i, pag in enumerate(paginas_bloque)
                    ]
                )

                jugadores_nuevos_en_bloque = 0
                for pagina, jugadores_tier, es_fin, motivo in sorted(resultados):
                    if es_fin and motivo == "sin filas":
                        alcanzado_limite_elo = True
                        break

                    for jugador_completo, tier in jugadores_tier:
                        if (
                            tier in tiers_sin_limite
                            or conteo_tier_region[tier] < MAX_JUGADORES_POR_TIER
                        ):
                            if jugador_completo not in jugadores_vistos:
                                todos_los_jugadores.append(jugador_completo)
                                jugadores_vistos.add(jugador_completo)
                                conteo_tier_region[tier] += 1
                                jugadores_nuevos_en_bloque += 1

                        if conteo_tier_region["iron 4"] >= MAX_JUGADORES_POR_TIER:
                            emitir_log(
                                f"🎯 Límite alcanzado en Iron 4 para {region.upper()}. Avanzando de región...",
                                "success",
                            )
                            alcanzado_limite_elo = True
                            break

                    if alcanzado_limite_elo:
                        break

                paginas_completadas = [
                    pag
                    for pag, _, es_fin, motivo in sorted(resultados)
                    if motivo not in ("error", "timeout")
                ]

                if paginas_completadas:
                    guardar_checkpoint(region, max(paginas_completadas))

                # 📝 LOG GENÉRICO DE FIN DE BLOQUE
                if jugadores_nuevos_en_bloque > 0:
                    emitir_log(
                        f"💾 [{region.upper()}] Guardados {jugadores_nuevos_en_bloque} jugadores nuevos (Total BD: {len(todos_los_jugadores)})",
                        "success",
                    )
                    guardado_atomico(todos_los_jugadores, ARCHIVO_JSON)
                else:
                    # Log sutil si están pasando muchas páginas donde ya tienen a los 50 guardados
                    emitir_log(
                        f"⏭️ [{region.upper()}] Límite de 50 alcanzado en estas divisiones. Saltando páginas rápidamente...",
                        "info",
                    )

                if alcanzado_limite_elo:
                    siguiente_indice = regiones.index(region) + 1
                    if siguiente_indice < len(regiones):
                        guardar_checkpoint(regiones[siguiente_indice], 0)
                    break

                pagina_base += NUM_PESTANAS
                await asyncio.sleep(
                    random.uniform(1.5, 3.0)
                )  # Reducido ligeramente gracias al JS

        await browser.close()

    emitir_log(
        f"🚀 ¡Proceso completado! Archivo actualizado con {len(todos_los_jugadores)} jugadores.",
        "success",
    )


def limpiar_unknowns_region_cache():
    archivo_cache = "region_cache.json"
    archivo_corregir = "nombres_a_corregir.json"

    if not os.path.exists(archivo_cache):
        return

    if shutdown_event.is_set():
        return

    try:
        with open(archivo_cache, "r", encoding="utf-8") as f:
            cache_data = json.load(f)
    except Exception as e:
        emitir_log(f"⚠️ Error leyendo {archivo_cache}: {e}", "error")
        return

    usuarios_unknown = [
        jugador for jugador, region in cache_data.items() if region == "unknown"
    ]

    if not usuarios_unknown or shutdown_event.is_set():
        return

    for jugador in usuarios_unknown:
        del cache_data[jugador]

    if not shutdown_event.is_set():
        try:
            with open(archivo_cache, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, indent=4, ensure_ascii=False)
            emitir_log(
                f"🧹 [LIMPIEZA] Eliminados {len(usuarios_unknown)} usuarios 'unknown' de {archivo_cache}.",
                "info",
            )
        except Exception as e:
            emitir_log(f"⚠️ Error limpiando {archivo_cache}: {e}", "error")
    else:
        return

    datos_corregir = []
    if os.path.exists(archivo_corregir):
        try:
            with open(archivo_corregir, "r", encoding="utf-8") as f:
                datos_corregir = json.load(f)
        except json.JSONDecodeError:
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
            emitir_log(
                f"✅ [LIMPIEZA] Traspasados {nuevos_añadidos} usuarios a {archivo_corregir}.",
                "success",
            )
        except Exception as e:
            emitir_log(f"⚠️ Error actualizando {archivo_corregir}: {e}", "error")


def wait_for_key_activation(new_key):
    url = "https://euw1.api.riotgames.com/lol/status/v4/platform-data"
    headers = {"X-Riot-Token": new_key}

    emitir_log("⏳ [GESTOR] Esperando a que Riot active la nueva API Key (puede tardar 1-2 min)...", "api")
    
    intentos = 0
    while not shutdown_event.is_set():
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            
            if resp.status_code == 200:
                emitir_log("✅ [GESTOR] ¡Nueva API Key totalmente operativa y propagada!", "success")
                break
                
            elif resp.status_code in [401, 403]:
                intentos += 1
                emitir_log(f"⏳ [GESTOR] La llave existe pero Riot aún no le da permisos (Error {resp.status_code}). Intento {intentos}... reintentando en 15s.", "api")
            else:
                emitir_log(f"⏳ [GESTOR] Estado inesperado de la API (Código {resp.status_code}). Reintentando en 15s...", "api")
                
        except requests.exceptions.RequestException as e:
            emitir_log(f"⚠️ [GESTOR] Error de red comprobando llave: {e}", "api")
        except Exception as e:
            emitir_log(f"⚠️ [GESTOR] Error de conexión comprobando llave: {e}", "error")
            
        time.sleep(15)


def background_key_manager(loop_principal):
    while not shutdown_event.is_set():
        wait_seconds = 600
        try:
            resultado = auto_renew_riot_key.run(api_event=riot_api_ready_sync)

            # Si ocurre un error grave y no devuelve tupla
            if not resultado:
                emitir_log("❌ [GESTOR] El renovador de llaves no devolvió datos. Reintentando en 10 min...", "error")
                shutdown_event.wait(600)
                continue

            if isinstance(resultado, tuple):
                nueva_llave, minutes_left = resultado
            else:
                nueva_llave = resultado
                minutes_left = 60

            if nueva_llave:
                if not riot_api_ready_sync.is_set():
                    loop_principal.call_soon_threadsafe(riot_api_ready_async.clear)

                # 1. Actualizamos en memoria
                os.environ["RIOT_API_KEY"] = nueva_llave
                global RIOT_API_KEY
                RIOT_API_KEY = nueva_llave
                
                # 2. 🔥 ACTUALIZAMOS FÍSICAMENTE EL ARCHIVO .env 🔥
                set_key(".env", "RIOT_API_KEY", nueva_llave)
                emitir_log("💾 [GESTOR] Archivo .env sobrescrito con la nueva API Key.", "success")

                wait_for_key_activation(nueva_llave)

                emitir_log("🟢 [GESTOR] Reanudando peticiones de la API de Riot.", "success")
                
                riot_api_ready_sync.set()
                loop_principal.call_soon_threadsafe(riot_api_ready_async.set)

                wait_seconds = 6 * 3600
                emitir_log("⏳ [GESTOR] Nueva API Key lista. Siguiente revisión en 6 horas...", "api")
                
            else:
                # Se ejecutará cuando no hay llave nueva (incluyendo los calentamientos)
                wait_minutes = max(minutes_left - 32, 5)
                wait_minutes = min(wait_minutes, 360)
                wait_seconds = wait_minutes * 60

                # Log modificado para que tenga sentido con los tiempos aleatorios de calentamiento
                emitir_log(f"⏳ [GESTOR] Mantenimiento/Calentamiento completado. Siguiente revisión en {wait_minutes} min...", "api")

        except Exception as e:
            emitir_log(f"❌ [GESTOR] Error crítico en el hilo de renovación: {e}", "error")
            riot_api_ready_sync.set()
            if loop_principal and riot_api_ready_async:
                loop_principal.call_soon_threadsafe(riot_api_ready_async.set)
            wait_seconds = 600

        shutdown_event.wait(wait_seconds)

RIOT_RATE_LIMITS = {}
RIOT_API_KEY = os.getenv("RIOT_API_KEY", "")
MATCHUPS_FILE = "matchups_stats.json"

# (Reducida lista para el script, respetando estructura original)
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
    puuid: str, macro_region: str, headers: dict, request_fn=None, timeout=10
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

    if isinstance(request_fn, int):
        timeout = request_fn
        request_fn = None

    if request_fn is None:
        if "hacer_peticion_riot" in globals():
            request_fn = globals()["hacer_peticion_riot"]
        else:
            request_fn = requests.get

    emitir_log(
        f"🔍 [RANGO] Buscando rango para PUUID: {puuid[:10]}... en macro-región: {macro_region}",
        "api",
    )

    for plataforma in plataformas.get(macro_region, plataformas["europe"]):
        if shutdown_event.is_set():
            emitir_log(
                "⚠️ Búsqueda de rango abortada por apagado del servidor.", "error"
            )
            return resultado

        try:
            league_url = f"https://{plataforma}.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}"

            league_response = request_fn(league_url, headers=headers, timeout=timeout)

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

        # 💡 CORRECCIÓN 2: Solución para los bloqueos de DNS y red ([Errno 11001] getaddrinfo failed)
        except requests.exceptions.RequestException as e:
            emitir_log(f"[API RIOT] Error de red en {plataforma}: {e}", "api")
            emitir_log(
                f"[API RIOT] Abortando petición. Dominio inalcanzable: {league_url}",
                "api",
            )
            continue
        except Exception as e:
            emitir_log(
                f"   ❌ [EXCEPCIÓN] Error procesando en {plataforma}: {e}", "error"
            )
            continue

    emitir_log(
        f"❌ [RANGO] No se pudo encontrar ningún rango para este jugador. Se devuelve UNRANKED.",
        "api",  # Cambiado a api para mantener congruencia en errores de busqueda
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


############################################################################################
def limpiar_base_de_datos(data):
    emitir_log(
        "Iniciando revisión y limpieza de la base de datos de matchups...", "info"
    )
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
    else:
        emitir_log(
            "No se requirieron cambios en la limpieza de la base de datos.", "info"
        )

    return data


def cargar_matchups():
    emitir_log("Cargando base de datos de matchups...", "info")
    data = {
        "processed_matches": [],
        "stats": {"TOP": {}, "JUNGLE": {}, "MID": {}, "ADC": {}, "SUPPORT": {}},
    }
    if os.path.exists(MATCHUPS_FILE):
        try:
            with open(MATCHUPS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            emitir_log(f"✅ {MATCHUPS_FILE} cargado correctamente.", "success")
        except Exception as e:
            emitir_log(f"Error al cargar {MATCHUPS_FILE}: {e}", "error")
            return data
    else:
        emitir_log(
            f"⚠️ {MATCHUPS_FILE} no existe. Se inicializará una base de datos vacía.",
            "warning",
        )

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
    emitir_log("Obteniendo campeones por rol desde la base de datos...", "info")
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

    emitir_log("Campeones clasificados por rol correctamente.", "success")
    return campeones_por_rol


def registrar_resultado_matchup(
    stats_dict, rol, champ1, champ2, gano_champ_1, elo="UNRANKED"
):
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


@app.get("/api/top-matchups")
def top_matchups():
    emitir_log("Solicitando top 15 matchups...", "info")
    tops = descargar_coachings.obtener_top_matchups(15)
    return tops


@app.get("/api/top-matchups-sin-guia")
def top_matchups_sin_guia():
    emitir_log("Solicitando top 10 matchups sin guía...", "info")
    tops = descargar_coachings.obtener_top_matchups_sin_guia(10)
    return tops


@app.post("/api/generate-guide")
def gen_guide(datos: GuiaRequest):
    emitir_log(
        f"Iniciando generación de guía IA para: {datos.rol} | {datos.champ_main} vs {datos.champ_enemy}",
        "info",
    )
    exito = descargar_coachings.generar_guia_ia_directa(
        datos.rol, datos.champ_main, datos.champ_enemy
    )

    if exito:
        emitir_log(
            f"✅ Guía generada exitosamente para {datos.champ_main} vs {datos.champ_enemy}.",
            "success",
        )
        return {"success": True}

    error_tipo = getattr(descargar_coachings, "ULTIMO_ERROR_GUIA", "error")
    mensajes_error = {
        "missing_keys": "No hay API Keys configuradas.",
        "quota": "Cuota diaria o tokens agotados en las API Keys.",
        "error": "Fallo de generación con IA.",
    }

    emitir_log(
        f"❌ Error al generar la guía: {mensajes_error.get(error_tipo, mensajes_error['error'])}",
        "error",
    )

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
        emitir_log(
            "Intento de procesamiento rechazado: No se proporcionó URL.", "warning"
        )
        return JSONResponse({"error": "No se proporcionó URL"}, status_code=400)

    emitir_log(f"Iniciando procesamiento de URL en segundo plano: {req.url}", "info")
    background_tasks.add_task(run_scraper, req.url)
    return {"message": "✅ Procesamiento iniciado."}


def run_scraper(url: str):
    try:
        subprocess.run(["python", "descargar_coachings.py", url])
        emitir_log(f"Scraper finalizado para la URL: {url}", "success")
    except Exception as e:
        emitir_log(f"Error ejecutando el scraper para la URL {url}: {e}", "error")


@app.get("/api/campeones")
def get_campeones(
    rol: str = Query(
        None, description="Filtrar campeones por rol (TOP, JUNGLE, MID, ADC, SUPPORT)"
    )
):
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
            emitir_log(
                "Lista de campeones obtenida correctamente desde Data Dragon.",
                "success",
            )
            return {"campeones": champs}
    except Exception as e:
        emitir_log(
            f"Fallo al contactar Data Dragon, usando fallback local. Detalle: {e}",
            "warning",
        )

    return {"campeones": sorted(CAMPEONES_BASE)}


CAMPEONES_POR_ROL = obtener_campeones_por_rol("matchups_stats.json")


@app.get("/api/guias")
def get_guias():
    emitir_log("Consultando estructura de guías locales...", "info")
    base_dir = "coachings"
    estructura = {}
    if not os.path.exists(base_dir):
        emitir_log(
            f"El directorio base '{base_dir}' no existe. Devolviendo estructura vacía.",
            "warning",
        )
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
        emitir_log(f"Error crítico leyendo el directorio de guías: {e}", "error")
    return {"guias": estructura}


@app.get("/api/guia_matchup/{rol}/{campeon1}/{campeon2}")
def get_guia_matchup(rol: str, campeon1: str, campeon2: str):
    emitir_log(f"Solicitando lectura de guía: {rol} | {campeon1} vs {campeon2}", "info")
    ruta_archivo = verificar_matchup_existe(rol, campeon1, campeon2)

    if not ruta_archivo:
        emitir_log(
            f"Guía no encontrada para: {rol} | {campeon1} vs {campeon2}", "warning"
        )
        raise HTTPException(status_code=404, detail="Guía de matchup no encontrada")

    try:
        with open(ruta_archivo, "r", encoding="utf-8") as f:
            contenido = f.read()
        emitir_log(f"✅ Guía cargada exitosamente: {ruta_archivo}", "success")
        return {"contenido": contenido}
    except Exception as e:
        emitir_log(f"Error al leer el archivo de la guía {ruta_archivo}: {e}", "error")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sugerencias_matchup")
def get_sugerencias_matchup(
    rol: str = Query(..., description="TOP, JUNGLE, MID, ADC, SUPPORT"),
    campeon: str = Query(..., description="Nombre del campeón"),
    side: str = Query("aliado", description="'aliado' o 'enemigo'"),
):
    emitir_log(
        f"Calculando sugerencias de matchup para {campeon} en {rol} (Side: {side})",
        "info",
    )
    rol_canon = normalizar_rol_canonico(rol)

    norm_c = normalizar(campeon)
    norm_c = ESTANDARIZAR_NOMBRES.get(norm_c, norm_c)

    if not norm_c:
        emitir_log("Nombre de campeón inválido o no reconocido.", "warning")
        return {"campeon": campeon, "rol": rol_canon, "mejores": [], "peores": []}

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

    if not lista_enfrentamientos:
        emitir_log(
            f"No se encontraron estadísticas para {norm_c}. Buscando en estructura de carpetas...",
            "info",
        )
        ruta_base = "coachings"
        roles_validos = mapear_rol(rol_canon)
        if os.path.exists(ruta_base):
            for r_folder in os.listdir(ruta_base):
                if normalizar(r_folder) in roles_validos:
                    rol_p = os.path.join(ruta_base, r_folder)
                    for c_folder in os.listdir(rol_p):

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
    emitir_log(
        f"Solicitud de extracción de partidas para el invocador: {riot_id} en {region}",
        "info",
    )

    if "#" not in riot_id:
        emitir_log(f"Formato de Riot ID incorrecto: {riot_id}", "error")
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
        emitir_log(
            "RIOT_API_KEY no detectada. Devolviendo datos simulados (mock).", "warning"
        )
        mock_matches = [
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

    game_name_encoded = urllib.parse.quote(game_name.strip())
    tag_line_encoded = urllib.parse.quote(tag_line.strip())

    try:
        puuid = None
        for reg_acc in ["europe", "americas", "asia"]:
            url_account = f"https://{reg_acc}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name_encoded}/{tag_line_encoded}"

            riot_api_ready_sync.wait()
            headers["X-Riot-Token"] = os.environ.get("RIOT_API_KEY")

            r_account = requests.get(url_account, headers=headers)

            if r_account.status_code == 200:
                puuid = r_account.json()["puuid"]
                emitir_log(
                    f"✅ PUUID resuelto correctamente en la región: {reg_acc}",
                    "success",
                )
                break
            elif r_account.status_code == 429:
                retry_time = int(r_account.headers.get("Retry-After", 10))
                emitir_log(
                    f"⚠️ Límite de peticiones excedido (429) al buscar PUUID. Esperando {retry_time}s...",
                    "warning",
                )
                time.sleep(retry_time)

        if not puuid:
            emitir_log(
                f"❌ Invocador '{riot_id}' no encontrado en ninguna región.", "error"
            )
            raise HTTPException(
                status_code=404,
                detail="Invocador no encontrado en la base de datos de Riot",
            )

        regiones_a_probar = [region] + [
            r for r in ["europe", "americas", "asia"] if r != region
        ]
        match_ids = []
        region_activa = None

        for match_reg in regiones_a_probar:
            url_matches = f"https://{match_reg}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?start=0&count=20"

            riot_api_ready_sync.wait()
            headers["X-Riot-Token"] = os.environ.get("RIOT_API_KEY")

            r_matches = requests.get(url_matches, headers=headers)

            if r_matches.status_code == 200:
                datos = r_matches.json()
                if len(datos) > 0:
                    match_ids = datos
                    region_activa = match_reg
                    emitir_log(
                        f"✅ Se encontraron {len(match_ids)} partidas recientes en la región: {region_activa}",
                        "success",
                    )
                    break
            elif r_matches.status_code == 429:
                retry_time = int(r_matches.headers.get("Retry-After", 10))
                emitir_log(
                    f"⚠️ Límite de peticiones (429) al recuperar Match IDs. Esperando {retry_time}s...",
                    "warning",
                )
                time.sleep(retry_time)

        if not match_ids:
            emitir_log(
                f"El invocador '{riot_id}' no tiene historial de partidas recientes.",
                "info",
            )
            return {"invocador": f"{game_name}#{tag_line}", "partidas": []}

        rango_actual = obtener_rango_riot(
            puuid, region_activa, headers, request_fn=hacer_peticion_riot, timeout=5
        )
        partidas_resultado = []
        hubo_cambios = False

        emitir_log(f"Iniciando procesamiento de {len(match_ids)} partidas...", "info")

        for match_id in match_ids:
            url_detail = f"https://{region_activa}.api.riotgames.com/lol/match/v5/matches/{match_id}"
            time.sleep(1.2)

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
                    emitir_log(
                        f"Extrayendo datos de la nueva partida: {match_id}", "info"
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
            elif r_detail.status_code == 429:
                emitir_log(
                    f"⚠️ Límite de peticiones (429) al procesar partida {match_id}. Ignorando partida...",
                    "warning",
                )

        if hubo_cambios:
            emitir_log(
                "Guardando nuevos registros de matchups extraídos de las partidas...",
                "success",
            )
            matchup_db["processed_matches"] = serializar_procesados(
                processed_matches, processed_metadata
            )
            matchup_db["stats"] = stats_dict
            guardar_matchups(matchup_db)

        return {"invocador": f"{game_name}#{tag_line}", "partidas": partidas_resultado}

    except HTTPException:
        raise
    except Exception as e:
        emitir_log(
            f"❌ Error inesperado obteniendo el historial para '{riot_id}': {e}",
            "error",
        )
        raise HTTPException(status_code=500, detail=str(e))


###############################################################################################
@app.post("/api/analizar_draft")
def analizar_draft(req: DraftRequest):
    emitir_log("[ANALIZAR_DRAFT] Iniciando análisis de draft...", "info")
    matchup_db = cargar_matchups()
    stats_dict = matchup_db.get("stats", {})

    emitir_log(
        f"[ANALIZAR_DRAFT] Base de datos de stats cargada con {len(stats_dict)} roles disponibles.",
        "info",
    )

    roles_input = [
        ("TOP", req.top_aliado, req.top_enemigo),
        ("JUNGLE", req.jungle_aliado, req.jungle_enemigo),
        ("MID", req.mid_aliado, req.mid_enemigo),
        ("ADC", req.adc_aliado, req.adc_enemigo),
        ("SUPPORT", req.support_aliado, req.support_enemigo),
    ]

    resultados = []

    for rol, c_aliado, c_enemigo in roles_input:
        emitir_log(
            f"[ANALIZAR_DRAFT] Procesando rol {rol}: {c_aliado} vs {c_enemigo}", "info"
        )
        if not c_aliado or not c_enemigo:
            emitir_log(
                f"[ANALIZAR_DRAFT] Faltan campeones en el rol {rol}. Se marca como sin_datos.",
                "info",
            )
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
                    "desglose_elo": [],
                }
            )
            continue

        rol_stats = stats_dict.get(rol, {})
        aliado_norm = normalizar(c_aliado)
        enemigo_norm = normalizar(c_enemigo)

        key_directa = f"{aliado_norm}_vs_{enemigo_norm}"
        key_inversa = f"{enemigo_norm}_vs_{aliado_norm}"

        emitir_log(
            f"[ANALIZAR_DRAFT] Buscando keys: Directa='{key_directa}', Inversa='{key_inversa}'",
            "info",
        )

        matchup_data = None
        es_inverso = False

        if key_directa in rol_stats:
            matchup_data = rol_stats[key_directa]
            emitir_log(
                f"[ANALIZAR_DRAFT] Matchup encontrado de forma DIRECTA.", "success"
            )
        elif key_inversa in rol_stats:
            matchup_data = rol_stats[key_inversa]
            es_inverso = True
            emitir_log(
                f"[ANALIZAR_DRAFT] Matchup encontrado de forma INVERSA.", "success"
            )

        if matchup_data:
            if not es_inverso:
                w = matchup_data.get("wins", 0)
                l = matchup_data.get("losses", 0)
            else:
                w = matchup_data.get("losses", 0)
                l = matchup_data.get("wins", 0)

            total = w + l
            wr = round((w / total) * 100, 1) if total > 0 else 0

            emitir_log(
                f"[ANALIZAR_DRAFT] Cálculos base: Total={total}, Wins={w}, Losses={l}, WR={wr}%",
                "info",
            )

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
            emitir_log(
                f"[ANALIZAR_DRAFT] Procesando desglose por Elos. Encontrados: {len(elos_dict.keys())}",
                "info",
            )

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

            desglose_elo.sort(key=lambda x: x["wins"] + x["losses"], reverse=True)

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
            emitir_log(
                f"[ANALIZAR_DRAFT] No se encontraron datos para {key_directa} en el rol {rol}.",
                "error",
            )
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

    emitir_log("[ANALIZAR_DRAFT] Análisis finalizado correctamente.", "success")
    return {"resultados": resultados}


SUB_TO_MACRO = {
    "euw1": "europe",
    "eun1": "europe",
    "tr1": "europe",
    "ru": "europe",
    "me1": "europe",
    "na1": "americas",
    "la1": "americas",
    "la2": "americas",
    "br1": "americas",
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
    emitir_log(
        f"[OPTIMIZAR] Iniciando con ruta_json={ruta_json}, ruta_cache={ruta_cache}, reintentar_unknown={reintentar_unknown}",
        "info",
    )

    if not os.path.exists(ruta_json):
        emitir_log(f"[OPTIMIZAR] Archivo {ruta_json} no encontrado.", "error")
        return []

    cache = {}
    if os.path.exists(ruta_cache):
        try:
            with open(ruta_cache, "r", encoding="utf-8") as f:
                cache = json.load(f)
            emitir_log(
                f"[OPTIMIZAR] Caché cargada correctamente con {len(cache)} registros.",
                "success",
            )
        except json.JSONDecodeError:
            emitir_log(
                "[OPTIMIZAR] Archivo de caché corrupto, se creará uno nuevo.", "error"
            )

    api_key = os.getenv("RIOT_API_KEY")
    headers = {"X-Riot-Token": api_key} if api_key else {}

    try:
        with open(ruta_json, "r", encoding="utf-8") as f:
            nicks = json.load(f)

        buckets = {"europe": [], "americas": [], "asia": [], "unknown": []}
        total_cuentas = len(nicks)
        emitir_log(
            f"[OPTIMIZAR] 🔍 Iniciando optimización. {total_cuentas} cuentas en la lista.",
            "info",
        )

        nuevos_procesados = 0

        for index, nick in enumerate(nicks):
            if shutdown_event.is_set():
                emitir_log(
                    "[OPTIMIZAR] Optimización abortada por el usuario (Ctrl+C). Guardando progreso...",
                    "success",
                )
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
                # LOG ÚNICAMENTE PARA NUEVAS CUENTAS NO CACHEADAS
                emitir_log(
                    f"[OPTIMIZAR] [{index + 1}/{total_cuentas}] 📡 Consultando API para nueva cuenta: {nick}",
                    "info",
                )

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
            emitir_log("[OPTIMIZAR] Guardando caché final en disco...", "info")
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

        emitir_log(
            f"[OPTIMIZAR] Escribiendo lista intercalada de {len(lista_intercalada)} cuentas en {ruta_json}...",
            "info",
        )
        with open(ruta_json, "w", encoding="utf-8") as f:
            json.dump(lista_intercalada, f, indent=4, ensure_ascii=False)

        emitir_log("\n" + "=" * 50, "info")
        emitir_log(
            f"✅ [PRELOAD] ¡Finalizado! {total_cuentas} cuentas procesadas.", "success"
        )
        emitir_log(
            f"⚡ Peticiones ahorradas por caché: {total_cuentas - nuevos_procesados}",
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
        emitir_log(
            f"[OPTIMIZAR] Error inesperado en optimizar_orden_preload: {e}", "error"
        )
        return []


def descubrir_macro_region_real(nick: str, headers: dict) -> str:

    if shutdown_event.is_set():

        return "unknown"

    if "#" not in nick:

        return "unknown"

    nombre, tag = nick.rsplit("#", 1)
    url_account = f"https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{quote(nombre)}/{quote(tag)}"
    headers["X-Riot-Token"] = os.environ.get("RIOT_API_KEY")

    resp_acc = hacer_peticion_riot(url_account, headers, riot_id=nick)

    if shutdown_event.is_set():
        return "unknown"

    if not resp_acc:

        return "unknown"

    if resp_acc.status_code != 200:

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
        if shutdown_event.is_set():
            return "unknown"

        url_summoner = f"https://{reg}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}"
        headers["X-Riot-Token"] = os.environ.get("RIOT_API_KEY")

        r = hacer_peticion_riot(url_summoner, headers)

        if r and r.status_code == 200:
            macro = SUB_TO_MACRO[reg]

            return macro

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
    emitir_log(
        f"[CORRECCION] Solicitud recibida para enviar a cuarentena a: {riot_id}", "info"
    )
    if not riot_id:
        emitir_log("[CORRECCION] riot_id vacío recibido. Abortando.", "error")
        return

    archivo_json = "nombres_a_corregir.json"
    try:
        datos = []
        if os.path.exists(archivo_json):
            with open(archivo_json, "r", encoding="utf-8") as f:
                try:
                    datos = json.load(f)
                    emitir_log(
                        f"[CORRECCION] {archivo_json} leído, {len(datos)} registros existentes.",
                        "info",
                    )
                except json.JSONDecodeError:
                    emitir_log(
                        f"[CORRECCION] {archivo_json} está corrupto o vacío.", "error"
                    )
                    pass

        if riot_id not in datos:
            datos.append(riot_id)
            with open(archivo_json, "w", encoding="utf-8") as f:
                json.dump(datos, f, indent=4, ensure_ascii=False)
            emitir_log(
                f"📝 [CORRECCIÓN] Jugador {riot_id} añadido a {archivo_json} tras error de red.",
                "success",
            )
        else:
            emitir_log(
                f"[CORRECCION] El jugador {riot_id} ya estaba en la lista de corrección. No se hacen cambios.",
                "info",
            )

    except Exception as ex:
        emitir_log(
            f"⚠️ [CORRECCION] Error crítico al escribir en {archivo_json}: {ex}",
            "error",
        )


def hacer_peticion_riot(
    # 1. Añadimos timeout a los parámetros (con valor por defecto de 10)
    url: str,
    headers: dict,
    max_reintentos_red: int = 1,
    riot_id: str = None,
    timeout: int = 10,
):

    if shutdown_event.is_set():
        emitir_log("[HTTP] Abortado por shutdown_event antes de iniciar.", "info")
        return None

    dominio = urlparse(url).netloc
    region = dominio.split(".")[0]

    with GLOBAL_LOCK:
        if region not in RIOT_RATE_LIMITS:
            RIOT_RATE_LIMITS[region] = deque()
            REGION_LOCKS[region] = threading.Lock()
            emitir_log(
                f"[HTTP] Inicializados locks y deques para la región {region}", "info"
            )

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
                emitir_log(
                    f"[HTTP-RATE] shutdown_event detectado durante la espera. Saliendo.",
                    "info",
                )
                return None
            continue

        try:
            # 2. Reemplazamos el "timeout=10" fijo por la variable "timeout"
            r = requests.get(url, headers=headers, timeout=timeout)

            if r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", 10))
                emitir_log(
                    f"[API RIOT] Límite superado en {region}. Esperando {retry_after}s... (HTTP 429)",
                    "api",
                )
                if shutdown_event.wait(retry_after):
                    return None
                continue

            if r.status_code >= 500:
                intentos += 1
                emitir_log(
                    f"[API RIOT] Error 5xx del servidor HTTP {r.status_code} ({intentos}/{max_reintentos_red})",
                    "error",
                )
                if intentos >= max_reintentos_red:
                    return r
                if shutdown_event.wait(5):
                    return None
                continue

            return r

        except requests.exceptions.RequestException as e:
            intentos += 1

            if intentos >= max_reintentos_red:

                if riot_id:
                    emitir_log(
                        f"[HTTP] Delegando a mandar_a_corregir usando riot_id {riot_id}",
                        "info",
                    )
                    mandar_a_corregir(riot_id)
                else:
                    match = re.search(r"/by-riot-id/([^/]+)/([^/?]+)", url)
                    if match:
                        game_name = unquote(match.group(1))
                        tag_line = unquote(match.group(2))
                        riot_generado = f"{game_name}#{tag_line}"
                        emitir_log(
                            f"[HTTP] Regex dedujo el riot_id {riot_generado} desde la URL. Delegando corrección.",
                            "info",
                        )
                        mandar_a_corregir(riot_generado)

                return None

            if shutdown_event.wait(5):
                return None

    return None


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

    headers = {"X-Riot-Token": RIOT_API_KEY}
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

            emitir_log(
                f"[PRELOAD_TASK] Esperando semáforo riot_api_ready_sync para Account API...",
                "api",
            )
            riot_api_ready_sync.wait()
            headers["X-Riot-Token"] = os.environ.get("RIOT_API_KEY")

            r_account = hacer_peticion_riot(url_account, headers)

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
            emitir_log(
                f"[PRELOAD] {error_api}. Se detiene la precarga sin purgar el jugador.",
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
                    emitir_log(
                        f"[PRELOAD_TASK] Volcando a disco el bloque (Múltiplo de 10 partidas procesadas)...",
                        "info",
                    )
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


################################################################################################
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

    # Crear un set en minúsculas para búsquedas rápidas (case-insensitive)
    set_a_corregir = {
        nombre.strip().lower() for nombre in nombres_corregir if isinstance(nombre, str)
    }
    emitir_log(
        f"[PURGA] Set de purga normalizado creado con {len(set_a_corregir)} cuentas únicas.",
        "info",
    )

    # Cargar el preload.json
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

    # Filtrar jugadores
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
            emitir_log(
                f"[PURGA] Coincidencia encontrada. Purgando cuenta: {riot_id}", "info"
            )
        else:
            jugadores_filtrados.append(jug)

    # Guardar los cambios si hubo eliminaciones
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
    emitir_log(
        f"[API_PRELOAD_STATUS] Consultando estado del servicio: {PRELOAD_STATUS['status']} | Progreso: {PRELOAD_STATUS.get('processed_matches', 0)}/{PRELOAD_STATUS.get('total_matches', 0)}",
        "info",
    )
    return PRELOAD_STATUS


if __name__ == "__main__":
    import uvicorn

    emitir_log(
        "[MAIN] Arrancando servidor ASGI con Uvicorn en http://127.0.0.1:8000 ...",
        "info",
    )
    uvicorn.run(app, host="127.0.0.1", port=8000, access_log=False)
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, access_log=False)
