"""Convierte vídeos/playlists de coaching de YouTube en guías de matchup.

Flujo (igual que el antiguo ``descargar_coachings.py`` pero integrado y sin
subprocesos): subtítulos de YouTube → si no hay, audio + Whisper local →
compresión del texto → Gemini extrae rol, campeones y guía → fichero en la
carpeta de guías. Todas las dependencias son opcionales y se importan solo
al usarlo (``pip install -r requirements-coaching.txt``).
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path

from app.coaching.gemini import GuideError, VideoSchema, call_gemini
from app.coaching.guides import ROLE_FOLDERS
from app.logbus import get_logger

log = get_logger("youtube")
FILLERS = re.compile(r"\b(eh|mmm|bueno|o sea|es decir|en plan|sabes|literalmente|tipo|vale|ok)\b", re.IGNORECASE)


def _has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def capabilities() -> dict:
    return {
        "yt_dlp": _has("yt_dlp"),
        "transcripts": _has("youtube_transcript_api"),
        "whisper": _has("whisper"),
        "sumy": _has("sumy") and _has("nltk"),
        "gemini": _has("google.genai"),
    }


class YouTubeJobs:
    def __init__(self, svc) -> None:
        self.svc = svc
        self.jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.history_path = svc.settings.data_dir / "historial_coachings.json"
        self._whisper = None

    # --------------------------------------------------------------- historial
    def _history(self) -> set[str]:
        try:
            return set(json.loads(self.history_path.read_text(encoding="utf-8")))
        except (FileNotFoundError, ValueError):
            return set()

    def _mark_done(self, video_id: str) -> None:
        with self._lock:
            done = self._history()
            done.add(video_id)
            self.history_path.write_text(json.dumps(sorted(done), indent=2), encoding="utf-8")

    # ------------------------------------------------------------------ jobs
    def start(self, url: str) -> dict:
        caps = capabilities()
        if not caps["yt_dlp"]:
            raise RuntimeError("Falta yt-dlp (pip install -r requirements-coaching.txt)")
        if not self.svc.settings.gemini_keys:
            raise RuntimeError("Faltan las claves de Gemini en .env")
        job_id = uuid.uuid4().hex[:8]
        job = {"id": job_id, "url": url, "status": "running", "done": 0, "total": 0, "saved": [], "errors": [], "started_at": time.time()}
        with self._lock:
            self.jobs[job_id] = job
        threading.Thread(target=self._run, args=(job,), name=f"youtube-{job_id}", daemon=True).start()
        return job

    def list(self) -> list[dict]:
        with self._lock:
            return sorted(self.jobs.values(), key=lambda j: j["started_at"], reverse=True)[:10]

    def _run(self, job: dict) -> None:
        import yt_dlp

        try:
            opts = {"quiet": True, "extract_flat": True, "ignoreerrors": True}
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(job["url"], download=False) or {}
            entries = [e for e in (info.get("entries") or [info]) if e and e.get("id")]
            job["total"] = len(entries)
            done = self._history()
            for entry in entries:
                video_id, title = entry["id"], entry.get("title") or "Sin título"
                if video_id in done:
                    job["done"] += 1
                    continue
                try:
                    saved = self._process(video_id, title)
                    if saved:
                        job["saved"].append(saved)
                except Exception as exc:
                    job["errors"].append(f"{title}: {exc}")
                    log.warning("No se pudo procesar '%s': %s", title, exc)
                job["done"] += 1
            job["status"] = "done"
        except Exception as exc:
            job["status"] = "error"
            job["errors"].append(str(exc))
            log.error("Error procesando %s: %s", job["url"], exc)

    # ---------------------------------------------------------------- vídeo
    def _process(self, video_id: str, title: str) -> str | None:
        log.info("Procesando vídeo: %s", title)
        text = self._subtitles(video_id) or self._transcribe(video_id)
        if not text:
            raise RuntimeError("No hay subtítulos y Whisper no está disponible")
        last_error = None
        for sentences in (200, 120, 70, 40):
            prompt = self._prompt(title, self._compress(text, sentences))
            try:
                data, _model = call_gemini(self.svc.settings.gemini_keys, self.svc.settings.gemini_models, prompt, VideoSchema)
                break
            except GuideError as exc:
                last_error = exc
                time.sleep(3)
        else:
            raise RuntimeError(str(last_error))

        dd = self.svc.dd
        champ = dd.resolve(data.get("campeon_principal"))
        opp = dd.resolve(data.get("campeon_enemigo"))
        if not champ or not opp:
            raise RuntimeError(f"No se reconocieron los campeones ({data.get('campeon_principal')} vs {data.get('campeon_enemigo')})")
        role = ROLE_FOLDERS.get(str(data.get("rol", "")).lower()) or "GENERAL"
        content = (
            f"VÍDEO: {title}\nURL: https://www.youtube.com/watch?v={video_id}\n"
            f"MATCHUP: {dd.name(champ)} vs {dd.name(opp)}\nDIFICULTAD APROX: {data.get('dificultad_matchup', 5)}/10\n"
            f"{'=' * 50}\n\n{(data.get('guia') or '').strip()}\n"
        )
        path = self.svc.guides.save(role, champ, opp, content, ai=False)
        self._mark_done(video_id)
        log.info("Guía guardada desde vídeo: %s", path)
        return path

    @staticmethod
    def _prompt(title: str, text: str) -> str:
        return f"""Eres un coach profesional de League of Legends.
