import os
import ctypes
import ctypes.util
import sys
import random
import json
import time
import re
import yt_dlp
import whisper
import imageio_ffmpeg
from enum import Enum
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from youtube_transcript_api import YouTubeTranscriptApi
from dotenv import load_dotenv

# Cargar variables de entorno (API KEYS)
load_dotenv()

# --- PARCHE PARA WHISPER EN WINDOWS + PYTHON NUEVO ---
_original_find_library = ctypes.util.find_library


def _patched_find_library(name):
    if name == "c" and os.name == "nt":
        return "msvcrt"
    return _original_find_library(name)


ctypes.util.find_library = _patched_find_library
# -----------------------------------------------------

# --- COMPRESIÓN LOCAL ---
import nltk
from sumy.parsers.plaintext import PlaintextParser
from sumy.nlp.tokenizers import Tokenizer
from sumy.summarizers.lsa import LsaSummarizer

# Descargar dependencias de NLP silenciosamente
for pkg in ["punkt", "punkt_tab"]:
    try:
        nltk.data.find(f"tokenizers/{pkg}")
    except LookupError:
        nltk.download(pkg, quiet=True)

# --- CONFIGURACIÓN DE API KEYS ---
API_KEYS = [
    os.getenv("GEMINI_KEY_1"),
    os.getenv("GEMINI_KEY_2"),
    os.getenv("GEMINI_KEY_3"),
]
API_KEYS = [key for key in API_KEYS if key]
ULTIMO_ERROR_GUIA = ""

ARCHIVO_HISTORIAL = "historial_coachings.json"

CAMPEONES_LOL = [
    "aatrox",
    "ahri",
    "akali",
    "akshan",
    "alistar",
    "ambessa",
    "amumu",
    "anivia",
    "annie",
    "aphelios",
    "ashe",
    "aurelion sol",
    "azir",
    "bard",
    "bel'veth",
    "blitzcrank",
    "brand",
    "braum",
    "briar",
    "caitlyn",
    "camille",
    "cassiopeia",
    "cho'gath",
    "corki",
    "darius",
    "diana",
    "dr. mundo",
    "mundo",
    "draven",
    "ekko",
    "elise",
    "evelynn",
    "ezreal",
    "fiddlesticks",
    "fiora",
    "fizz",
    "galio",
    "gangplank",
    "garen",
    "gnar",
    "gragas",
    "graves",
    "gwen",
    "hecarim",
    "heimerdinger",
    "hwei",
    "illaoi",
    "irelia",
    "ivern",
    "janna",
    "jarvan iv",
    "jarvan",
    "jax",
    "jayce",
    "jhin",
    "jinx",
    "k'sante",
    "kai'sa",
    "kalista",
    "karma",
    "karthus",
    "kassadin",
    "katarina",
    "kayle",
    "kayn",
    "kennen",
    "kha'zix",
    "kindred",
    "kled",
    "kog'maw",
    "leblanc",
    "lee sin",
    "leona",
    "lillia",
    "lissandra",
    "lucian",
    "lulu",
    "lux",
    "malphite",
    "malzahar",
    "maokai",
    "master yi",
    "yi",
    "mel",
    "milio",
    "miss fortune",
    "mordekaiser",
    "morgana",
    "naafiri",
    "nami",
    "nasus",
    "nautilus",
    "neeko",
    "nidalee",
    "nilah",
    "nocturne",
    "nunu",
    "olaf",
    "orianna",
    "ornn",
    "pantheon",
    "poppy",
    "pyke",
    "qiyana",
    "quinn",
    "rakan",
    "rammus",
    "rek'sai",
    "rell",
    "renata glasc",
    "renekton",
    "rengar",
    "riven",
    "rumble",
    "ryze",
    "samira",
    "sejuani",
    "senna",
    "seraphine",
    "sett",
    "shaco",
    "shen",
    "shyvana",
    "singed",
    "sion",
    "sivir",
    "skarner",
    "smolder",
    "sona",
    "soraka",
    "swain",
    "sylas",
    "syndra",
    "tahm kench",
    "taliyah",
    "talon",
    "taric",
    "teemo",
    "thresh",
    "tristana",
    "trundle",
    "tryndamere",
    "twisted fate",
    "twitch",
    "udyr",
    "urgot",
    "varus",
    "vayne",
    "veigar",
    "vel'koz",
    "vex",
    "vi",
    "viego",
    "viktor",
    "vladimir",
    "volibear",
    "warwick",
    "wukong",
    "xayah",
    "xerath",
    "xin zhao",
    "yasuo",
    "yone",
    "yorick",
    "yuumi",
    "zac",
    "zed",
    "zeri",
    "ziggs",
    "zilean",
    "zoe",
    "zyra",
]

