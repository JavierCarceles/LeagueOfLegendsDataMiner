"""Datos sacados de los frames de un vídeo de partida, sin guardar ninguna imagen.

1. **Minimapa**: se busca el mapa de Data Dragon a varias escalas en las esquinas de abajo de ~16 frames repartidos.
   Si no aparece en varios, el vídeo no es una partida (charla, dibujos…) y no se descarga nada más.
2. **Tramos con partida**: en cada muestra, el parecido del recorte del minimapa con el mapa.
3. **Reloj de la partida** (OCR con las plantillas de ``assets/digitos.png``): alinea lo que se dice con el minuto.
4. **Cámara**: el recuadro blanco del minimapa, es decir, hacia dónde mira el jugador (casi siempre donde está).
5. **Campeón del jugador**: el retrato grande del HUD, por votos en varios frames.
6. **Iconos de campeón** del minimapa con el color del aro (aliado azul / enemigo rojo); identificarlos es para
   después (a 1080p los retratos miden ~20 px).

Las coordenadas se guardan como las de la app y Riot (de -120 a 14870/14980; la base azul abajo a la izquierda).
"""

from __future__ import annotations

import math
import os
import re
import threading
from collections import Counter
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

ASSETS = Path(__file__).with_name("assets")
MAP_MIN = -120
MAP_MAX_X = 14870
MAP_MAX_Y = 14980

PROBE_FRAMES = 16
WORK_HEIGHT = 360  # la búsqueda del minimapa se hace a 360p (rápida) y se afina a resolución completa
# Parecido con el mapa: 0,55-0,71 con minimapa de verdad (HUD de jugador y retransmisiones); un trozo de la cámara del
# coach llegó a 0,355 y se tomó por partida (25-11-2026).
MAP_SCORE = 0.45  # mínimo para aceptar un minimapa en la búsqueda
PRESENT_SCORE = 0.42  # y para dar una muestra por «hay partida»
MIN_PROBES = 3  # frames de la sonda con minimapa para tratar el vídeo como partida
STEP_S = 2.0  # una muestra cada 2 s
CLOCK_EVERY = 5  # reloj cada 5 muestras (10 s)
ICONS_EVERY = 2  # iconos del minimapa cada 2 muestras (4 s)
CAMERA_COVER = 0.40  # parte del contorno del recuadro que tiene que verse blanca
CAMERA_SCORE = 0.28  # contorno blanco menos interior blanco
DIGIT_SCORE = 0.55
# Campeón del jugador por los iconos de sus 4 habilidades, cada uno en su casilla (media de las 3 que más se parecen):
# Vi y Qiyana daban 0,97 y el segundo campeón 0,44; con 3 habilidades en enfriamiento, 0,51 (se descarta esa lectura).
CHAMPION_SCORE = 0.75
CHAMPION_MARGIN = 0.15  # ventaja mínima sobre el segundo campeón
CHAMPION_EVERY = 5  # campeón cada 5 muestras (10 s), solo con HUD de jugador (cada lectura son ~20 ms)
ABILITY_SIZES = tuple(range(24, 62, 2))  # lado del icono de habilidad a 1080p según la escala del HUD
ABILITY_INNER = 0.7  # el centro del icono: fuera quedan el marco, el enfriamiento y el coste
SLOT_SCORE = 0.85  # parecido de un icono para fijar con él las casillas
SLOT_PITCH = (1.0, 1.6)  # de una casilla a la siguiente, en lados de icono (icono más hueco)
SLOT_SLACK_PX = 3  # holgura al buscar cada icono en su casilla
CALIBRATION_FRAMES = 4  # frames de HUD de jugador de la sonda para buscar las casillas (~2 s cada uno)
MAX_GAME_S = 80 * 60  # un reloj por encima es una mala lectura
CLOCK_TOLERANCE_S = 5  # lo que puede bailar el desfase reloj − vídeo entre lecturas sin que sea un salto
GENERIC_CLOCK_EVERY_S = 10


@dataclass
class Box:
    x: int
    y: int
    size: int
    corner: str
    score: float

    def crop(self, frame: np.ndarray) -> np.ndarray:
        return frame[self.y:self.y + self.size, self.x:self.x + self.size]

    def to_game(self, u: float, v: float) -> tuple[int, int]:
        """Píxel del minimapa → coordenadas del juego (y invertida)."""
        gx = MAP_MIN + (u / self.size) * (MAP_MAX_X - MAP_MIN)
        gy = MAP_MAX_Y - (v / self.size) * (MAP_MAX_Y - MAP_MIN)
        return round(gx), round(gy)

    def public(self, frame_shape: tuple[int, ...]) -> dict:
        return {"x": self.x, "y": self.y, "lado": self.size, "esquina": self.corner, "parecido": round(self.score, 3),
                "resolucion": [int(frame_shape[1]), int(frame_shape[0])]}


# ---------------------------------------------------------------------- lectura de frames
def read_image(path: Path, flags: int = cv2.IMREAD_COLOR) -> np.ndarray | None:
    """``cv2.imread`` falla en Windows si la ruta tiene tildes (p. ej. el usuario «Iván»): se lee con Python."""
    try:
        raw = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    return cv2.imdecode(raw, flags) if raw.size else None


def _ascii_path(path: str) -> str:
    """Ruta corta 8.3 de Windows (sin tildes) para FFmpeg si la normal tiene caracteres raros."""
    if os.name != "nt" or path.isascii():
        return path
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.kernel32.GetShortPathNameW(path, buffer, 1024):
            return buffer.value
    except (AttributeError, OSError):
        pass
    return path


