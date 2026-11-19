"""Relojes de los objetivos de la Grieta por temporada y estructuras del mapa.

Para juzgar la preparación de un objetivo hay que saber cuándo **apareció**, no solo cuándo murió. El
timeline no trae la aparición: se calcula con los relojes de la temporada de la partida (el parche
``16.x`` es la temporada 2026).

Relojes de 2026 (16.x, Grieta del Invocador sin Swiftplay), comprobados con partidas reales de los
parches 16.21-16.23: ningún objetivo murió antes de su hora (dragón ≥ 5:36, larvas ≥ 8:15, heraldo
≥ 15:20, barón ≥ 20:50), entre dragones pasan ≥ 311 s, entre barones ≥ 383 s y el ancestral sale ≥ 382 s
después del alma.

* Dragón a las 5:00 y 5 min después de cada muerte; tras el alma (4.º dragón de un equipo) ya no salen
  dragones elementales: el ancestral aparece 6 min después y luego 6 min tras cada muerte.
* Larvas (3, una sola vez) a las 8:00 hasta las 14:45; heraldo a las 15:00 hasta las 19:45.
* Barón a las 20:00 y 6 min después de cada muerte (en 2026 ya no hay Atakhan).

Si en una partida algún objetivo muere antes de la hora que dan estos relojes (otra temporada, un modo
especial o un cambio de parche que no esté aquí), los relojes de **esa partida** se dan por no fiables y
no se juzga nada que dependa de ellos.
"""

from __future__ import annotations

from app.coach import geometry as geo

SECOND = 1000
MINUTE = 60 * SECOND

SEASON_CLOCKS: dict[str, dict[str, int]] = {
    "16": {
        "dragon_first": 5 * MINUTE,
        "dragon_respawn": 5 * MINUTE,
        "elder_after_soul": 6 * MINUTE,
        "elder_respawn": 6 * MINUTE,
        "grubs_first": 8 * MINUTE,
        "grubs_until": 14 * MINUTE + 45 * SECOND,
        "herald_first": 15 * MINUTE,
        "herald_until": 19 * MINUTE + 45 * SECOND,
        "baron_first": 20 * MINUTE,
        "baron_respawn": 6 * MINUTE,
    },
}
EXCLUDED_QUEUES = {480}  # Swiftplay: objetivos antes y con otros tiempos
EARLY_TOLERANCE = 5 * SECOND  # un objetivo que «muere» antes de aparecer invalida los relojes de la partida
SOUL_DRAGONS = 4

# Torres de la Grieta: (equipo dueño, línea, tipo) -> posición (las de los eventos BUILDING_KILL reales).
_BLUE_TURRETS = {
    ("top", "OUTER_TURRET"): (981, 10441),
    ("top", "INNER_TURRET"): (1512, 6699),
    ("top", "BASE_TURRET"): (1169, 4287),
    ("mid", "OUTER_TURRET"): (5846, 6396),
    ("mid", "INNER_TURRET"): (5048, 4812),
    ("mid", "BASE_TURRET"): (3651, 3696),
    ("bot", "OUTER_TURRET"): (10504, 1029),
    ("bot", "INNER_TURRET"): (6919, 1483),
    ("bot", "BASE_TURRET"): (4281, 1253),
}
_RED_TURRETS = {
    ("top", "OUTER_TURRET"): (4318, 13875),
    ("top", "INNER_TURRET"): (7943, 13411),
    ("top", "BASE_TURRET"): (10481, 13650),
    ("mid", "OUTER_TURRET"): (8955, 8510),
    ("mid", "INNER_TURRET"): (9767, 10113),
    ("mid", "BASE_TURRET"): (11134, 11207),
    ("bot", "OUTER_TURRET"): (13866, 4505),
    ("bot", "INNER_TURRET"): (13327, 8226),
    ("bot", "BASE_TURRET"): (13624, 10572),
}
TURRETS = {100: _BLUE_TURRETS, 200: _RED_TURRETS}
TURRET_ORDER = ("OUTER_TURRET", "INNER_TURRET", "BASE_TURRET")  # una torre no se puede atacar hasta que cae la anterior
TURRET_LABEL = {"OUTER_TURRET": "exterior", "INNER_TURRET": "interior", "BASE_TURRET": "del inhibidor"}


def season_of(version) -> str | None:
    head = str(version or "").split(".")[0]
    return head if head.isdigit() else None


def clocks_for(info: dict) -> dict[str, int] | None:
    """Relojes de la temporada de la partida (``None`` si no se conocen o el modo no los usa)."""
    try:
        queue = int(info.get("queueId") or 0)
    except (TypeError, ValueError):
        queue = 0
    if queue in EXCLUDED_QUEUES:
        return None
    map_id = info.get("mapId")
    if map_id not in (None, 11, "11"):
        return None
    return SEASON_CLOCKS.get(season_of(info.get("gameVersion")) or "")


