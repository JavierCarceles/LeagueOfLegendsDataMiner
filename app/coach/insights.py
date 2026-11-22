"""Resumen de tus partidas: qué haces mal, propósitos y si estás listo para rankeds.

Entrada: filas de ``my_games`` (más recientes primero) con su ``digest``
(métricas + muertes clasificadas + pathing de jungla). Salida: medias frente
a la referencia de tu rol y elo objetivo, una lista de puntos débiles
ordenados por gravedad (con consejo y propósito sugerido), el progreso de
tus propósitos y una nota de «preparado para clasificatorias».
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict

from app.coach import learn
from app.coach.benchmarks import CATEGORIES, METRICS, References, applies

RECENT = 10
READY_SCORE = 70
MIN_GAMES_READY = 10
MIN_GAMES_WEAKNESS = 5  # con menos partidas cualquier punto débil es ruido
SHRINK_GAMES = 5  # la desviación se encoge con pocas partidas: dev · n / (n + 5)
MIN_GOAL_GAMES = 5  # partidas mínimas de un propósito para contar en la nota
TREND_MIN = 8  # partidas (del rol más jugado) para hablar de «Mejorando» o «Empeorando»
TREND_MIN_DEV = 0.1  # y que el cambio sea de al menos un 10 % de la referencia
Z_80 = 1.28  # intervalo al 80 %
# Partidas que no sirven para las conclusiones (se siguen viendo una a una): Swiftplay empieza a nivel 3
# con 1.400 de oro y objetivos antes, y con alguien AFK la partida no es comparable.
EXCLUDED_QUEUES = {480}
AFK_EXCLUDE_MIN = 3.0

# Métrica, peso, plantilla de título, detalle, consejo y propósito sugerido.
RULES: list[dict] = [
    {
        "metric": "deaths_to_jungler_pre14",
        "weight": 1.35,
        "title": "Te pilla el jungla rival",
        "detail": "{v} muertes por partida antes del 14 en las que participa el jungla rival (referencia {ref}).",
        "tip": "Antes de empujar, piensa dónde está el jungla rival: si limpia toda su jungla suele llegar hacia el 3:15-3:45, y si ganquea tras 3 campamentos, desde el 2:30. Si no lo has visto en 30 s, juega como si viniera a tu línea y pon un ward en el río o en el arbusto lateral hacia el 2:15.",
        "goal": ("<=", 0, "Que no te mate el jungla rival antes del minuto 14"),
    },
    {
        "metric": "deaths_ganked",
        "weight": 1.15,
        "skip_if": ["deaths_to_jungler_pre14"],
        "title": "Te ganquean demasiado",
        "detail": "{v} muertes por partida antes del 14 con rivales que no son de tu línea (referencia {ref}).",
        "tip": "Cuando no veas a varios rivales en el mapa, retrasa tu posición. Las rotaciones de mid y support también son ganks: vigila el minimapa cada pocos segundos.",
        "goal": ("<=", 0, "Ninguna muerte por gank antes del minuto 14"),
    },
    {
        "metric": "deaths_no_vision_pre14",
        "weight": 1.0,
        "title": "Mueres sin visión",
        "detail": "{v} muertes por partida antes del 14 en jugadas donde la visión importaba (ganks, río o jungla) sin ward tuyo ni de un aliado cerca en los 2 minutos previos (referencia {ref}).",
        "tip": "Usa el trinket en cuanto salga de recarga (no lo guardes) y compra una pink en cada vuelta a base. Un ward en el río te avisa del gank 5-6 s antes.",
        "goal": (">=", "wards_pre14", "Poner al menos {t} wards antes del minuto 14"),
    },
    {
        "metric": "deaths_overextended",
        "weight": 1.0,
        "title": "Mueres adelantado en la línea",
        "detail": "{v} muertes por partida antes del 14 más cerca de la torre rival que de la tuya (referencia {ref}).",
        "tip": "Si no sabes dónde están su jungla y su support, no pases de la mitad de la línea sin ward. Empuja cuando sepas que el jungla rival está al otro lado del mapa.",
        "goal": ("<=", 0, "No morir adelantado en la línea antes del minuto 14"),
    },
    {
        "metric": "deaths_pre14",
        "weight": 1.2,
        "title": "Mueres demasiado en la fase de líneas",
        "detail": "{v} muertes antes del minuto 14 por partida (referencia {ref}).",
        "tip": "Cada muerte temprana te cuesta ~300 de oro, la oleada y experiencia. Tradea solo con ventaja (nivel, oleada, enfriamientos) y respeta el all-in del rival.",
        "goal": ("<=", 1, "Morir como máximo {t} vez antes del minuto 14"),
    },
    {
        "metric": "deaths_solo",
        "weight": 0.9,
        "title": "Pierdes muchos 1 contra 1",
        "detail": "{v} muertes por partida en las que te mata un único rival (referencia {ref}).",
        "tip": "Repasa tus picos de poder y los del rival (niveles 2, 3 y 6, primer objeto). Antes de un all-in compara vida, nivel, oleada y si tiene el flash.",
        "goal": ("<=", 1, "Morir como máximo {t} vez en 1 contra 1"),
    },
    {
        "metric": "deaths_enemy_jungle",
        "weight": 0.9,
        "title": "Mueres en la jungla rival",
        "detail": "{v} muertes por partida dentro de la jungla rival (referencia {ref}).",
        "tip": "Entra en su jungla solo con visión, sabiendo dónde está su jungla y con tus líneas con prioridad para que puedan ayudarte.",
        "goal": ("<=", 0, "No morir en la jungla rival"),
    },
    {
        "metric": "deaths_isolated_late",
        "weight": 0.85,
        "title": "Te cogen solo a mitad de partida",
        "detail": "{v} muertes por partida después del 14 sin aliados cerca (referencia {ref}).",
        "tip": "Si no ves a los rivales en el mapa, no farmees una línea lateral sin visión. Agrúpate antes de los objetivos y no vayas solo a poner wards profundos.",
        "goal": ("<=", 1, "Como máximo {t} muerte aislado después del 14"),
    },
    {
        "metric": "deaths_unspent_gold",
        "weight": 0.8,
        "title": "Mueres con oro que podías gastar",
        "detail": "{v} muertes por partida llevando desde antes de la pelea oro para algo de tu build y hueco para comprarlo (referencia {ref}).",
        "tip": "Si te llega para una pieza de tu siguiente objeto, vuelve a base antes de pelear: pelear con ese oro sin gastar es pelear con un objeto menos.",
        "goal": ("<=", 0, "No morir con oro para tu build sin gastar"),
    },
    {
        "metric": "gold_hoard_min",
        "weight": 1.0,
        "title": "Tardas en gastar el oro",
        "detail": "{v} minutos de más por partida con oro para tu siguiente compra y hueco en el inventario (referencia {ref}). No cuenta ahorrar con el inventario lleno, la build completa ni las peleas y objetivos.",
        "tip": "Vuelve a base cuando te llegue para un componente de tu siguiente objeto y la oleada lo permita (tras empujarla o al acabar un campamento). Con el inventario lleno sí toca ahorrar para el objeto completo.",
        "goal": ("<=", 0.5, "Como máximo {t} min de más con oro útil sin gastar"),
    },
    {
        "metric": "idle_pct",
        "weight": 1.0,
        "title": "Minutos vacíos a mitad de partida",
        "detail": "El {v} % de los minutos desde el 15 no farmeas, peleas, pones wards ni haces objetivos (referencia {ref} %).",
        "tip": "Si no hay objetivo ni pelea a la vista, farmea la línea con oleada más cercana o tu jungla. Llega a los objetivos 30-60 s antes de que aparezcan, no cuando ya empezó la pelea.",
        "goal": ("<=", 15, "Menos de un {t} % de minutos vacíos desde el 15"),
    },
    {
        "metric": "time_dead_pct",
        "weight": 0.9,
        "title": "Pasas demasiado tiempo muerto",
        "detail": "Un {v} % de la partida muerto (referencia {ref} %).",
        "tip": "Las muertes tardías duran mucho más: después del 25 cada una puede costar 40-60 s y un objetivo.",
        "goal": ("<=", "ref", "Pasar menos de un {t} % de la partida muerto"),
    },
    {
        "metric": "cs_min",
        "weight": 2.2,
        "title": "Te falta farm",
        "detail": "{v} CS por minuto (referencia {ref}).",
        "tip": "Practica el último golpe 10 minutos al día en la herramienta de práctica. Tras cada vuelta a base, ve a la línea que tenga más súbditos.",
        "goal": (">=", "ref", "Hacer {t} CS por minuto"),
    },
    {
        "metric": "cs10",
        "weight": 1.8,
        "skip_if": ["cs_min"],
        "title": "Poco farm en los primeros 10 minutos",
        "detail": "{v} CS a los 10 minutos (referencia {ref}).",
        "tip": "En la fase de líneas el farm manda: no pierdas súbditos por tradear y, si te empujan bajo torre, aprende a rematar con los golpes de torre.",
        "goal": (">=", "ref", "Llegar a {t} CS en el minuto 10"),
    },
    {
        "metric": "cs_diff10",
        "weight": 0.8,
        "min_abs": 6,
        "title": "Tu rival de línea farmea más que tú",
        "detail": "{v} CS a los 10 minutos frente a tu rival directo.",
        "tip": "Si el rival te gana el farm, deja de tradear cuando haya súbditos a punto de morir y prioriza no perder el último golpe.",
        "goal": (">=", 0, "No ir por detrás en CS a los 10 frente a tu rival"),
    },
    {
        "metric": "gold_diff15",
        "weight": 0.8,
        "min_abs": 500,
        "title": "Sales perdiendo de la fase de líneas",
        "detail": "{v} de oro a los 15 minutos frente a tu rival directo.",
        "tip": "Mira tus partidas perdidas: ¿pierdes oro por muertes, por farm o por placas? Lo que más pese es lo primero a corregir.",
        "goal": (">=", 0, "Llegar al minuto 15 sin ir por detrás en oro"),
    },
    {
        "metric": "vision_min",
        "weight": 0.8,
        "title": "Poca visión",
        "detail": "{v} de puntuación de visión por minuto (referencia {ref}).",
        "tip": "Pon el trinket al salir de recarga, compra una pink en cada vuelta y, como support o jungla, cambia a la lente tras el primer objeto.",
        "goal": (">=", "ref", "Al menos {t} de visión por minuto"),
    },
    {
        "metric": "control_wards",
        "weight": 0.6,
        "title": "Compras pocas pinks",
        "detail": "{v} pinks por partida (referencia {ref}).",
        "tip": "Una pink cuesta 75 de oro y aguanta hasta que la destruyen: compra una en cada vuelta a base y colócala en el río o en tu jungla antes de cada objetivo.",
        "goal": (">=", "ref", "Comprar al menos {t} pinks por partida"),
    },
    {
        "metric": "obj_participation",
        "weight": 1.4,
        "title": "No llegas a los objetivos",
        "detail": "Participas en el {v} % de los objetivos de tu equipo (referencia {ref} %).",
        "tip": "Cuando falte 1 minuto para un dragón, las larvas o el barón, empuja tu oleada y ve hacia el objetivo. Como jungla, prepara la visión 45 s antes.",
        "goal": (">=", "ref", "Participar en el {t} % de los objetivos de tu equipo"),
    },
    {
        "metric": "obj_lost_far",
        "weight": 1.3,
        "title": "Regalas objetivos",
        "detail": "{v} objetivos por partida que el rival cogió estando tú lejos y sin que tu equipo sacara algo equivalente a cambio (referencia {ref}). Los buenos cambios (su jungla, torres, un dive, otro objetivo) no cuentan.",
        "tip": "Antes de cada dragón, larvas o barón decide: o llegas a disputarlo o haces algo de valor parecido al otro lado (invadir su jungla, presionar una torre, un dive). Lo que no puede pasar es estar lejos sin sacar nada.",
        "goal": ("<=", 0, "No regalar objetivos: disputarlos o cambiarlos por algo"),
    },
    {
        "metric": "deaths_before_objective",
        "weight": 1.0,
        "title": "Mueres justo antes de los objetivos",
        "detail": "{v} muertes por partida seguidas de un objetivo del rival sin que tu equipo sacara nada a cambio (referencia {ref}). No cuentan las peleas que ganasteis.",
        "tip": "En el minuto previo a un objetivo no te expongas: nada de farmear solo en la línea contraria ni de entrar sin visión en arbustos.",
        "goal": ("<=", 0, "No morir justo antes de un objetivo rival"),
    },
    {
        "metric": "kp",
        "weight": 1.2,
        "title": "Poca participación en peleas",
        "detail": "Participas en el {v} % de las kills de tu equipo (referencia {ref} %).",
        "tip": "Cuando tu equipo se agrupe o vaya a por un objetivo, ve tú también. Empuja tu oleada antes de rotar para no perder farm.",
        "goal": (">=", "ref", "Participar en el {t} % de las kills"),
    },
    {
        "metric": "impact_pre10",
        "weight": 0.9,
        "title": "Poco impacto en el early",
        "detail": "{v} jugadas con impacto antes del minuto 10 por partida: kills en cualquier zona, larvas o dragón e invasiones (referencia {ref}).",
        "tip": "Planifica la ruta en la pantalla de carga: gank a una línea con control de masas y empuje, o si no hay gank, objetivo con prioridad o invadir el lado donde no está su jungla.",
        "goal": (">=", 2, "Al menos {t} jugadas con impacto antes del minuto 10"),
    },
    {
        "metric": "enemy_ganks_far",
        "weight": 1.0,
        "title": "No sigues al jungla rival",
        "detail": "{v} ganks del jungla rival por partida en la otra mitad del mapa mientras tú estabas lejos (referencia {ref}).",
        "tip": "Busca dónde aparece en los primeros minutos (wards en su jungla, quién falta en las líneas) y sigue su rastro: ve a su mismo lado para contraganquear, o al contrario para invadir o hacer un objetivo.",
        "goal": ("<=", 0, "Que el jungla rival no ganquee al otro lado sin respuesta"),
    },
    {
        "metric": "jungle_cs10",
        "weight": 1.5,
        "title": "Limpias la jungla despacio",
        "detail": "{v} CS de jungla a los 10 minutos (referencia {ref}).",
        "tip": "Practica la primera limpieza completa (6 campamentos hacia el 3:15) y no dejes campamentos vivos al ir a ganquear.",
        "goal": (">=", "ref", "Llegar a {t} CS de jungla en el minuto 10"),
    },
    {
        "metric": "first_item_min",
        "weight": 1.0,
        "title": "Tardas en completar tu primer objeto",
        "detail": "Tu primer objeto llega de media al minuto {v} (referencia {ref}).",
        "tip": "El primer objeto es tu mayor pico de poder: farmea las oleadas y campamentos libres entre jugadas, vuelve a base en cuanto te llegue para un componente y no mueras con el oro encima.",
        "goal": ("<=", "ref", "Completar el primer objeto antes del minuto {t}"),
    },
    {
        "metric": "r_late",
        "weight": 0.6,
        "title": "Subes la R tarde",
        "detail": "{v} veces por partida subes la R más de 30 s después de llegar a nivel 6 (referencia {ref}).",
        "tip": "Usa el atajo de subir habilidad (Ctrl + tecla) en cuanto suba el nivel, o activa la subida automática de la R en las opciones.",
        "goal": ("<=", 0, "Subir la R en cuanto llegues a nivel 6"),
    },
    {
        "metric": "deaths",
        "weight": 0.8,
        "skip_if": ["deaths_pre14", "deaths_isolated_late"],
        "title": "Mueres demasiado",
        "detail": "{v} muertes por partida (referencia {ref}).",
        "tip": "Antes de cada pelea pregúntate si puedes ganarla: número de rivales visibles, objetos, niveles y enfriamientos.",
        "goal": ("<=", "ref", "Morir {t} veces o menos"),
    },
]

RULE_BY_METRIC = {r["metric"]: r for r in RULES}

FUNDAMENTALS = [
    ("deaths_pre14", 1.2),
    ("deaths_to_jungler_pre14", 1.0),
    ("cs_min", 1.0),
    ("vision_min", 0.8),
    ("kp", 0.6),
    ("time_dead_pct", 0.8),
    ("idle_pct", 0.8),
    ("gold_hoard_min", 0.6),
    ("obj_participation", 0.7),
    ("obj_lost_far", 0.5),
    ("first_item_min", 0.4),
]

GOAL_CATALOG = [
    {"metric": "deaths_pre14", "op": "<=", "target": 1, "label": "Morir como máximo {t} vez antes del minuto 14"},
    {"metric": "deaths_to_jungler_pre14", "op": "<=", "target": 0, "label": "Que no te mate el jungla rival antes del minuto 14"},
    {"metric": "deaths_no_vision_pre14", "op": "<=", "target": 0, "label": "No morir sin un ward reciente antes del minuto 14"},
    {"metric": "cs10", "op": ">=", "target": 65, "label": "Llegar a {t} CS en el minuto 10"},
    {"metric": "cs_min", "op": ">=", "target": 7, "label": "Hacer {t} CS por minuto"},
    {"metric": "wards_pre14", "op": ">=", "target": 4, "label": "Poner al menos {t} wards antes del minuto 14"},
    {"metric": "control_wards", "op": ">=", "target": 2, "label": "Comprar al menos {t} pinks por partida"},
    {"metric": "vision_min", "op": ">=", "target": 1.0, "label": "Al menos {t} de visión por minuto"},
    {"metric": "gold_hoard_min", "op": "<=", "target": 0.5, "label": "Como máximo {t} min de más con oro útil sin gastar"},
    {"metric": "idle_pct", "op": "<=", "target": 15, "label": "Menos de un {t} % de minutos vacíos desde el 15"},
    {"metric": "time_dead_pct", "op": "<=", "target": 6, "label": "Pasar menos de un {t} % de la partida muerto"},
    {"metric": "obj_participation", "op": ">=", "target": 60, "label": "Participar en el {t} % de los objetivos de tu equipo"},
    {"metric": "deaths_before_objective", "op": "<=", "target": 0, "label": "No morir justo antes de un objetivo rival"},
    {"metric": "deaths_unspent_gold", "op": "<=", "target": 0, "label": "No morir con oro para tu build sin gastar"},
    {"metric": "obj_lost_far", "op": "<=", "target": 0, "label": "No regalar objetivos: disputarlos o cambiarlos por algo"},
    {"metric": "first_item_min", "op": "<=", "target": 12, "label": "Completar el primer objeto antes del minuto {t}"},
    {"metric": "r_late", "op": "<=", "target": 0, "label": "Subir la R en cuanto llegues a nivel 6"},
    {"metric": "kp", "op": ">=", "target": 50, "label": "Participar en el {t} % de las kills"},
    {"metric": "deaths", "op": "<=", "target": 5, "label": "Morir {t} veces o menos"},
    {"metric": "ganks_pre10", "op": ">=", "target": 1, "label": "Al menos {t} gank con kill antes del minuto 10", "role": "JUNGLE"},
    {"metric": "impact_pre10", "op": ">=", "target": 2, "label": "Al menos {t} jugadas con impacto antes del minuto 10", "role": "JUNGLE"},
    {"metric": "enemy_ganks_far", "op": "<=", "target": 0, "label": "Que el jungla rival no ganquee al otro lado sin respuesta", "role": "JUNGLE"},
]

TAG_LABELS = {
    "gank": "Gank",
    "jungler": "Jungla rival",
    "no_ward": "Sin ward previo",
    "overextended": "Adelantado",
    "solo": "1 contra 1",
    "enemy_jungle": "En su jungla",
    "unspent_gold": "Oro sin gastar",
    "before_objective": "Antes de objetivo",
    "isolated": "Aislado",
    "executed": "Torre/súbditos",
    "fight_won": "Pelea ganada",
    "fight_even": "Pelea igualada",
    "traded": "Con algo a cambio",
    "game_end": "Empujón final",
    "afk": "Antes de desconectarte",
    "fight_outnumbered": "Con uno menos en un objetivo",
    "collapsed": "Cazado en su mitad",
    "baron_buff": "Con la mejora del barón",
    "bounty": "Recompensa regalada",
}


# ------------------------------------------------------------------ utilidades
def reliable(game: dict) -> bool:
    """¿Sirve esta partida para las conclusiones? No si es Swiftplay ni si alguien estuvo AFK 3 minutos o más."""
    if game.get("queue") in EXCLUDED_QUEUES:
        return False
    m = game.get("metrics") or {}
    if (m.get("riot_afk") or 0) > 0:  # Riot detectó a alguien AFK (aunque nuestra detección por posiciones no llegue a 3 min)
        return False
    return all((m.get(k) or 0) < AFK_EXCLUDE_MIN for k in ("afk_min", "ally_afk_min", "enemy_afk_min"))


def _mean(values: list[float]) -> float | None:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _var(values: list[float]) -> float:
    """Varianza muestral (0 con menos de 2 valores)."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / (len(values) - 1)


