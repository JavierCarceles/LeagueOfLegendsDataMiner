"""Todo lo que se dice en un vídeo, con tiempos, por el primer método que funcione:

1. subtítulos de YouTube con yt-dlp (los escritos a mano antes que los automáticos; json3, si no srv3/vtt);
2. la librería ``youtube-transcript-api`` (otro camino para los mismos subtítulos);
3. voz a texto en este ordenador con faster-whisper (se baja solo el audio, que se borra al terminar).

Se guarda el idioma original (español, inglés o el que hable el coach); si YouTube solo da una traducción, se usa
la inglesa o la española.
"""

from __future__ import annotations

import html
import json
import os
import re
import threading
from collections.abc import Callable
from pathlib import Path

from app.logbus import get_logger
from app.videos import deps, youtube

log = get_logger("videos")

PREFERRED = ("es", "en")
ORIG = "-orig"  # subtítulos automáticos de lo que se dice en esa pista de audio (no una traducción)
FORMATS = ("json3", "srv3", "vtt")
CHUNK_S = 12.0  # trozos de ~12 s: suficiente para alinear lo dicho con la partida sin repetir tiempos
CHUNK_CHARS = 240
WHISPER_MODEL = os.environ.get("VIDEOS_WHISPER_MODEL", "small")
_TAG = re.compile(r"<[^>]+>")
_TIMING = re.compile(r"(\d+):(\d{2}):(\d{2})[.,](\d{3})\s+-->")
_SHORT_TIMING = re.compile(r"^(\d{1,2}):(\d{2})[.,](\d{3})\s+-->")

_model = None
_model_lock = threading.Lock()


def _base(lang: str | None) -> str:
    return (lang or "").split("-")[0].lower()


def _audio_rank(f: dict) -> int:
    """Cuánto parece la pista de audio original: 2 si se marca «original», +1 si tiene la preferencia del original (10)."""
    if f.get("acodec") in (None, "none") or not f.get("language"):
        return 0
    rank = 2 if "original" in str(f.get("format_note") or "").lower() else 0
    return rank + (1 if (f.get("language_preference") or -1) >= 10 else 0)


def original_language(info: dict) -> str:
    """Idioma en el que habla el coach. Con doblajes automáticos de YouTube, ``language`` puede ser el de un doblaje
    (un vídeo en español salía «ar»): manda la pista de audio marcada como original."""
    ranked = [(_audio_rank(f), _base(f.get("language"))) for f in info.get("formats") or []]
    best = max((r for r in ranked if r[0] > 0), default=None, key=lambda r: r[0])
    return best[1] if best else _base(info.get("language"))


def language_order(info: dict) -> list[str]:
    """Idioma original del vídeo primero, después español e inglés."""
    order: list[str] = []
    for lang in (original_language(info), *PREFERRED):
        if lang and lang not in order:
            order.append(lang)
    return order


def candidate_tracks(info: dict) -> list[tuple[str, str, list[dict]]]:
    """``(tipo, idioma, formatos)`` por orden de preferencia: manuales, automáticos originales, traducciones."""
    langs = language_order(info)
    return _manual_tracks(info.get("subtitles") or {}, langs) + _auto_tracks(info.get("automatic_captions") or {}, langs, original_language(info))


def _manual_tracks(manual: dict, langs: list[str]) -> list[tuple[str, str, list[dict]]]:
    return [("manual", key, manual[key]) for lang in langs for key in manual if _base(key) == lang and key != "live_chat"]


def _auto_tracks(auto: dict, langs: list[str], spoken: str) -> list[tuple[str, str, list[dict]]]:
    """Primero el automático de lo que dice el coach («xx-orig» del idioma original), después los de las otras pistas de
    audio en español o inglés (doblajes), las traducciones y, al final, el resto."""
    rank = {lang: i for i, lang in enumerate(langs)}
    originals = sorted((k for k in auto if k.endswith(ORIG)), key=lambda k: (_base(k) != spoken, rank.get(_base(k), len(rank))))
    covered = {_base(k) for k in originals}
    out = [_orig_track(auto, k, spoken) for k in originals if _base(k) in rank]
    out += [("auto" if lang == spoken else "traducido", lang, auto[lang]) for lang in langs if lang in auto and lang not in covered]
    out += [_orig_track(auto, k, spoken) for k in originals if _base(k) not in rank]
    return out