def assign_spawns(objectives: list[dict], clocks: dict[str, int] | None) -> bool:
    """Pone en cada objetivo ``spawn`` (ms en que apareció según los relojes) y devuelve si cuadran.

    ``objectives``: los de ``_Game`` (ordenados por tiempo, con ``kind``, ``team`` y ``t``). Si algún
    objetivo murió antes de aparecer, ninguno lleva ``spawn`` (relojes no fiables en esta partida).
    """
    for o in objectives:
        o["spawn"] = None
    if not clocks:
        return False
    next_dragon = clocks["dragon_first"]
    next_baron = clocks["baron_first"]
    next_elder: int | None = None
    dragons = {100: 0, 200: 0}
    spawns: list[tuple[dict, int]] = []
    for o in objectives:
        kind, t = o["kind"], o["t"]
        if kind == "dragon":
            spawn = next_dragon
            next_dragon = t + clocks["dragon_respawn"]
            if o["team"] in dragons:
                dragons[o["team"]] += 1
                if dragons[o["team"]] == SOUL_DRAGONS and next_elder is None:
                    next_elder = t + clocks["elder_after_soul"]
                    next_dragon = 10**12  # tras el alma solo sale el ancestral
        elif kind == "elder":
            if next_elder is None:
                return _invalid(objectives)
            spawn = next_elder
            next_elder = t + clocks["elder_respawn"]
        elif kind == "baron":
            spawn = next_baron
            next_baron = t + clocks["baron_respawn"]
        elif kind == "grubs":
            spawn = clocks["grubs_first"]
            if t > clocks["grubs_until"] + EARLY_TOLERANCE:
                return _invalid(objectives)
        elif kind == "herald":
            spawn = clocks["herald_first"]
            if t > clocks["herald_until"] + EARLY_TOLERANCE:
                return _invalid(objectives)
        else:  # Atakhan u otro objetivo que estos relojes no conocen: la temporada no es la de la tabla
            return _invalid(objectives)
        if t < spawn - EARLY_TOLERANCE:
            return _invalid(objectives)
        spawns.append((o, spawn))
    for o, spawn in spawns:
        o["spawn"] = spawn
    return True


def _invalid(objectives: list[dict]) -> bool:
    for o in objectives:
        o["spawn"] = None
    return False


def alive_at(objectives: list[dict], clocks: dict[str, int], t: int) -> list[str]:
    """Objetivos vivos en el mapa en el instante ``t`` según los relojes (``dragon``, ``elder``, ``grubs``…)."""
    out: list[str] = []
    before = [o for o in objectives if o["t"] <= t]
    dragons = [o for o in before if o["kind"] == "dragon"]
    soul = None
    counts = {100: 0, 200: 0}
    for o in dragons:
        if o["team"] in counts:
            counts[o["team"]] += 1
            if counts[o["team"]] == SOUL_DRAGONS:
                soul = o
                break
    if soul is None:
        spawn = (dragons[-1]["t"] + clocks["dragon_respawn"]) if dragons else clocks["dragon_first"]
        if t >= spawn:
            out.append("dragon")
    else:
        elders = [o for o in before if o["kind"] == "elder"]
        spawn = (elders[-1]["t"] + clocks["elder_respawn"]) if elders else soul["t"] + clocks["elder_after_soul"]
        if t >= spawn:
            out.append("elder")
    barons = [o for o in before if o["kind"] == "baron"]
    spawn = (barons[-1]["t"] + clocks["baron_respawn"]) if barons else clocks["baron_first"]
    if t >= spawn:
        out.append("baron")
    if clocks["grubs_first"] <= t <= clocks["grubs_until"] and not any(o["kind"] == "grubs" for o in before):
        out.append("grubs")
    if clocks["herald_first"] <= t <= clocks["herald_until"] and not any(o["kind"] == "herald" for o in before):
        out.append("herald")
    return out


def pit_of(kind: str) -> tuple[int, int]:
    return geo.PITS["dragon"] if kind in ("dragon", "elder") else geo.PITS["baron"]


def standing_turrets(buildings: list[dict], team: int, t: int) -> list[tuple[str, str, tuple[int, int]]]:
    """Torres de ``team`` que siguen en pie en ``t`` y que ya se pueden atacar (la primera de cada línea).

    ``buildings``: los de ``_Game`` (``kind == "tower"`` con ``owner``, ``lane``, ``tower_type`` y ``t``).
    """
    fallen = {(b["lane"], b.get("tower_type")) for b in buildings if b["kind"] == "tower" and b["owner"] == team and b["t"] <= t}
    out = []
    for lane in ("top", "mid", "bot"):
        for kind in TURRET_ORDER:
            if (lane, kind) in fallen:
                continue
            out.append((lane, kind, TURRETS[team][(lane, kind)]))
            break
    return out