CHAMPS_SORTED = sorted(CAMPEONES_LOL, key=len, reverse=True)
CHAMPS_REGEX = re.compile(
    r"\b(" + "|".join(re.escape(c) for c in CHAMPS_SORTED) + r")\b", re.IGNORECASE
)


# --- ESQUEMA ESTRUCTURADO ---
class RolLoL(str, Enum):
    TOP = "Top"
    JUNGLE = "Jungle"
    MID = "Mid"
    ADC = "ADC"
    SUPPORT = "Support"
    GENERAL = "General"


class RespuestaCoaching(BaseModel):
    rol: RolLoL = Field(
        description="Rol principal del coaching. Top, Jungle, Mid, ADC, Support o General."
    )
    campeon_principal: str = Field(
        description="Nombre EXACTO del campeón jugado por el alumno (Ej: 'Khazix'). Sin descripciones extras."
    )
    campeon_enemigo: str = Field(
        description="Nombre EXACTO del campeón rival directo (Ej: 'Lee Sin'). 'Desconocido' si no aplica."
    )
    dificultad_matchup: int = Field(
        description="Puntuación del 1 al 10 sobre la dificultad del matchup para el alumno.",
        default=5,
    )
    guia: str = Field(
        description="Análisis del matchup estructurado obligatoriamente en EARLY GAME, MID GAME y LATE GAME."
    )


# --- VARIABLES GLOBALES Y CACHÉ ---
LOCAL_FFMPEG = os.path.join(os.getcwd(), "ffmpeg.exe")
FFMPEG_PATH = (
    LOCAL_FFMPEG if os.path.exists(LOCAL_FFMPEG) else imageio_ffmpeg.get_ffmpeg_exe()
)

HISTORIAL_CACHE = set()
WHISPER_MODEL = None


def inicializar_historial():
    global HISTORIAL_CACHE
    if os.path.exists(ARCHIVO_HISTORIAL):
        try:
            with open(ARCHIVO_HISTORIAL, "r", encoding="utf-8") as f:
                HISTORIAL_CACHE = set(json.load(f))
        except:
            pass


def marcar_procesado(vid):
    HISTORIAL_CACHE.add(vid)
    with open(ARCHIVO_HISTORIAL, "w", encoding="utf-8") as f:
        json.dump(list(HISTORIAL_CACHE), f, indent=2)


def obtener_modelo_whisper():
    global WHISPER_MODEL
    if WHISPER_MODEL is None:
        print("  🧠 Cargando modelo de Inteligencia Artificial de audio (Whisper)...")
        WHISPER_MODEL = whisper.load_model("base")
    return WHISPER_MODEL


def obtener_url_video(video_id):
    return f"https://www.youtube.com/watch?v={video_id}"


def limpiar_nombre_archivo(nombre):
    if not nombre:
        return "Desconocido"
    return re.sub(r'[\\/*?:"<>|]', "_", str(nombre)).strip().title() or "Desconocido"


# =====================================================================
# NUEVAS FUNCIONES PARA GENERACIÓN DE GUÍAS DIRECTAS Y LECTURA DE STATS
# =====================================================================

import os
import json


