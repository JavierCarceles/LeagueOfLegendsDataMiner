"""El recolector de vídeos: un hilo en segundo plano que procesa la cola de ``data/videos`` de uno en uno.

Por cada fuente pendiente (canal, lista, búsqueda) saca sus vídeos; por cada vídeo pendiente guarda metadatos,
transcripción y, si es una partida, los datos de los frames (ver :mod:`app.videos.frames`). Todo lo descargado va
a una carpeta temporal que se borra al acabar cada vídeo, pase lo que pase.

Se pausa mientras juegas (como la precarga), espera entre vídeos para no enfadar a YouTube y, si YouTube frena
(429 o «confirma que no eres un bot»), para 30 min. Al vaciar la cola (o cada 10 vídeos) pide una subida a GitHub.
"""

from __future__ import annotations

import random
import shutil
import tempfile
import threading
import time
from pathlib import Path

import requests

from app.logbus import get_logger
from app.videos import deps
from app.videos.store import PENDING, VideoStore

log = get_logger("videos")

DIR_NAME = "videos"
PAUSE_BETWEEN_S = (20, 45)  # entre vídeo y vídeo
LIMIT_PAUSE_S = 30 * 60  # YouTube frena: media hora sin pedirle nada
IDLE_S = 15
PUSH_EVERY = 10  # vídeos recogidos entre subidas a GitHub (y siempre al vaciar la cola)
MAX_FRAMES_DURATION_S = 3 * 3600  # directos larguísimos: solo transcripción
MIN_FREE_BYTES = 3 * 1024**3
MIN_DURATION_S = 90  # los shorts no sirven para analizar
TEMP_PREFIX = "tempu-video-"
LANGS = ("es", "en")
CDN = "https://ddragon.leagueoflegends.com/cdn"
VERSION = 1

# Búsquedas guiadas (rol y tema) en los dos idiomas.
ROLES = {
    "top": ("top", "top"),
    "jungla": ("jungle", "jungla"),
    "mid": ("mid", "mid"),
    "adc": ("adc", "adc"),
    "support": ("support", "support"),
}
TOPICS = {
    "macro": ("macro guide", "guía macro"),
    "oleadas": ("wave management", "control de oleadas"),
    "vision": ("vision warding guide", "visión y wardeo"),
    "objetivos": ("objectives dragon baron guide", "objetivos dragón barón"),
    "early": ("early game guide", "early game guía"),
    "rutas": ("jungle pathing", "rutas de jungla"),
    "teamfight": ("teamfight positioning", "teamfights posicionamiento"),
    "fase_lineas": ("laning phase tips", "fase de líneas consejos"),
    "coaching": ("coaching vod review", "coaching revisión de partida"),
    "mentalidad": ("mindset climbing tips", "mentalidad subir elo"),
}


def build_queries(text: str = "", role: str = "", topic: str = "", champion: str = "", langs: tuple[str, ...] = ("es", "en")) -> list[tuple[str, str]]:
    """``(idioma, búsqueda)`` para cada idioma: texto libre + rol + tema + campeón, siempre con «league of legends»."""
    out: list[tuple[str, str]] = []
    for lang in langs:
        idx = 0 if lang == "en" else 1
        parts = [champion.strip(), ROLES[role][idx] if role in ROLES else "", TOPICS[topic][idx] if topic in TOPICS else "", text.strip()]
        query = " ".join(p for p in parts if p)
        if not query:
            continue
        if "league" not in query.lower() and "lol" not in query.lower().split():
            query += " league of legends"
        out.append((lang, query))
    return out


