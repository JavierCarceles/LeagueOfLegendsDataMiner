"""Grabación de la pantalla mientras juegas y análisis de la grabación al terminar.

Cada ``POLL_S`` se pregunta al cliente de LoL en qué fase está. Al entrar en partida (``InProgress``, desde la pantalla
de carga) se lanza ffmpeg grabando la pantalla; al salir se para y, sin partida en curso, la grabación pasa por el
mismo lector que los vídeos de YouTube (minimapa, reloj, cámara, campeón e iconos) y se calculan los vistazos de cámara
(:mod:`app.videos.camera`) frente a los junglas de elo alto.

- **Captura** (Windows): ``ddagrab`` (Desktop Duplication: también pantalla completa, casi sin coste) y, si no va,
  ``gdigrab``. Codifica con la gráfica (NVIDIA, AMD o Intel) y si no con ``libx264`` en su modo más rápido.
  Se prueban en orden y se recuerda la primera que funciona. ffmpeg va con prioridad baja: el juego va primero.
- **Ficheros**: ``data/grabaciones/<nombre>.mp4`` (MP4 fragmentado: si se corta de golpe sigue siendo legible) y al
  lado ``<nombre>.json`` con el análisis y la partida de Riot (``match_id``) para cruzarlo con su timeline. El vídeo
  es solo material de trabajo: **se borra en cuanto el análisis acaba** (o tras ``MAX_TRIES`` intentos fallidos) y
  queda solo el ``.json``, que se sube a GitHub con el resto de datos (el vídeo nunca: ``.gitignore``).
- **Interruptor**: ``meta`` ``recorder:enabled`` (encendido por defecto), desde la vista de repeticiones.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from app import lcu
from app.logbus import get_logger

log = get_logger("grabacion")

WINDOWS = os.name == "nt"
POLL_S = 5
FPS = 15
AVG_RATE, MAX_RATE, BUF_SIZE = "3M", "5M", "10M"
SCALE = "scale=-2:'min(1080,ih)'"  # 1440p o 4K se graban a 1080p (el lector de frames trabaja a esa escala)
IN_GAME = {"InProgress", "Reconnect"}
STARTUP_CHECK_S = 4  # si ffmpeg muere antes, esa forma de capturar no vale en este equipo
STOP_WAIT_S = 15
MAX_PENDING_VIDEOS = 20  # vídeos sin analizar como mucho (si el análisis no puede ir, no se llena el disco)
MAX_TRIES = 3  # intentos de análisis con error antes de borrar el vídeo
STEP_S = 1.0  # una muestra por segundo (el doble que los vídeos de YouTube: el vídeo se borra después)
MIN_VIDEO_S = 120  # menos que esto (remake, cierre en la carga) no se analiza
META_ENABLED = "recorder:enabled"
META_METHOD = "recorder:method"
VERSION = 2  # 2: HUD (vida, recurso, habilidades, invocadores, oro) y una muestra por segundo


def _encoders(nvidia: bool) -> list[tuple[str, list[str]]]:
    """(nombre, opciones del codificador), del que menos CPU gasta al que siempre está. Tope de ``MAX_RATE``: una
    partida de 30 min ocupa ~1 GB."""
    gop = ["-g", str(FPS * 4)]
    cap = ["-maxrate", MAX_RATE, "-bufsize", BUF_SIZE]
    out = [
        ("amf", ["-c:v", "h264_amf", "-quality", "speed", "-rc", "vbr_peak", "-b:v", AVG_RATE, *cap, *gop]),
        ("qsv", ["-c:v", "h264_qsv", "-preset", "veryfast", "-b:v", AVG_RATE, *cap, *gop]),
        ("x264", ["-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-crf", "30", *cap, *gop]),
    ]
    if nvidia:
        out.insert(0, ("nvenc", ["-c:v", "h264_nvenc", "-preset", "p2", "-rc", "vbr", "-cq", "32", "-b:v", AVG_RATE, *cap, *gop]))
    return out


def capture_methods(nvidia: bool) -> list[tuple[str, list[str]]]:
    """Formas de grabar, de la mejor a la más compatible: ``(nombre, argumentos de ffmpeg sin la salida)``.

    ``ddagrab`` da frames en la gráfica: nvenc y AMF los toman tal cual; el resto los baja a memoria (``hwdownload``)."""
    dda = ["-f", "lavfi", "-i", f"ddagrab=output_idx=0:framerate={FPS}:draw_mouse=0"]
    gdi = ["-f", "gdigrab", "-framerate", str(FPS), "-draw_mouse", "0", "-i", "desktop"]
    to_cpu = ["-vf", f"hwdownload,format=bgra,{SCALE},format=yuv420p"]
    methods = []
    for name, enc in _encoders(nvidia):
        methods.append((f"ddagrab+{name}", [*dda, *enc] if name in ("nvenc", "amf") else [*dda, *to_cpu, *enc]))
    for name, enc in _encoders(nvidia):
        methods.append((f"gdigrab+{name}", [*gdi, "-vf", f"{SCALE},format=yuv420p", *enc]))
    return methods


def ffmpeg_exe() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg  # noqa: PLC0415 - opcional: llega con requirements-videos.txt

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - sin el paquete o sin binario para este sistema
        return None


def _creation_flags() -> int:
    if not WINDOWS:
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)


class GameRecorder:
    def __init__(self, svc) -> None:
        self.svc = svc
        self.root = Path(svc.settings.data_dir) / "grabaciones"
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.proc: subprocess.Popen | None = None
        self.current: dict | None = None  # la grabación en curso
        self.analyzing: dict | None = None
        self.last_error: str | None = None
        self._phase: str | None = None
        self._gave_up = False  # en esta partida ya no se pudo grabar: no se reintenta hasta la siguiente
        self._deps_started = 0.0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ ajustes
    @property
    def enabled(self) -> bool:
        return self.svc.db.meta_get(META_ENABLED, "1") == "1"

    def set_enabled(self, value: bool) -> dict:
        self.svc.db.meta_set(META_ENABLED, "1" if value else "0")
        log.info("Grabación de partidas %s.", "activada" if value else "desactivada")
        return self.status()

    # ------------------------------------------------------------------ ciclo de vida
    def start(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            return
        self.root.mkdir(parents=True, exist_ok=True)
        self._recover()
        self._prune()
        self.thread = threading.Thread(target=self._loop, name="grabacion", daemon=True)
        self.thread.start()

    def shutdown(self) -> None:
        self.stop_event.set()
        self._stop_recording()

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self._tick()
            except Exception:
                log.exception("Grabación: fallo en la vuelta del vigilante")
            self.stop_event.wait(POLL_S)

    def _tick(self) -> None:
        phase = lcu.gameflow_phase() if lcu.available() else None
        self._phase = phase
        playing = phase in IN_GAME
        if self.current is not None:
            if not playing or (self.proc is not None and self.proc.poll() is not None):
                self._stop_recording()
            return
        if playing:
            if self.enabled and WINDOWS and not self._gave_up:
                self._start_recording()
            return
        self._gave_up = False
        self._analyze_pending()

    def in_game(self) -> bool:
        return self._phase in IN_GAME

    # ------------------------------------------------------------------ grabar
    def _start_recording(self) -> None:
        exe = ffmpeg_exe()
        if exe is None:
            self._gave_up = True
            self._ensure_ffmpeg()
            return
        name = time.strftime("%Y%m%d-%H%M%S")
        game = self._game_info()
        if game.get("game_id"):
            name += f"_{game['game_id']}"
        path = self.root / f"{name}.mp4"
        from app import hardware

        methods = capture_methods(hardware.gpu() is not None)
        remembered = self.svc.db.meta_get(META_METHOD)
        methods.sort(key=lambda m: m[0] != remembered)
        for method, args in methods:
            proc = self._launch(exe, args, path)
            if proc is None:
                continue
            self.proc = proc
            self.current = {"nombre": name, "inicio": time.time(), "metodo": method, "fichero": path.name, **game}
            self._save(name, {**self.current, "estado": "grabando"})
            if remembered != method:
                self.svc.db.meta_set(META_METHOD, method)
            self.last_error = None
            log.info("Grabación: empieza la partida, grabo la pantalla (%s) en %s", method, path.name)
            return
        self._gave_up = True
        self.last_error = "ffmpeg no pudo grabar la pantalla con ninguna de las formas probadas"
        log.warning("Grabación: %s (ver data/registro).", self.last_error)

    def _launch(self, exe: str, args: list[str], path: Path) -> subprocess.Popen | None:
        cmd = [exe, "-hide_banner", "-loglevel", "error", "-y", *args, "-an",
               "-movflags", "+frag_keyframe+empty_moov+default_base_moof", str(path)]
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                    creationflags=_creation_flags())
        except OSError as exc:
            log.warning("Grabación: no se pudo lanzar ffmpeg: %s", exc)
            return None
        try:
            proc.wait(STARTUP_CHECK_S)
        except subprocess.TimeoutExpired:
            threading.Thread(target=self._drain, args=(proc,), name="grabacion-ffmpeg", daemon=True).start()
            return proc
        err = (proc.stderr.read() if proc.stderr else b"").decode("utf-8", "replace").strip()
        log.info("Grabación: %s no funciona aquí: %s", " ".join(args[:4]), err[-300:])
        path.unlink(missing_ok=True)
        return None

    @staticmethod
    def _drain(proc: subprocess.Popen) -> None:
        """Lee lo que ffmpeg escribe en stderr (si no, se llena la tubería y se para) y lo deja en el registro."""
        if proc.stderr is None:
            return
        for line in proc.stderr:
            text = line.decode("utf-8", "replace").strip()
            if text:
                log.debug("ffmpeg: %s", text[:300])

    def _game_info(self) -> dict:
        try:
            session = lcu._get("/lol-gameflow/v1/session", timeout=2.0) or {}
        except Exception:  # noqa: BLE001 - sin datos de la partida, la grabación vale igual
            return {}
        data = session.get("gameData") or {}
        queue = (data.get("queue") or {}).get("id")
        game_id = data.get("gameId") or None
        return {"game_id": game_id, "cola": queue, "match_id": self._match_id(game_id)}

    def _match_id(self, game_id) -> str | None:
        """``EUW1_7412345678``: el id de Riot de la partida (con el servidor de tu perfil) para cruzarla con su timeline."""
        if not game_id:
            return None
        try:
            from app.coach import store

            platform = (store.get_profile(self.svc.db) or {}).get("platform")
        except Exception:  # noqa: BLE001 - sin perfil, queda el game_id
            platform = None
        return f"{platform.upper()}_{game_id}" if platform else None

    def _stop_recording(self) -> None:
        with self._lock:
            proc, current = self.proc, self.current
            self.proc, self.current = None, None
        if proc is not None and proc.poll() is None:
            try:
                if proc.stdin:
                    proc.stdin.write(b"q")
                    proc.stdin.flush()
                proc.wait(STOP_WAIT_S)
            except (OSError, subprocess.TimeoutExpired):
                proc.kill()
        if current is None:
            return
        current["fin"] = time.time()
        current["duracion"] = round(current["fin"] - current["inicio"])
        path = self.root / current["fichero"]
        ok = path.exists() and path.stat().st_size > 0 and current["duracion"] >= MIN_VIDEO_S
        current["estado"] = "pendiente" if ok else "corta"
        if not ok:
            self._drop_video(current)
        self._save(current["nombre"], current)
        log.info("Grabación: partida terminada (%d min grabados).", current["duracion"] // 60)
        self._prune()

    # ------------------------------------------------------------------ analizar
    def _analyze_pending(self) -> None:
        if self.stop_event.is_set():
            return
        for rec in self.recordings():
            if rec.get("estado") == "pendiente" and (self.root / rec["fichero"]).exists():
                self._analyze(rec)
                return

    def _analyze(self, rec: dict) -> None:
        from app.videos import deps

        if not deps.ensure_base():
            return
        from app.videos.camera import glances
        from app.videos.collector import analyze_file

        videos = self.svc.videos
        self.analyzing = {"nombre": rec["nombre"], "paso": "preparando"}
        try:
            tools = videos.frame_tools()

            def progress(label: str, frac: float | None = None) -> None:
                self.analyzing = {"nombre": rec["nombre"], "paso": label, "progreso": frac}

            def should_stop() -> bool:
                if self.stop_event.is_set():
                    return True
                phase = lcu.gameflow_phase() if lcu.available() else None
                return phase in IN_GAME  # vuelves a jugar: se deja para después (y se graba la nueva)

            out = analyze_file(self.root / rec["fichero"], float(rec["duracion"]), tools, "grabación", should_stop,
                               progress, videos._champ_name, step=STEP_S)
            if out is None:
                return
            from app.videos.hud import habits

            rec.update(frames=out, vistazos=glances(out), habitos=habits(out.get("hud_datos")),
                       estado="analizada" if out.get("partida") else "sin partida", version=VERSION, analizada=time.time())
            self._drop_video(rec)
            self._save(rec["nombre"], rec)
            log.info("Grabación: %s analizada (%s); borrado el vídeo, quedan los datos.", rec["nombre"], rec["estado"])
        except Exception as exc:
            rec["intentos"] = int(rec.get("intentos") or 0) + 1
            rec["error"] = str(exc)[:300]
            if rec["intentos"] >= MAX_TRIES:
                rec["estado"] = "error"
                self._drop_video(rec)
            self._save(rec["nombre"], rec)
            log.exception("Grabación: no se pudo analizar %s (intento %d de %d)", rec["nombre"], rec["intentos"], MAX_TRIES)
        finally:
            self.analyzing = None

    # ------------------------------------------------------------------ ficheros
    def _save(self, name: str, data: dict) -> None:
        path = self.root / f"{name}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)

    def recordings(self) -> list[dict]:
        out = []
        for path in sorted(self.root.glob("*.json"), reverse=True):
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return out

    def video_path(self, name: str) -> Path | None:
        rec = next((r for r in self.recordings() if r.get("nombre") == name), None)
        path = self.root / rec["fichero"] if rec else None
        return path if path is not None and path.exists() else None

    def _recover(self) -> None:
        """Una grabación que quedó «grabando» (la app se cerró en mitad de la partida): lo grabado se analiza igual."""
        for rec in self.recordings():
            if rec.get("estado") != "grabando":
                continue
            path = self.root / rec["fichero"]
            end = path.stat().st_mtime if path.exists() else rec["inicio"]
            rec.update(fin=end, duracion=round(end - rec["inicio"]))
            rec["estado"] = "pendiente" if rec["duracion"] >= MIN_VIDEO_S else "corta"
            if rec["estado"] == "corta":
                self._drop_video(rec)
            self._save(rec["nombre"], rec)

    def _drop_video(self, rec: dict) -> None:
        """Fuera el vídeo: ya se sacó lo que sirve (o no se puede sacar)."""
        path = self.root / rec["fichero"]
        try:
            path.unlink(missing_ok=True)
            rec["video_borrado"] = True
        except OSError:
            log.debug("No se pudo borrar %s", path, exc_info=True)

    def _prune(self) -> None:
        """Vídeos sueltos: sin su ``.json`` o ya analizados (se borran) y, si el análisis no puede ir, como mucho los
        ``MAX_PENDING_VIDEOS`` más recientes (para no llenar el disco)."""
        wanted = {r["fichero"]: r for r in self.recordings() if r.get("estado") in ("grabando", "pendiente")}
        keep = sorted(wanted, reverse=True)[:MAX_PENDING_VIDEOS]
        for path in self.root.glob("*.mp4"):
            if path.name in keep or (self.current and path.name == self.current.get("fichero")):
                continue
            try:
                path.unlink()
                log.info("Grabación: borro el vídeo %s (no hace falta: queda su análisis).", path.name)
            except OSError:
                log.debug("No se pudo borrar %s", path, exc_info=True)
            rec = wanted.get(path.name)
            if rec is not None:  # pendiente que no cabe: se queda sin analizar
                rec.update(estado="sin analizar", video_borrado=True)
                self._save(rec["nombre"], rec)

    def _ensure_ffmpeg(self) -> None:
        from app.videos import deps

        self.last_error = "falta ffmpeg: se instala con las herramientas de vídeo y graba desde la próxima partida"
        if time.time() - self._deps_started < 600:
            return
        self._deps_started = time.time()
        threading.Thread(target=deps.ensure_base, name="grabacion-deps", daemon=True).start()

    # ------------------------------------------------------------------ estado
    def status(self) -> dict:
        current = dict(self.current) if self.current else None
        if current:
            current["segundos"] = round(time.time() - current["inicio"])
        return {
            "activo": self.enabled,
            "disponible": WINDOWS,
            "grabando": current,
            "analizando": dict(self.analyzing) if self.analyzing else None,
            "ultimo_error": self.last_error,
            "carpeta": str(self.root),
        }