def _round(value: float | None, decimals: int) -> float | None:
    if value is None:
        return None
    return round(value, decimals) if decimals else round(value)


def _fmt(value: float | None, decimals: int) -> str:
    if value is None:
        return "—"
    text = f"{value:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return text


def deviation(metric_key: str, value: float, ref: float) -> float:
    """> 0 = peor que la referencia (en proporción de la referencia)."""
    m = METRICS[metric_key]
    scale = max(abs(ref), m.floor)
    return (value - ref) / scale if m.better == "lower" else (ref - value) / scale


def passes(value: float | None, op: str, target: float) -> bool | None:
    if value is None:
        return None
    return value <= target if op == "<=" else value >= target


def fundamentals_score(metrics: dict, refs: dict, role: str | None) -> float | None:
    """Nota 0-10 de fundamentos en una partida (5 = como la referencia)."""
    total = weight_sum = 0.0
    for key, weight in FUNDAMENTALS:
        m = METRICS[key]
        value, ref = metrics.get(key), refs.get(key)
        if value is None or ref is None or not applies(m, role):
            continue
        if m.better == "higher":
            if ref <= 0:
                continue
            term = math.log2(max(value, 1e-3) / ref)
        else:
            term = math.log2((ref + m.floor) / (value + m.floor))
        total += weight * max(-1.0, min(1.0, term))
        weight_sum += weight
    if not weight_sum:
        return None
    return round(max(0.0, min(10.0, 5 + 5 * total / weight_sum)), 1)


