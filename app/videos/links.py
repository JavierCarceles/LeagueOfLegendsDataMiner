"""Enlaces de YouTube: qué es lo que se ha pegado (vídeo, lista, canal o una búsqueda).

Sin dependencias (ni yt-dlp ni nada de la red): lo usa el botón «Añadir» de la web, que tiene que funcionar antes de
que se instalen las herramientas de vídeo (se instalan justo después, cuando el recolector empieza con lo añadido).
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
YT_HOST = re.compile(r"^(?:https?://)?(?:[\w-]+\.)*(?:youtube\.com|youtu\.be|youtube-nocookie\.com)(?:/|$)", re.IGNORECASE)
WATCH = "https://www.youtube.com/watch?v="


def classify(text: str) -> tuple[str, str]:
    """``(tipo, valor)``: ``video`` (id), ``lista`` / ``canal`` (URL normalizada) o ``busqueda`` (el texto)."""
    raw = (text or "").strip()
    if VIDEO_ID.match(raw):
        return "video", raw
    if not YT_HOST.match(raw):
        return "busqueda", raw
    url = raw if raw.lower().startswith("http") else "https://" + raw
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    parts = [p for p in parsed.path.split("/") if p]
    query = parse_qs(parsed.query)
    if host.endswith("youtu.be") and parts and VIDEO_ID.match(parts[0]):
        return "video", parts[0]
    return _classify_path(parts, query, raw)


def _classify_path(parts: list[str], query: dict, raw: str) -> tuple[str, str]:
    first = parts[0] if parts else ""
    if first == "watch" and VIDEO_ID.match((query.get("v") or [""])[0]):
        return "video", query["v"][0]
    if first in ("shorts", "live", "embed", "v") and len(parts) > 1 and VIDEO_ID.match(parts[1]):
        return "video", parts[1]
    if first == "playlist" and query.get("list"):
        return "lista", f"https://www.youtube.com/playlist?list={query['list'][0]}"
    if first.startswith("@"):
        return "canal", f"https://www.youtube.com/{first}/videos"
    if first in ("channel", "c", "user") and len(parts) > 1:
        return "canal", f"https://www.youtube.com/{first}/{parts[1]}/videos"
    if first == "results" and query.get("search_query"):
        return "busqueda", query["search_query"][0]
    return "busqueda", raw


def watch_url(video_id: str) -> str:
    return WATCH + video_id