def obtener_top_matchups_sin_guia(limite=10):
    """
    Lee matchups_stats.json y devuelve los matchups SIN GUÍA agrupados por rol.
    Cada rol tendrá como máximo 'limite' matchups, ordenados por cantidad de partidas.
    Retorna un diccionario: {"Top": [...], "Jungle": [...], ...}
    """
    ruta_json = "matchups_stats.json"
    if not os.path.exists(ruta_json):
        return {}

    try:
        with open(ruta_json, "r", encoding="utf-8") as f:
            data = json.load(f)

        stats = data.get("stats", {})
        matchups_agrupados = {
            "Top": [],
            "Jungle": [],
            "Mid": [],
            "Adc": [],
            "Support": [],
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

                # El campeón que va primero alfabéticamente SIEMPRE será main,
                # para que siempre se sumen correctamente sin importar cómo vengan en el JSON.
                if c_main_title > c_enemy_title:
                    c_main_title, c_enemy_title = c_enemy_title, c_main_title
                    # Si le dimos la vuelta, invertimos victorias/derrotas para que
                    # w y l sigan siendo del 'main' (aunque aquí sumamos totales, así que da un poco igual)
                    wins = resultados.get("losses", 0)
                    losses = resultados.get("wins", 0)
                else:
                    wins = resultados.get("wins", 0)
                    losses = resultados.get("losses", 0)

                clave_unica = (rol_cap, c_main_title, c_enemy_title)

                # Comprobamos guía en ambos sentidos
                ruta_guia_1 = os.path.join(
                    "coachings",
                    rol_cap,
                    c_main_title,
                    f"{c_main_title}_vs_{c_enemy_title}_IA.txt",
                )
                ruta_guia_yt_1 = os.path.join(
                    "coachings",
                    rol_cap,
                    c_main_title,
                    f"{c_main_title}_vs_{c_enemy_title}.txt",
                )
                ruta_guia_2 = os.path.join(
                    "coachings",
                    rol_cap,
                    c_enemy_title,
                    f"{c_enemy_title}_vs_{c_main_title}_IA.txt",
                )
                ruta_guia_yt_2 = os.path.join(
                    "coachings",
                    rol_cap,
                    c_enemy_title,
                    f"{c_enemy_title}_vs_{c_main_title}.txt",
                )

                tiene_guia = (
                    os.path.exists(ruta_guia_1)
                    or os.path.exists(ruta_guia_yt_1)
                    or os.path.exists(ruta_guia_2)
                    or os.path.exists(ruta_guia_yt_2)
                )

                if not tiene_guia:
                    if clave_unica in matchups_dict:

                        matchups_dict[clave_unica]["total"] += wins + losses
                        matchups_dict[clave_unica]["wins"] += wins
                        matchups_dict[clave_unica]["losses"] += losses
                    else:
                        matchups_dict[clave_unica] = {
                            "rol": rol_cap,
                            "champ_main": c_main_title,
                            "champ_enemy": c_enemy_title,
                            "total": wins + losses,
                            "wins": wins,
                            "losses": losses,
                            "tiene_guia": False,
                        }

        # Convertimos el diccionario a la estructura final
        for item in matchups_dict.values():
            matchups_agrupados[item["rol"]].append(item)

        for rol in matchups_agrupados:
            matchups_agrupados[rol].sort(key=lambda x: x["total"], reverse=True)
            matchups_agrupados[rol] = matchups_agrupados[rol][:limite]

        return matchups_agrupados

    except Exception as e:
        print(f"Error leyendo stats: {e}")
        return {}


def obtener_top_matchups(limite=10):

    ruta_json = "matchups_stats.json"
    if not os.path.exists(ruta_json):
        return {}

    try:
        with open(ruta_json, "r", encoding="utf-8") as f:
            data = json.load(f)

        stats = data.get("stats", {})
        matchups_agrupados = {
            "Top": [],
            "Jungle": [],
            "Mid": [],
            "Adc": [],
            "Support": [],
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

                # Misma lógica: agrupamos alfabéticamente para sumar todos los encuentros entre ambos
                if c_main_title > c_enemy_title:
                    c_main_title, c_enemy_title = c_enemy_title, c_main_title
                    wins = resultados.get("losses", 0)
                    losses = resultados.get("wins", 0)
                else:
                    wins = resultados.get("wins", 0)
                    losses = resultados.get("losses", 0)

                clave_unica = (rol_cap, c_main_title, c_enemy_title)

                # Aquí sí comprobamos y pasamos el valor real de 'tiene_guia' al frontend
                ruta_guia_1 = os.path.join(
                    "coachings",
                    rol_cap,
                    c_main_title,
                    f"{c_main_title}_vs_{c_enemy_title}_IA.txt",
                )
                ruta_guia_yt_1 = os.path.join(
                    "coachings",
                    rol_cap,
                    c_main_title,
                    f"{c_main_title}_vs_{c_enemy_title}.txt",
                )
                ruta_guia_2 = os.path.join(
                    "coachings",
                    rol_cap,
                    c_enemy_title,
                    f"{c_enemy_title}_vs_{c_main_title}_IA.txt",
                )
                ruta_guia_yt_2 = os.path.join(
                    "coachings",
                    rol_cap,
                    c_enemy_title,
                    f"{c_enemy_title}_vs_{c_main_title}.txt",
                )

                tiene_guia = (
                    os.path.exists(ruta_guia_1)
                    or os.path.exists(ruta_guia_yt_1)
                    or os.path.exists(ruta_guia_2)
                    or os.path.exists(ruta_guia_yt_2)
                )

                if clave_unica in matchups_dict:
                    # Sumamos
                    matchups_dict[clave_unica]["total"] += wins + losses
                    matchups_dict[clave_unica]["wins"] += wins
                    matchups_dict[clave_unica]["losses"] += losses
                    # Si al menos uno tiene guía, el matchup general tiene guía
                    matchups_dict[clave_unica]["tiene_guia"] = (
                        matchups_dict[clave_unica]["tiene_guia"] or tiene_guia
                    )
                else:
                    matchups_dict[clave_unica] = {
                        "rol": rol_cap,
                        "champ_main": c_main_title,
                        "champ_enemy": c_enemy_title,
                        "total": wins + losses,
                        "wins": wins,
                        "losses": losses,
                        "tiene_guia": tiene_guia,
                    }

        for item in matchups_dict.values():
            matchups_agrupados[item["rol"]].append(item)

        for rol in matchups_agrupados:
            matchups_agrupados[rol].sort(key=lambda x: x["total"], reverse=True)
            matchups_agrupados[rol] = matchups_agrupados[rol][:limite]

        return matchups_agrupados

    except Exception as e:
        print(f"Error leyendo stats: {e}")
        return {}


def generar_guia_ia_directa(rol, champ_main, champ_enemy):
    """Usa un prompt directo de Gemini para crear una guía de texto sobre un matchup."""
    global ULTIMO_ERROR_GUIA
    ULTIMO_ERROR_GUIA = ""
    if not API_KEYS:
        ULTIMO_ERROR_GUIA = "missing_keys"
        print(json.dumps({"success": False, "error": "No hay API Keys configuradas."}))
        return False

    prompt = f"""Eres un Coach profesional y analista de League of Legends (nivel Challenger). 
Genera una guía MUY CLARA Y DIRECTA para el siguiente matchup. La guía debe ser fácil de entender para un jugador Oro, pero con detalles lo suficientemente profundos para ser útil en Challenger.

Matchup: {champ_main} (tu campeón) contra {champ_enemy} (campeón rival).
Rol: {rol}

El campo "guia" DEBE estar en ESPAÑOL, en texto claro, y con la siguiente estructura estricta:

DIFICULTAD: [Tu nota del 1 al 10 justificada en 1 línea]

EARLY GAME:
- [Puntos clave: trades nivel 1-3, control de oleada, gankeos, prioridades]

MID GAME:
- [Puntos clave: power spikes, control de visión, rotaciones, peleas por objetivos]

LATE GAME:
- [Puntos clave: rol en teamfights, win condition, posicionamiento]
"""

    for modelo in ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]:
        for key_idx, api_key in enumerate(API_KEYS):
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

                # Crear la ruta de guardado
                ruta_carpeta = os.path.join(
                    "coachings", rol.capitalize(), champ_main.title()
                )
                os.makedirs(ruta_carpeta, exist_ok=True)
                ruta_completa = os.path.join(
                    ruta_carpeta,
                    f"{champ_main.title()}_vs_{champ_enemy.title()}_IA.txt",
                )

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

                print(json.dumps({"success": True, "ruta": ruta_completa}))
                return True

            except Exception as e:
                error_text = str(e)
                if (
                    "429" in error_text
                    or "RESOURCE_EXHAUSTED" in error_text
                    or "quota" in error_text.lower()
                ):
                    ULTIMO_ERROR_GUIA = "quota"
                elif not ULTIMO_ERROR_GUIA:
                    ULTIMO_ERROR_GUIA = "error"
                continue

    print(json.dumps({"success": False, "error": "Fallo al generar guía con IA."}))
    return False


# =====================================================================


def obtener_subtitulos_yt(video_id):
    for api_method in ["fetch", "get_transcript"]:
        try:
            api = (
                YouTubeTranscriptApi()
                if api_method == "fetch"
                else YouTubeTranscriptApi
            )
            func = getattr(api, api_method)
            t_data = func(video_id, languages=["es", "en"])
            texto_crudo = " ".join([f["text"] for f in t_data])
            return re.sub(r"\[.*?\]", "", texto_crudo).strip()
        except Exception:
            continue
    return None


def transcribir_local(video_id):
    url = obtener_url_video(video_id)
    nombre_base = f"audio_{video_id}"

    try:
        print("  📥 Extrayendo y convirtiendo audio usando yt-dlp y FFmpeg...")
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": f"{nombre_base}.%(ext)s",
            "quiet": True,
            "noplaylist": True,
            "ffmpeg_location": FFMPEG_PATH,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "wav",
                    "preferredquality": "192",
                }
            ],
            "extractor_args": {"youtube": ["player_client=ios,android"]},
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=True)

        archivo_wav = f"{nombre_base}.wav"
        if not os.path.exists(archivo_wav):
            return None

        print("  🤖 Transcribiendo audio con Whisper...")
        modelo = obtener_modelo_whisper()
        res = modelo.transcribe(archivo_wav, language="es", fp16=False)

        try:
            os.remove(archivo_wav)
        except:
            pass

        return res.get("text", "")
    except Exception as e:
        print(f"  ❌ Error en transcripción local: {e}")
        return None


