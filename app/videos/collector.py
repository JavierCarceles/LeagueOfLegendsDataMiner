"""El recolector de vídeos: un hilo en segundo plano que procesa la cola de ``data/videos`` de uno en uno.

Por cada fuente pendiente (canal, lista, búsqueda) saca sus vídeos; por cada vídeo pendiente guarda metadatos,
transcripción y, si es una partida, los datos de los frames (ver :mod:`app.videos.frames`). Todo lo descargado va
a la carpeta de trabajo del vídeo (en la carpeta temporal del sistema, fuera del proyecto) con lo ya hecho en
``estado.json``: si la app se cierra o se reinicia a mitad (una versión nueva de GitHub), el vídeo sigue por ahí
(metadatos, transcripción, descargas y trozos de frames ya analizados). Al acabar el vídeo se borra.

Se pausa mientras juegas (como la precarga), espera entre vídeos para no enfadar a YouTube y, si YouTube frena
(429 o «confirma que no eres un bot»), para 30 min. Cada vídeo recogido cuenta para la subida a GitHub
(``AUTO_PUSH_VIDEOS``, 5 por defecto, en :mod:`app.autopush`) y, al vaciar la cola, se sube lo que haya.
"""

from __future__ import annotations

import contextlib
import json
import random
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from app import hardware
from app.logbus import add_videos_log, get_logger
from app.videos import deps, review
from app.videos.store import DONE, PENDING, VideoStore

log = get_logger("videos")

