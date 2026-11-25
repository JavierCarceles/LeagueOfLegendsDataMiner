"""Revisión automática de lo recogido de cada vídeo (``python -m app.videos revisar``).

No cambia nada: marca lo que parece mal para mirarlo a mano. Por ejemplo, poca transcripción para lo que dura, una
partida dudosa (muy corta), un reloj que casi no se lee o salta sin sentido, o una cámara que casi nunca aparece.
"""

from __future__ import annotations

from itertools import pairwise

MIN_CHUNKS_PER_MIN = 0.5  # un trozo de texto (~12 s) cada 2 minutos como poco
MIN_GAME_S = 60  # menos partida que esto en todo el vídeo: ¿un falso positivo?
CLOCK_STEP_S = 10  # el reloj se lee cada 10 s de partida
CLOCK_MIN_RATIO = 0.35
CAMERA_MIN_RATIO = 0.25  # posiciones de cámara por muestra de partida (una cada 2 s)


def _base(lang: str | None) -> str:
    return (lang or "").split("-")[0].lower()


def check(data: dict) -> list[str]:
    """Avisos de un vídeo recogido (vacío = todo bien)."""
    return _check_text(data) + _check_frames(data) + [f"aviso de la recogida: {a}" for a in data.get("avisos") or []]


def _check_text(data: dict) -> list[str]:
    text = data.get("transcripcion") or {}
    chunks = text.get("segmentos") or []
    if not chunks:
        return ["sin transcripción"]
    issues = []
    minutes = float(data.get("duracion") or 0) / 60
    if minutes >= 2 and len(chunks) / minutes < MIN_CHUNKS_PER_MIN:
        issues.append(f"poca transcripción: {len(chunks)} trozos en {round(minutes)} min (¿mucho rato sin hablar?)")
    if text.get("tipo") in ("traducido", "doblaje"):
        issues.append(f"transcripción {text['tipo']} ({text.get('idioma')}), no la del audio original")
    spoken = _base(data.get("idioma"))
    if spoken and _base(text.get("idioma")) not in ("", spoken):
        issues.append(f"el vídeo es en «{spoken}» y la transcripción en «{text.get('idioma')}»")
    return issues


def _check_frames(data: dict) -> list[str]:
    frames = data.get("frames")
    if frames is None:
        return ["sin datos de frames"]
    if not frames.get("partida"):
        return []
    game = float(frames.get("segundos_con_partida") or 0)
    issues = []
    if game < MIN_GAME_S:
        issues.append(f"partida muy corta ({round(game)} s en todo el vídeo): ¿falso positivo?")
    issues += _check_clock(frames.get("reloj") or [], game)
    odd = len(frames.get("reloj_apartadas") or [])
    if odd > max(3, 0.15 * len(frames.get("reloj") or [])):
        issues.append(f"muchas lecturas sueltas del reloj apartadas ({odd}): ¿el OCR lee otro texto de la pantalla?")
    issues += _check_camera(frames, game)
    if frames.get("hud") == "jugador" and not frames.get("campeon"):
        issues.append("HUD de jugador sin campeón reconocido (solo se da si es seguro)")
    return issues


def _check_clock(clock: list, game: float) -> list[str]:
    expected = game / CLOCK_STEP_S
    issues = []
    if expected >= 3 and len(clock) / expected < CLOCK_MIN_RATIO:
        issues.append(f"reloj poco leído ({len(clock)} de unas {round(expected)} lecturas)")
    jumps = clock_jumps(clock)
    if len(clock) >= 4 and jumps / (len(clock) - 1) > 0.5:
        issues.append(f"reloj con muchos saltos ({jumps} de {len(clock) - 1}): cortes de edición, rebobinados del coach o malas lecturas")
    return issues


def clock_jumps(clock: list) -> int:
    """Lecturas seguidas en las que el reloj del juego no avanza como el vídeo (ni pausa ni ritmo normal)."""
    bad = 0
    for (t1, g1), (t2, g2) in pairwise(clock):
        video, game = t2 - t1, g2 - g1
        if video > 0 and not 0 <= game <= video * 1.5 + 2:
            bad += 1
    return bad


def _check_camera(frames: dict, game: float) -> list[str]:
    samples = game / 2
    if frames.get("camara_px") is None:
        return ["no se encontró el recuadro de la cámara en el minimapa"]
    seen = len(frames.get("camara") or [])
    if samples >= 10 and seen / samples < CAMERA_MIN_RATIO:
        return [f"cámara poco vista ({seen} de unas {round(samples)} muestras)"]
    return []


def line(data: dict) -> str:
    """Una línea con lo esencial de un vídeo recogido."""
    text = data.get("transcripcion") or {}
    parts = [data.get("id", "?"), (data.get("titulo") or "")[:60], data.get("canal") or "",
             f"{round((data.get('duracion') or 0) / 60)} min", f"texto {text.get('idioma') or '-'} ({len(text.get('segmentos') or [])})"]
    frames = data.get("frames") or {}
    if frames.get("partida"):
        parts.append(_game(frames))
    return " · ".join(str(p) for p in parts if p)


def _game(frames: dict) -> str:
    out = (f"partida {round((frames.get('segundos_con_partida') or 0) / 60)} min, {frames.get('hud') or '?'}, "
           f"reloj {len(frames.get('reloj') or [])}, cámara {len(frames.get('camara') or [])}")
    champion = champion_names(frames)
    return f"{out}, {champion}" if champion else out


def champion_names(frames: dict) -> str:
    """El campeón del vídeo o, si hubo varios (varias partidas), todos: «Vi/Jinx»."""
    main = (frames.get("campeon") or {}).get("nombre")
    return main or "/".join(c.get("nombre") or c.get("clave") or "?" for c in frames.get("campeones") or [])


def report(results: list[dict], everything: bool = False) -> tuple[list[str], dict]:
    """Líneas del informe (solo los vídeos con avisos, salvo ``everything``) y las cuentas."""
    lines: list[str] = []
    counts = {"revisados": len(results), "con_avisos": 0}
    for data in results:
        issues = check(data)
        if issues:
            counts["con_avisos"] += 1
        if issues or everything:
            lines.append(("⚠ " if issues else "✓ ") + line(data))
            lines += [f"    - {issue}" for issue in issues]
    return lines, counts