def comprimir_texto_localmente(texto, num_oraciones=150):
    print(f"  🧹 Comprimiendo transcripción a ~{num_oraciones} oraciones...")

    muletillas = (
        r"\b(eh|mmm|bueno|o sea|es decir|en plan|sabes|literalmente|tipo|vale|ok)\b"
    )
    texto_limpio = re.sub(muletillas, "", texto, flags=re.IGNORECASE)
    texto_limpio = " ".join(texto_limpio.split())

    try:
        parser = PlaintextParser.from_string(texto_limpio, Tokenizer("spanish"))
        todas_las_oraciones = list(parser.document.sentences)

        oraciones_importantes_campeones = []
        oraciones_para_resumir = []

        for oracion in todas_las_oraciones:
            texto_oracion = str(oracion)
            if CHAMPS_REGEX.search(texto_oracion):
                oraciones_importantes_campeones.append(texto_oracion)
            else:
                oraciones_para_resumir.append(texto_oracion)

        texto_sin_campeones = " ".join(oraciones_para_resumir)
        parser_secundario = PlaintextParser.from_string(
            texto_sin_campeones, Tokenizer("spanish")
        )

        oraciones_restantes = max(
            10, num_oraciones - len(oraciones_importantes_campeones)
        )
        resumen_matematico = LsaSummarizer()(
            parser_secundario.document, oraciones_restantes
        )

        oraciones_finales = [str(o) for o in resumen_matematico]

        texto_final = " ".join(oraciones_importantes_campeones + oraciones_finales)
        print(
            f"  📉 Text reducido. Frases clave de campeones aseguradas: {len(oraciones_importantes_campeones)}"
        )
        return texto_final
    except Exception as e:
        print(f"  ⚠️ LSA falló ({e}). Usando recorte estándar.")
        return texto_limpio[:15000]