DIR_NAME = "videos"
PAUSE_BETWEEN_S = (8, 20)  # entre vídeo y vídeo (sin saturar a YouTube)
LIMIT_PAUSE_S = 30 * 60  # YouTube frena: media hora sin pedirle nada
IDLE_S = 15
MAX_FRAMES_DURATION_S = 3 * 3600  # directos larguísimos: solo transcripción
MIN_FREE_BYTES = 3 * 1024**3
MIN_DURATION_S = 90  # los shorts no sirven para analizar
TEMP_PREFIX = "tempu-video-"  # carpetas temporales de antes (se borran al arrancar)
WORK_DIR = "tempu-videos"  # en la carpeta temporal del sistema: una carpeta de trabajo por vídeo
WORK_KEEP_S = 3 * 86_400  # un vídeo a medias que no se retoma en 3 días: su carpeta se borra
CHECKPOINT = "estado.json"
LANGS = ("es", "en")
CDN = "https://ddragon.leagueoflegends.com/cdn"
# Sube cuando hay que rehacer todo lo recogido: al arrancar la app, todos los vídeos hechos con una versión anterior
# vuelven a la cola y se rehacen solos. 2: todo lo recogido antes del 25-11-2026 (campeón, reloj, metadatos y subtítulos
# con lo último).
VERSION = 2
# Sube cuando cambia lo que se saca de los frames: los vídeos con partida (o recogidos sin frames) se rehacen solos al
# arrancar la app. 2: reloj frame a frame (plantillas u OCR), campeón conservador, sin partidas falsas ni duplicados.
# 3: campeón por los iconos de sus habilidades (cada 10 s: vídeos con varias partidas) y lecturas sueltas del reloj aparte.
FRAMES_VERSION = 3

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
        add_videos_log(svc.settings.data_dir)  # todo el detalle en data/registro/videos_vivo.log (→ videos.log en GitHub)
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self.current: dict | None = None
        self.limited_until = 0.0
        self.last_error: str | None = None
        self.tools = None
        self.threads: int | None = None  # hilos del último análisis de frames (el 90 % de la CPU libre al empezar)

    # ------------------------------------------------------------------ control
    def autostart(self) -> None:
        """Al arrancar la app sigue solo si hay algo pendiente, como el buscador de partidas: la pausa a mano dura hasta
        cerrar la app (mientras juegas se sigue pausando solo)."""
        _clean_temp()
        self.store.reset_working()
        _clean_work({v["id"] for v in self.store.videos(PENDING, limit=1_000_000)})
        if self.store.paused:
            self.store.set_paused(False)
            log.info("Vídeos: la pausa era de la sesión anterior; el recolector sigue solo.")
        self._requeue_outdated()
        counts = self.store.counts()
        if counts.get("pendiente") or counts.get("fuentes_pendientes"):
            self.start()

    def _requeue_outdated(self) -> int:
        """Lo recogido con una versión anterior vuelve a la cola, lo primero (ver :func:`_outdated`)."""
        stale = [video["id"] for video in self.store.videos(DONE, limit=1_000_000)
                 if _outdated(self.store.load_result(video["id"]) or {})]
        if stale:
            self.store.requeue(stale)
            log.info("Vídeos: %d vídeos analizados con una versión anterior vuelven a la cola para rehacerlos.", len(stale))
        return len(stale)

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
        from app.videos.links import classify  # sin yt-dlp: funciona antes de instalar las herramientas

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
            "ritmo": self.store.pace(),
            "herramientas": deps.state(),
            "ultimo_error": self.last_error,
            "carpeta": str(self.root),
            "equipo": hardware.summary(self.threads),
        }

    # ------------------------------------------------------------------ bucle
    def _loop(self) -> None:
        log.info("Vídeos: recolector en marcha.")
        while not self.stop_event.is_set():
            reason = self.paused_reason()
            if reason:
                if reason == "manual" and self._pending():
                    self._push()  # al parar, lo recogido sube ya (y el vídeo que acabe después, en una subida en cola)
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
        if self._pending():
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
        folder = work_dir(vid)
        ckpt = Checkpoint(folder, vid)
        if ckpt.resumed:
            log.info("Vídeos: sigo con %s donde se quedó (%s).", vid, ckpt.summary())
        keep = False  # la carpeta se queda (para seguir) si se paró a mitad o el fallo se puede reintentar
        try:
            data = self._collect(vid, folder, ckpt)
            keep = data is None
        except youtube.VideoError as exc:
            self.store.fail(vid, str(exc), final=exc.kind == "final")
            self._error(f"{vid}: {exc}", exc.kind)
            keep = exc.kind != "final"
            return
        except Exception as exc:
            log.exception("Vídeos: fallo inesperado con %s", vid)
            self.store.fail(vid, f"{type(exc).__name__}: {exc}")
            return
        finally:
            if not keep:
                shutil.rmtree(folder, ignore_errors=True)
            self.current = None
        if data is None:  # se pidió parar a mitad: vuelve a la cola y sigue por su carpeta de trabajo
            self.store.update(vid, estado=PENDING)
            return
        self.store.save_result(vid, data)
        self.store.done(vid, _summary(data))
        self.last_error = None
        log.info("Vídeos: recogido «%s» (%s).", data.get("titulo") or vid, _summary_text(data))
        from app import autopush  # aquí: evita un import circular

        autopush.note_video(self.svc)  # AUTO_PUSH_VIDEOS vídeos recogidos suben los datos (y la cuenta sobrevive a reiniciar)

    def _collect(self, vid: str, folder: Path, ckpt: Checkpoint) -> dict | None:
        from app.videos import youtube

        info = ckpt.get("info")
        if info is None:
            info = self._info(vid)
            ckpt.put("info", info)
        if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
            raise youtube.VideoError("Es un directo en curso o programado: se recoge cuando termine", "otro")
        data = _metadata(vid, info)
        queued = self.store.get(vid) or {}
        data["origen"] = {"fuente": queued.get("fuente"), "alta": queued.get("alta")}
        self._check_language(queued, info)
        text, notes = self._transcript(vid, info, folder, ckpt)
        data["transcripcion"] = text
        data["avisos"] = notes
        if info.get("_plan_b"):
            notes.insert(0, f"metadatos reducidos (yt-dlp falló: {info['_plan_b']})")
        if self.stop_event.is_set():
            return None
        self._step_label("frames (¿es una partida?)")
        data["frames"] = self._frames(vid, info, folder, data["avisos"], ckpt)
        if self.stop_event.is_set():
            return None
        if text is None and not (data["frames"] or {}).get("partida"):
            data["avisos"].append("sin transcripción ni partida: solo metadatos")
        return data

    def _transcript(self, vid: str, info: dict, folder: Path, ckpt: Checkpoint) -> tuple[dict | None, list[str]]:
        """La transcripción (la de antes del reinicio si ya estaba hecha)."""
        from app.videos import transcript

        saved = ckpt.get("transcripcion")
        if saved is not None:
            return saved.get("texto"), list(saved.get("avisos") or [])
        self._step_label("transcripción")
        text, notes = transcript.collect(vid, info, folder, self.root, self.stop_event.is_set)
        if not self.stop_event.is_set():
            ckpt.put("transcripcion", {"texto": text, "avisos": notes})
        return text, list(notes)

    def _info(self, vid: str) -> dict:
        """Metadatos con yt-dlp; si falla por algo raro (no por vídeo privado ni por límite), plan B con oEmbed:
        título y canal, y la transcripción sale por youtube-transcript-api."""
        from app.videos import youtube

        try:
            return youtube.video_info(vid, self.root)
        except youtube.VideoError as exc:
            if exc.kind != "otro":
                raise
            basic = youtube.oembed(vid)
            if basic is None:
                raise
            log.info("Vídeos: yt-dlp falló con %s (%s); sigo con oEmbed.", vid, str(exc)[:160])
            return {**basic, "_plan_b": str(exc)[:300]}

    def _check_language(self, queued: dict, info: dict) -> None:
        """De una búsqueda solo interesan vídeos en español o inglés (un enlace pegado a mano entra siempre)."""
        from app.videos.transcript import original_language
        from app.videos.youtube import VideoError

        spoken = original_language(info)
        source = next((s for s in self.store.sources() if s.get("id") == queued.get("fuente")), None)
        if spoken and spoken not in LANGS and source is not None and source.get("tipo") == "busqueda":
            raise VideoError(f"Vídeo en otro idioma ({spoken}): solo se recogen en español o inglés", "final")

    def _step_label(self, label: str, progress: float | None = None) -> None:
        if self.current is not None:
            self.current.update(paso=label, progreso=progress)

    # ------------------------------------------------------------------ frames
    def _frames(self, vid: str, info: dict, folder: Path, notes: list[str], ckpt: Checkpoint | None = None) -> dict | None:
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
        from app.videos import frames as vf
        from app.videos import youtube

        resumed = _downloaded(folder / "video", vid)
        if resumed is not None:  # sigue un vídeo a medias: ya estaba bajado (la sonda ya dijo que es una partida)
            return self._pass_and_clean(resumed, duration, tools, "descarga", ckpt)
        small = self._download(vid, folder / "sonda", notes, youtube.PROBE_FORMATS, "¿es una partida? (copia pequeña)")
        if small is None:
            return None
        box, _ = _probe_file(small, duration, tools)
        if box is None:
            small.unlink(missing_ok=True)
            return {"partida": False, "metodo": "sonda 360p", "probados": vf.PROBE_FRAMES}
        big = self._download(vid, folder / "video", notes, youtube.VIDEO_FORMATS, "descargando el vídeo (se borra al terminar)")
        path = big or small
        if big is not None:
            small.unlink(missing_ok=True)
        return self._pass_and_clean(path, duration, tools, "descarga" if big else "copia pequeña", ckpt)

    def _pass_and_clean(self, path: Path, duration: float, tools, method: str, ckpt: Checkpoint | None) -> dict | None:
        """La pasada de frames y, si acaba, fuera la descarga (si se paró a mitad se queda para seguir)."""
        result = self._full_pass(path, duration, tools, method, ckpt)
        if result is not None:
            path.unlink(missing_ok=True)
        return result

    def _full_pass(self, path: Path, duration: float, tools, method: str, ckpt: Checkpoint | None = None) -> dict | None:
        from app.videos import frames as vf

        self._step_label("frames: calibrando minimapa, reloj y cámara")
        box, probe_frames = _probe_file(path, duration, tools)
        if box is None:
            return {"partida": False, "metodo": method, "probados": vf.PROBE_FRAMES}
        clock_ok = self._pick_clock(tools, probe_frames)
        self.threads = workers = hardware.threads()  # el 90 % de la CPU libre ahora mismo
        # El campeón, solo con frames de HUD de jugador (donde el reloj sale con las plantillas)
        player = [f for f in probe_frames if tools.clock.read(f) is not None]
        tools.ability_slots, probe_keys = tools.abilities.calibrate(player, workers) if tools.abilities and player else (None, [])
        cam = vf.camera_size([vf.white_mask(box.crop(f)) for f in probe_frames], box.size)
        label = f"frames ({workers} hilos)"
        self._step_label(label, 0.0)
        done, on_done = ckpt.frames(f"{path.name}|{box.x},{box.y},{box.size}|{cam}") if ckpt else ({}, None)
        collected = vf.sample_pass(str(path), box, cam, tools, duration, self._should_stop_frames,
                                   lambda p: self._step_label(label, p), workers, gpu=hardware.gpu() is not None,
                                   done=done, on_done=on_done)
        if self.stop_event.is_set():
            return None
        reader = tools.clock_reader
        keys = probe_keys + [c[1] for c in collected.champions]
        champion, champions = vf.vote_champion(keys), vf.champion_counts(keys)
        for item in ([champion] if champion else []) + champions:
            item["nombre"] = self._champ_name(item["clave"])
        out = vf.summarize(collected, cam, champion, clock_ok, f"{method} {collected.shape[0]}p", reader.hud if reader else None,
                           champions)
        out["habilidades"] = tools.ability_slots.public() if tools.ability_slots else None
        out["hud_lecturas"] = dict(reader.modes) if reader else {}
        out["hilos"] = workers
        out["gpu_decodifica"] = collected.gpu
        return out

    @staticmethod
    def _pick_clock(tools, frames) -> bool:
        """Escala del HUD para las plantillas y, de plan B, el OCR genérico; ¿se lee el reloj en algún frame?"""
        from app.videos import frames as vf

        tools.clock.calibrate(frames)
        tools.clock_reader = vf.ClockReader(tools.clock, vf.GenericClock.create())
        found = sum(1 for f in frames[:8] if tools.clock_reader.read(f) is not None)
        tools.clock_reader.modes.clear()  # las de la sonda no cuentan para el HUD del vídeo
        return found > 0

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
                return youtube.download(vid, folder, fmt, self.root, format_sort=youtube.VIDEO_SORT,
                                        progress_hooks=[self._download_hook(label)])
            except youtube.VideoError as exc:
                if exc.kind == "limite":
                    raise
                notes.append(f"descarga {fmt} fallida: {str(exc)[:160]}")
        return None

    def _download_hook(self, label: str):
        """Progreso de yt-dlp para la web («descargando… 45 %»)."""

        def hook(event: dict) -> None:
            total = event.get("total_bytes") or event.get("total_bytes_estimate")
            if event.get("status") == "downloading" and total:
                self._step_label(label, min(1.0, float(event.get("downloaded_bytes") or 0) / float(total)))

        return hook

    # ------------------------------------------------------------------ herramientas de imagen
    def _tools(self):
        if self.tools is not None:
            return self.tools

        from app.videos import frames as vf

        dd = self.svc.dd
        if not dd.version:
            dd.load()
        img_dir = Path(dd.cache_dir) / str(dd.version) / "img"
        map_path = self._cached(img_dir / "map11.png", f"{CDN}/{dd.version}/img/map/map11.png")
        map_bgr = vf.read_image(map_path)
        if map_bgr is None:
            raise OSError("map11.png dañado")
        spells = self._spell_icons(img_dir, str(dd.version))
        self.tools = vf.Tools(vf.MapMatcher(map_bgr), vf.Clock(vf.load_digits()), vf.Abilities(spells) if spells else None)
        return self.tools

    def _spell_icons(self, img_dir: Path, version: str) -> dict:
        """Los iconos de las 4 habilidades de cada campeón (Data Dragon, una vez por parche): ~700 ficheros pequeños."""
        from app.videos import frames as vf

        path = img_dir.parent / "championFull.json"
        try:
            data = json.loads(self._cached(path, f"{CDN}/{version}/data/en_US/championFull.json").read_text(encoding="utf-8"))["data"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            path.unlink(missing_ok=True)  # si quedó mal, se vuelve a bajar la próxima vez
            log.warning("Vídeos: sin habilidades de Data Dragon (no se reconocerá el campeón): %s", exc)
            return {}
        wanted = {key: [s["image"]["full"] for s in champ.get("spells") or []] for key, champ in data.items()}

        def fetch(name: str):
            try:
                return name, vf.read_image(self._cached(img_dir / "spell" / name, f"{CDN}/{version}/img/spell/{name}"))
            except OSError:
                return name, None

        with ThreadPoolExecutor(max_workers=8, thread_name_prefix="videos-iconos") as pool:
            images = dict(pool.map(fetch, sorted({n for names in wanted.values() for n in names})))
        return {
            key: [images[n] for n in names] for key, names in wanted.items()
            if len(names) == 4 and all(images.get(n) is not None for n in names)
        }

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

    def _pending(self) -> int:
        """Vídeos recogidos que aún no se han subido a GitHub (los cuenta la subida automática)."""
        from app import autopush

        auto = autopush.AutoPush.of(self.svc)
        return int((auto.state.get("videos_pending") or 0) if auto is not None else 0)

    def _push(self) -> None:
        """Sube lo recogido a GitHub con el resto de ``data/`` (en cola si ya hay una subida en marcha)."""
        try:
            from app import autopush

            auto = autopush.AutoPush.of(self.svc)
            if auto is None or autopush.every() <= 0 or auto.state.get("git_ok") is False or auto.busy:
                return
            if time.time() - float(auto.state.get("last_error_at") or 0) < autopush.RETRY_AFTER_S:
                return  # acaba de fallar (sin red, conflicto…): se reintenta más tarde, no en cada vuelta
            n = self._pending()
            started = auto.start()
            log.info("Vídeos: %s los %d vídeos recogidos.", "subiendo a GitHub" if started else "en cola para subir a GitHub", n)
        except Exception:  # subir no puede parar la recogida
            log.exception("Vídeos: no se pudo pedir la subida a GitHub")


# ---------------------------------------------------------------------- vídeos a medias
def work_dir(vid: str) -> Path:
    """Carpeta de trabajo de un vídeo: fuera del proyecto (en Windows suele estar en OneDrive) y la misma tras reiniciar."""
    return Path(tempfile.gettempdir()) / WORK_DIR / vid


def _downloaded(folder: Path, vid: str) -> Path | None:
    """El vídeo ya bajado entero en una vez anterior (yt-dlp solo quita el ``.part`` al terminar), si lo hay."""
    try:
        files = [p for p in folder.glob(f"{vid}.*") if ".part" not in p.name and p.suffix not in (".ytdl", ".tmp") and p.stat().st_size > 0]
    except OSError:
        return None
    return max(files, key=lambda p: p.stat().st_size) if files else None


def _jsonable(value):
    """Números de numpy y lo raro de los metadatos de yt-dlp, para guardarlos en ``estado.json``."""
    item = getattr(value, "item", None)
    return item() if callable(item) else str(value)


class Checkpoint:
    """Lo ya hecho de un vídeo (``estado.json`` en su carpeta de trabajo): metadatos, transcripción y los trozos de
    frames analizados. Si la app se reinicia a mitad, el vídeo sigue por ahí; lo descargado también se queda (yt-dlp
    no lo vuelve a bajar). Con otra versión del recolector se empieza de cero (las descargas siguen valiendo)."""

    def __init__(self, folder: Path, vid: str) -> None:
        self.path = folder / CHECKPOINT
        self.lock = threading.Lock()
        folder.mkdir(parents=True, exist_ok=True)
        fresh = {"id": vid, "version": VERSION, "frames_version": FRAMES_VERSION}
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        ok = isinstance(saved, dict) and all(saved.get(k) == v for k, v in fresh.items())
        self.data: dict = saved if ok else fresh
        self.resumed = ok and len(self.data) > len(fresh)

    def summary(self) -> str:
        parts = [name for key, name in (("info", "metadatos"), ("transcripcion", "transcripción")) if key in self.data]
        trozos = len((self.data.get("frames") or {}).get("trozos") or {})
        if trozos:
            parts.append(f"{trozos} trozos de frames")
        return ", ".join(parts) or "sin nada hecho"

    def get(self, key: str):
        with self.lock:
            return self.data.get(key)

    def put(self, key: str, value) -> None:
        with self.lock:
            self.data[key] = value
            self._save()

    def frames(self, key: str) -> tuple[dict, Callable]:
        """Los trozos ya hechos de la pasada de frames con ``key`` (fichero, minimapa y cámara: si cambian, no valen) y
        la función que guarda cada trozo terminado."""
        with self.lock:
            saved = self.data.get("frames") or {}
            if saved.get("clave") != key:
                self.data["frames"] = saved = {"clave": key, "trozos": {}}
            done = dict(saved["trozos"])

        def on_done(piece: str, part) -> None:
            with self.lock:
                self.data["frames"]["trozos"][piece] = part.to_json()
                self._save()

        return done, on_done

    def _save(self) -> None:
        try:
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, default=_jsonable), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            log.debug("Vídeos: no se pudo guardar el punto de control %s", self.path, exc_info=True)