class Frames:
    """Frames por segundo de vídeo de un fichero o de un enlace directo (OpenCV con FFmpeg).

    ``threads``: hilos de FFmpeg para decodificar; ``gpu``: pedir decodificación por la gráfica (D3D11 en Windows). Si
    esta versión de OpenCV no puede, decodifica con la CPU (``self.gpu`` dice qué se usa de verdad)."""

    def __init__(self, source: str, threads: int | None = None, gpu: bool = False) -> None:
        params: list[int] = []
        if threads:
            params += [cv2.CAP_PROP_N_THREADS, int(threads)]
        if gpu:
            params += [cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_ANY]
        path = _ascii_path(str(source))
        self.cap = cv2.VideoCapture(path, cv2.CAP_FFMPEG, params)
        if not self.cap.isOpened() and params:
            self.cap = cv2.VideoCapture(path, cv2.CAP_FFMPEG)
        if not self.cap.isOpened():
            raise OSError(f"OpenCV no puede abrir el vídeo ({str(source)[:80]}…)")
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.gpu = bool(gpu and self.cap.get(cv2.CAP_PROP_HW_ACCELERATION))

    def at(self, t: float) -> np.ndarray | None:
        self.cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000)
        ok, frame = self.cap.read()
        return frame if ok else None

    def every(self, step: float, start: float = 0.0, end: float | None = None) -> Iterator[tuple[float, np.ndarray]]:
        """Un frame cada ``step`` segundos, leyendo en orden (decodificar en orden es lo más rápido)."""
        self.cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, start) * 1000)
        wanted = start
        while self.cap.grab():
            t = self.cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
            if end is not None and t > end:
                return
            if t + 1e-3 < wanted:
                continue
            ok, frame = self.cap.retrieve()
            if ok:
                yield t, frame
            while wanted <= t:
                wanted += step

    def close(self) -> None:
        self.cap.release()


# ---------------------------------------------------------------------- minimapa
class MapMatcher:
    """Busca el minimapa comparando con el mapa de la Grieta (``map11.png`` de Data Dragon)."""

    def __init__(self, map_bgr: np.ndarray) -> None:
        self.gray = cv2.cvtColor(map_bgr, cv2.COLOR_BGR2GRAY)
        self._sizes: dict[int, np.ndarray] = {}
        self.ref = self._template(96).astype(np.float32)

    def _template(self, size: int) -> np.ndarray:
        if size not in self._sizes:
            self._sizes[size] = cv2.resize(self.gray, (size, size), interpolation=cv2.INTER_AREA)
        return self._sizes[size]

    def locate(self, frame: np.ndarray) -> Box | None:
        """El mejor minimapa en las dos esquinas de abajo (a 360p) y afinado a resolución completa."""
        h, w = frame.shape[:2]
        scale = WORK_HEIGHT / h
        small = cv2.cvtColor(cv2.resize(frame, (max(1, round(w * scale)), WORK_HEIGHT), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        best: Box | None = None
        for corner in ("derecha", "izquierda"):
            found = self._search_corner(small, corner)
            if found and (best is None or found.score > best.score):
                best = found
        if best is None or best.score < MAP_SCORE:
            return None
        rough = Box(round(best.x / scale), round(best.y / scale), round(best.size / scale), best.corner, best.score)
        return self.refine(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), rough)

    def _search_corner(self, small: np.ndarray, corner: str) -> Box | None:
        sh, sw = small.shape
        top = int(sh * 0.35)
        x0 = int(sw * 0.55) if corner == "derecha" else 0
        x1 = sw if corner == "derecha" else int(sw * 0.45)
        region = small[top:, x0:x1]
        best: Box | None = None
        for size in range(int(sh * 0.16), int(sh * 0.46), 2):
            if size >= region.shape[0] or size >= region.shape[1]:
                break
            res = cv2.matchTemplate(region, self._template(size), cv2.TM_CCOEFF_NORMED)
            _, score, _, loc = cv2.minMaxLoc(res)
            if best is None or score > best.score:
                best = Box(loc[0] + x0, loc[1] + top, size, corner, float(score))
        return best

    def refine(self, gray: np.ndarray, rough: Box, spread: int = 6) -> Box:
        best = rough
        pad = spread + 2
        y0, x0 = max(0, rough.y - pad), max(0, rough.x - pad)
        for size in range(max(16, rough.size - spread), rough.size + spread + 1):
            region = gray[y0:rough.y + rough.size + pad, x0:rough.x + rough.size + pad]
            if size > region.shape[0] or size > region.shape[1]:
                continue
            res = cv2.matchTemplate(region, self._template(size), cv2.TM_CCOEFF_NORMED)
            _, score, _, loc = cv2.minMaxLoc(res)
            if score > best.score:
                best = Box(loc[0] + x0, loc[1] + y0, size, rough.corner, float(score))
        return best

    def presence(self, frame: np.ndarray, box: Box) -> float:
        """Parecido del recorte con el mapa (~0,5 con minimapa, casi 0 sin él)."""
        crop = box.crop(frame)
        if crop.shape[0] != box.size or crop.shape[1] != box.size:
            return 0.0
        gray = cv2.resize(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), (96, 96), interpolation=cv2.INTER_AREA).astype(np.float32)
        a = gray - gray.mean()
        b = self.ref - self.ref.mean()
        den = float(np.sqrt((a * a).sum() * (b * b).sum()))
        return float((a * b).sum() / den) if den else 0.0


