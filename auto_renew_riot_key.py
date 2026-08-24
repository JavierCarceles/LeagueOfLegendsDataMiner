import os
import re
import smtplib
from email.mime.text import MIMEText
from dotenv import load_dotenv, set_key
from playwright.sync_api import sync_playwright

ENV_PATH = ".env"
CHROME_PROFILE_PATH = os.path.join(os.path.expanduser("~"), "riot_bot_profile")


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


def send_error_email(email_to, error_msg):
    """Envía un correo de alerta mediante la contraseña de aplicación de Outlook."""
    email_pass = os.getenv("EMAIL_PASS")
    if not email_pass:
        print("⚠️ No se puede enviar el correo porque falta EMAIL_PASS en el .env")
        return

    msg = MIMEText(f"Error al intentar renovar la RIOT_API_KEY:\n\n{error_msg}")
    msg["Subject"] = "🚨 Error crítico: Renovación API Key Riot"
    msg["From"] = email_to
    msg["To"] = email_to

    try:
        with smtplib.SMTP("smtp.office365.com", 587) as server:
            server.starttls()
            server.login(email_to, email_pass)
            server.send_message(msg)
        print("📧 Correo de alerta enviado correctamente.")
    except Exception as e:
        print(f"Error al enviar el email: {e}")


def run(api_event=None):
    """Ejecuta la comprobación y renovación automática de la API Key.
    Devuelve una tupla: (nueva_llave, minutos_restantes)
    """
    load_dotenv(ENV_PATH)
    email_user = os.getenv("EMAIL_USER")

    try:
        with sync_playwright() as p:
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

            page = context.pages[0] if context.pages else context.new_page()
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
                print(
                    "⚠️ [BOT] No se detectó sesión. Inicia sesión en la ventana de Chrome..."
                )
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

            if minutes_left < 30:
                print(
                    "[RENOVADOR] ⚠️ Faltan menos de 30 minutos. Pausando peticiones y renovando..."
                )

                if api_event:
                    api_event.clear()

                try:
                    try:
                        captcha_frame = page.frame_locator(
                            'iframe[title*="reCAPTCHA"], iframe[src*="recaptcha"]'
                        )
                        captcha_checkbox = captcha_frame.locator(
                            ".recaptcha-checkbox-border, #recaptcha-anchor"
                        )
                        if captcha_checkbox.is_visible(timeout=3000):
                            captcha_checkbox.click()
                            page.wait_for_timeout(3000)
                    except Exception:
                        pass

                    page.locator(
                        "input[value='REGENERATE API KEY'], button:has-text('REGENERATE API KEY')"
                    ).click()
                    page.wait_for_timeout(2000)

                    key_input = page.locator("#development-api-key, input[readonly]")
                    new_key = None
                    if key_input.is_visible(timeout=3000):
                        new_key = key_input.input_value()

                    if not new_key or not new_key.startswith("RGAPI-"):
                        page.locator("button:has-text('Copy')").click()
                        new_key = page.evaluate("navigator.clipboard.readText()")

                    if new_key and new_key.startswith("RGAPI-"):
                        set_key(ENV_PATH, "RIOT_API_KEY", new_key)
                        print(
                            "[RENOVADOR] ✅ Nueva RIOT_API_KEY generada y guardada en .env."
                        )
                        context.close()
                        return new_key, 1440
                    else:
                        raise Exception("No se pudo extraer la clave generada.")
                except Exception as ex:
                    if api_event:
                        api_event.set()
                    raise ex

            context.close()
            return None, minutes_left

    except Exception as e:
        error_msg = str(e)
        print(f"[RENOVADOR] ❌ Error: {error_msg}")
        if email_user:
            send_error_email(email_user, error_msg)
        raise e