def goal_label(template: str, target: float, metric_key: str) -> str:
    decimals = METRICS[metric_key].decimals if metric_key in METRICS else 0
    if float(target).is_integer():
        decimals = 0
    return template.replace("{t}", _fmt(target, min(decimals, 2)))


# ---------------------------------------------------------------- resumen
def _trend(m, pairs: list[tuple[float, float, str | None]]) -> dict | None:
    """Últimas partidas frente a las anteriores, solo si el cambio es claro.

    Dentro del rol más jugado (cada rol tiene su referencia: mezclar roles inventa tendencias), con al menos
    ``TREND_MIN`` partidas, un cambio de al menos ``TREND_MIN_DEV`` de la referencia y mayor que su error típico.
    """
    if len(pairs) < TREND_MIN:
        return None
    role = Counter(r for _v, _ref, r in pairs).most_common(1)[0][0]
    same = [(v, ref) for v, ref, r in pairs if r == role]
    if len(same) < TREND_MIN:
        return None
    half = len(same) // 2
    recent_v, older_v = [v for v, _ in same[:half]], [v for v, _ in same[half:]]
    recent, older = _mean(recent_v), _mean(older_v)
    ref = _mean([r for _, r in same])
    delta = deviation(m.key, older, ref) - deviation(m.key, recent, ref)  # > 0: mejora
    se = math.sqrt(_var(recent_v) / len(recent_v) + _var(older_v) / len(older_v))
    clear = abs(delta) >= TREND_MIN_DEV and abs(recent - older) > se
    return {"recent": _round(recent, m.decimals), "older": _round(older, m.decimals), "improving": (delta > 0) if clear else None, "games": len(same)}