def consensus(boxes: list[Box]) -> Box | None:
    """El minimapa en el que coinciden más frames de la sonda (la mediana del grupo más grande)."""
    if len(boxes) < MIN_PROBES:
        return None
    groups: list[list[Box]] = []
    for box in sorted(boxes, key=lambda b: -b.score):
        tol = max(4, box.size * 0.04)
        for group in groups:
            ref = group[0]
            if ref.corner == box.corner and abs(ref.x - box.x) <= tol and abs(ref.y - box.y) <= tol and abs(ref.size - box.size) <= tol:
                group.append(box)
                break
        else:
            groups.append([box])
    group = max(groups, key=len)
    if len(group) < MIN_PROBES:
        return None
    return Box(_median([b.x for b in group]), _median([b.y for b in group]), _median([b.size for b in group]), group[0].corner,
               float(np.median([b.score for b in group])))


def _median(values: list[int]) -> int:
    return round(float(np.median(values)))


# ---------------------------------------------------------------------- cámara
def white_mask(minimap: np.ndarray) -> np.ndarray:
    """Píxeles blancos (las líneas del recuadro miden 1 px: se engordan 1 px para tolerar la compresión del vídeo)."""
    hsv = cv2.cvtColor(minimap, cv2.COLOR_BGR2HSV)
    mask = ((hsv[..., 1] < 70) & (hsv[..., 2] > 170)).astype(np.uint8)
    return cv2.dilate(mask, np.ones((3, 3), np.uint8)).astype(np.float32)


def _outline(w: int, h: int, thick: int) -> tuple[np.ndarray, np.ndarray]:
    """El contorno del recuadro y su interior (que no debe ser blanco: así las letras de una marca de agua no cuentan)."""
    edge = np.zeros((h, w), np.float32)
    edge[:thick, :] = 1
    edge[-thick:, :] = 1
    edge[:, :thick] = 1
    edge[:, -thick:] = 1
    inner = np.zeros((h, w), np.float32)
    m = thick + 3
    if h > 2 * m and w > 2 * m:
        inner[m:-m, m:-m] = 1
    return edge, inner


def find_camera(mask: np.ndarray, w: int, h: int) -> tuple[float, float, float, float]:
    """Centro del recuadro de cámara de ``w``×``h`` (puede salirse del minimapa), su puntuación y cuánto contorno se ve."""
    edge, inner = _outline(w, h, 1)
    padded = cv2.copyMakeBorder(mask, h, h, w, w, cv2.BORDER_CONSTANT, value=0)
    cover = cv2.matchTemplate(padded, edge, cv2.TM_CCORR) / float(edge.sum())
    filled = cv2.matchTemplate(padded, inner, cv2.TM_CCORR) / max(1.0, float(inner.sum()))
    score = cover - 1.5 * filled
    _, best, _, loc = cv2.minMaxLoc(score)
    return loc[0] - w + w / 2, loc[1] - h + h / 2, float(best), float(cover[loc[1], loc[0]])


