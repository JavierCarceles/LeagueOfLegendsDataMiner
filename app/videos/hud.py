"""HUD de jugador: vida, recurso, habilidades e invocadores listos, y oro, de cada frame de una partida.

Todo cuelga de las casillas de las habilidades que ya calibra :class:`app.videos.frames.Abilities` (``Slots``), así que
vale para cualquier escala del HUD. Medido el 26-11-2026 en vídeos de 1080p con lados de icono de 38, 44 y 50 px:

- **Invocadores**: D a 1,12 casillas a la derecha de la R y F a 1,87; lado del 75 % de un icono de habilidad.
- **Barras**: la de vida (verde) entre 1 y 2,4 lados por debajo de la fila de habilidades; la del recurso, 0,34 lados
  más abajo. Lo lleno se mide por la punta derecha del color; el 100 % es la punta más a la derecha del vídeo.
- **Listo o en enfriamiento**: parecido (escala de grises, ``TM_CCOEFF_NORMED``) con su icono de Data Dragon: listo
  0,87-0,98; en enfriamiento (velo oscuro, barrido y número) 0,16-0,61. El parecido no cambia con el brillo, así que
  un icono en gris (muerto, sin maná o sin aprender) se separa por la saturación frente al icono original.
- **Texto (OCR, ~0,4 s)**: el oro (abajo a la derecha) y «vida/máxima» y «recurso/máximo» sobre las barras.

Los iconos que cambian al mejorarse o transformarse (Castigo y Teleport mejorados, las formas de Kayn…) no están en
Data Dragon: ahí el estado sale «?» en vez de inventarlo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import cv2
import numpy as np

SUMMONERS = ("SummonerFlash", "SummonerDot", "SummonerTeleport", "SummonerSmite", "SummonerHeal", "SummonerBarrier",
             "SummonerExhaust", "SummonerHaste", "SummonerBoost")
UNSTABLE = {"SummonerSmite", "SummonerTeleport"}  # cambian de icono al mejorarse: su estado no se lee
SPELL_DX = (1.12, 1.87)  # D y F, en casillas desde la R
SPELL_SIDE = 0.75
READY_SCORE = 0.70
GREY_SAT = 0.45  # saturación frente al icono original por debajo de la cual el icono está en gris
SUMMONER_SCORE = 0.80  # parecido para dar por buena la identidad de un invocador
SLACK = 3
BAR_TOP, BAR_BOTTOM = 1.0, 2.4  # zona de la barra de vida, en lados de icono bajo el borde de arriba de la fila
RES_DY = 0.34
MIN_BAR_PX = 3  # píxeles de color seguidos al principio para que haya barra
BAR_DENSITY = 0.5
LEFT_SLACK_PX = 4
STATES = {"ready": "L", "cooldown": "C", "grey": "G", "unknown": "?"}
_NUM = re.compile(r"^\d{1,5}$")
_PAIR = re.compile(r"^(\d{1,5})\s*/\s*(\d{1,5})$")


@dataclass
class Layout:
    """Dónde está cada cosa en el vídeo en curso (``boxes``: Q, W, E, R, D, F como (x, y, lado) en píxeles)."""

    boxes: list[tuple[int, int, int]]
    side: int
    abilities: list[np.ndarray | None]  # plantilla gris del centro de cada icono de habilidad (None = sin campeón)
    ability_sat: list[float]
    summoners: list[str | None]
    summoner_tpl: list[np.ndarray | None]
    summoner_sat: list[float]
    bar: tuple[int, int, int, int | None] | None = None  # izquierda, derecha (llena), fila de la vida, fila del recurso

    def public(self) -> dict:
        return {"casillas": [list(b) for b in self.boxes], "invocadores": self.summoners, "barra": self.bar}


def _inner(img: np.ndarray, side: int) -> np.ndarray:
    inner = max(6, round(side * 0.7))
    off = (side - inner) // 2
    return cv2.resize(img, (side, side), interpolation=cv2.INTER_AREA)[off:off + inner, off:off + inner]


def _sat(img: np.ndarray) -> float:
    return float(cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[..., 1].mean())


def boxes_from(slots, shape: tuple[int, ...]) -> tuple[list[tuple[int, int, int]], int]:
    """Las 6 casillas (Q, W, E, R, D, F) a partir de las casillas de :class:`frames.Slots`."""
    h, w = shape[:2]
    ox, oy = int(w * 0.30), int(h * 0.82)  # el origen de Abilities.region
    side = slots.side
    off = (side - max(6, round(side * 0.7))) // 2
    abil = [(ox + x - off, oy + slots.y - off, side) for x in slots.xs]
    pitch = (abil[3][0] - abil[0][0]) / 3
    rx, ry, _ = abil[3]
    small = round(side * SPELL_SIDE)
    return abil + [(rx + round(dx * pitch), ry, small) for dx in SPELL_DX], side


def _crop(frame: np.ndarray, box: tuple[int, int, int], slack: int = 0) -> np.ndarray:
    x, y, s = box
    return frame[max(0, y - slack):y + s + slack, max(0, x - slack):x + s + slack]


def _score(frame_gray: np.ndarray, box, tpl: np.ndarray) -> float:
    win = _crop(frame_gray, box, SLACK)
    if win.shape[0] < tpl.shape[0] or win.shape[1] < tpl.shape[1]:
        return 0.0
    return float(cv2.minMaxLoc(cv2.matchTemplate(win, tpl, cv2.TM_CCOEFF_NORMED))[1])


class Hud:
    def __init__(self, spells: dict[str, list[np.ndarray]], summoners: dict[str, np.ndarray]) -> None:
        self.spells = spells  # clave del campeón → sus 4 iconos (BGR)
        self.summoners = summoners  # SummonerFlash… → icono (BGR)

    def calibrate(self, frames: list[np.ndarray], slots, champion: str | None) -> Layout | None:
        """Las casillas del vídeo, las plantillas del campeón y qué invocadores lleva (lo más claro entre ``frames``)."""
        if slots is None or not frames:
            return None
        boxes, side = boxes_from(slots, frames[0].shape)
        icons = self.spells.get(champion or "") or []
        abilities = [cv2.cvtColor(_inner(i, side), cv2.COLOR_BGR2GRAY) for i in icons] if len(icons) == 4 else [None] * 4
        ability_sat = [_sat(_inner(i, side)) for i in icons] if len(icons) == 4 else [0.0] * 4
        small = boxes[4][2]
        best: list[tuple[float, str | None]] = [(0.0, None), (0.0, None)]
        grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]
        for name, icon in self.summoners.items():
            tpl = cv2.cvtColor(_inner(icon, small), cv2.COLOR_BGR2GRAY)
            for k in (0, 1):
                score = max(_score(g, boxes[4 + k], tpl) for g in grays)
                if score > best[k][0]:
                    best[k] = (score, name)
        names = [name if score >= SUMMONER_SCORE else None for score, name in best]
        tpls = [cv2.cvtColor(_inner(self.summoners[n], small), cv2.COLOR_BGR2GRAY) if n else None for n in names]
        sats = [_sat(_inner(self.summoners[n], small)) if n else 0.0 for n in names]
        return Layout(boxes, side, abilities, ability_sat, names, tpls, sats, _calibrate_bar(frames, boxes, side))

    # ------------------------------------------------------------------ cada frame
    def read(self, frame: np.ndarray, layout: Layout) -> list:
        """``[izq. vida, dcha. vida, dcha. recurso, estados]`` (las puntas en píxeles; ``None`` si no hay barra)."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        states = []
        for k, box in enumerate(layout.boxes):
            tpl = layout.abilities[k] if k < 4 else layout.summoner_tpl[k - 4]
            unstable = k >= 4 and layout.summoners[k - 4] in UNSTABLE
            if tpl is None or unstable:
                states.append(STATES["unknown"])
                continue
            ref_sat = layout.ability_sat[k] if k < 4 else layout.summoner_sat[k - 4]
            states.append(self._state(frame, gray, box, tpl, ref_sat))
        return [*self._bars(frame, layout), "".join(states)]

    @staticmethod
    def _state(frame, gray, box, tpl, ref_sat: float) -> str:
        if _score(gray, box, tpl) < READY_SCORE:
            return STATES["cooldown"]
        patch = _crop(frame, box)
        if ref_sat > 0 and _sat(patch) / ref_sat < GREY_SAT:
            return STATES["grey"]
        return STATES["ready"]

    @staticmethod
    def _bars(frame: np.ndarray, layout: Layout) -> tuple:
        """``(izquierda, punta de la vida, punta del recurso)`` en las filas calibradas (``None`` sin barra pintada)."""
        if layout.bar is None:
            return None, None, None
        left, right, hp_y, res_y = layout.bar
        if hp_y >= frame.shape[0]:
            return None, None, None
        hsv = cv2.cvtColor(frame[hp_y:hp_y + 1, left:right + 1], cv2.COLOR_BGR2HSV)[0]
        margin = max(MIN_BAR_PX, layout.side // 2)  # el borde de la barra no siempre sale del color
        end = _filled_end(_green(hsv), margin)
        res_end = None
        if res_y is not None and res_y < frame.shape[0]:
            res = cv2.cvtColor(frame[res_y:res_y + 1, left:right + 1], cv2.COLOR_BGR2HSV)[0]
            res_end = _filled_end(_colored(res), margin)
        return left, None if end is None else left + end, None if res_end is None else left + res_end

    # ------------------------------------------------------------------ texto
    @staticmethod
    def read_text(frame: np.ndarray, layout: Layout, engine) -> list:
        """``[oro, vida, vida máx., recurso, recurso máx.]`` por OCR (``None`` lo que no se lea)."""
        q, f = layout.boxes[0], layout.boxes[5]
        side = layout.side
        h = frame.shape[0]
        fx, fy, fs = f
        gold_crop = frame[fy + side:min(h, fy + round(2.6 * side)), fx + fs:fx + fs + round(4.5 * side)]
        bars_crop = frame[q[1] + side:min(h, q[1] + round(2.3 * side)), max(0, q[0] - side):fx + fs]
        gold = None
        for text in _texts(engine, gold_crop):
            if _NUM.match(text) and (gold is None or len(text) > len(str(gold))):
                gold = int(text)
        pairs = []
        for text in _texts(engine, bars_crop):
            m = _PAIR.match(text.replace(" ", ""))
            if m and int(m.group(1)) <= int(m.group(2)) and int(m.group(2)) > 0:
                pairs.append((int(m.group(1)), int(m.group(2))))
        hp = pairs[0] if pairs else (None, None)
        res = pairs[1] if len(pairs) > 1 else (None, None)
        return [gold, *hp, *res]


def _green(hsv: np.ndarray) -> np.ndarray:
    """Verde de la barra de vida: tono 58-66, brillo 72-200 (degradado). El borde del HUD, verde azulado y oscuro (tono
    75-83 o brillo < 60), no cuenta. Medido en 3 HUD de 1080p el 26-11-2026."""
    return (hsv[..., 0] >= 50) & (hsv[..., 0] <= 72) & (hsv[..., 1] > 85) & (hsv[..., 2] > 65)


def _colored(hsv: np.ndarray) -> np.ndarray:
    """Color de la barra de recurso: maná azul (tono 95-125), energía amarilla (18-35) o furia/calor rojos (< 10 o
    > 165), saturado y claro. El borde del HUD (verde azulado, tono 75-90) no cuenta."""
    hue = hsv[..., 0]
    tone = ((hue >= 95) & (hue <= 125)) | ((hue >= 18) & (hue <= 35)) | (hue < 10) | (hue > 165)
    return tone & (hsv[..., 1] > 90) & (hsv[..., 2] > 70)


def _calibrate_bar(frames: list[np.ndarray], boxes, side: int) -> tuple | None:
    """Las barras del vídeo: en la zona bajo las habilidades, la fila que más veces sale verde (las de arriba y abajo
    de la barra no tienen el texto encima) y, en ella, las columnas que se pintan de verde en 2 frames o más: de la
    primera a la última es la barra llena. El recurso, la fila con más color entre 0,2 y 0,55 lados más abajo."""
    q, f = boxes[0], boxes[5]
    h, w = frames[0].shape[:2]
    x0, x1 = max(0, q[0] - side), min(w, f[0] + f[2] + side)
    y0, y1 = q[1] + round(side * BAR_TOP), min(h, q[1] + round(side * BAR_BOTTOM))
    if y1 <= y0 or len(frames) < 2:
        return None
    hsvs = [cv2.cvtColor(fr[y0:y1, x0:x1], cv2.COLOR_BGR2HSV) for fr in frames]
    greens = np.stack([_green(x) for x in hsvs])  # [frame, fila, columna]
    hp_row = int(np.argmax(greens.sum(axis=(0, 2))))
    cols = greens[:, hp_row, :].sum(axis=0) >= 2
    runs = _runs(_close_gaps(cols, max(2, side // 3)))
    if not runs:
        return None
    start, length = max(runs, key=lambda r: r[1])
    if length < side * 2:
        return None
    res_y = None
    lo, hi = hp_row + round(side * 0.2), min(greens.shape[1], hp_row + round(side * 0.55) + 1)
    if hi > lo:
        colored = np.stack([_colored(x[lo:hi, start:start + length]) for x in hsvs])
        per_row = (colored.mean(axis=2) > 0.3).mean(axis=0)  # en qué parte de los frames la fila sale pintada
        k = int(np.argmax(per_row))
        if per_row[k] >= 0.2:
            res_y = y0 + lo + k
    return x0 + start, x0 + start + length - 1, y0 + hp_row, res_y


def _close_gaps(mask: np.ndarray, gap: int) -> np.ndarray:
    """Une tramos separados por huecos de hasta ``gap`` columnas."""
    out = mask.copy()
    runs = _runs(mask)
    for (a, n), (b, _) in zip(runs, runs[1:]):
        if b - (a + n) <= gap:
            out[a + n:b] = True
    return out


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Tramos seguidos de ``True``: ``[(inicio, largo)]``."""
    padded = np.concatenate(([0], mask.astype(np.int8), [0]))
    edges = np.flatnonzero(np.diff(padded))
    return [(int(a), int(b - a)) for a, b in zip(edges[::2], edges[1::2])]


def _filled_end(mask: np.ndarray, margin: int) -> int | None:
    """Punta derecha de una barra que empieza en las primeras ``margin`` columnas: el punto más a la derecha hasta el
    que al menos la mitad de la franja está pintada (el texto «vida/máxima» tapa un trozo; un píxel de color suelto
    más allá no cuenta). ``None`` si la barra está vacía."""
    first = np.flatnonzero(mask[:margin])
    if first.size == 0:
        return None
    start = int(first[0])
    seg = mask[start:].astype(np.int32)
    density = np.cumsum(seg) / np.arange(1, seg.size + 1)
    ok = np.flatnonzero((density >= BAR_DENSITY) & (seg == 1))
    return start + int(ok.max()) if ok.size else None


def _texts(engine, crop: np.ndarray) -> list[str]:
    if crop.size == 0:
        return []
    result = engine(crop)
    return [str(t).strip() for t, s in zip(result.txts or (), result.scores or ()) if float(s) >= 0.7]


# ---------------------------------------------------------------------- resumen
def _span_from_texts(raw: list[list], texts: list[list], left: float) -> float | None:
    """Largo de la barra llena según el OCR: en cada lectura con «vida/máxima», la punta medida / la fracción leída."""
    by_t = {r[0]: r for r in raw}
    spans = []
    for t, _, hp, hp_max, *_ in texts:
        row = by_t.get(t)
        if row is None or row[2] is None or not hp or not hp_max or hp / hp_max < 0.3:
            continue
        spans.append((row[2] - left) / (hp / hp_max))
    return float(np.median(spans)) if len(spans) >= 3 else None


def _is_hud(row: list) -> bool:
    """Con barra de vida o con iconos que casan: HUD de jugador (muerto: sin barra pero iconos en gris)."""
    states = row[4]
    return row[2] is not None or sum(c in "LG" for c in states) >= 2


def summarize(raw: list[list], texts: list[list], layout: Layout | None) -> dict | None:
    """De las lecturas en bruto a porcentajes: el 0 de la barra es su punta izquierda (mediana) y el 100 %, lo que dice
    el OCR («vida/máxima» frente a la punta medida) o, sin él, la punta más a la derecha del vídeo (percentil 98)."""
    if layout is None or not raw:
        return None
    raw = [r for r in raw if _is_hud(r)]
    lefts = [r[1] for r in raw if r[1] is not None]
    if len(lefts) < 10:
        return None
    left = float(np.median(lefts))
    # Una «barra» que no empieza donde la del vídeo es otra cosa verde (un objeto, un ward): sin barra.
    raw = [r if r[1] is None or abs(r[1] - left) <= LEFT_SLACK_PX else [r[0], None, None, None, r[4]] for r in raw]
    rights = [r[2] for r in raw if r[2] is not None]
    if len(rights) < 10:
        return None
    calibrated = layout.bar[1] - layout.bar[0] if layout.bar else None
    span = _span_from_texts(raw, texts, left) or calibrated or float(np.percentile(rights, 98)) - left
    span = max(1.0, span)
    res_rights = [r[3] for r in raw if r[3] is not None]
    has_res = len(res_rights) >= len(raw) * 0.2
    rows = []
    for t, _, right, res_right, states in raw:
        hp = 0.0 if right is None else min(1.0, max(0.0, (right - left) / span))
        res = None if not has_res else (0.0 if res_right is None else min(1.0, max(0.0, (res_right - left) / span)))
        rows.append([t, round(hp, 3), None if res is None else round(res, 3), states])
    return {
        "invocadores": layout.summoners,
        "orden": "QWERDF",  # cada letra del estado: L lista · C en enfriamiento · G en gris (muerto, sin maná…) · ? no se sabe
        "muestras": rows,  # [t, vida 0-1, recurso 0-1 (None sin barra), estados]
        "textos": texts,  # [t, oro, vida, vida máx., recurso, recurso máx.] cada ~10 s
    }


# ---------------------------------------------------------------------- hábitos
DEATH_SAMPLES = 3  # muestras seguidas sin vida para contar una muerte
FLASH_BEFORE_S = 4  # Flash lista en los segundos antes de morir
LOW_HP = 0.30
RICH_GOLD = 1_300  # oro en el bolsillo para contar «sin gastar» (una pieza de objeto)


def habits(data: dict | None) -> dict | None:
    """Lo que dice el HUD de una partida: muertes (y cuántas con Flash lista), tiempo con poca vida y con oro sin gastar."""
    if not data or not data.get("muestras"):
        return None
    rows = data["muestras"]
    flash = data["invocadores"].index("SummonerFlash") + 4 if "SummonerFlash" in (data.get("invocadores") or []) else None
    deaths, flash_up, low, alive = 0, 0, 0, 0
    run = 0
    for i, (t, hp, _, states) in enumerate(rows):
        if hp > 0:
            alive += 1
            low += hp < LOW_HP
            run = 0
            continue
        run += 1
        if run == DEATH_SAMPLES:
            deaths += 1
            start = rows[i - DEATH_SAMPLES + 1][0]
            before = [r for r in rows[:i] if start - FLASH_BEFORE_S <= r[0] < start and r[1] > 0]
            if flash is not None and any(r[3][flash] == STATES["ready"] for r in before):
                flash_up += 1
    step = (rows[-1][0] - rows[0][0]) / max(1, len(rows) - 1)
    golds = [g for _, g, *_ in data.get("textos") or [] if g is not None]
    return {
        "muertes": deaths,
        "muertes_con_flash": flash_up if flash is not None else None,
        "segundos_poca_vida": round(low * step),
        "parte_poca_vida": round(low / alive, 3) if alive else None,
        "parte_oro_sin_gastar": round(sum(g >= RICH_GOLD for g in golds) / len(golds), 3) if golds else None,
    }
