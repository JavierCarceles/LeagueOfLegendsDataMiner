"""Lectura del HUD de jugador (app/videos/hud.py) con frames sintéticos."""

from __future__ import annotations

import cv2
import numpy as np

from app.videos import hud as H
from app.videos.frames import Slots

BAR_GREEN = (40, 170, 60)  # BGR: tono ~62, como la barra de vida del juego
MANA_BLUE = (200, 110, 30)


def _frame(hp: float, mana: float | None, slots: Slots) -> np.ndarray:
    frame = np.full((1080, 1920, 3), 20, np.uint8)
    boxes, side = H.boxes_from(slots, frame.shape)
    q, f = boxes[0], boxes[5]
    left, right = q[0] - side // 2, f[0] + f[2]
    y = q[1] + round(side * 1.4)
    cv2.rectangle(frame, (left, y - 4), (left + round((right - left) * hp), y + 4), BAR_GREEN, -1)
    if mana is not None:
        my = y + round(side * H.RES_DY)
        cv2.rectangle(frame, (left, my - 3), (left + round((right - left) * mana), my + 3), MANA_BLUE, -1)
    return frame


SLOTS = Slots(50, 88, (192, 250, 308, 366))


def test_barras_calibradas_y_porcentajes():
    frames = [_frame(1.0, 1.0, SLOTS), _frame(1.0, 0.8, SLOTS), _frame(0.6, 1.0, SLOTS), _frame(1.0, 1.0, SLOTS)]
    layout = H.Hud({}, {}).calibrate(frames, SLOTS, None)
    assert layout.bar is not None and layout.bar[3] is not None
    raw = [[float(t), *H.Hud._bars(_frame(hp, mana, SLOTS), layout), "????"] for t, (hp, mana) in
           enumerate([(1.0, 1.0)] * 8 + [(0.5, 0.25), (0.25, 0.5), (0.0, 0.0)] * 3)]
    out = H.summarize(raw, [], layout)
    rows = {r[0]: r for r in out["muestras"]}
    assert abs(rows[8.0][1] - 0.5) < 0.03 and abs(rows[8.0][2] - 0.25) < 0.03
    assert abs(rows[9.0][1] - 0.25) < 0.03 and abs(rows[9.0][2] - 0.5) < 0.03
    assert rows[10.0][1] == 0.0


def test_sin_barra_de_recurso():
    frames = [_frame(1.0, None, SLOTS) for _ in range(4)]
    layout = H.Hud({}, {}).calibrate(frames, SLOTS, None)
    assert layout.bar is not None and layout.bar[3] is None


def test_punta_de_barra_con_texto_encima():
    mask = np.array([0, 1, 1, 1, 1, 0, 0, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1], bool)
    assert H._filled_end(mask, 4) == 11  # el hueco del texto no corta; el píxel suelto del final no cuenta
    assert H._filled_end(np.zeros(10, bool), 4) is None


def test_el_ocr_fija_el_largo_de_la_barra():
    raw = [[float(t), 100, 100 + 150, None, "LLLL"] for t in range(12)]  # punta a 150 px
    texts = [[float(t), 500, 750, 1000, None, None] for t in range(0, 12, 3)]  # 75 % de vida
    layout = H.Layout([(0, 0, 50)] * 6, 50, [None] * 4, [0.0] * 4, [None, None], [None, None], [0.0, 0.0], (100, 400, 0, None))
    out = H.summarize(raw, texts, layout)
    assert abs(out["muestras"][0][1] - 0.75) < 0.01  # 150 px son el 75 %: la barra llena mide 200, no 300


def test_habitos_muerte_con_flash():
    rows = [[float(t), 0.8, None, "LLLLLL"] for t in range(10)] + [[10.0 + t, 0.0, None, "GGGGGG"] for t in range(5)]
    rows += [[15.0 + t, 0.2, None, "LLLLCC"] for t in range(5)]
    data = {"muestras": rows, "invocadores": ["SummonerFlash", "SummonerDot"], "textos": [[0, 1500], [10, 200]]}
    out = H.habits(data)
    assert out["muertes"] == 1 and out["muertes_con_flash"] == 1
    assert out["segundos_poca_vida"] == 5 and out["parte_oro_sin_gastar"] == 0.5