def game_refs(refs, g: dict) -> dict:
    """Referencias de una partida: las de su rol y, si hay datos de OTPs de su campeón, las de ese campeón en farm."""
    return refs.for_game(g) if hasattr(refs, "for_game") else refs.for_role(g.get("role"))


def metric_table(games: list[dict], refs: References) -> list[dict]:
    games = [g for g in games if reliable(g)]
    rows = []
    for key, m in METRICS.items():
        pairs = []
        for g in games:
            if not applies(m, g["role"]):
                continue
            value = (g["metrics"] or {}).get(key)
            ref = game_refs(refs, g).get(key)
            if value is None or ref is None:
                continue
            pairs.append((value, ref, g["role"]))
        if not pairs:
            continue
        # Muestra de la referencia (si es calibrada en todos los roles de estas partidas): su incertidumbre también cuenta.
        sizes = [refs.sample_size(r, key) if hasattr(refs, "sample_size") else None for r in {r for _, _, r in pairs}]
        ref_n = min(sizes) if sizes and all(sizes) else None
        values = [v for v, _, _ in pairs]
        value = _mean(values)
        ref = _mean([r for _, r, _ in pairs])
        dev = deviation(key, value, ref)
        status = "good" if dev <= -0.05 else "bad" if dev >= 0.15 else "ok"
        trend = _trend(m, pairs)
        rows.append(
            {
                "key": key,
                "label": m.label,
                "category": m.category,
                "unit": m.unit,
                "decimals": m.decimals,
                "better": m.better,
                "value": _round(value, m.decimals + 1),
                "ref": _round(ref, m.decimals + 1),
                "dev": round(dev, 3),
                "status": status,
                "games": len(pairs),
                "ref_n": ref_n,
                "sd": _round(math.sqrt(_var(values)), m.decimals + 2) if len(values) >= 2 else None,
                "trend": trend,
                "series": [_round(v, m.decimals + 1) for v, _, _ in pairs[:20]],
            }
        )
    return rows


def _count_beyond(value: float, ref: float, n: int, better: str, ref_n: int | None = None) -> bool:
    """Recuentos por partida (muertes, objetivos…): el intervalo al 80 % tiene que quedar fuera de la referencia.

    Si la referencia sale de una muestra (calibrada), también se suma su incertidumbre (dos tasas de Poisson).
    """
    variance = max(value, 0.0) / max(n, 1) + (max(ref, 0.0) / ref_n if ref_n else 0.0)
    margin = Z_80 * math.sqrt(variance)
    return value - margin > ref if better == "lower" else value + margin < ref


def _mean_beyond(row: dict, ref: float, m) -> bool:
    """Métricas que no son recuentos: el intervalo al 80 % de tu media tiene que quedar fuera de la referencia."""
    sd, n = row.get("sd"), row["games"]
    if sd is None or n < 2:
        return False
    bound = Z_80 * sd / math.sqrt(n)
    return row["value"] - bound > ref if m.better == "lower" else row["value"] + bound < ref


def _consistent(row: dict, m) -> bool:
    """Diferencias frente al rival: que vayas por detrás de forma consistente, no por una sola partida."""
    sd, n = row.get("sd"), row["games"]
    if sd is None:
        return False
    bound = Z_80 * sd / math.sqrt(n)
    return row["value"] + bound < 0 if m.better == "higher" else row["value"] - bound > 0


def weaknesses(table: list[dict], death_tags: dict, limit: int = 8) -> list[dict]:
    by_key = {row["key"]: row for row in table}
    found: dict[str, dict] = {}
    for rule in RULES:
        row = by_key.get(rule["metric"])
        if not row or row["games"] < MIN_GAMES_WEAKNESS:
            continue
        m = METRICS[rule["metric"]]
        value, ref, n = row["value"], row["ref"], row["games"]
        dev = row["dev"] * n / (n + SHRINK_GAMES)  # con pocas partidas la desviación pesa menos
        if "min_abs" in rule:
            if (m.better == "higher" and value > -rule["min_abs"]) or (m.better == "lower" and value < rule["min_abs"]):
                continue
            if not _consistent(row, m):
                continue
        elif dev < 0.15:
            continue
        if m.count and not _count_beyond(value, ref, n, m.better, row.get("ref_n")):
            continue
        if not m.count and "min_abs" not in rule and not _mean_beyond(row, ref, m):
            continue  # una o dos partidas malas no bastan: la media tiene que quedar fuera con claridad
        if any(s in found for s in rule.get("skip_if", [])):
            continue
        # Escala saturante: conserva el orden aunque te alejes mucho de la referencia.
        severity = round(100 * (1 - math.exp(-max(0.0, dev) * rule["weight"])), 1)
        detail = rule["detail"].replace("{v}", _fmt(value, m.decimals)).replace("{ref}", _fmt(ref, m.decimals))
        extra = _death_context(rule["metric"], death_tags)
        found[rule["metric"]] = {
            "id": rule["metric"],
            "category": m.category,
            "category_label": CATEGORIES.get(m.category, m.category),
            "severity": severity,
            "level": "high" if severity >= 50 else "medium" if severity >= 25 else "low",
            "title": rule["title"],
            "detail": detail + (f" {extra}" if extra else ""),
            "tip": rule["tip"],
            "metric": rule["metric"],
            "value": value,
            "ref": ref,
            "unit": m.unit,
            "games": row["games"],
            "trend": row["trend"],
            "goal": _suggested_goal(rule, row, by_key),
            "learn": learn.for_metric(rule["metric"]),
        }
    return sorted(found.values(), key=lambda w: w["severity"], reverse=True)[:limit]