def _clean_work(pending: set[str], older_than_s: float = WORK_KEEP_S) -> None:
    """Carpetas de trabajo de vídeos que ya no están en la cola, o que nadie retoma desde hace días: fuera."""
    base = Path(tempfile.gettempdir()) / WORK_DIR
    now = time.time()
    for path in base.glob("*"):
        with contextlib.suppress(OSError):
            if path.is_dir() and (path.name not in pending or now - path.stat().st_mtime > older_than_s):
                shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------------- frames
def _clean_temp(older_than_s: float = 600) -> None:
    """Restos de un vídeo a medias (la app se cerró descargando): se borran al volver a arrancar."""
    base = Path(tempfile.gettempdir())
    now = time.time()
    for path in base.glob(f"{TEMP_PREFIX}*"):
        with contextlib.suppress(OSError):
            if path.is_dir() and now - path.stat().st_mtime > older_than_s:
                shutil.rmtree(path, ignore_errors=True)


def _probe_file(path: Path, duration: float, tools):
    from app.videos import frames as vf

    reader = vf.Frames(str(path))
    try:
        return vf.probe(reader, duration, tools)
    finally:
        reader.close()


# ---------------------------------------------------------------------- resumen
def _metadata(vid: str, info: dict) -> dict:
    chapters = [[round(float(c.get("start_time") or 0), 1), c.get("title") or ""] for c in info.get("chapters") or []]
    return {
        "version": VERSION,
        "version_frames": FRAMES_VERSION,
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
        "idioma": _spoken(info),
        "idioma_youtube": info.get("language"),
        "etiquetas": (info.get("tags") or [])[:40],
        "descripcion": (info.get("description") or "")[:6000],
        "capitulos": chapters,
        "recogido": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _spoken(info: dict) -> str | None:
    from app.videos.transcript import original_language

    return original_language(info) or None


def _date(raw: str | None) -> str | None:
    if raw and len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw


def _outdated(data: dict) -> bool:
    """¿Hay que rehacerlo? Todo lo recogido con un ``VERSION`` anterior; con un ``FRAMES_VERSION`` anterior, solo los
    vídeos con partida o recogidos sin frames (los que son solo explicación no cambian)."""
    if int(data.get("version") or 0) < VERSION:
        return True
    frames = data.get("frames")
    return int(data.get("version_frames") or 0) < FRAMES_VERSION and (frames is None or bool(frames.get("partida")))


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
        "campeon": review.champion_names(frames) or None,
        "segundos_partida": frames.get("segundos_con_partida"),
        "reloj": len(frames.get("reloj") or []),
        "camara": len(frames.get("camara") or []),
        "avisos": len(data.get("avisos") or []),
        "revision": review.check(data),  # lo que parece mal, para mirarlo (ver app.videos.review)
    }


def _summary_text(data: dict) -> str:
    s = _summary(data)
    parts = [f"transcripción: {s['transcripcion'] or 'no'}"]
    if s["partida"]:
        parts.append(f"partida {s['segundos_partida']} s" + (f" con {s['campeon']}" if s["campeon"] else ""))
    return ", ".join(parts)