class VideoCollector:
    def __init__(self, svc) -> None:
        self.svc = svc
        self.root = Path(svc.settings.data_dir) / DIR_NAME
        self.store = VideoStore(self.root)
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self.current: dict | None = None
        self.limited_until = 0.0
        self.collected_since_push = 0
        self.last_error: str | None = None
        self.tools = None

    # ------------------------------------------------------------------ control
    def autostart(self) -> None:
        """Al arrancar la app: si hay algo pendiente y no lo pausaste, sigue solo."""
        _clean_temp()
        self.store.reset_working()
        counts = self.store.counts()
        if counts.get("pendiente") or counts.get("fuentes_pendientes"):
            self.start()

    def start(self) -> None:
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                self.wake.set()
                return
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._loop, name="videos", daemon=True)
            self.thread.start()

    def shutdown(self) -> None:
        self.stop_event.set()
        self.wake.set()

    def set_paused(self, paused: bool) -> None:
        self.store.set_paused(paused)
        log.info("Vídeos: recolector %s.", "en pausa" if paused else "reanudado")
        if not paused:
            self.start()
        self.wake.set()

    def add(self, text: str, role: str = "", topic: str = "", champion: str = "", langs: tuple[str, ...] = ("es", "en"),
            per_query: int = 10, limit: int | None = 50, min_minutes: float = 3, start: bool = True) -> list[dict]:
        """Añade un enlace (vídeo, lista, canal) o una búsqueda; el hilo la convierte en vídeos (``start``: arrancarlo)."""
        from app.videos.youtube import classify  # sin yt-dlp: solo mira el texto

        options = {"min_minutos": min_minutes}
        kind, value = classify(text) if text.strip() else ("busqueda", "")
        sources = []
        if kind == "busqueda":
            for lang, query in build_queries(value, role, topic, champion, langs):
                sources.append(self.store.add_source("busqueda", query, {**options, "idioma": lang, "n": per_query}))
        else:
            sources.append(self.store.add_source(kind, value, {**options, "limite": limit}))
        if not sources:
            raise ValueError("Escribe un enlace o qué buscar (o elige un rol o un tema)")
        if start:
            self.store.set_paused(False)
            self.start()
        return sources

    # ------------------------------------------------------------------ estado
    def paused_reason(self) -> str | None:
        if self.store.paused:
            return "manual"
        if getattr(self.svc.settings, "pause_in_game", True):
            tracker = getattr(self.svc, "tracker", None)
            if tracker is not None and tracker.state.get("in_game"):
                return "en_partida"
        if time.time() < self.limited_until:
            return "youtube_frena"
        return None

    def status(self) -> dict:
        alive = bool(self.thread and self.thread.is_alive())
        return {
            "activo": alive,
            "pausa": self.paused_reason(),
            "hasta": self.limited_until if time.time() < self.limited_until else None,
            "actual": dict(self.current) if self.current else None,
            "cuentas": self.store.counts(),
            "herramientas": deps.state(),
            "ultimo_error": self.last_error,
            "carpeta": str(self.root),
        }

    # ------------------------------------------------------------------ bucle
    def _loop(self) -> None:
        log.info("Vídeos: recolector en marcha.")
        while not self.stop_event.is_set():
            if self.paused_reason():
                self._sleep(IDLE_S)
                continue
            if not deps.ensure_base():
                self._sleep(60)
                continue
            if self._step():
                self._sleep(random.uniform(*PAUSE_BETWEEN_S))
            elif not self._idle():
                break
        log.info("Vídeos: recolector parado.")

    def _idle(self) -> bool:
        """Nada que hacer: sube lo recogido y espera a que llegue algo (``False`` = salir del hilo)."""
        if self.collected_since_push:
            self._push()
        self.wake.clear()
        self.wake.wait(60)
        return not self.stop_event.is_set()

    def _sleep(self, seconds: float) -> None:
        self.wake.clear()
        self.wake.wait(seconds)

    def _step(self) -> bool:
        """Una fuente o un vídeo; ``True`` si se hizo algo (para esperar un poco antes del siguiente)."""
        source = self.store.next_source()
        if source is not None:
            self._expand(source)
            return True
        video = self.store.next_video()
        if video is None:
            return False
        self._process(video)
        return True

    # ------------------------------------------------------------------ fuentes
    def _expand(self, source: dict) -> None:
        from app.videos import youtube

        opts = source.get("opciones") or {}
        self.current = {"tipo": "fuente", "fuente": source, "paso": "leyendo la lista de vídeos"}
        try:
            if source["tipo"] == "video":
                entries = [{"id": source["valor"]}]
            elif source["tipo"] == "busqueda":
                entries = youtube.search(source["valor"], int(opts.get("n") or 10), self.root)
            else:
                entries = youtube.expand(source["valor"], opts.get("limite"), self.root)
        except youtube.VideoError as exc:
            self._error(f"No se pudo leer «{source['valor']}»: {exc}", exc.kind)
            if exc.kind != "limite":
                self.store.finish_source(source["id"], error=str(exc)[:300])
            return
        finally:
            self.current = None
        # Un vídeo pegado a mano entra siempre; de canales y búsquedas, sin shorts ni vídeos más cortos de lo pedido.
        floor = 0.0 if source["tipo"] == "video" else max(float(opts.get("min_minutos") or 0) * 60, MIN_DURATION_S)
        kept = [e for e in entries if not e.get("duracion") or e["duracion"] >= floor]
        added = self.store.add_videos(kept, source)
        self.store.finish_source(source["id"], found=len(entries), added=added)
        log.info("Vídeos: «%s» → %d vídeos (%d nuevos en la cola).", source["valor"], len(entries), added)

    # ------------------------------------------------------------------ vídeos
    def _process(self, video: dict) -> None:
        from app.videos import youtube

        vid = video["id"]
        self.store.start(vid)
        self.current = {"tipo": "video", "id": vid, "titulo": video.get("titulo"), "paso": "metadatos", "progreso": None}
        folder = Path(tempfile.mkdtemp(prefix=f"{TEMP_PREFIX}{vid}-"))
        try:
            data = self._collect(vid, folder)
        except youtube.VideoError as exc:
            self.store.fail(vid, str(exc), final=exc.kind == "final")
            self._error(f"{vid}: {exc}", exc.kind)
            return
        except Exception as exc:
            log.exception("Vídeos: fallo inesperado con %s", vid)
            self.store.fail(vid, f"{type(exc).__name__}: {exc}")
            return
        finally:
            shutil.rmtree(folder, ignore_errors=True)
            self.current = None
        if data is None:  # se pidió parar a mitad: vuelve a la cola
            self.store.update(vid, estado=PENDING)
            return
        self.store.save_result(vid, data)
        self.store.done(vid, _summary(data))
        self.collected_since_push += 1
        self.last_error = None
        log.info("Vídeos: recogido «%s» (%s).", data.get("titulo") or vid, _summary_text(data))
        if self.collected_since_push >= PUSH_EVERY:
            self._push()

    def _collect(self, vid: str, folder: Path) -> dict | None:
        from app.videos import transcript, youtube

        info = youtube.video_info(vid, self.root)
        if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
            raise youtube.VideoError("Es un directo en curso o programado: se recoge cuando termine", "otro")
        data = _metadata(vid, info)
        queued = self.store.get(vid) or {}
        data["origen"] = {"fuente": queued.get("fuente"), "alta": queued.get("alta")}
        self._check_language(queued, info)
        self._step_label("transcripción")
        text, notes = transcript.collect(vid, info, folder, self.root, self.stop_event.is_set)
        data["transcripcion"] = text
        data["avisos"] = notes
        if self.stop_event.is_set():
            return None
        self._step_label("frames (¿es una partida?)")
        data["frames"] = self._frames(vid, info, folder, data["avisos"])
        if self.stop_event.is_set():
            return None
        if text is None and not (data["frames"] or {}).get("partida"):
            data["avisos"].append("sin transcripción ni partida: solo metadatos")
        return data

    def _check_language(self, queued: dict, info: dict) -> None:
        """De una búsqueda solo interesan vídeos en español o inglés (un enlace pegado a mano entra siempre)."""
        from app.videos.youtube import VideoError

        spoken = str(info.get("language") or "").split("-")[0].lower()
        source = next((s for s in self.store.sources() if s.get("id") == queued.get("fuente")), None)
        if spoken and spoken not in LANGS and source is not None and source.get("tipo") == "busqueda":
            raise VideoError(f"Vídeo en otro idioma ({spoken}): solo se recogen en español o inglés", "final")

    def _step_label(self, label: str, progress: float | None = None) -> None:
        if self.current is not None:
            self.current.update(paso=label, progreso=progress)

    # ------------------------------------------------------------------ frames
    def _frames(self, vid: str, info: dict, folder: Path, notes: list[str]) -> dict | None:
        """Copia pequeña (360p) para ver si es una partida; si lo es, el vídeo a 1080p para sacar los datos.

        Los ficheros se borran en cuanto dejan de hacer falta (y la carpeta entera al acabar el vídeo)."""
        duration = float(info.get("duration") or 0)
        if not duration or duration > MAX_FRAMES_DURATION_S:
            notes.append("frames: duración desconocida o demasiado largo")
            return None
        try:
            tools = self._tools()
        except Exception as exc:  # noqa: BLE001 - sin Data Dragon no hay minimapa, pero el resto vale
            notes.append(f"frames: sin herramientas ({exc})")
            return None
        from app.videos import frames as F
        from app.videos import youtube

        small = self._download(vid, folder / "sonda", notes, youtube.PROBE_FORMATS, "¿es una partida? (copia pequeña)")
        if small is None:
            return None
        box, _ = _probe_file(small, duration, tools)
        if box is None:
            small.unlink(missing_ok=True)
            return {"partida": False, "metodo": "sonda 360p", "probados": F.PROBE_FRAMES}
        big = self._download(vid, folder / "video", notes, youtube.VIDEO_FORMATS, "descargando el vídeo (se borra al terminar)")
        path = big or small
        if big is not None:
            small.unlink(missing_ok=True)
        try:
            return self._full_pass(path, duration, tools, "descarga" if big else "copia pequeña")
        finally:
            path.unlink(missing_ok=True)

    def _full_pass(self, path: Path, duration: float, tools, method: str) -> dict | None:
        from app.videos import frames as F

        self._step_label("frames: calibrando minimapa, reloj y cámara")
        box, probe_frames = _probe_file(path, duration, tools)
        if box is None:
            return {"partida": False, "metodo": method, "probados": F.PROBE_FRAMES}
        champion = F.vote_champion(tools.portraits, probe_frames[:6]) if tools.portraits else None
        if champion:
            champion["nombre"] = self._champ_name(champion["clave"])
        clock_ok = tools.clock.calibrate(probe_frames)
        cam = F.camera_size([F.white_mask(box.crop(f)) for f in probe_frames], box.size)
        self._step_label("frames", 0.0)
        reader = F.Frames(str(path))
        try:
            collected = F.sample_pass(reader, box, cam, tools, duration, self._should_stop_frames, lambda p: self._step_label("frames", p))
        finally:
            reader.close()
        if self.stop_event.is_set():
            return None
        return F.summarize(collected, cam, champion, clock_ok, f"{method} {collected.shape[0]}p")

    def _should_stop_frames(self) -> bool:
        """Durante el análisis de frames: parar si se cierra la app; si empiezas a jugar, esperar a que acabe."""
        while not self.stop_event.is_set() and self.paused_reason() == "en_partida":
            self._step_label("en pausa: estás jugando")
            self.stop_event.wait(20)
        return self.stop_event.is_set()

    def _download(self, vid: str, folder: Path, notes: list[str], formats: tuple[str, ...], label: str) -> Path | None:
        from app.videos import youtube

        try:
            free = shutil.disk_usage(folder.parent).free
        except OSError:
            free = MIN_FREE_BYTES
        if free < MIN_FREE_BYTES:
            notes.append("poco espacio libre en el disco: sin frames")
            return None
        self._step_label(label)
        for fmt in formats:
            if self.stop_event.is_set():
                return None
            try:
                return youtube.download(vid, folder, fmt, self.root, format_sort=youtube.VIDEO_SORT)
            except youtube.VideoError as exc:
                if exc.kind == "limite":
                    raise
                notes.append(f"descarga {fmt} fallida: {str(exc)[:160]}")
        return None

    # ------------------------------------------------------------------ herramientas de imagen
    def _tools(self):
        if self.tools is not None:
            return self.tools

        from app.videos import frames as F

        dd = self.svc.dd
        if not dd.version:
            dd.load()
        img_dir = Path(dd.cache_dir) / str(dd.version) / "img"
        map_path = self._cached(img_dir / "map11.png", f"{CDN}/{dd.version}/img/map/map11.png")
        map_bgr = F.read_image(map_path)
        if map_bgr is None:
            raise OSError("map11.png dañado")
        icons = {}
        for champ in dd.champions.values():
            try:
                path = self._cached(img_dir / "champion" / f"{champ['key']}.png", f"{CDN}/{dd.version}/img/champion/{champ['key']}.png")
            except OSError:
                continue
            img = F.read_image(path)
            if img is not None:
                icons[champ["key"]] = img
        self.tools = F.Tools(F.MapMatcher(map_bgr), F.Clock(F.load_digits()), F.Portraits(icons) if icons else None)
        return self.tools

    @staticmethod
    def _cached(path: Path, url: str) -> Path:
        if path.exists() and path.stat().st_size > 0:
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(response.content)
        tmp.replace(path)
        return path

    def _champ_name(self, key: str) -> str:
        for champ in self.svc.dd.champions.values():
            if champ["key"] == key:
                return champ["name"]
        return key

    # ------------------------------------------------------------------ errores y subidas
    def _error(self, message: str, kind: str = "otro") -> None:
        self.last_error = message[:400]
        if kind == "limite":
            self.limited_until = time.time() + LIMIT_PAUSE_S
            log.warning("Vídeos: YouTube está frenando las descargas; paro 30 min. (%s)", message[:200])
            deps.update_ytdlp()  # a veces es una versión vieja de yt-dlp
        else:
            log.warning("Vídeos: %s", message[:300])

    def _push(self) -> None:
        """Sube lo recogido a GitHub con el resto de ``data/`` (si la subida automática está activada)."""
        try:
            from app import autopush

            auto = autopush.AutoPush.of(self.svc)
            if auto is None or autopush.every() <= 0:
                return
            if auto.start():
                log.info("Vídeos: subiendo a GitHub los %d vídeos recogidos.", self.collected_since_push)
                self.collected_since_push = 0
        except Exception:
            log.exception("Vídeos: no se pudo pedir la subida a GitHub")