# ------------------------------------------------------------ errores que repites
# Errores concretos que el análisis detecta en cada partida (cada uno se puede ver en la repetición), agrupados por
# tipo. No dependen de ninguna referencia: si se repiten en muchas partidas, son algo a corregir aunque tus medias
# estén en lo normal de tu elo.
RECURRING_MIN_GAMES = 3  # partidas distintas con ese error…
RECURRING_MIN_RATE = 0.2  # …y al menos en el 20 % de tus partidas fiables con timeline
RECURRING_EXAMPLES = 4
# No son errores aunque se muestren: peleas ganadas, buenos cambios, AFK, final de partida, build completa (ahorrar
# es lo correcto) y objetivos que no se podían disputar (inferioridad numérica).
NOT_A_MISTAKE = {"fight_won", "traded", "fair_trade", "afk", "game_end", "full_build", "outnumbered"}
SEVERITY_WEIGHT = {"high": 3.0, "medium": 2.0, "low": 1.0}
MISTAKE_TYPES: list[dict] = [  # el primero que encaje (de lo más concreto a lo más general)
    {"id": "death_outnumbered_objective", "kind": "death", "need": ("fight_outnumbered",), "metric": None,
     "title": "Peleas los objetivos con uno menos",
     "tip": "Antes de entrar al foso cuenta los vivos: si un aliado lleva un rato muerto y ellos están todos, cede el objetivo o cámbialo al otro lado (su jungla, una torre). Solo el ancestral y su alma merecen pelearse con uno menos."},
    {"id": "death_before_objective", "kind": "death", "need": ("before_objective",), "metric": "deaths_before_objective",
     "title": "Mueres justo antes de un objetivo rival"},
    {"id": "death_gank_no_ward", "kind": "death", "need": ("gank", "no_ward"), "metric": "deaths_no_vision_pre14",
     "title": "Te ganquean sin visión"},
    {"id": "death_overextended", "kind": "death", "need": ("overextended",), "metric": "deaths_overextended",
     "title": "Mueres adelantado en la línea"},
    {"id": "death_enemy_jungle", "kind": "death", "need": ("enemy_jungle",), "metric": "deaths_enemy_jungle",
     "title": "Mueres en la jungla rival"},
    {"id": "death_unspent_gold", "kind": "death", "need": ("unspent_gold",), "metric": "deaths_unspent_gold",
     "title": "Mueres con oro sin gastar"},
    {"id": "death_collapsed", "kind": "death", "need": ("collapsed",), "metric": None,
     "title": "Te cazan solo en la mitad rival del mapa",
     "tip": "Al otro lado del río, en una línea lateral, entra solo con wards y sabiendo dónde están al menos 3 rivales. Si desaparecen del mapa, retírate hacia tu torre antes de que lleguen."},
    {"id": "death_isolated", "kind": "death", "need": ("isolated",), "metric": "deaths_isolated_late",
     "title": "Te cogen solo, lejos de tu equipo"},
    {"id": "death_solo", "kind": "death", "need": ("solo",), "metric": "deaths_solo", "title": "Pierdes duelos 1 contra 1"},
    {"id": "death_gank", "kind": "death", "need": ("gank",), "metric": "deaths_ganked", "title": "Te ganquean"},
    {"id": "objective_far", "kind": "objective", "need": ("far",), "metric": "obj_lost_far",
     "title": "El rival coge objetivos estando tú lejos y sin cambio"},
    {"id": "objective_late_recall", "kind": "objective", "need": ("late_recall",), "metric": None,
     "title": "Vuelves a base a destiempo antes de los objetivos",
     "tip": "Mira el reloj del objetivo: empuja tu oleada o acaba el campamento y vuelve a base 60-90 s antes de que aparezca. Si ya está en el mapa y tu equipo va a por él, no vuelvas: ve con ellos."},
    {"id": "objective_late", "kind": "objective", "need": ("late",), "metric": None,
     "title": "Llegas tarde a los objetivos que se pelean",
     "tip": "Un minuto antes de que aparezca un objetivo, ponte en camino: empuja la oleada y llega al foso con tiempo para poner visión. Llegar cuando ya ha empezado la pelea deja a tu equipo con uno menos."},
    {"id": "fight_unconverted", "kind": "objective", "need": ("unconverted",), "metric": None,
     "title": "Ganáis peleas y no las convertís",
     "tip": "Tras ganar una pelea con ventaja numérica, lo primero es el objetivo más cercano (dragón, barón, una torre) y después la base. La ventaja dura hasta que reaparecen."},
    {"id": "baron_wasted", "kind": "objective", "need": ("baron_wasted",), "metric": None,
     "title": "No aprovecháis el barón",
     "tip": "Con la mejora del barón, agrupaos y asediad una torre (o presionad dos líneas a la vez) en los 3 minutos que dura; no os separéis a farmear."},
    {"id": "objective_dead", "kind": "objective", "need": ("dead",), "metric": "obj_lost_dead",
     "title": "El rival coge objetivos mientras estás muerto",
     "tip": "Antes de cada objetivo, juega para estar vivo: retrocede si no ves a varios rivales y no busques peleas sin visión en el minuto previo."},
    {"id": "gold_hoard", "kind": "gold", "need": (), "metric": "gold_hoard_min", "title": "Tardas en gastar el oro"},
    {"id": "r_late", "kind": "build", "need": ("skills",), "metric": "r_late", "title": "Subes la R tarde"},
    {"id": "no_boots", "kind": "build", "need": ("boots",), "metric": None, "title": "Sin botas mejoradas al minuto 20",
     "tip": "Las botas de nivel 2 cuestan poco y dan mucha movilidad: cómpralas entre el primer y el segundo objeto salvo que tu campeón no las use."},
    {"id": "no_antiheal", "kind": "build", "need": ("anti_heal",), "metric": None, "title": "Sin Heridas graves contra curaciones",
     "tip": "Contra campeones que se curan mucho, compra un componente de Heridas graves (desde 800 de oro) en cuanto empiecen a pelear."},
    {"id": "no_resist", "kind": "build", "need": ("resist",), "metric": None, "title": "No te defiendes del daño que más recibes",
     "tip": "Mira en la pantalla de muerte qué tipo de daño te mata: si casi todo es físico o mágico, mete armadura o resistencia mágica en tu siguiente compra."},
    {"id": "enemy_jungler_far", "kind": "jungle", "need": ("tracking",), "metric": "enemy_ganks_far", "title": "No sigues al jungla rival"},
    {"id": "pink_unused", "kind": "vision", "need": ("pink",), "metric": None, "title": "Llevas pinks y no las usas en los objetivos",
     "tip": "Una pink en el inventario no da visión: ponla en el foso o en el arbusto de entrada un minuto antes de cada objetivo."},
]


def _mistake_type(kind: str, tags: set) -> dict | None:
    if tags & NOT_A_MISTAKE:
        return None
    for mt in MISTAKE_TYPES:
        if mt["kind"] == kind and all(t in tags for t in mt["need"]):
            return mt
    return None


def recurring_mistakes(games: list[dict]) -> dict:
    """Tipos de error que se repiten en tus partidas: en cuántas, cuántas veces y ejemplos para verlos."""
    pool = [g for g in games if reliable(g) and g.get("has_timeline") and g.get("mistakes") is not None]
    groups: dict[str, dict] = {}
    for g in pool:
        for kind, severity, t, tags, title in g["mistakes"]:
            mt = _mistake_type(kind, set(tags or []))
            if mt is None:
                continue
            grp = groups.setdefault(mt["id"], {"type": mt, "count": 0, "games": set(), "weight": 0.0, "examples": []})
            grp["count"] += 1
            grp["games"].add(g["match_id"])
            grp["weight"] += SEVERITY_WEIGHT.get(severity, 1.0)
            if len(grp["examples"]) < RECURRING_EXAMPLES and not any(e["match_id"] == g["match_id"] for e in grp["examples"]):
                grp["examples"].append({"match_id": g["match_id"], "t": t, "title": title, "champ": g["champ"], "win": g["win"]})
    n = len(pool)
    out = []
    for grp in groups.values():
        games_with = len(grp["games"])
        rate = games_with / n if n else 0.0
        if games_with < RECURRING_MIN_GAMES or rate < RECURRING_MIN_RATE:
            continue
        mt = grp["type"]
        rule = RULE_BY_METRIC.get(mt["metric"]) if mt["metric"] else None
        out.append(
            {
                "id": mt["id"],
                "title": mt["title"],
                "tip": mt.get("tip") or (rule or {}).get("tip") or "",
                "metric": mt["metric"],
                "games": games_with,
                "count": grp["count"],
                "of": n,
                "rate": round(rate * 100),
                "severity": round(grp["weight"] / grp["count"], 2),
                "score": round(rate * grp["weight"] / grp["count"], 3),
                "examples": grp["examples"],
                "learn": learn.for_mistake(mt["id"]),
            }
        )
    out.sort(key=lambda x: x["score"], reverse=True)
    return {"items": out, "sample": n}