Analiza la transcripción resumida del vídeo de coaching "{title}".
Extrae SOLO información útil sobre el enfrentamiento entre el campeón del alumno/streamer y su rival directo.
Ignora bromas, donaciones y charla irrelevante. Presta mucha atención a los nombres de campeones.

El campo "guia" debe estar en ESPAÑOL con esta estructura:
DIFICULTAD: [1-10 y justificación en una línea]
EARLY GAME:
- [...]
MID GAME:
- [...]
LATE GAME:
- [...]

Transcripción:
{text}"""

    @staticmethod
    def _subtitles(video_id: str) -> str | None:
        if not _has("youtube_transcript_api"):
            return None
        from youtube_transcript_api import YouTubeTranscriptApi

        try:
            if hasattr(YouTubeTranscriptApi, "get_transcript"):
                raw = YouTubeTranscriptApi.get_transcript(video_id, languages=["es", "en"])
            else:
                fetched = YouTubeTranscriptApi().fetch(video_id, languages=["es", "en"])
                raw = fetched.to_raw_data() if hasattr(fetched, "to_raw_data") else [{"text": s.text} for s in fetched]
            text = " ".join(item["text"] for item in raw)
            return re.sub(r"\[.*?\]", "", text).strip() or None
        except Exception:
            return None

    def _transcribe(self, video_id: str) -> str | None:
        if not (_has("whisper") and _has("yt_dlp")):
            return None
        import whisper
        import yt_dlp

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg and _has("imageio_ffmpeg"):
            import imageio_ffmpeg

            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / f"audio_{video_id}"
            opts = {
                "format": "bestaudio/best",
                "outtmpl": f"{base}.%(ext)s",
                "quiet": True,
                "noplaylist": True,
                "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
            }
            if ffmpeg:
                opts["ffmpeg_location"] = ffmpeg
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
            wav = Path(f"{base}.wav")
            if not wav.exists():
                return None
            if self._whisper is None:
                if os.name == "nt":  # parche histórico de Whisper en Windows
                    import ctypes.util

                    original = ctypes.util.find_library
                    ctypes.util.find_library = lambda name: "msvcrt" if name == "c" else original(name)
                log.info("Cargando Whisper (la primera vez tarda)…")
                self._whisper = whisper.load_model("base")
            result = self._whisper.transcribe(str(wav), language="es", fp16=False)
            return result.get("text") or None

    def _compress(self, text: str, sentences: int) -> str:
        clean = " ".join(FILLERS.sub("", text).split())
        if not (_has("sumy") and _has("nltk")):
            return clean[: sentences * 120]
        try:
            import nltk
            from sumy.nlp.tokenizers import Tokenizer
            from sumy.parsers.plaintext import PlaintextParser
            from sumy.summarizers.lsa import LsaSummarizer

            for pkg in ("punkt", "punkt_tab"):
                try:
                    nltk.data.find(f"tokenizers/{pkg}")
                except LookupError:
                    nltk.download(pkg, quiet=True)
            names = [c["name"] for c in self.svc.dd.champions.values()] + [c["key"] for c in self.svc.dd.champions.values()]
            champ_re = re.compile(r"\b(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + r")\b", re.IGNORECASE)
            parsed = PlaintextParser.from_string(clean, Tokenizer("spanish"))
            all_sentences = [str(s) for s in parsed.document.sentences]
            key = [s for s in all_sentences if champ_re.search(s)]
            rest = " ".join(s for s in all_sentences if not champ_re.search(s))
            remaining = max(10, sentences - len(key))
            summary = LsaSummarizer()(PlaintextParser.from_string(rest, Tokenizer("spanish")).document, remaining)
            return " ".join(key + [str(s) for s in summary])
        except Exception as exc:
            log.debug("Compresión LSA no disponible (%s); recorte simple.", exc)
            return clean[: sentences * 120]
