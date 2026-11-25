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

import os
from collections import Counter
from collections.abc import Callable, Iterator
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
MAP_SCORE = 0.35  # parecido mínimo con el mapa para aceptar un minimapa en la búsqueda
PRESENT_SCORE = 0.30  # y para dar una muestra por «hay partida»
MIN_PROBES = 3  # frames de la sonda con minimapa para tratar el vídeo como partida
STEP_S = 2.0  # una muestra cada 2 s
CLOCK_EVERY = 5  # reloj cada 5 muestras (10 s)
ICONS_EVERY = 2  # iconos del minimapa cada 2 muestras (4 s)
CAMERA_COVER = 0.40  # parte del contorno del recuadro que tiene que verse blanca
CAMERA_SCORE = 0.28  # contorno blanco menos interior blanco
DIGIT_SCORE = 0.55


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
    """Frames por segundo de vídeo de un fichero o de un enlace directo (OpenCV con FFmpeg)."""

    def __init__(self, source: str) -> None:
        self.cap = cv2.VideoCapture(_ascii_path(str(source)), cv2.CAP_FFMPEG)
        if not self.cap.isOpened():
            raise OSError(f"OpenCV no puede abrir el vídeo ({str(source)[:80]}…)")
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0

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
    if seconds >= 60:
        return None
    return minutes * 60 + seconds


# ---------------------------------------------------------------------- campeón del jugador
class Portraits:
    """El retrato grande del HUD (abajo, en el centro) comparado con los iconos de Data Dragon."""

    def __init__(self, icons: dict[str, np.ndarray]) -> None:
        self.icons = icons
        self._cache: dict[int, dict[str, np.ndarray]] = {}

    def _templates(self, d: int) -> dict[str, np.ndarray]:
        if d not in self._cache:
            side = max(8, round(d * 0.68))  # el cuadrado que cabe dentro del círculo del retrato
            off = (d - side) // 2
            self._cache[d] = {
                k: cv2.resize(img, (d, d), interpolation=cv2.INTER_AREA)[off:off + side, off:off + side] for k, img in self.icons.items()
            }
        return self._cache[d]

    def best(self, frame: np.ndarray) -> tuple[str | None, float]:
        h, w = frame.shape[:2]
        region = frame[int(h * 0.82):h, int(w * 0.22):int(w * 0.58)]
        best: tuple[str | None, float] = (None, 0.0)
        for frac in (0.052, 0.06, 0.068, 0.076):
            d = round(h * frac)
            for key, tpl in self._templates(d).items():
                if tpl.shape[0] > region.shape[0] or tpl.shape[1] > region.shape[1]:
                    continue
                score = float(cv2.minMaxLoc(cv2.matchTemplate(region, tpl, cv2.TM_CCOEFF_NORMED))[1])
                if score > best[1]:
                    best = (key, score)
        return best


def vote_champion(portraits: Portraits, frames: list[np.ndarray], min_score: float = 0.5) -> dict | None:
    votes: Counter = Counter()
    scores: dict[str, list[float]] = {}
    for frame in frames:
        key, score = portraits.best(frame)
        if key and score >= min_score:
            votes[key] += 1
            scores.setdefault(key, []).append(score)
    if not votes:
        return None
    key, n = votes.most_common(1)[0]
    return {"clave": key, "votos": n, "de": len(frames), "parecido": round(float(np.mean(scores[key])), 3)}


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
    portraits: Portraits | None = None


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
    present: list[tuple[float, float]] = field(default_factory=list)
    camera: list[list] = field(default_factory=list)
    clock: list[list] = field(default_factory=list)
    icons: list[list] = field(default_factory=list)
    samples: int = 0


def sample_pass(reader: Frames, box: Box, cam: tuple[int, int] | None, tools: Tools, duration: float,
                should_stop: Callable[[], bool], progress: Callable[[float], None] | None = None) -> Collected:
    """Una muestra cada ``STEP_S``: partida sí/no, cámara, reloj e iconos."""
    out: Collected | None = None
    for i, (t, frame) in enumerate(reader.every(STEP_S)):
        if out is None:
            out = Collected(box, frame.shape)
        if i % 30 == 0 and should_stop():
            break
        out.samples += 1
        score = tools.maps.presence(frame, box)
        out.present.append((round(t, 1), score))
        if score < PRESENT_SCORE:
            continue
        _sample(frame, t, i, out, cam, tools)
        if progress and duration and i % 15 == 0:
            progress(min(1.0, t / duration))
    return out if out is not None else Collected(box, (0, 0))


def _sample(frame: np.ndarray, t: float, i: int, out: Collected, cam: tuple[int, int] | None, tools: Tools) -> None:
    box = out.box
    minimap = box.crop(frame)
    if cam is not None:
        u, v, score, cover = find_camera(white_mask(minimap), cam[0], cam[1])
        if cover >= CAMERA_COVER and score >= CAMERA_SCORE:
            out.camera.append([round(t, 1), *box.to_game(u, v)])
    if i % CLOCK_EVERY == 0:
        seconds = tools.clock.read(frame)
        if seconds is not None:
            out.clock.append([round(t, 1), seconds])
    if i % ICONS_EVERY == 0:
        found = [[*box.to_game(u, v), team] for u, v, team in ring_icons(minimap)]
        if found:
            out.icons.append([round(t, 1), found])


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


def summarize(collected: Collected, cam: tuple[int, int] | None, champion: dict | None, clock_ok: bool, method: str) -> dict:
    tramos = segments(collected.present)
    in_game = sum(b - a for a, b in tramos)
    return {
        "partida": bool(tramos),
        "metodo": method,
        "muestra_s": STEP_S,
        "muestras": collected.samples,
        "segundos_con_partida": round(in_game),
        "minimapa": collected.box.public(collected.shape),
        "camara_px": list(cam) if cam else None,
        "campeon": champion,
        "reloj_ok": clock_ok,
        "tramos": tramos,
        "reloj": collected.clock,
        "camara": collected.camera,
        "iconos": collected.icons,
    }