# ------------------------------------------------------------------ hábitos
SESSION_GAP_MS = 60 * 60_000  # más de una hora entre partidas: otra sesión
TILT_STREAK = 2  # derrotas seguidas
TILT_MIN_GAMES = 5
TILT_MIN_DROP = 10  # puntos de porcentaje
LONG_SESSION = 5  # los coaches recomiendan sesiones de 3-5 partidas concentradas
POOL_MAX = 3
POOL_WINDOW = 20  # últimas partidas del rol
POOL_MIN_GAMES = 10
LEAD_WASTED = 2000  # oro de equipo a favor


def _wr(results: list[bool]) -> int | None:
    return round(sum(results) / len(results) * 100) if results else None


def sessions_of(games: list[dict]) -> list[list[dict]]:
    """Partidas agrupadas en sesiones (de la más antigua a la más reciente; > 1 h entre partidas = otra sesión)."""
    ordered = sorted((g for g in games if g.get("game_start")), key=lambda g: g["game_start"])
    out: list[list[dict]] = []
    prev_end = None
    for g in ordered:
        start = g["game_start"]
        if prev_end is None or start - prev_end > SESSION_GAP_MS:
            out.append([])
        out[-1].append(g)
        prev_end = start + (g.get("duration") or 0) * 1000
    return out


def tilt_report(games: list[dict]) -> dict | None:
    """Tu porcentaje de victorias tras 2 derrotas seguidas (en la misma sesión) y por número de partida en la sesión.

    Solo avisa si la caída es clara: al menos 10 puntos y más que el error típico de esa muestra.
    """
    sessions = sessions_of(games)
    if not sessions:
        return None
    after: list[bool] = []
    by_index: dict[int, list[bool]] = defaultdict(list)
    for s in sessions:
        streak = 0
        for i, g in enumerate(s):
            by_index[min(i + 1, 4)].append(bool(g["win"]))
            if streak >= TILT_STREAK:
                after.append(bool(g["win"]))
            streak = 0 if g["win"] else streak + 1
    results = [bool(g["win"]) for s in sessions for g in s]
    overall = sum(results) / len(results)
    wr_after = sum(after) / len(after) if after else None
    se = math.sqrt(max(overall * (1 - overall), 0.05) / len(after)) if after else None
    drop = (overall - wr_after) * 100 if wr_after is not None else None
    tilted = bool(after) and len(after) >= TILT_MIN_GAMES and drop >= max(TILT_MIN_DROP, Z_80 * se * 100)
    long_sessions = [s for s in sessions if len(s) >= LONG_SESSION]
    if tilted:
        message = (
            f"Tras {TILT_STREAK} derrotas seguidas ganas el {round(wr_after * 100)} % ({len(after)} partidas), frente al {round(overall * 100)} % "
            "general: después de 2 derrotas, para al menos 15 minutos antes de la siguiente."
        )
    elif len(after) >= TILT_MIN_GAMES:
        message = f"Tras {TILT_STREAK} derrotas seguidas: {round(wr_after * 100)} % de victorias en {len(after)} partidas (general {round(overall * 100)} %). No hay una caída clara."
    else:
        message = f"Aún hay pocas partidas jugadas tras {TILT_STREAK} derrotas seguidas ({len(after)}) para saber si te afecta."
    return {
        "games": len(results),
        "sessions": len(sessions),
        "wr": round(overall * 100),
        "after_losses": {"games": len(after), "wr": _wr(after)},
        "tilted": tilted,
        "by_game": [{"n": n, "label": f"{n}.ª" if n < 4 else "4.ª o más", "games": len(v), "wr": _wr(v)} for n, v in sorted(by_index.items())],
        "long_sessions": len(long_sessions),
        "message": message,
        "learn": learn.topic("tilt") if tilted else None,
    }


def pool_report(games: list[dict]) -> list[dict]:
    """Por rol: cuántos campeones distintos juegas en tus últimas partidas y un núcleo propuesto de 2-3."""
    by_role: dict[str, list[dict]] = defaultdict(list)
    for g in sorted(games, key=lambda g: g.get("game_start") or 0, reverse=True):
        if g.get("role") and len(by_role[g["role"]]) < POOL_WINDOW:
            by_role[g["role"]].append(g)
    out = []
    for role, items in by_role.items():
        if len(items) < POOL_MIN_GAMES:
            continue
        champs: dict[int, list[bool]] = defaultdict(list)
        for g in items:
            champs[g["champ"]].append(bool(g["win"]))
        ranked = sorted(champs.items(), key=lambda kv: (len(kv[1]), sum(kv[1])), reverse=True)
        core = [{"champ": c, "games": len(r), "wr": _wr(r)} for c, r in ranked[:POOL_MAX] if len(r) >= 2]
        wide = len(champs) > POOL_MAX
        out.append(
            {
                "role": role,
                "games": len(items),
                "champions": len(champs),
                "wide": wide,
                "core": core,
                "message": (
                    f"Has jugado {len(champs)} campeones distintos en tus últimas {len(items)} partidas de este rol: los coaches recomiendan un núcleo "
                    "de 2-3 para dominar sus matchups y picos de poder."
                    if wide else f"Juegas {len(champs)} {'campeón' if len(champs) == 1 else 'campeones'} en tus últimas {len(items)} partidas de este rol."
                ),
            }
        )
    out.sort(key=lambda r: r["games"], reverse=True)
    return out


def leads_report(games: list[dict]) -> dict | None:
    """Derrotas con 2.000 de oro de equipo a favor en algún momento (ventajas que se escapan) y remontadas."""
    pool = [g for g in games if reliable(g) and g.get("has_timeline") and (g.get("metrics") or {}).get("max_lead") is not None]
    if not pool:
        return None
    losses = [g for g in pool if not g["win"]]
    wasted = [g for g in losses if g["metrics"]["max_lead"] >= LEAD_WASTED]
    comebacks = [g for g in pool if g["win"] and (g["metrics"].get("max_deficit") or 0) <= -LEAD_WASTED]
    return {
        "games": len(pool),
        "losses": len(losses),
        "wasted": len(wasted),
        "comebacks": len(comebacks),
        "examples": [
            {"match_id": g["match_id"], "champ": g["champ"], "lead": g["metrics"]["max_lead"], "t": round((g["metrics"].get("max_lead_min") or 0) * 60)}
            for g in sorted(wasted, key=lambda g: g["metrics"]["max_lead"], reverse=True)[:RECURRING_EXAMPLES]
        ],
        "learn": learn.topic("macro") if wasted and len(wasted) >= 2 else None,
    }


# ------------------------------------------------------------ revisión guiada
# Método de revisión de Mauro Garih: 3 momentos por partida y 3 preguntas en cada uno (docs/coaches.md).
REVIEW_QUESTIONS = [
    {"id": "knew", "label": "¿Qué sabías en ese momento?", "hint": "Dónde estaban los rivales y su jungla, tu oro, la oleada, qué objetivo venía…"},
    {"id": "decided", "label": "¿Qué decidiste y por qué?", "hint": "La jugada que hiciste y lo que pensabas que iba a pasar."},
    {"id": "better", "label": "¿Qué habría sido mejor?", "hint": "Lo que harías la próxima vez en la misma situación."},
]
REVIEW_MOMENTS = 3


