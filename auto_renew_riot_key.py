import os
import re
import time
import random
from dotenv import load_dotenv, set_key
from playwright_stealth import stealth_sync
from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
CHROME_PROFILE_PATH = os.path.join(os.path.expanduser("~"), "riot_bot_profile")


def calentar_perfil(page):
    """Simula comportamiento humano visitando webs aleatorias."""
    sitios = [
        "https://es.wikipedia.org/wiki/League_of_Legends",
        "https://www.youtube.com/results?search_query=lol+esports",
        "https://www.reddit.com/r/leagueoflegends/",
        "https://lolesports.com/",
    ]

    # Visita 2 sitios al azar
    for sitio in random.sample(sitios, 2):
        try:
            page.goto(sitio, wait_until="domcontentloaded", timeout=15000)
            # Simula lectura con scroll aleatorio
            for _ in range(random.randint(3, 6)):
                page.mouse.wheel(0, random.randint(300, 700))
                time.sleep(random.uniform(1.5, 4.5))
        except Exception:
            pass  # Si una web falla al cargar, la ignoramos y seguimos


def parse_remaining_minutes(text):
    """Extrae los minutos restantes del texto de Riot."""
    match = re.search(
        r"in\s+(?:(\d+)\s+hours?\s+and\s+)?(\d+)\s+minutes", text, re.IGNORECASE
    )
    if match:
        hours = int(match.group(1)) if match.group(1) else 0
        minutes = int(match.group(2))
        return (hours * 60) + minutes
    if "expired" in text.lower() or "0 minutes" in text.lower():
        return 0
    return 9999


def run(api_event=None):
    """Ejecuta la comprobación y renovación automática de la API Key."""
    load_dotenv(ENV_PATH)

    try:
        with sync_playwright() as p:
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=CHROME_PROFILE_PATH,
                    channel="chrome",
                    headless=False,
                    ignore_default_args=["--enable-automation"],
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--start-maximized",
                    ],
                    permissions=["clipboard-read", "clipboard-write"],
                )
            except Exception as ctx_err:
                error_str = str(ctx_err).lower()
                if "lock" in error_str or "in use" in error_str:
                    print(
                        "\n[RENOVADOR] ❌ ERROR CRÍTICO: El perfil de Chrome está bloqueado."
                    )
                    print(
                        "[RENOVADOR] 💡 Causa probable 1: Tienes una ventana de Chrome abierta usando este perfil."
                    )
                    print(
                        "[RENOVADOR] 💡 Causa probable 2: OneDrive u otro proceso ha bloqueado la carpeta."
                    )
                raise ctx_err

            page = context.pages[0] if context.pages else context.new_page()
            stealth_sync(page)
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            page.goto("https://developer.riotgames.com/", wait_until="networkidle")

            logged_in = False
            try:
                page.wait_for_selector("p:has-text('Expires:')", timeout=5000)
                logged_in = True
            except Exception:
                logged_in = False

            if not logged_in:
                print("⚠️ [BOT] No se detectó sesión. Intentando iniciar sesión...")
                try:
                    login_btn = page.locator(
                        "a:has-text('LOG IN'), a:has-text('Log In')"
                    ).first
                    if login_btn.is_visible(timeout=2000):
                        login_btn.click()
                except Exception:
                    pass
                page.wait_for_selector("p:has-text('Expires:')", timeout=120000)

            expiration_locator = page.locator("p:has-text('Expires:')")
            expiration_text = expiration_locator.inner_text()
            minutes_left = parse_remaining_minutes(expiration_text)

            print(f"[RENOVADOR] Tiempo restante de la API Key: {minutes_left} minutos.")

            # NUEVA LÓGICA: Renovar si faltan menos de 30 minutos
            if minutes_left < 30:
                print(
                    "[RENOVADOR] ⚠️ Faltan menos de 30 minutos. Pausando peticiones y renovando..."
                )

                if api_event:
                    api_event.clear()

                try:
                    captcha_frame = page.frame_locator(
                        'iframe[title="reCAPTCHA"]'
                    ).first
                    captcha_checkbox = captcha_frame.locator("#recaptcha-anchor")

                    if captcha_checkbox.is_visible(timeout=5000):
                        captcha_checkbox.click()
                        print(
                            "[RENOVADOR] 🤖 Clic en el reCAPTCHA realizado. Comprobando validación..."
                        )

                        try:
                            captcha_frame.locator(
                                '#recaptcha-anchor[aria-checked="true"]'
                            ).wait_for(timeout=5000)
                            print("[RENOVADOR] ✅ reCAPTCHA superado automáticamente.")
                        except Exception:
                            print(
                                "⚠️ [ATENCIÓN] El reCAPTCHA pide resolver imágenes. Tienes 45s para hacerlo manualmente."
                            )
                            captcha_frame.locator(
                                '#recaptcha-anchor[aria-checked="true"]'
                            ).wait_for(timeout=45000)
                            print("[RENOVADOR] ✅ reCAPTCHA resuelto con éxito.")

                    print("[RENOVADOR] 🔄 Haciendo clic en Regenerar...")
                    page.locator("input[name='confirm_action']").first.click()
                    page.wait_for_load_state("networkidle")
                    page.wait_for_timeout(3000)

                    print("[RENOVADOR] 📋 Extrayendo la nueva llave del DOM...")
                    page.wait_for_selector("#apikey", state="visible", timeout=15000)
                    new_key = page.locator("#apikey").get_attribute("value")

                    if not new_key:
                        raise Exception(
                            "No se pudo extraer el atributo 'value' del input #apikey."
                        )

                    if new_key and new_key.startswith("RGAPI-"):
                        try:
                            set_key(ENV_PATH, "RIOT_API_KEY", new_key)
                            print(
                                "[RENOVADOR] ✅ Nueva RIOT_API_KEY generada y guardada en .env."
                            )
                        except Exception as write_err:
                            print(
                                f"\n[RENOVADOR] ❌ ERROR INESPERADO AL GUARDAR EL .env: {write_err}"
                            )
                            raise write_err

                        context.close()
                        return new_key, 1440
                    else:
                        raise Exception(
                            f"No se pudo extraer la clave generada. Extracción devolvió: {new_key}"
                        )

                except Exception as ex:
                    if api_event:
                        api_event.set()
                    raise ex

            else:
                print(
                    f"[RENOVADOR] ☕ Sobran 30 minutos o más. Calentando perfil para generar confianza..."
                )
                calentar_perfil(page)

                minutos_objetivo = random.randint(22, 28)
                espera_real = max(1, minutes_left - minutos_objetivo)

                tiempo_falso_espera = espera_real + 32

                print(
                    f"[RENOVADOR] ℹ️ Navegador cerrado. Siguiente revisión programada en aprox {espera_real} min reales."
                )
                context.close()
                return None, tiempo_falso_espera

    except Exception as e:
        print(f"[RENOVADOR] ❌ Error general: {str(e)}")
        raise e