def camera_size(masks: list[np.ndarray], size: int) -> tuple[int, int] | None:
    """Tamaño del recuadro de cámara en este vídeo (fijo: depende de la pantalla del jugador)."""
    if not masks:
        return None
    best: tuple[float, int, int] | None = None
    for frac in np.arange(0.19, 0.33, 0.01):
        w = max(8, round(size * frac))
        for ratio in (1.6, 1.7, 1.8, 1.9):
            h = max(6, round(w / ratio))
            scores = sorted((find_camera(m, w, h)[2] for m in masks), reverse=True)
            score = float(np.mean(scores[: max(1, len(scores) * 2 // 3)]))
            if best is None or score > best[0]:
                best = (score, w, h)
    if best is None or best[0] < CAMERA_SCORE:
        return None
    return best[1], best[2]


# ---------------------------------------------------------------------- reloj
def load_digits(path: Path = ASSETS / "digitos.png") -> list[np.ndarray]:
    strip = read_image(path, cv2.IMREAD_GRAYSCALE)
    if strip is None:
        raise OSError(f"Faltan las plantillas de dígitos ({path})")
    width = strip.shape[1] // 10
    return [strip[:, i * width:(i + 1) * width] for i in range(10)]


@dataclass
class Clock:
    """OCR del reloj del HUD (arriba a la derecha): plantillas de 1080p escaladas a la resolución y escala del HUD."""

    digits: list[np.ndarray]
    scale: float = 1.0
    _cache: dict = field(default_factory=dict)

    def _templates(self, factor: float) -> list[np.ndarray]:
        key = round(factor, 3)
        if key not in self._cache:
            self._cache[key] = [
                cv2.resize(d, (max(4, round(d.shape[1] * factor)), max(6, round(d.shape[0] * factor))), interpolation=cv2.INTER_AREA)
                for d in self.digits
            ]
        return self._cache[key]

    def read(self, frame: np.ndarray, hud: float | None = None) -> int | None:
        """Segundos de partida (``mm:ss``) o ``None`` si no se lee con seguridad."""
        h, w = frame.shape[:2]
        factor = (h / 1080) * (hud if hud is not None else self.scale)
        region = frame[0:max(8, int(h * 0.045)), int(w * 0.86):w].max(axis=2)
        tpls = self._templates(factor)
        if region.shape[0] < tpls[0].shape[0] or region.shape[1] < tpls[0].shape[1] * 4:
            return None
        peaks = _digit_peaks(region, tpls)
        return _parse_clock(peaks, tpls[0].shape[1])

    def calibrate(self, frames: list[np.ndarray]) -> bool:
        """Escala del HUD que mejor lee el reloj en los frames de la sonda."""
        best = (0, 1.0)
        for hud in (0.8, 0.9, 1.0, 1.1, 1.2, 1.3):
            ok = sum(1 for f in frames if self.read(f, hud) is not None)
            if ok > best[0]:
                best = (ok, hud)
        self.scale = best[1]
        return best[0] > 0


def _digit_peaks(region: np.ndarray, tpls: list[np.ndarray]) -> list[tuple[int, int, int, float]]:
    """``(x, y, dígito, parecido)`` de izquierda a derecha, sin solaparse."""
    tw = tpls[0].shape[1]
    maps = np.stack([cv2.matchTemplate(region, t, cv2.TM_CCOEFF_NORMED) for t in tpls])  # dígito × fila × columna
    best_digit = maps.argmax(axis=0)
    best_score = maps.max(axis=0)
    rows = best_score.argmax(axis=0)
    cols = np.arange(best_score.shape[1])
    col_score = best_score[rows, cols]
    peaks: list[tuple[int, int, int, float]] = []
    for x in np.argsort(-col_score):
        score = float(col_score[x])
        if score < DIGIT_SCORE:
            break
        if any(abs(int(x) - p[0]) < tw * 0.75 for p in peaks):
            continue
        y = int(rows[x])
        peaks.append((int(x), y, int(best_digit[y, x]), score))
    return sorted(peaks)


def _parse_clock(peaks: list[tuple[int, int, int, float]], tw: int) -> int | None:
    """Los 4 últimos dígitos con el hueco de los dos puntos en medio: ``mm:ss``."""
    if len(peaks) < 4:
        return None
    a, b, c, d = peaks[-4:]
    if max(p[1] for p in (a, b, c, d)) - min(p[1] for p in (a, b, c, d)) > max(2, tw * 0.3):
        return None
    gap1, gap2, gap3 = b[0] - a[0], c[0] - b[0], d[0] - c[0]
    normal = (gap1 + gap3) / 2
    if not (tw * 0.7 <= gap1 <= tw * 1.5 and tw * 0.7 <= gap3 <= tw * 1.5) or gap2 < normal * 1.15 or gap2 > normal * 2.6:
        return None
    minutes = a[2] * 10 + b[2]
    seconds = c[2] * 10 + d[2]
    if seconds >= 60 or minutes * 60 + seconds > MAX_GAME_S:
        return None
    return minutes * 60 + seconds


class GenericClock:
    """Plan B del reloj con OCR de verdad (RapidOCR): las retransmisiones (LPL, LCK, Worlds…) y las repeticiones lo
    ponen arriba en el centro, cada una con su tipografía. Más lento (~0,3 s por lectura) pero vale para cualquiera."""

    REGIONS = (("espectador", 0.0, 0.14, 0.35, 0.65), ("jugador", 0.0, 0.06, 0.82, 1.0))
    _local = threading.local()  # un motor por hilo: con el análisis en paralelo, cada hilo lee su reloj

    @classmethod
    def create(cls) -> GenericClock | None:
        """``None`` si RapidOCR no está instalado o no arranca (el vídeo se recoge igual, sin reloj)."""
        try:
            cls._engine()
        except Exception:  # noqa: BLE001 - sin OCR genérico no hay reloj de retransmisiones, nada más
            return None
        return cls()

    @classmethod
    def _engine(cls):
        engine = getattr(cls._local, "engine", None)
        if engine is None:
            import logging

            from rapidocr import RapidOCR

            # 2 hilos por lectura: el paralelismo lo ponen los hilos del análisis (si no, se pisan todos)
            engine = RapidOCR(params={"EngineConfig.onnxruntime.intra_op_num_threads": 2})
            logging.getLogger("RapidOCR").setLevel(logging.ERROR)  # «text detection result is empty» en cada fotograma sin texto
            cls._local.engine = engine
        return engine

    def read(self, frame: np.ndarray, region) -> int | None:
        _, y0, y1, x0, x1 = region
        h, w = frame.shape[:2]
        crop = frame[int(h * y0):max(int(h * y0) + 8, int(h * y1)), int(w * x0):int(w * x1)]
        result = self._engine()(crop)
        best: tuple[float, int] | None = None
        for text, score, box in zip(result.txts or (), result.scores or (), result.boxes if result.boxes is not None else ()):
            seconds = parse_time(str(text))
            if seconds is None or float(score) < 0.8:
                continue
            off_center = abs(float(np.mean([p[0] for p in box])) - crop.shape[1] / 2)
            if best is None or off_center < best[0]:
                best = (off_center, seconds)
        return best[1] if best else None



class ClockReader:
    """El reloj de cada frame: plantillas del HUD de jugador (rápido) y, si no salen, OCR genérico arriba en el centro
    (retransmisiones, repeticiones) o a la derecha. Un mismo vídeo mezcla repeticiones, partidas y la herramienta de
    práctica: se decide frame a frame. Cuenta de qué HUD salió cada lectura."""

    def __init__(self, clock: Clock, generic: GenericClock | None) -> None:
        self.clock = clock
        self.generic = generic
        self.modes: Counter = Counter()
        self._lock = threading.Lock()

    def read(self, frame: np.ndarray) -> int | None:
        seconds, mode = self.clock.read(frame), "jugador"
        if seconds is None and self.generic is not None:
            for region in GenericClock.REGIONS:
                seconds, mode = self.generic.read(frame, region), region[0]
                if seconds is not None:
                    break
        if seconds is not None:
            with self._lock:
                self.modes[mode] += 1
        return seconds

    @property
    def hud(self) -> str | None:
        """El HUD con más lecturas: «jugador» o «espectador»."""
        with self._lock:
            return self.modes.most_common(1)[0][0] if self.modes else None


def parse_time(text: str) -> int | None:
    """«04:07» o «19.09» → segundos (minutos y segundos de una partida)."""
    match = re.fullmatch(r"(\d{1,2})[:.](\d{2})", text.strip())
    if not match or int(match.group(2)) >= 60:
        return None
    seconds = int(match.group(1)) * 60 + int(match.group(2))
    return seconds if seconds <= MAX_GAME_S else None


# ---------------------------------------------------------------------- campeón del jugador
@dataclass(frozen=True)
class Slots:
    """Dónde están las 4 habilidades en el vídeo en curso, en la zona de :meth:`Abilities.region`: lado del icono (en
    píxeles del vídeo), fila y columna de cada una (Q, W, E, R), para el centro del icono que se compara."""

    side: int
    y: int
    xs: tuple[int, ...]

    def public(self) -> dict:
        return {"lado": self.side, "fila": self.y, "columnas": list(self.xs)}


class Abilities:
    """Las 4 habilidades del HUD de jugador (abajo, en el centro) comparadas con los iconos de Data Dragon.

    El retrato no vale: cambia con el aspecto (la Vi de Arcane de un vídeo no se parecía al icono base y ganaban Ambessa
    o Pantheon por poco). Los iconos de las habilidades casi nunca cambian. Una vez por vídeo se buscan las casillas
    (tamaño y posición, que dependen de la escala del HUD); después cada casilla se compara solo con la habilidad que le
    toca a cada campeón: rápido y los demás campeones se quedan muy lejos. En gris (separa igual y va el doble de rápido).
    """

    def __init__(self, spells: dict[str, list[np.ndarray]]) -> None:
        self.names = [key for key, icons in spells.items() if len(icons) == 4]
        self._icons = [[cv2.cvtColor(icon, cv2.COLOR_BGR2GRAY) for icon in spells[key]] for key in self.names]
        self._cache: dict[int, list[list[np.ndarray]]] = {}
        self._lock = threading.Lock()

    def _templates(self, side: int) -> list[list[np.ndarray]]:
        with self._lock:
            if side not in self._cache:
                inner = max(6, round(side * ABILITY_INNER))
                off = (side - inner) // 2
                self._cache[side] = [
                    [cv2.resize(icon, (side, side), interpolation=cv2.INTER_AREA)[off:off + inner, off:off + inner] for icon in row]
                    for row in self._icons
                ]
            return self._cache[side]

    @staticmethod
    def region(frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        return cv2.cvtColor(frame[int(h * 0.82):h, int(w * 0.30):int(w * 0.62)], cv2.COLOR_BGR2GRAY)

    def read(self, frame: np.ndarray, slots: Slots) -> tuple[str, float] | None:
        """El campeón de un frame con las casillas del vídeo, solo si sale claro: ``(clave, parecido)``."""
        region = self.region(frame)
        templates = self._templates(slots.side)
        inner = templates[0][0].shape[0]
        m = SLOT_SLACK_PX
        scores = np.zeros((len(self.names), 4))
        for k, x in enumerate(slots.xs):
            window = region[max(0, slots.y - m):slots.y + inner + m, max(0, x - m):x + inner + m]
            if window.shape[0] < inner or window.shape[1] < inner:
                continue
            for c, row in enumerate(templates):
                scores[c, k] = cv2.minMaxLoc(cv2.matchTemplate(window, row[k], cv2.TM_CCOEFF_NORMED))[1]
        top = np.sort(scores, axis=1)[:, 1:].mean(axis=1)  # las 3 casillas que más se parecen
        order = np.argsort(-top)
        best = float(top[order[0]])
        second = float(top[order[1]]) if len(order) > 1 else 0.0
        return (self.names[order[0]], best) if sure(best, best - second) else None

    def _scan(self, region: np.ndarray, side: int) -> tuple[np.ndarray, np.ndarray] | None:
        """Cada icono de ese lado buscado en toda la zona: parecido ``[campeón, casilla]`` y posición ``[campeón, casilla]``."""
        templates = self._templates(side)
        if templates[0][0].shape[0] > region.shape[0] or templates[0][0].shape[1] > region.shape[1]:
            return None
        scores = np.zeros((len(self.names), 4))
        locs = np.zeros((len(self.names), 4, 2), int)
        for c, row in enumerate(templates):
            for k, tpl in enumerate(row):
                _, scores[c, k], _, locs[c, k] = cv2.minMaxLoc(cv2.matchTemplate(region, tpl, cv2.TM_CCOEFF_NORMED))
        return scores, locs

    def _add_scans(self, merged: dict[int, tuple[np.ndarray, np.ndarray]], frame: np.ndarray, workers: int) -> None:
        """Busca los iconos de todos los tamaños en un frame y se queda lo mejor de cada uno (con los frames anteriores)."""
        region = self.region(frame)
        sides = sorted({max(8, round(size * frame.shape[0] / 1080)) for size in ABILITY_SIZES})
        if workers <= 1:
            scans = [self._scan(region, side) for side in sides]
        else:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="videos-campeon") as pool:
                scans = list(pool.map(lambda side: self._scan(region, side), sides))
        for side, scan in zip(sides, scans, strict=True):
            if scan is not None:
                merged[side] = _keep_best(merged.get(side), scan)

    def _pick(self, merged: dict[int, tuple[np.ndarray, np.ndarray]], frames: list[np.ndarray]) -> tuple[Slots | None, bool]:
        """Las mejores casillas posibles y si ya leen claro el campeón en alguno de ``frames``."""
        fits = [slots for _, slots in sorted(_fits(merged), key=lambda f: -f[0])[:5]]
        for slots in fits:
            if any(self.read(frame, slots) for frame in frames):
                return slots, True
        return (fits[0] if fits else None), False

    def calibrate(self, frames: list[np.ndarray], workers: int = 1) -> tuple[Slots | None, list[str]]:
        """Las casillas con los frames de HUD de jugador de la sonda: cada icono se busca en toda la zona con cada tamaño
        y se queda lo mejor de cada casilla entre frames (en uno hay habilidades en enfriamiento o sin aprender y en otro
        no). Valen las casillas que dan los iconos claros de un campeón en fila y a distancia de casilla; mejor si en algún
        frame ya se lee ese campeón claro. Devuelve también los campeones que se leen claros en todos los frames."""
        merged: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        slots: Slots | None = None
        for i, frame in enumerate(frames[:CALIBRATION_FRAMES]):
            self._add_scans(merged, frame, workers)
            slots, clear = self._pick(merged, frames[:i + 1])
            if clear:
                break
        if slots is None:
            return None, []
        return slots, [found[0] for frame in frames if (found := self.read(frame, slots))]


def _keep_best(old: tuple[np.ndarray, np.ndarray] | None, new: tuple[np.ndarray, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Lo mejor de cada icono entre frames (con su posición)."""
    if old is None:
        return new
    better = new[0] > old[0]
    return np.where(better, new[0], old[0]), np.where(better[..., None], new[1], old[1])


def _fits(merged: dict[int, tuple[np.ndarray, np.ndarray]]) -> list[tuple[float, Slots]]:
    return [fit for side, (scores, locs) in merged.items() for c in range(len(scores))
            if (fit := fit_slots([float(v) for v in scores[c]], [(int(x), int(y)) for x, y in locs[c]], side))]


def fit_slots(scores: list[float], locs: list[tuple[int, int]], side: int) -> tuple[float, Slots] | None:
    """Casillas a partir de los iconos de un campeón: los dos más claros (≥ ``SLOT_SCORE``) tienen que estar en la misma
    fila, en orden (Q, W, E, R) y a distancia de casilla. Devuelve ``(calidad, casillas)``: la suma del parecido de los
    iconos claros que caen en su casilla."""
    good = sorted((k for k in range(4) if scores[k] >= SLOT_SCORE), key=lambda k: -scores[k])
    if len(good) < 2:
        return None
    a, b = good[0], good[1]
    (xa, ya), (xb, yb) = locs[a], locs[b]
    pitch = (xb - xa) / (b - a)
    if abs(ya - yb) > SLOT_SLACK_PX or not SLOT_PITCH[0] * side <= pitch <= SLOT_PITCH[1] * side:
        return None
    xs = tuple(round(xa + (k - a) * pitch) for k in range(4))
    y = round((ya + yb) / 2)
    fitting = [k for k in good if abs(locs[k][0] - xs[k]) <= SLOT_SLACK_PX and abs(locs[k][1] - y) <= SLOT_SLACK_PX]
    return float(sum(scores[k] for k in fitting)), Slots(side, y, xs)


def sure(score: float, margin: float) -> bool:
    return score >= CHAMPION_SCORE and margin >= CHAMPION_MARGIN


def vote_champion(keys: list[str]) -> dict | None:
    """El campeón del jugador si sale en la mitad de las lecturas claras o más (y al menos en ``MIN_PROBES``); en vídeos
    con varias partidas puede no haber uno (ver :func:`champion_counts`)."""
    if not keys:
        return None
    key, n = Counter(keys).most_common(1)[0]
    if n < max(MIN_PROBES, 0.5 * len(keys)):
        return None
    return {"clave": key, "votos": n, "de": len(keys)}


def champion_counts(keys: list[str], min_votes: int = 2) -> list[dict]:
    """Todos los campeones leídos al menos ``min_votes`` veces (vídeos con varias partidas), de más a menos."""
    return [{"clave": k, "votos": n} for k, n in Counter(keys).most_common() if n >= min_votes]


# ---------------------------------------------------------------------- iconos del minimapa
def ring_icons(minimap: np.ndarray) -> list[tuple[float, float, str]]:
    """Círculos del tamaño de un icono de campeón con el color del aro: ``a`` (azul, aliado) o ``e`` (rojo, enemigo)."""
    size = minimap.shape[0]
    gray = cv2.GaussianBlur(cv2.cvtColor(minimap, cv2.COLOR_BGR2GRAY), (3, 3), 0)
    circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1, minDist=max(4, size * 0.035), param1=70, param2=14,
                               minRadius=max(3, round(size * 0.034)), maxRadius=max(5, round(size * 0.047)))
    if circles is None:
        return []
    hsv = cv2.cvtColor(minimap, cv2.COLOR_BGR2HSV).astype(np.int16)
    yy, xx = np.mgrid[0:size, 0:size]
    out = []
    for cx, cy, r in circles[0][:20]:
        dist = np.hypot(xx - cx, yy - cy)
        ring = (dist >= r - 1.0) & (dist <= r + 1.5)
        team = _ring_team(hsv[ring])
        if team:
            out.append((float(cx), float(cy), team))
    return out


def _ring_team(pixels: np.ndarray) -> str | None:
    if not len(pixels):
        return None
    hue, sat, val = pixels[:, 0], pixels[:, 1], pixels[:, 2]
    lit = (sat > 70) & (val > 80)
    red = float((lit & ((hue < 10) | (hue > 165))).mean())
    blue = float((lit & (hue > 90) & (hue < 130)).mean())
    if red >= 0.15 and red > blue * 2:
        return "e"
    if blue >= 0.15 and blue > red * 2:
        return "a"
    return None


# ---------------------------------------------------------------------- análisis completo
@dataclass
class Tools:
    maps: MapMatcher
    clock: Clock
    abilities: Abilities | None = None
    clock_reader: object | None = None  # el lector de reloj elegido para el vídeo en curso (None = plantillas)
    ability_slots: Slots | None = None  # las casillas de las habilidades en el vídeo en curso (None = sin campeón)


def probe(reader: Frames, duration: float, tools: Tools) -> tuple[Box | None, list[np.ndarray]]:
    """~16 frames repartidos: ¿hay minimapa? Devuelve el minimapa común y los frames que lo tienen."""
    if not duration or duration <= 0:
        return None, []
    times = np.linspace(duration * 0.04, duration * 0.96, PROBE_FRAMES)
    boxes: list[Box] = []
    with_map: list[np.ndarray] = []
    for t in times:
        frame = reader.at(float(t))
        if frame is None:
            continue
        box = tools.maps.locate(frame)
        if box is not None:
            boxes.append(box)
            with_map.append(frame)
    return consensus(boxes), with_map


@dataclass
class Collected:
    box: Box
    shape: tuple[int, ...]
    gpu: bool = False  # se decodificó con la gráfica
    present: list[tuple[float, float]] = field(default_factory=list)
    camera: list[list] = field(default_factory=list)
    clock: list[list] = field(default_factory=list)
    icons: list[list] = field(default_factory=list)
    champions: list[list] = field(default_factory=list)
    samples: int = 0


def sample_pass(source: str, box: Box, cam: tuple[int, int] | None, tools: Tools, duration: float,
                should_stop: Callable[[], bool], progress: Callable[[float], None] | None = None, workers: int = 1,
                gpu: bool = False) -> Collected:
    """Una muestra cada ``STEP_S``: partida sí/no, cámara, reloj e iconos.

    Decodificar el vídeo es lo que más tarda (36 de 39 s en uno de 21 min): se parte en trozos que se leen a la vez,
    cada uno con su lector y en su hilo (FFmpeg y OpenCV sueltan el GIL), y al final se juntan en orden."""
    spans = _spans(duration, max(1, workers))
    seen = [0]
    lock = threading.Lock()
    expected = max(1.0, duration / STEP_S)

    def tick() -> None:
        with lock:
            seen[0] += 1
            n = seen[0]
        if progress and n % 15 == 0:
            progress(min(1.0, n / expected))

    previous = cv2.getNumThreads()
    cv2.setNumThreads(1)  # el reparto lo hacen los trozos: sin esto, cada función usaría todos los núcleos a la vez
    try:
        with ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="videos-frames") as pool:
            parts = list(pool.map(lambda span: _sample_span(source, span, box, cam, tools, should_stop, gpu, tick), spans))
    finally:
        cv2.setNumThreads(previous)
    out = Collected(box, next((p.shape for p in parts if p.shape != (0, 0)), (0, 0)))
    for part in parts:
        out.samples += part.samples
        out.present += part.present
        out.camera += part.camera
        out.clock += part.clock
        out.icons += part.icons
        out.champions += part.champions
        out.gpu = out.gpu or part.gpu
    return out


def _spans(duration: float, workers: int) -> list[tuple[float, float]]:
    """Trozos de vídeo alineados con las muestras (el doble que hilos: los que no tienen partida acaban antes)."""
    if not duration or duration <= 0:
        return [(0.0, float("inf"))]
    pieces = max(1, min(workers * 2, int(duration // (STEP_S * 30)) or 1))
    size = math.ceil(duration / STEP_S / pieces) * STEP_S
    return [(k * size, (k + 1) * size if k < pieces - 1 else float("inf")) for k in range(pieces)]


def _sample_span(source: str, span: tuple[float, float], box: Box, cam: tuple[int, int] | None, tools: Tools,
                 should_stop: Callable[[], bool], gpu: bool, tick: Callable[[], None]) -> Collected:
    start, end = span
    part = Collected(box, (0, 0))
    reader = Frames(source, threads=1, gpu=gpu)
    part.gpu = reader.gpu
    last = -1
    try:
        for t, frame in reader.every(STEP_S, start):
            # Cada frame a su hueco de 2 s, y un hueco solo una vez: con frames de tiempo irregular (vídeos con
            # fotogramas perdidos) dos lecturas seguidas caían en el mismo segundo.
            slot = round(t / STEP_S)
            at = round(slot * STEP_S, 1)
            if at >= end or (part.samples % 30 == 0 and should_stop()):
                break
            if at < start or slot <= last:
                continue
            last = slot
            part.shape = frame.shape
            part.samples += 1
            score = tools.maps.presence(frame, box)
            part.present.append((at, score))
            if score >= PRESENT_SCORE:
                _merge(part, _analyze(frame, at, slot, box, cam, tools))
            tick()
    finally:
        reader.close()
    return part


def _analyze(frame: np.ndarray, t: float, i: int, box: Box, cam: tuple[int, int] | None, tools: Tools) -> dict:
    """Lo de una muestra con partida: dónde mira la cámara, el reloj (cada ``CLOCK_EVERY``) y los iconos."""
    minimap = box.crop(frame)
    part: dict = {"t": t}
    if cam is not None:
        u, v, score, cover = find_camera(white_mask(minimap), cam[0], cam[1])
        if cover >= CAMERA_COVER and score >= CAMERA_SCORE:
            part["camera"] = [t, *box.to_game(u, v)]
    if i % CLOCK_EVERY == 0:
        seconds = (tools.clock_reader or tools.clock).read(frame)
        if seconds is not None:
            part["clock"] = [t, seconds]
    if i % CHAMPION_EVERY == 0 and (champion := _champion(frame, t, tools)):
        part["champion"] = champion
    if i % ICONS_EVERY == 0:
        found = [[*box.to_game(u, v), team] for u, v, team in ring_icons(minimap)]
        if found:
            part["icons"] = [t, found]
    return part


def _champion(frame: np.ndarray, t: float, tools: Tools) -> list | None:
    """``[t, campeón, parecido]`` si el frame tiene HUD de jugador (reloj de plantillas) y el campeón sale claro."""
    if tools.abilities is None or tools.ability_slots is None or tools.clock.read(frame) is None:
        return None
    found = tools.abilities.read(frame, tools.ability_slots)
    return [t, found[0], round(found[1], 2)] if found else None


def _merge(out: Collected, part: dict) -> None:
    """Las muestras se juntan en el orden en que se leyeron (los hilos pueden acabar desordenados)."""
    for key, target in (("camera", out.camera), ("clock", out.clock), ("icons", out.icons), ("champion", out.champions)):
        if key in part:
            target.append(part[key])


def segments(present: list[tuple[float, float]], gap: float = STEP_S * 3, min_len: float = 20.0) -> list[list[float]]:
    """Tramos del vídeo con partida (muestras seguidas con minimapa; huecos cortos no cortan el tramo)."""
    out: list[list[float]] = []
    start = last = None
    for t, score in present:
        if score < PRESENT_SCORE:
            continue
        if start is None or t - last > gap:
            if start is not None and last - start >= min_len:
                out.append([start, last])
            start = t
        last = t
    if start is not None and last - start >= min_len:
        out.append([start, last])
    return out


def clean_clock(clock: list[list]) -> tuple[list[list], list[list]]:
    """Aparta las lecturas sueltas que no casan con sus vecinas: un «0:21» entre 10:41 y 11:01 (el OCR de reserva leyó
    otro texto de la pantalla). Los saltos de verdad (cortes del vídeo, pausas, rebobinados del coach) siguen en las
    lecturas de después y se quedan. Se quita la peor y se vuelve a mirar: con dos malas casi seguidas (773, 417, 793,
    397, 813) la buena de en medio no sale perjudicada. Devuelve ``(buenas, apartadas)``."""
    keep = list(clock)
    odd: list[list] = []
    while keep:
        excess = [_excess(keep, i) for i in range(len(keep))]
        worst = max(range(len(keep)), key=excess.__getitem__)
        if excess[worst] <= 0:
            break
        odd.append(keep.pop(worst))
    return keep, sorted(odd)


def _excess(clock: list[list], i: int) -> float:
    """Cuánto se sale una lectura de sus vecinas (0 si casa)."""
    last = len(clock) - 1
    if 0 < i < last:
        return _middle_excess(_offset(clock[i]), _offset(clock[i - 1]), _offset(clock[i + 1]))
    if last >= 2 and i in (0, last):
        step = 1 if i == 0 else -1
        return _edge_excess(clock[i], clock[i + step], clock[i + 2 * step], first=i == 0)
    return 0.0


def _offset(reading: list) -> float:
    return reading[1] - reading[0]


def _middle_excess(own: float, before: float, after: float) -> float:
    """Con vecinas a los dos lados se mira el desfase reloj − vídeo: con cortes de edición sube a saltos, con pausas o
    rebobinados baja y se queda; una lectura suelta se sale por arriba o por abajo de las dos más de lo que las vecinas
    se separan entre sí (601, 585, 634 cada 10 s: sobra 585)."""
    if (own - before) * (own - after) <= 0:  # entre las dos, o igual que una
        return 0.0
    off = min(abs(own - before), abs(own - after))
    return off - abs(after - before) if off > CLOCK_TOLERANCE_S else 0.0


def _edge_excess(item: list, near: list, far: list, first: bool) -> float:
    """La primera o la última: sobra si no casa con la de al lado y esa sí casa con la siguiente."""
    if first:
        lone = _follows(near, far) and not _follows(item, near)
    else:
        lone = _follows(far, near) and not _follows(near, item)
    return abs(_offset(item) - _offset(near)) if lone else 0.0


def _follows(a: list, b: list) -> bool:
    """¿La lectura ``b`` puede venir después de ``a``? Vale el reloj parado (pausa) y que corra más que el vídeo (cortes de
    edición: hasta 6 veces), no que vaya hacia atrás."""
    video, game = b[0] - a[0], b[1] - a[1]
    return video > 0 and -2 <= game <= video * 6 + 2


def summarize(collected: Collected, cam: tuple[int, int] | None, champion: dict | None, clock_ok: bool, method: str,
              hud: str | None = None, champions: list[dict] | None = None) -> dict:
    tramos = segments(collected.present)
    in_game = sum(b - a for a, b in tramos)
    clock, odd = clean_clock(collected.clock)
    return {
        "partida": bool(tramos),
        "metodo": method,
        "hud": hud,  # «jugador» (su partida) o «espectador» (retransmisión o repetición); None si no se supo
        "muestra_s": STEP_S,
        "muestras": collected.samples,
        "segundos_con_partida": round(in_game),
        "minimapa": collected.box.public(collected.shape),
        "camara_px": list(cam) if cam else None,
        "campeon": champion,  # el de la mayoría de las lecturas (None si no está claro o hay varios a medias)
        "campeones": champions or [],  # todos los que se leyeron (vídeos con varias partidas)
        "campeon_lecturas": collected.champions,  # [[t, clave, parecido]] cada 10 s con HUD de jugador
        "reloj_ok": clock_ok,
        "tramos": tramos,
        "reloj": clock,
        "reloj_apartadas": odd,  # lecturas sueltas que no casaban con sus vecinas (ver clean_clock)
        "camara": collected.camera,
        "iconos": collected.icons,
    }