def review_moments(moments: list[dict], n: int = REVIEW_MOMENTS) -> list[dict]:
    """Los 3 momentos que más merece la pena revisar: los más graves, a ser posible de tipos distintos."""
    cands = [
        m for m in moments
        if m.get("severity") in SEVERITY_WEIGHT and m.get("kind") not in ("afk", "trade") and not set(m.get("tags") or []) & NOT_A_MISTAKE
    ]
    cands.sort(key=lambda m: (-SEVERITY_WEIGHT[m["severity"]], m["t"]))
    picked: list[dict] = []
    kinds: set[str] = set()
    for m in cands:  # primero uno de cada tipo (muerte, objetivo, oro…), solo entre los graves o a mejorar
        if len(picked) == n or SEVERITY_WEIGHT[m["severity"]] < 2:
            break
        if m["kind"] not in kinds:
            picked.append(m)
            kinds.add(m["kind"])
    for m in cands:
        if len(picked) >= n:
            break
        if m not in picked:
            picked.append(m)
    return sorted(picked, key=lambda m: m["t"])


def habits(games: list[dict]) -> dict:
    """Hábitos entre partidas (tilt, sesiones, pool de campeones) y ventajas que se escapan."""
    return {"tilt": tilt_report(games), "pool": pool_report(games), "leads": leads_report(games)}


def _death_context(metric: str, tags: dict) -> str:
    early = tags.get("early_total") or 0
    if metric not in ("deaths_to_jungler_pre14", "deaths_ganked", "deaths_pre14") or early < 3:
        return ""
    parts = []
    for tag, text in (("no_ward", "sin ward (tuyo o de un aliado cerca) en los 2 minutos previos"), ("overextended", "adelantado en la línea")):
        n = tags.get("early", {}).get(tag, 0)
        if n:
            parts.append(f"en el {round(n / early * 100)} % {text}")
    return ("De tus muertes antes del 14: " + " y ".join(parts) + ".") if parts else ""


def _suggested_goal(rule: dict, row: dict, by_key: dict) -> dict | None:
    op, target, label = rule["goal"]
    metric = rule["metric"]
    m = METRICS[metric]
    if isinstance(target, str):
        if target == "ref":
            ref = row["ref"]
            if ref is None:
                return None
            target = _round(ref, min(m.decimals, 2))
        else:  # otra métrica (p. ej. muertes sin visión → wards antes del 14)
            other = by_key.get(target)
            metric = target
            m = METRICS[metric]
            base = other["ref"] if other else 4
            target = max(1, round(base or 4))
    return {"metric": metric, "op": op, "target": target, "label": goal_label(label, target, metric)}


def death_summary(games: list[dict], limit_games: int = 30) -> dict:
    counts: Counter = Counter()
    early_counts: Counter = Counter()
    points = []
    early_total = total = 0
    for g in [x for x in games if reliable(x)][:limit_games]:
        for d in (g.get("deaths") or []):
            total += 1
            counts.update(d.get("tags") or [])
            if d.get("early"):
                early_total += 1
                early_counts.update(d.get("tags") or [])
            if d.get("x") is not None:
                points.append({"x": d["x"], "y": d["y"], "t": d["t"], "tags": d.get("tags") or [], "match_id": g["match_id"], "team": g.get("team")})
    return {
        "total": total,
        "early_total": early_total,
        "tags": {k: {"label": TAG_LABELS.get(k, k), "count": v} for k, v in counts.most_common()},
        "early": dict(early_counts),
        "points": points,
    }


def jungle_summary(games: list[dict]) -> dict | None:
    jungle = [g for g in games if g["role"] == "JUNGLE" and g.get("jungle")]
    if not jungle:
        return None
    starts: Counter = Counter()
    presence: Counter = Counter()
    first_ganks = []
    paths = []
    for g in jungle:
        j = g["jungle"]
        if j.get("start_camp"):
            starts[f"{j['start_camp']}:{j.get('start_side') or ''}"] += 1
        presence.update(j.get("presence") or {})
        if (g["metrics"] or {}).get("first_gank_min") is not None:
            first_ganks.append(g["metrics"]["first_gank_min"])
        paths.append({"match_id": g["match_id"], "team": g.get("team"), "win": g["win"], "path": j.get("path") or []})
    lanes_total = sum(presence[z] for z in ("top", "mid", "bot")) or 0
    return {
        "games": len(jungle),
        "starts": [{"camp": k.split(":")[0], "side": k.split(":")[1] or None, "games": v} for k, v in starts.most_common()],
        "presence": {z: round(presence[z] / lanes_total * 100) if lanes_total else 0 for z in ("top", "mid", "bot")},
        "first_gank_min": round(_mean(first_ganks), 1) if first_ganks else None,
        "paths": paths[:12],
    }


def evaluate_goals(goals: list[dict], games: list[dict]) -> list[dict]:
    out = []
    pool = [g for g in games if reliable(g)]
    for goal in goals:
        m = METRICS.get(goal["metric"])
        # Solo partidas en las que la métrica aplica a tu rol (el CS no se le pide a un support, ni al
        # jungla que no le mate el jungla rival).
        applicable = [
            g
            for g in pool
            if m is not None
            and applies(m, g["role"])
            and (not goal.get("role") or g["role"] == goal["role"])
            and (g["metrics"] or {}).get(goal["metric"]) is not None
        ]
        recent = applicable[:RECENT]
        history = []
        for g in recent:
            value = g["metrics"][goal["metric"]]
            history.append({"match_id": g["match_id"], "champ": g["champ"], "win": g["win"], "value": value, "ok": passes(value, goal["op"], goal["target"])})
        oks = [h["ok"] for h in history]
        streak = 0
        for ok in oks:
            if not ok:
                break
            streak += 1
        out.append(
            {
                **goal,
                "games": len(recent),
                "rate": round(sum(oks) / len(oks) * 100) if oks else None,
                "streak": streak,
                "history": history,
            }
        )
    return out


def readiness(games: list[dict], refs: References, goals: list[dict]) -> dict:
    games = [g for g in games if reliable(g)]
    recent = games[:RECENT]
    scores = [fundamentals_score(g["metrics"] or {}, game_refs(refs, g), g["role"]) for g in recent]
    scores = [s for s in scores if s is not None]
    fundamentals = round(sum(scores) / len(scores), 1) if scores else None
    rated = [g["rate"] for g in goals if g.get("active", True) and g.get("rate") is not None and g.get("games", 0) >= MIN_GOAL_GAMES]
    goals_rate = round(sum(rated) / len(rated)) if rated else None
    if fundamentals is None and goals_rate is None:
        score = None
    elif goals_rate is None:
        score = round(fundamentals * 10)
    elif fundamentals is None:
        score = goals_rate
    else:
        score = round(0.6 * goals_rate + 0.4 * fundamentals * 10)
    missing = max(0, MIN_GAMES_READY - len(games))
    ready = score is not None and score >= READY_SCORE and not missing
    if score is None:
        message = "Juega unas partidas con el seguimiento activo para calcularlo."
    elif missing:
        message = f"Te faltan {missing} partidas analizadas para valorarlo con fiabilidad."
    elif ready:
        message = (
            "Cumples tus propósitos y tus fundamentos están al nivel de tu elo objetivo: es buen momento para jugar clasificatorias."
            if goals_rate is not None
            else "Tus fundamentos están al nivel de tu elo objetivo: es buen momento para jugar clasificatorias. Ponte un propósito para seguir mejorando."
        )
    elif goals_rate is not None and goals_rate < READY_SCORE:
        message = f"Cumples tus propósitos en el {goals_rate} % de tus últimas partidas. Llega al 70 % antes de saltar a clasificatorias."
    else:
        message = "Tus fundamentos aún están por debajo de tu elo objetivo: céntrate en los puntos débiles de arriba."
    return {
        "score": score,
        "ready": ready,
        "fundamentals": fundamentals,
        "goals_rate": goals_rate,
        "games": len(recent),
        "missing_games": missing,
        "message": message,
        "threshold": READY_SCORE,
    }


