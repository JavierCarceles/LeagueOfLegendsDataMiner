import asyncio
import json
import time
import os
import random
from playwright.async_api import (
    async_playwright,
    TimeoutError as PlaywrightTimeoutError,
)
from dotenv import load_dotenv

ARCHIVO_JSON = "preload.json"


def guardado_atomico(datos, ruta_archivo):

    ruta_temporal = f"{ruta_archivo}.tmp"
    with open(ruta_temporal, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=4)
    # Reemplazo atómico a nivel de sistema operativo
    os.replace(ruta_temporal, ruta_archivo)


def cargar_datos_previos(ruta_archivo):
    
    if os.path.exists(ruta_archivo):
        try:
            with open(ruta_archivo, "r", encoding="utf-8") as f:
                datos = json.load(f)
                print(
                    f"[*] Se han cargado {len(datos)} usuarios existentes de '{ruta_archivo}'."
                )
                return datos
        except json.JSONDecodeError:
            print(
                f"[!] Error leyendo '{ruta_archivo}'. Asegúrate de que es un JSON válido. Empezando de cero..."
            )
            return []
    else:
        print(f"[*] No se encontró '{ruta_archivo}'. Se creará uno nuevo.")
        return []


NUM_PESTANAS = 5


def cargar_checkpoint():
    """Obtiene la region y la ultima pagina guardadas en .env."""
    load_dotenv(override=True)
    region = os.getenv("REGIONSCRAPPER", "KR").strip().lower()
    try:
        pagina = max(0, int(os.getenv("PAGESCRAPPER", "0")))
    except ValueError:
        pagina = 0

    return region, pagina


def guardar_checkpoint(region, pagina):
    """Actualiza solo el checkpoint del scraper y conserva el resto de .env."""
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
    """Lee una pagina y devuelve sus jugadores y si activa el corte de elo."""
    url = f"https://www.op.gg/leaderboards/tier?region={region}&page={pagina}"

    for intento in range(1, 4):
        try:
            print(f">> Leyendo pagina {pagina} de {region.upper()}...")
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
            print(
                f"[!] Timeout en pagina {pagina} de {region.upper()} (intento {intento}/3)"
            )
            if intento < 3:
                await asyncio.sleep(10)
        except Exception as e:
            print(f"[!] Error en pagina {pagina} de {region.upper()}: {e}")
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

    # 1. Cargar la lista existente del preload.json
    todos_los_jugadores = cargar_datos_previos(ARCHIVO_JSON)
    # Usamos un Set para búsquedas ultra rápidas de duplicados
    jugadores_vistos = set(todos_los_jugadores)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        pages = [await context.new_page() for _ in range(NUM_PESTANAS)]

        for region in regiones[indice_region:]:
            print(f"\n=======================================================")
            print(f"--- [ INICIANDO REGIÓN: {region.upper()} ] ---")
            print(f"=======================================================")
            alcanzado_limite_elo = False
            pagina_base = max(1, pagina_guardada) if region == region_inicial else 1

            while not alcanzado_limite_elo:
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
                        print(
                            f"[!] Corte en pagina {pagina} de {region.upper()}: {jugador_corte} ({tier_corte})."
                        )
                        alcanzado_limite_elo = True
                        break

                    for jugador_completo in jugadores:
                        if jugador_completo not in jugadores_vistos:
                            todos_los_jugadores.append(jugador_completo)
                            jugadores_vistos.add(jugador_completo)
                            jugadores_nuevos_en_bloque += 1
                            print(f"  [+] Añadido: {jugador_completo}")

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
                    print(
                        f"  [GUARDADO] Bloque terminado. Total usuarios: {len(todos_los_jugadores)}"
                    )
                    guardado_atomico(todos_los_jugadores, ARCHIVO_JSON)

                if alcanzado_limite_elo:
                    if any(
                        motivo in ("error", "timeout")
                        for _, _, _, _, motivo in resultados
                    ):
                        print(
                            "[!] No se actualiza el checkpoint porque hubo un error de lectura."
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

    print(
        f"\n¡Proceso completado! Archivo '{ARCHIVO_JSON}' actualizado con {len(todos_los_jugadores)} jugadores en total."
    )


def scrape_opgg():
    asyncio.run(scrape_opgg_async())


if __name__ == "__main__":
    scrape_opgg()