def _orig_track(auto: dict, key: str, spoken: str) -> tuple[str, str, list[dict]]:
    return ("auto" if _base(key) == spoken else "doblaje", key.removesuffix(ORIG), auto[key])


# ---------------------------------------------------------------------- formatos
def parse_json3(data: dict) -> list[tuple[float, str]]:
    """Palabras (o líneas) con su segundo de un json3 de YouTube."""
    words: list[tuple[float, str]] = []
    for event in data.get("events") or []:
        start = float(event.get("tStartMs") or 0)
        for seg in event.get("segs") or []:
            text = seg.get("utf8") or ""
            if text.strip():
                words.append(((start + float(seg.get("tOffsetMs") or 0)) / 1000, text))
    return words


def parse_srv3(text: str) -> list[tuple[float, str]]:
    """``<p t="ms">texto</p>`` (con ``<s>`` por palabra en los automáticos)."""
    words: list[tuple[float, str]] = []
    for start, body in re.findall(r'<p t="(\d+)"[^>]*>(.*?)</p>', text, flags=re.DOTALL):
        clean = html.unescape(_TAG.sub(" ", body)).strip()
        if clean:
            words.append((int(start) / 1000, clean))
    return words


def _vtt_time(line: str) -> float | None:
    match = _TIMING.search(line)
    if match:
        h, m, s, ms = (int(x) for x in match.groups())
        return h * 3600 + m * 60 + s + ms / 1000
    match = _SHORT_TIMING.search(line)
    if match:
        m, s, ms = (int(x) for x in match.groups())
        return m * 60 + s + ms / 1000
    return None


def parse_vtt(text: str) -> list[tuple[float, str]]:
    """WebVTT. Los automáticos repiten la línea anterior en cada bloque: solo se guarda lo nuevo."""
    words: list[tuple[float, str]] = []
    previous: set[str] = set()
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        lines = block.strip().split("\n")
        start = next((t for t in (_vtt_time(line) for line in lines) if t is not None), None)
        if start is None:
            continue
        body = [html.unescape(_TAG.sub("", line)).strip() for line in lines if "-->" not in line]
        body = [line for line in body if line and not line.isdigit()]
        fresh = [line for line in body if line not in previous]
        previous = set(body)
        if fresh:
            words.append((start, " ".join(fresh)))
    return words


def _join(parts: list[str]) -> str:
    return " ".join(" ".join(parts).split())


def chunks(words: list[tuple[float, str]], span: float = CHUNK_S, max_chars: int = CHUNK_CHARS) -> list[list]:
    """Agrupa palabras en trozos ``[segundo, texto]`` de hasta ~``span`` segundos (o hasta un punto)."""
    out: list[list] = []
    start: float | None = None
    parts: list[str] = []
    size = 0
    for t, text in sorted(words, key=lambda w: w[0]):
        if start is not None and (t - start >= span or size >= max_chars):
            out.append([round(start, 1), _join(parts)])
            start, parts, size = None, [], 0
        if start is None:
            start = t
        parts.append(text)
        size += len(text) + 1
        if text.rstrip().endswith((".", "?", "!")) and t - start >= span / 3:
            out.append([round(start, 1), _join(parts)])
            start, parts, size = None, [], 0
    if parts and start is not None:
        out.append([round(start, 1), _join(parts)])
    return out


def _decode(fmt: str, raw: bytes) -> list[tuple[float, str]]:
    text = raw.decode("utf-8", errors="replace")
    if fmt == "json3":
        return parse_json3(json.loads(text))
    if fmt == "srv3":
        return parse_srv3(text)
    return parse_vtt(text)


# ---------------------------------------------------------------------- métodos
def from_ytdlp(info: dict, data_dir: Path | None = None) -> dict | None:
    """Plan A: los subtítulos que lista yt-dlp (el primero que se pueda bajar y leer)."""
    for kind, lang, tracks in candidate_tracks(info):
        by_ext = {t.get("ext"): t for t in tracks if t.get("url")}
        for fmt in FORMATS:
            track = by_ext.get(fmt)
            if not track:
                continue
            try:
                words = _decode(fmt, youtube.fetch(track["url"], data_dir))
            except Exception as exc:  # noqa: BLE001 - se prueba el siguiente formato o método
                log.debug("Subtítulos %s/%s (%s) fallidos: %s", lang, fmt, kind, exc)
                continue
            if words:
                return {"metodo": f"yt-dlp {fmt}", "tipo": kind, "idioma": lang, "segmentos": chunks(words)}
    return None