def champion_stats(games: list[dict], refs: References) -> list[dict]:
    by: dict[tuple[int, str | None], list[dict]] = defaultdict(list)
    for g in games:
        by[(g["champ"], g["role"])].append(g)
    out = []
    for (champ, role), items in by.items():
        scores = [fundamentals_score(g["metrics"] or {}, game_refs(refs, g), role) for g in items if reliable(g)]
        scores = [s for s in scores if s is not None]
        wins = sum(1 for g in items if g["win"])
        out.append(
            {
                "champ": champ,
                "role": role,
                "games": len(items),
                "wins": wins,
                "wr": round(wins / len(items) * 100, 1),
                "kda": round(_mean([(g["metrics"] or {}).get("kda") for g in items]) or 0, 2),
                "score": round(sum(scores) / len(scores), 1) if scores else None,
            }
        )
    out.sort(key=lambda c: (c["games"], c["wins"]), reverse=True)
    return out


# ---------------------------------------------------------------- builds
def compare_build(mine: dict | None, ref: dict | None) -> dict | None:
    """Tu build (orden de objetos terminados, botas y habilidades) frente a la más jugada en OP.GG."""
    if not mine or not ref:
        return None
    legendary = list(mine.get("items") or [])
    core = list(ref.get("core") or [])
    popular = {p["id"]: p.get("pick_rate") for p in ref.get("popular") or [] if p.get("id")}
    first = legendary[0] if legendary else None
    notes: list[dict] = []
    overlap = len(set(legendary[:3]) & set(core)) if core else None
    if first and core:
        if first == core[0]:
            notes.append({"ok": True, "text": "Primer objeto: el mismo que la build más jugada."})
        elif first in core:
            notes.append({"ok": True, "text": "Empiezas por otro de los objetos clave de la build más jugada (cambia el orden).", "items": [first, core[0]]})
        elif first in popular:
            notes.append({"ok": True, "text": f"Primer objeto distinto al más habitual, pero lo lleva un {round((popular[first] or 0) * 100)} % de las builds.", "items": [first, core[0]]})
        else:
            notes.append({"ok": False, "text": "Tu primer objeto no está entre los habituales de tu campeón en este rol.", "items": [first, core[0]]})
    if overlap is not None and len(legendary) >= 3:
        notes.append({"ok": overlap >= 2, "text": f"Coincides en {overlap} de {len(core)} objetos clave con la build más jugada."})
    boots = mine.get("boots")
    ref_boots = ref.get("boots") or []
    if boots and ref_boots and boots not in ref_boots:
        notes.append({"ok": None, "text": "Botas distintas a las más habituales (cámbialas si es por el daño rival).", "items": [boots, ref_boots[0]]})
    skill_max = "".join(mine.get("skill_max") or [])[:3]
    ref_max = "".join(ref.get("skill_max") or [])[:3]
    if skill_max and ref_max:
        same = skill_max == ref_max
        notes.append({"ok": same, "text": f"Habilidades: maximizas {' > '.join(skill_max)}" + ("" if same else f"; lo habitual es {' > '.join(ref_max)}") + "."})
    return {"core": core, "boots": ref_boots, "skill_max": list(ref_max), "popular": ref.get("popular") or [], "overlap": overlap, "first": first, "notes": notes}


def build_summary(games: list[dict], refs: dict) -> list[dict]:
    """Por campeón: tu primer objeto, tu núcleo de build y tiempos, frente a la build más jugada."""
    by: dict[tuple[int, str | None], list[dict]] = defaultdict(list)
    for g in games:
        if g.get("build") and g["build"].get("items") is not None:
            by[(g["champ"], g["role"])].append(g)
    out = []
    for (champ, role), items in sorted(by.items(), key=lambda kv: len(kv[1]), reverse=True)[:4]:
        firsts: Counter = Counter()
        cores: Counter = Counter()
        boots: Counter = Counter()
        maxes: Counter = Counter()
        minutes = []
        for g in items:
            b = g["build"]
            legend = list(b.get("items") or [])
            if legend:
                firsts[legend[0]] += 1
            if len(legend) >= 3:
                cores[tuple(legend[:3])] += 1
            if b.get("boots"):
                boots[b["boots"]] += 1
            if b.get("skill_max"):
                maxes["".join(b["skill_max"])[:3]] += 1
            if (g["metrics"] or {}).get("first_item_min") is not None:
                minutes.append(g["metrics"]["first_item_min"])
        first = firsts.most_common(1)[0] if firsts else None
        core = cores.most_common(1)[0] if cores else None
        mine = {
            "items": list(core[0]) if core else ([first[0]] if first else []),
            "boots": boots.most_common(1)[0][0] if boots else None,
            "skill_max": list(maxes.most_common(1)[0][0]) if maxes else [],
        }
        out.append(
            {
                "champ": champ,
                "role": role,
                "games": len(items),
                "first_item": {"id": first[0], "games": first[1]} if first else None,
                "core": {"ids": list(core[0]), "games": core[1]} if core else None,
                "boots": {"id": mine["boots"], "games": boots.most_common(1)[0][1]} if boots else None,
                "skill_max": mine["skill_max"],
                "first_item_min": round(_mean(minutes), 1) if minutes else None,
                "compare": compare_build(mine, refs.get((champ, role))),
            }
        )
    return out


def summarize(games: list[dict], refs: References, goals: list[dict], build_refs: dict | None = None) -> dict:
    table = metric_table(games, refs)
    deaths = death_summary(games)
    evaluated = evaluate_goals(goals, games)
    wins = sum(1 for g in games if g["win"])
    roles = Counter(g["role"] or "?" for g in games)
    for g in games:  # Swiftplay o con alguien AFK: sin nota (no es comparable con tu referencia)
        g["score"] = fundamentals_score(g["metrics"] or {}, game_refs(refs, g), g["role"]) if reliable(g) else None
    # Muestra que de verdad sirve para señalar puntos débiles (partidas fiables del rol de cada métrica).
    sample = max((row.get("games") or 0 for row in table), default=0)
    weak = weaknesses(table, deaths)
    recurring = recurring_mistakes(games)
    # Si un error repetido ya tiene su punto débil (misma métrica), se cuenta en él y no se repite en la lista.
    by_metric = {w["metric"]: w for w in weak}
    rest = []
    for item in recurring["items"]:
        if item["metric"] in by_metric:
            by_metric[item["metric"]]["recurring"] = item
        else:
            rest.append(item)
    return {
        "games": len(games),
        "reliable_games": sum(1 for g in games if reliable(g)),
        "weakness_sample": sample,
        "min_games_weakness": MIN_GAMES_WEAKNESS,
        "wins": wins,
        "wr": round(wins / len(games) * 100, 1) if games else None,
        "roles": [{"role": r, "games": n} for r, n in roles.most_common()],
        "metrics": table,
        "weaknesses": weak,
        "recurring": rest,
        "recurring_sample": recurring["sample"],
        "deaths": deaths,
        "jungle": jungle_summary(games),
        "goals": evaluated,
        "readiness": readiness(games, refs, evaluated),
        "champions": champion_stats(games, refs),
        "builds": build_summary(games, build_refs or {}),
        "categories": CATEGORIES,
    }
