"""YouTube con yt-dlp (y planes B sin él): enlaces, búsquedas, listas, canales, metadatos y descargas.

Cada operación tiene un plan B por si YouTube cambia algo y yt-dlp falla:

* búsqueda: yt-dlp → la página de resultados de YouTube;
* canal o lista: yt-dlp → el RSS de YouTube (solo los ~15 últimos vídeos);
* metadatos: yt-dlp → oEmbed (título y canal).
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlparse

import requests
import yt_dlp

from app.logbus import get_logger
from app.videos import deps
from app.videos.links import VIDEO_ID, classify, watch_url  # noqa: F401 - ``classify`` también se usa como youtube.classify

log = get_logger("videos")

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept-Language": "es-ES,es;q=0.9,en;q=0.8"}
# Sin esto, desde la UE YouTube manda a la página de consentimiento de cookies en vez de al canal o a la búsqueda.
CONSENT = {"SOCS": "CAI", "CONSENT": "YES+1"}
TIMEOUT_S = 30
COOKIES_FILE = "cookies.txt"  # opcional, en data/videos: para vídeos con restricción de edad o si YouTube pide iniciar sesión

# Mensajes de yt-dlp que significan «este vídeo no se va a poder bajar nunca» (no se reintenta).
FINAL_ERRORS = (
    "private video", "video unavailable", "has been removed", "members-only", "join this channel",
    "account associated with this video has been terminated", "copyright", "not available in your country",
    "this video is not available", "premieres in", "live event will begin", "is not a valid url",
)
# Y los que significan «YouTube nos está frenando»: se para un rato.
LIMIT_ERRORS = ("http error 429", "too many requests", "confirm you're not a bot", "confirm you’re not a bot", "rate-limit")


class VideoError(RuntimeError):
    """Error de YouTube con su tipo: ``final`` (no se reintenta), ``limite`` (esperar) u ``otro``."""

    def __init__(self, message: str, kind: str = "otro") -> None:
        super().__init__(message)
        self.kind = kind


def classify_error(message: str) -> str:
    text = (message or "").lower()
    if any(p in text for p in LIMIT_ERRORS):
        return "limite"
    if any(p in text for p in FINAL_ERRORS):
        return "final"
    return "otro"


# ---------------------------------------------------------------------- yt-dlp
class _Logger:
    """Lo que dice yt-dlp va al registro de la app (los avisos en modo depuración: son muchos y casi nunca importan)."""

    def debug(self, msg: str) -> None:
        if not msg.startswith("[debug] "):
            log.debug("yt-dlp: %s", msg)

    def info(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def warning(self, msg: str) -> None:
        log.debug("yt-dlp aviso: %s", msg)

    def error(self, msg: str) -> None:
        log.debug("yt-dlp error: %s", msg)


def options(data_dir: Path | None = None, **extra) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": TIMEOUT_S,
        "retries": 3,
        "extractor_retries": 2,
        "fragment_retries": 5,
        "logger": _Logger(),
    }
    runtimes = deps.js_runtimes()
    if runtimes:
        opts["js_runtimes"] = runtimes
    if data_dir is not None and (Path(data_dir) / COOKIES_FILE).exists():
        opts["cookiefile"] = str(Path(data_dir) / COOKIES_FILE)
    opts.update(extra)
    return opts


def _extract(url: str, data_dir: Path | None, **extra) -> dict:
    try:
        with yt_dlp.YoutubeDL(options(data_dir, **extra)) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        message = str(exc).replace("ERROR: ", "")
        raise VideoError(message, classify_error(message)) from exc
    if not isinstance(info, dict):
        raise VideoError(f"yt-dlp no devolvió datos para {url}")
    return info


def _entry(raw: dict) -> dict | None:
    vid = raw.get("id")
    if not vid or not VIDEO_ID.match(str(vid)):
        return None
    return {
        "id": vid,
        "titulo": raw.get("title") or "",
        "canal": raw.get("channel") or raw.get("uploader") or "",
        "canal_id": raw.get("channel_id"),
        "duracion": raw.get("duration"),
        "vistas": raw.get("view_count"),
    }


def search(query: str, n: int = 10, data_dir: Path | None = None) -> list[dict]:
    """Los ``n`` primeros resultados de una búsqueda (solo vídeos)."""
    try:
        info = _extract(f"ytsearch{max(1, n)}:{query}", data_dir, extract_flat="in_playlist")
        found = [e for e in (_entry(x) for x in info.get("entries") or [] if isinstance(x, dict)) if e]
        if found:
            return found
    except VideoError as exc:
        if exc.kind == "limite":
            raise
        log.info("Búsqueda con yt-dlp fallida (%s); pruebo con la página de resultados.", exc)
    return scrape_search(query, n)


def expand(url: str, limit: int | None = None, data_dir: Path | None = None) -> list[dict]:
    """Los vídeos de una lista o de un canal (los más recientes primero en un canal)."""
    extra = {"extract_flat": "in_playlist"}
    if limit:
        extra["playlistend"] = int(limit)
    try:
        info = _extract(url, data_dir, **extra)
        found = _flatten(info.get("entries") or [])
        if found:
            return found[:limit] if limit else found
    except VideoError as exc:
        if exc.kind == "limite":
            raise
        log.info("No pude leer %s con yt-dlp (%s); pruebo con el RSS de YouTube.", url, exc)
    found = rss(url)
    return found[:limit] if limit else found


def _flatten(entries) -> list[dict]:
    """Las entradas de un canal pueden venir agrupadas por pestañas (vídeos, directos…)."""
    out: list[dict] = []
    for raw in entries:
        if not isinstance(raw, dict):
            continue
        if raw.get("_type") == "playlist" or raw.get("entries"):
            out.extend(_flatten(raw.get("entries") or []))
            continue
        entry = _entry(raw)
        if entry:
            out.append(entry)
    return out


def video_info(video_id: str, data_dir: Path | None = None) -> dict:
    """Metadatos completos (formatos, subtítulos, capítulos…). Lanza :class:`VideoError`."""
    return _extract(watch_url(video_id), data_dir)


def fetch(url: str, data_dir: Path | None = None) -> bytes:
    """Descarga un recurso de YouTube (subtítulos) con la misma conexión y cookies que yt-dlp."""
    with yt_dlp.YoutubeDL(options(data_dir)) as ydl:
        return ydl.urlopen(url).read()


def download(video_id: str, folder: Path, fmt: str, data_dir: Path | None = None, **extra) -> Path:
    """Descarga un formato a ``folder`` y devuelve el fichero (yt-dlp elige según ``fmt``)."""
    folder.mkdir(parents=True, exist_ok=True)
    opts = options(data_dir, format=fmt, outtmpl=str(folder / "%(id)s.%(format_id)s.%(ext)s"), continuedl=False, **extra)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([watch_url(video_id)])
    except yt_dlp.utils.DownloadError as exc:
        message = str(exc).replace("ERROR: ", "")
        raise VideoError(message, classify_error(message)) from exc
    files = [p for p in folder.glob(f"{video_id}.*") if p.suffix not in (".part", ".ytdl", ".tmp") and p.stat().st_size > 0]
    if not files:
        raise VideoError("La descarga terminó sin ningún fichero")
    return max(files, key=lambda p: p.stat().st_size)


# Formatos para los frames: hasta 1080p (el minimapa se lee bien), sin AV1 (OpenCV no siempre lo decodifica).
VIDEO_FORMATS = (
    "bv*[height<=1080][height>=1080][vcodec!^=av01]",
    "bv*[height<=1080][height>=720][vcodec!^=av01]",
    "bv*[height<=1080][vcodec!^=av01]",
    "b[height<=1080]",
)
# Copia pequeña para la sonda (¿hay minimapa?): ~30-60 MB por cada 20 min.
PROBE_FORMATS = ("bv*[height<=360][vcodec!^=av01]", "bv*[height<=480][vcodec!^=av01]", "b[height<=480]", "wv*")
VIDEO_SORT = ["res:1080", "+size", "fps:30"]  # a igual resolución, el fichero más pequeño (y 30 fps antes que 60)
AUDIO_FORMAT = "ba[abr<=96]/wa/ba/b"


# ---------------------------------------------------------------------- planes B
def scrape_search(query: str, n: int) -> list[dict]:
    """Resultados leyendo la página de búsqueda de YouTube (si yt-dlp falla)."""
    try:
        html = requests.get(
            f"https://www.youtube.com/results?search_query={quote_plus(query)}", headers=HEADERS, cookies=CONSENT, timeout=TIMEOUT_S
        ).text
    except requests.RequestException as exc:
        raise VideoError(f"No se pudo buscar en YouTube: {exc}") from exc
    found: list[dict] = []
    for block in re.findall(r'"videoRenderer":(\{.*?"videoId":"[A-Za-z0-9_-]{11}".*?\}),"trackingParams"', html)[: n * 3]:
        vid = re.search(r'"videoId":"([A-Za-z0-9_-]{11})"', block)
        title = re.search(r'"title":\{"runs":\[\{"text":"(.*?)"\}', block)
        channel = re.search(r'"ownerText":\{"runs":\[\{"text":"(.*?)"', block)
        if vid and vid.group(1) not in {f["id"] for f in found}:
            found.append({"id": vid.group(1), "titulo": _unescape(title.group(1)) if title else "",
                          "canal": _unescape(channel.group(1)) if channel else "", "duracion": None})
        if len(found) >= n:
            break
    return found


def _unescape(text: str) -> str:
    try:
        return json.loads(f'"{text}"')
    except ValueError:
        return text


def rss(url: str) -> list[dict]:
    """Los últimos vídeos de un canal o de una lista por el RSS de YouTube (plan B: solo ~15)."""
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if query.get("list"):
        feed = f"https://www.youtube.com/feeds/videos.xml?playlist_id={query['list'][0]}"
    else:
        channel_id = _channel_id(url)
        if not channel_id:
            raise VideoError(f"No se encontró el canal de {url}")
        feed = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    try:
        root = ET.fromstring(requests.get(feed, headers=HEADERS, cookies=CONSENT, timeout=TIMEOUT_S).content)
    except (requests.RequestException, ET.ParseError) as exc:
        raise VideoError(f"No se pudo leer el RSS de YouTube: {exc}") from exc
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
    out = []
    for entry in root.findall("a:entry", ns):
        vid = entry.findtext("yt:videoId", default="", namespaces=ns)
        if VIDEO_ID.match(vid):
            out.append({"id": vid, "titulo": entry.findtext("a:title", default="", namespaces=ns),
                        "canal": entry.findtext("a:author/a:name", default="", namespaces=ns), "duracion": None})
    return out


def _channel_id(url: str) -> str | None:
    match = re.search(r"/channel/(UC[\w-]{22})", url)
    if match:
        return match.group(1)
    try:
        html = requests.get(url.removesuffix("/videos"), headers=HEADERS, cookies=CONSENT, timeout=TIMEOUT_S).text
    except requests.RequestException:
        return None
    for pattern in (r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"', r'"externalId":"(UC[\w-]{22})"',
                    r'"channelId":"(UC[\w-]{22})"'):
        match = re.search(pattern, html)
        if match:
            return match.group(1)
    return None


def oembed(video_id: str) -> dict | None:
    """Título y canal sin yt-dlp (plan B de los metadatos)."""
    try:
        response = requests.get(
            f"https://www.youtube.com/oembed?url={quote_plus(watch_url(video_id))}&format=json", headers=HEADERS, cookies=CONSENT, timeout=TIMEOUT_S
        )
        if response.status_code != 200:
            return None
        data = response.json()
    except (requests.RequestException, ValueError):
        return None
    return {"id": video_id, "title": data.get("title"), "channel": data.get("author_name"), "uploader_url": data.get("author_url")}