def from_transcript_api(video_id: str, info: dict | None = None) -> dict | None:
    """Plan B: ``youtube-transcript-api`` (pide los subtítulos por otro camino)."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return None
    langs = language_order(info or {})
    try:
        listing = YouTubeTranscriptApi().list(video_id)
        transcript = _pick_transcript(listing, langs)
        if transcript is None:
            return None
        fetched = transcript.fetch()
    except Exception as exc:  # noqa: BLE001 - la librería lanza muchos tipos distintos
        log.debug("youtube-transcript-api falló para %s: %s", video_id, exc)
        return None
    words = [(float(s.start), str(s.text)) for s in fetched if str(s.text).strip()]
    if not words:
        return None
    kind = "auto" if getattr(transcript, "is_generated", False) else "manual"
    return {"metodo": "youtube-transcript-api", "tipo": kind, "idioma": transcript.language_code, "segmentos": chunks(words)}


def _pick_transcript(listing, langs: list[str]):
    for finder in (listing.find_manually_created_transcript, listing.find_generated_transcript):
        try:
            return finder(langs)
        except Exception as exc:  # noqa: BLE001 - NoTranscriptFound: se prueba el siguiente
            log.debug("youtube-transcript-api: %s", exc)
    available = list(listing)
    if not available:
        return None
    first = available[0]
    if _base(first.language_code) in PREFERRED or not getattr(first, "is_translatable", False):
        return first
    try:
        return first.translate("en")
    except Exception as exc:  # noqa: BLE001 - sin traducción vale el original
        log.debug("youtube-transcript-api sin traducción: %s", exc)
        return first


def from_whisper(video_id: str, folder: Path, info: dict | None = None, data_dir: Path | None = None,
                 should_stop: Callable[[], bool] | None = None) -> dict | None:
    """Plan C: baja solo el audio y lo pasa a texto aquí (faster-whisper). El audio se borra al terminar."""
    if not deps.ensure_voice():
        return None
    audio = youtube.download(video_id, folder, youtube.AUDIO_FORMAT, data_dir)
    try:
        model = _whisper()
        lang = original_language(info or {}) or None
        segments, detected = model.transcribe(str(audio), language=lang, vad_filter=True, beam_size=1)
        words: list[tuple[float, str]] = []
        for seg in segments:
            if should_stop and should_stop():
                return None
            if seg.text.strip():
                words.append((float(seg.start), seg.text.strip()))
    finally:
        audio.unlink(missing_ok=True)
    if not words:
        return None
    return {"metodo": f"faster-whisper {WHISPER_MODEL}", "tipo": "voz", "idioma": lang or detected.language, "segmentos": chunks(words)}


def _whisper():
    global _model
    with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel

            threads = max(1, (os.cpu_count() or 2) // 2)  # la mitad de los núcleos: el PC sigue usable
            _model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8", cpu_threads=threads)
        return _model


def collect(video_id: str, info: dict, folder: Path, data_dir: Path | None = None,
            should_stop: Callable[[], bool] | None = None) -> tuple[dict | None, list[str]]:
    """La transcripción por el primer método que funcione, y los avisos de los que fallaron."""
    notes: list[str] = []
    result = from_ytdlp(info, data_dir)
    if result:
        return result, notes
    notes.append("sin subtítulos con yt-dlp")
    result = from_transcript_api(video_id, info)
    if result:
        return result, notes
    notes.append("sin subtítulos con youtube-transcript-api")
    try:
        result = from_whisper(video_id, folder, info, data_dir, should_stop)
    except Exception as exc:  # noqa: BLE001 - sin transcripción el vídeo se guarda igual (metadatos y frames)
        log.warning("Voz a texto fallida en %s: %s", video_id, exc)
        notes.append(f"voz a texto fallida: {exc}")
        return None, notes
    if result is None:
        notes.append("voz a texto no disponible")
    return result, notes