def analizar_con_ia(titulo, texto):
    if not API_KEYS:
        print("  ❌ ERROR: No se encontraron API Keys en el archivo .env")
        return None

    MODELOS_A_PROBAR = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
    niveles_compresion = [200, 120, 70, 40]

    for i, num_oraciones in enumerate(niveles_compresion):
        texto_resumido = comprimir_texto_localmente(texto, num_oraciones)

        prompt = f"""Eres un Coach profesional y analista de League of Legends. 
Analiza la siguiente transcripción resumida de un vídeo de coaching titulado: "{titulo}". 

Extrae información valiosa EXCLUSIVAMENTE SOBRE EL MATCHUP entre el campeón principal (el alumno/streamer) y el campeón enemigo.
Ignora chistes, donaciones o charlas irrelevantes. Presta máxima atención a los nombres de los campeones mencionados.

El campo "guia" DEBE estar en ESPAÑOL, en texto claro, y con la siguiente estructura estricta:

DIFICULTAD: [Tu nota del 1 al 10 justificada en 1 línea]

EARLY GAME:
- [Puntos clave: trades nivel 1-3, oleadas, gankeos tempranos]

MID GAME:
- [Puntos clave: power spikes, rotaciones, peleas por dragón/heraldo]

LATE GAME:
- [Puntos clave: rol en teamfights, win condition, posicionamiento]

Transcripción:
{texto_resumido}"""

        for modelo in MODELOS_A_PROBAR:
            for key_idx, api_key in enumerate(API_KEYS):
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
                    datos_dict = json.loads(res.text)
                    print(
                        f"    ✅ Matchup analizado con {modelo} (API Key {key_idx + 1})"
                    )
                    return datos_dict

                except Exception as e:
                    error_str = str(e)
                    if "404" in error_str:
                        break
                    elif "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                        print(
                            f"    ⚠️ Cuota excedida en API KEY {key_idx + 1}. Intentando otra..."
                        )
                        continue
                    else:
                        continue

        if i < len(niveles_compresion) - 1:
            print(
                f"  ⏳ Texto muy largo. Reduciendo nivel de detalle y reintentando..."
            )
            time.sleep(5)

    print(f"  ❌ Fallaron todos los intentos de IA.")
    return None