# ---------------------------------------------------------------------- frames
def _clean_temp(older_than_s: float = 600) -> None:
    """Restos de un vídeo a medias (la app se cerró descargando): se borran al volver a arrancar."""
    base = Path(tempfile.gettempdir())
    now = time.time()
    for path in base.glob(f"{TEMP_PREFIX}*"):
        try:
            if path.is_dir() and now - path.stat().st_mtime > older_than_s:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue


def _probe_file(path: Path, duration: float, tools):
    from app.videos import frames as F

    reader = F.Frames(str(path))
    try:
        return F.probe(reader, duration, tools)
    finally:
        reader.close()


# ---------------------------------------------------------------------- resumen
def _metadata(vid: str, info: dict) -> dict:
    chapters = [[round(float(c.get("start_time") or 0), 1), c.get("title") or ""] for c in info.get("chapters") or []]
    return {
        "version": VERSION,
        "id": vid,
        "url": f"https://www.youtube.com/watch?v={vid}",
        "titulo": info.get("title") or "",
        "canal": info.get("channel") or info.get("uploader") or "",
        "canal_id": info.get("channel_id"),
        "canal_url": info.get("channel_url") or info.get("uploader_url"),
        "publicado": _date(info.get("upload_date")),
        "duracion": info.get("duration"),
        "vistas": info.get("view_count"),
        "likes": info.get("like_count"),
        "idioma": info.get("language"),
        "etiquetas": (info.get("tags") or [])[:40],
        "descripcion": (info.get("description") or "")[:6000],
        "capitulos": chapters,
        "recogido": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _date(raw: str | None) -> str | None:
    if raw and len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw


def _summary(data: dict) -> dict:
    text = data.get("transcripcion") or {}
    frames = data.get("frames") or {}
    return {
        "titulo": data.get("titulo"),
        "canal": data.get("canal"),
        "duracion": data.get("duracion"),
        "idioma": text.get("idioma") or data.get("idioma"),
        "transcripcion": text.get("metodo"),
        "trozos": len(text.get("segmentos") or []),
        "partida": bool(frames.get("partida")),
        "campeon": (frames.get("campeon") or {}).get("nombre"),
        "segundos_partida": frames.get("segundos_con_partida"),
        "reloj": len(frames.get("reloj") or []),
        "camara": len(frames.get("camara") or []),
        "avisos": len(data.get("avisos") or []),
    }


def _summary_text(data: dict) -> str:
    s = _summary(data)
    parts = [f"transcripción: {s['transcripcion'] or 'no'}"]
    if s["partida"]:
        parts.append(f"partida {s['segundos_partida']} s" + (f" con {s['campeon']}" if s["campeon"] else ""))
    return ", ".join(parts)