def procesar_video(video_id, titulo):
    if video_id in HISTORIAL_CACHE:
        print(f"  ⏩ SALTADO (Ya existe en historial): {titulo}")
        return True

    print(f"\n⏳ Procesando: {titulo}")

    texto = obtener_subtitulos_yt(video_id)
    if texto:
        print("  📝 Subtítulos obtenidos de YouTube.")
    else:
        print("  ⚠️ No hay subtítulos. Usando Whisper Local (Audio a Texto)...")
        texto = transcribir_local(video_id)

    if not texto:
        print("  ❌ Imposible obtener texto del vídeo.")
        return False

    datos = analizar_con_ia(titulo, texto)
    if not datos:
        return False

    rol = limpiar_nombre_archivo(datos.get("rol", "General"))
    champ_main = limpiar_nombre_archivo(datos.get("campeon_principal", "Unknown"))
    champ_enemy = limpiar_nombre_archivo(datos.get("campeon_enemigo", "General"))
    dificultad = datos.get("dificultad_matchup", 5)
    guia = datos.get("guia", "Sin guía disponible.")

    ruta_carpeta = os.path.join("coachings", rol, champ_main)
    os.makedirs(ruta_carpeta, exist_ok=True)

    ruta_completa = os.path.join(ruta_carpeta, f"{champ_main}_vs_{champ_enemy}.txt")

    contenido_archivo = f"""VÍDEO: {titulo}
URL: {obtener_url_video(video_id)}
MATCHUP: {champ_main} vs {champ_enemy}
DIFICULTAD APROX: {dificultad}/10
{'='*50}

{guia}"""

    with open(ruta_completa, "w", encoding="utf-8") as f:
        f.write(contenido_archivo)

    marcar_procesado(video_id)
    print(f"  ✅ Guardado exitosamente en: {ruta_completa}")
    return True


def principal(url_externa=None):
    inicializar_historial()

    # -------------------------------------------------------------
    # NUEVOS PARÁMETROS DE CLI PARA COMUNICACIÓN CON EL FRONTEND
    # -------------------------------------------------------------
    if len(sys.argv) > 1:
        comando = sys.argv[1]
        if comando == "--get-top":
            limite = int(sys.argv[2]) if len(sys.argv) > 2 else 15
            tops = obtener_top_matchups(limite)
            print(
                json.dumps(tops)
            )  # Imprime el JSON puro para que Node/Flask lo recoja
            return

        elif comando == "--gen-guide":
            if len(sys.argv) >= 5:
                rol = sys.argv[2]
                main = sys.argv[3]
                enemy = sys.argv[4]
                generar_guia_ia_directa(rol, main, enemy)
            else:
                print(
                    json.dumps(
                        {
                            "success": False,
                            "error": "Faltan argumentos: --gen-guide <rol> <main> <enemy>",
                        }
                    )
                )
            return

        else:
            url = comando
    # -------------------------------------------------------------
    elif url_externa:
        url = url_externa
    else:
        url = input("URL del vídeo o playlist: ").strip()

    if not url:
        return

    ydl_opts_playlist = {
        "quiet": True,
        "extract_flat": True,
        "ignoreerrors": True,
        "extractor_args": {"youtube": ["player_client=ios,android"]},
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts_playlist) as ydl:
            print("\n🔍 Analizando enlace...")
            info = ydl.extract_info(url, download=False)
            if not info:
                print("❌ yt-dlp no pudo obtener la información.")
                return

            entradas = (
                info.get("entries", [info])
                if "entries" in info and info["entries"]
                else [info]
            )

            for v in entradas:
                if not v:
                    continue
                vid = v.get("id")
                title = v.get("title", "Sin título")
                if vid and len(vid) == 11:
                    exito = procesar_video(vid, title)
                    if not exito:
                        print(
                            f"  ⚠️ Problema al procesar '{title}'. Pasando al siguiente..."
                        )

    except Exception as e:
        print(f"\n❌ Ocurrió un error extrayendo el enlace: {e}")


if __name__ == "__main__":
    principal()
