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

from app.coach import consistency, learn
from app.coach.rule_metrics import ROWS as RULE_ROWS
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
        # Con 1.818 partidas de junglas OTP: 0,05 por partida en Challenger, 0,13 en Esmeralda y 0,11 en Platino.
        "metric": "deaths_duel_behind",
        "weight": 1.0,
        "title": "Aceptas duelos con niveles u objetos de menos",
        "detail": "{v} muertes por partida en un 1 contra 1 en el que el rival te sacaba 2 niveles, un legendario o la definitiva (referencia {ref}).",
        "tip": "Antes de un 1 contra 1 mira el nivel y los objetos del rival (pulsa Tab): con 2 niveles menos, un legendario menos o sin tu R contra la suya, retrocede y farmea seguro o espera ayuda. Pelea cuando subas tú antes de nivel o cierres tu objeto.",
        "goal": ("<=", 0, "Ningún duelo con niveles u objetos de menos"),
    },
    {
        "metric": "deaths_solo",
        "skip_if": ["deaths_duel_behind"],
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
    "duel_behind": "Duelo en desventaja",
    "seen_ward": "Visto por un ward rival",
    "lens_unused": "Con la lente sin usar",
    "lens_cd": "Con la lente en recarga",
}


# ------------------------------------------------------------------ utilidades
def reliable(game: dict) -> bool:
    """¿Sirve esta partida para las conclusiones? No si es Swiftplay ni si alguien estuvo AFK 3 minutos o más."""
    return unreliable_reason(game) is None


# Quién estuvo AFK según nuestra detección por posiciones (minutos): para decir por qué una partida no tiene nota.
AFK_WHO = (("afk_min", "Estuviste AFK {} min"), ("ally_afk_min", "Un aliado estuvo AFK {} min"), ("enemy_afk_min", "Un rival estuvo AFK {} min"))


def unreliable_reason(game: dict) -> str | None:
    """Por qué una partida no cuenta para tus medias ni tiene nota (``None`` si cuenta): Swiftplay o alguien AFK."""
    if game.get("queue") in EXCLUDED_QUEUES:
        return "Swiftplay: otro ritmo de partida"
    m = game.get("metrics") or {}
    for key, text in AFK_WHO:
        if (m.get(key) or 0) >= AFK_EXCLUDE_MIN:
            return text.format(round(m[key]))
    if (m.get("riot_afk") or 0) > 0:  # Riot detectó a alguien AFK (aunque nuestra detección por posiciones no llegue a 3 min)
        return "Riot marcó a alguien como AFK"
    return None


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
    role = Counter(r for _, _, r in pairs).most_common(1)[0][0]
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
        pairs = _metric_pairs(games, refs, key, m)
        if not pairs:
            continue
        # Muestra de la referencia (si es calibrada en todos los roles de estas partidas): su incertidumbre también cuenta.
        sizes = [refs.sample_size(r, key) if hasattr(refs, "sample_size") else None for r in {r for _, _, r in pairs}]
        ref_n = min(sizes) if sizes and all(sizes) else None
        rows.append(_metric_row(key, m, pairs, ref_n))
    return rows


def _metric_pairs(games: list[dict], refs: References, key: str, m) -> list[tuple]:
    """(valor, referencia, rol) de esa métrica en las partidas en las que aplica a su rol y tienen los dos datos."""
    pairs = []
    for g in games:
        if not applies(m, g["role"]):
            continue
        value = (g["metrics"] or {}).get(key)
        ref = game_refs(refs, g).get(key)
        if value is None or ref is None:
            continue
        pairs.append((value, ref, g["role"]))
    return pairs


def _dev_status(dev: float) -> str:
    """Estado de una media frente a su referencia por su desviación: «good», «ok» o «bad»."""
    if dev <= -0.05:
        return "good"
    return "bad" if dev >= 0.15 else "ok"


def _metric_row(key: str, m, pairs: list[tuple], ref_n: int | None) -> dict:
    """Fila de la tabla de una métrica: tu media, la referencia, la desviación, la tendencia y la serie."""
    values = [v for v, _, _ in pairs]
    value = _mean(values)
    ref = _mean([r for _, r, _ in pairs])
    dev = deviation(key, value, ref)
    status = _dev_status(dev)
    trend = _trend(m, pairs)
    return {
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
        if not _is_weak(rule, row, m, value, ref, dev):
            continue
        if any(s in found for s in rule.get("skip_if", [])):
            continue
        found[rule["metric"]] = _weakness(rule, row, m, dev, death_tags, by_key)
    return sorted(found.values(), key=lambda w: w["severity"], reverse=True)[:limit]


def _below_min_abs(rule: dict, m, value: float) -> bool:
    """¿La diferencia frente al rival no llega al mínimo de la regla (``min_abs``)? Entonces no es un punto débil."""
    return (m.better == "higher" and value > -rule["min_abs"]) or (m.better == "lower" and value < rule["min_abs"])


def _is_weak(rule: dict, row: dict, m, value: float, ref: float, dev: float) -> bool:
    """¿Es esa fila un punto débil claro según su regla (con la desviación ya encogida por las pocas partidas)?"""
    if "min_abs" in rule:
        if _below_min_abs(rule, m, value) or not _consistent(row, m):
            return False
    elif dev < 0.15:
        return False
    if m.count and not _count_beyond(value, ref, row["games"], m.better, row.get("ref_n")):
        return False
    # Una o dos partidas malas no bastan: la media tiene que quedar fuera con claridad.
    return m.count or "min_abs" in rule or _mean_beyond(row, ref, m)


def _level(severity: float) -> str:
    """Nivel de un punto débil por su gravedad: «high», «medium» o «low»."""
    if severity >= 50:
        return "high"
    return "medium" if severity >= 25 else "low"


def _weakness(rule: dict, row: dict, m, dev: float, death_tags: dict, by_key: dict) -> dict:
    """Punto débil listo para la web: gravedad, texto con tus números, consejo, propósito sugerido y material."""
    value, ref = row["value"], row["ref"]
    # Escala saturante: conserva el orden aunque te alejes mucho de la referencia.
    severity = round(100 * (1 - math.exp(-max(0.0, dev) * rule["weight"])), 1)
    detail = rule["detail"].replace("{v}", _fmt(value, m.decimals)).replace("{ref}", _fmt(ref, m.decimals))
    extra = _death_context(rule["metric"], death_tags)
    return {
        "id": rule["metric"],
        "category": m.category,
        "category_label": CATEGORIES.get(m.category, m.category),
        "severity": severity,
        "level": _level(severity),
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


# ------------------------------------------------------------ errores que repites
# Errores concretos que el análisis detecta en cada partida (cada uno se puede ver en la repetición), agrupados por
# tipo. No dependen de ninguna referencia: si se repiten en muchas partidas, son algo a corregir aunque tus medias
# estén en lo normal de tu elo.
RECURRING_MIN_GAMES = 3  # partidas distintas con ese error…
RECURRING_MIN_RATE = 0.2  # …y al menos en el 20 % de tus partidas fiables con timeline
RECURRING_EXAMPLES = 4
# No son errores aunque se muestren: peleas ganadas, buenos cambios, AFK, final de partida, build completa (ahorrar
# es lo correcto) y objetivos que no se podían disputar (inferioridad numérica).
NOT_A_MISTAKE = {"fight_won", "traded", "fair_trade", "afk", "game_end", "full_build", "outnumbered", "no_prio"}
SEVERITY_WEIGHT = {"high": 3.0, "medium": 2.0, "low": 1.0}
MISTAKE_TYPES: list[dict] = [  # el primero que encaje (de lo más concreto a lo más general)
    {"id": "death_outnumbered_objective", "kind": "death", "need": ("fight_outnumbered",), "metric": None,
     "title": "Peleas los objetivos con uno menos",
     "tip": "Antes de entrar al foso cuenta los vivos: si un aliado lleva un rato muerto y ellos están todos, cede el objetivo o cámbialo al otro lado (su jungla, una torre). Solo el ancestral y su alma merecen pelearse con uno menos."},
    {"id": "death_before_objective", "kind": "death", "need": ("before_objective",), "metric": "deaths_before_objective",
     "title": "Mueres justo antes de un objetivo rival"},
    # ``unless``: con esas etiquetas no es error. Un cambio igualado (caéis los mismos en los dos bandos) no es morir
    # adelantado ni que te ganqueen: lo era en el 20-30 % de esas muertes en top y mid y en el 40-50 % en bot, y más en
    # Aspirante que en Platino (contaba como error justo lo que hacen los mejores). Medido el 24-11-2026, ~34.000 partidas.
    {"id": "death_lens_unused", "kind": "death", "need": ("seen_ward", "lens_unused"), "metric": "deaths_lens_unused",
     "unless": ("fight_even",), "title": "Te ve un ward rival antes de la jugada y llevabas la lente",
     "tip": "Antes de entrar en el río, en una jungla o de ir a un gank, usa la lente (trinket rojo) en los arbustos y la "
            "entrada por la que vas a pasar: si te ven llegar, la jugada se acaba antes de empezar. Si no puedes barrer, "
            "entra por otro camino."},
    {"id": "death_gank_no_ward", "kind": "death", "need": ("gank", "no_ward"), "metric": "deaths_no_vision_pre14",
     "unless": ("fight_even",), "title": "Te ganquean sin visión"},
    {"id": "death_overextended", "kind": "death", "need": ("overextended",), "metric": "deaths_overextended",
     "unless": ("fight_even",), "title": "Mueres adelantado en la línea"},
    {"id": "death_duel_behind", "kind": "death", "need": ("duel_behind",), "metric": "deaths_duel_behind",
     "title": "Aceptas duelos con niveles u objetos de menos"},
    # Para el jungla, entrar en la jungla rival es su trabajo: los Aspirante mueren ahí más que los Platino (0,50 frente a
    # 0,45 por partida; invaden más). Solo es error si entró sin visión; aislado, en duelo… lo recogen los tipos de abajo.
    {"id": "death_enemy_jungle", "kind": "death", "need": ("enemy_jungle",), "metric": "deaths_enemy_jungle",
     "role_need": {"JUNGLE": ("enemy_jungle", "no_ward")},
     "title": "Mueres en la jungla rival"},
    {"id": "death_unspent_gold", "kind": "death", "need": ("unspent_gold",), "metric": "deaths_unspent_gold",
     "title": "Mueres con oro sin gastar"},
    {"id": "death_collapsed", "kind": "death", "need": ("collapsed",), "metric": None,
     "title": "Te cazan solo en la mitad rival del mapa",
     "tip": "Al otro lado del río, en una línea lateral, entra solo con wards y sabiendo dónde están al menos 3 rivales. Si desaparecen del mapa, retírate hacia tu torre antes de que lleguen."},
    {"id": "death_isolated", "kind": "death", "need": ("isolated",), "metric": "deaths_isolated_late",
     "title": "Te cogen solo, lejos de tu equipo"},
    {"id": "death_solo", "kind": "death", "need": ("solo",), "metric": "deaths_solo", "title": "Pierdes duelos 1 contra 1"},
    {"id": "death_gank", "kind": "death", "need": ("gank",), "metric": "deaths_ganked", "unless": ("fight_even",),
     "title": "Te ganquean"},
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
    {"id": "enemy_pink_ignored", "kind": "vision", "need": ("enemy_pink",), "metric": "enemy_pinks_ignored",
     "title": "Pinks rivales que revelan tu jungla mucho rato",
     "tip": "Una pink rival en tu jungla les dice tus rutas, tus campamentos y quién entra al río. Se ve sin lente: si "
            "pasas a su lado, quítala (unos 3 golpes). Jungla y support: revisad los arbustos del río y la entrada a vuestra "
            "jungla cada vez que volváis de base."},
    {"id": "gold_hoard", "kind": "gold", "need": (), "metric": "gold_hoard_min", "title": "Tardas en gastar el oro"},
    {"id": "skill_order", "kind": "build", "need": ("skill_order",), "metric": None,
     "title": "Subes las habilidades en un orden que no usan los OTPs de tu campeón",
     "tip": "Mira el orden de habilidades de los OTPs de tu campeón (página OTPs o «Tú frente a los OTPs») y cópialo en los niveles 1-9: decide la velocidad del despeje y quién gana los primeros duelos."},
    {"id": "first_back", "kind": "build", "need": ("first_back",), "metric": None,
     "title": "Tu primera vuelta no compra lo que compran los OTPs",
     "tip": "En la primera vuelta compra piezas de tu primer objeto (o botas si es lo habitual en tu campeón). Los componentes sueltos de otros objetos retrasan tu primer pico de poder."},
    {"id": "item_tempo", "kind": "build", "need": ("item_tempo",), "metric": "first_item_min",
     "title": "Terminas tus objetos más tarde que los OTPs de tu campeón",
     "tip": "Entre jugada y jugada limpia campamentos u oleadas y vuelve a base en cuanto te llegue para una pieza grande: un minuto de retraso en el primer objeto es un minuto peleando más débil."},
    {"id": "r_late", "kind": "build", "need": ("skills",), "metric": "r_late", "title": "Subes la R tarde"},
    # En top no: los Aspirante lo hacen el doble que los Diamante (0,079 frente a 0,035 por partida; en jungla, igual).
    # Ni Cassiopeia ni Yuumi pueden comprar botas: con Yuumi era el 77-84 % de los «sin botas» de support (24-11-2026).
    {"id": "no_boots", "kind": "build", "need": ("boots",), "metric": None, "skip_roles": ("TOP", "JUNGLE"), "skip_champs": (69, 350),
     "title": "Sin botas mejoradas al minuto 20",
     "tip": "Las botas de nivel 2 cuestan poco y dan mucha movilidad: cómpralas entre el primer y el segundo objeto salvo que tu campeón no las use."},
    {"id": "no_antiheal", "kind": "build", "need": ("anti_heal",), "metric": None, "title": "Sin Heridas graves contra curaciones",
     "tip": "Contra campeones que se curan mucho, compra un componente de Heridas graves (desde 800 de oro) en cuanto empiecen a pelear."},
    # En top no: los Aspirante lo hacen más que los Diamante (0,075 frente a 0,052 por partida).
    {"id": "no_resist", "kind": "build", "need": ("resist",), "metric": None, "skip_roles": ("TOP",), "title": "No te defiendes del daño que más recibes",
     "tip": "Mira en la pantalla de muerte qué tipo de daño te mata: si casi todo es físico o mágico, mete armadura o resistencia mágica en tu siguiente compra."},
    {"id": "enemy_jungler_far", "kind": "jungle", "need": ("tracking",), "metric": "enemy_ganks_far", "title": "No sigues al jungla rival"},
    {"id": "no_pinks", "kind": "vision", "need": ("no_pinks",), "metric": "control_wards", "title": "Partidas sin ninguna pink",
     "tip": "Compra un guardián de control (75) en cada vuelta a base y ponlo en el río o junto al siguiente objetivo: en Aspirante casi nadie acaba una partida sin ninguno."},
    {"id": "few_wards", "kind": "vision", "need": ("few_wards",), "metric": "wards_pre14", "title": "Casi sin wards en la fase de líneas",
     "tip": "Usa el abalorio cada vez que se recargue: río, entrada de tu jungla y el campamento que el rival suele hacer."},
    {"id": "no_sweep", "kind": "vision", "need": ("no_sweep",), "metric": "wards_killed", "title": "Partidas sin quitar ningún ward rival",
     "tip": "Cambia al abalorio de lente cuando tu rol lo permita y úsalo antes de cada objetivo y al entrar en la jungla rival; la pink también quita los wards de su zona."},
    {"id": "slow_clear", "kind": "jungle", "need": ("slow_clear",), "metric": "jungle_cs10", "title": "Despejas la jungla más lento que los OTPs de tu campeón",
     "tip": "Practica tu ruta de 3 minutos en una partida personalizada hasta hacerla sin pararte; en partida, encadena campamentos y no esperes en el río sin un motivo concreto."},
    {"id": "low_kp", "kind": "jungle", "need": ("low_kp",), "metric": "kp", "title": "Participas en pocas kills de tu equipo",
     "tip": "Sigue las peleas de tus líneas: llega a los ganks del rival (contragank), juega hacia la línea que va ganando y agrupa antes de cada objetivo."},
    {"id": "pink_unused", "kind": "vision", "need": ("pink",), "metric": None, "title": "Llevas pinks y no las usas en los objetivos",
     "tip": "Una pink en el inventario no da visión: ponla en el foso o en el arbusto de entrada un minuto antes de cada objetivo."},
]


def lens_on_cooldown(moments: list[dict], wards_killed_s: list[float]) -> list[dict]:
    """Partidas analizadas antes de mirar la recarga de la lente: una muerte «con la lente sin usar» en la que quitaste
    un ward en los 90 s anteriores (``LENS_COOLDOWN_MS``) no lo es (la lente estaba en recarga). Devuelve copias."""
    from app.coach.analyzer import LENS_COOLDOWN_MS

    kills = [float(k) for k in wards_killed_s or []]
    out = []
    for m in moments or []:
        tags = m.get("tags") or []
        if "lens_unused" in tags and any(0 <= float(m.get("t") or 0) - k <= LENS_COOLDOWN_MS / 1000 for k in kills):
            m = {**m, "tags": [("lens_cd" if x == "lens_unused" else x) for x in tags]}
        out.append(m)
    return out


def mistake_type(kind: str, tags: set, role: str | None = None) -> dict | None:
    """Tipo de error de un momento (el primero de ``MISTAKE_TYPES`` que encaje) o ``None`` si no es un error.

    ``role_need``: en ese rol hacen falta más etiquetas (lo que en él es su trabajo no es error por sí solo)."""
    if tags & NOT_A_MISTAKE:
        return None
    for mt in MISTAKE_TYPES:
        need = (mt.get("role_need") or {}).get(role or "", mt["need"])
        if mt["kind"] == kind and all(t in tags for t in need):
            return None if role in mt.get("skip_roles", ()) or tags & set(mt.get("unless", ())) else mt
    return None


def excused(moment: dict, role: str | None) -> bool:
    """¿Momento que el análisis marcó como error y no lo es (``unless``: un cambio igualado)? Se ve, pero no cuenta."""
    tags = set(moment.get("tags") or [])
    if tags & NOT_A_MISTAKE:
        return False
    for mt in MISTAKE_TYPES:
        need = (mt.get("role_need") or {}).get(role or "", mt["need"])
        if mt["kind"] == moment.get("kind") and all(t in tags for t in need):
            return bool(tags & set(mt.get("unless", ())))
    return False


def dropped(moment: dict, role: str | None, champ: int | None = None) -> bool:
    """¿Momento de una regla que en ese rol o con ese campeón ya no cuenta (``skip_roles``, ``skip_champs``)? Para las
    partidas analizadas antes del cambio."""
    tags = set(moment.get("tags") or [])
    for mt in MISTAKE_TYPES:
        if mt["kind"] == moment.get("kind") and mt["need"] and all(t in tags for t in mt["need"]):
            return role in mt.get("skip_roles", ()) or (champ is not None and champ in mt.get("skip_champs", ()))
    return False




# ------------------------------------------------------------------ errores frente a cada elo
ELO_GROUPS = ("BRONZE", "GOLD", "PLATINUM", "EMERALD", "DIAMOND", "MASTER", "CHALLENGER")
ELO_LABEL = {"BRONZE": "Hierro-Bronce", "GOLD": "Plata-Oro", "PLATINUM": "Platino", "EMERALD": "Esmeralda", "DIAMOND": "Diamante", "MASTER": "Maestro", "CHALLENGER": "Aspirante"}
# Un tipo de error «no separa elos» si el elo más alto con datos lo comete al menos el 85 % de veces que el más bajo.
FLAT_RATIO = 0.85
FLAT_WEIGHT = 0.5  # en «Errores que repites» pesa la mitad: es parte del juego, no lo que te impide subir
# Y si el elo más alto lo comete claramente más (un 15 % o más), es parte de jugar con presión, no lo que te frena: pesa
# un cuarto. Pasa con «te ganquean» y «adelantado» en las líneas (el support de Aspirante muere en ganks 0,39 veces por
# partida, el de Platino 0,23: está más en el río, en otras líneas y en la jungla rival).
RISE_RATIO = 1.15
RISE_WEIGHT = 0.25
MIN_ELO_GAMES = 5
# Un elo con menos partidas que esto (con tu campeón salen desde 20, a veces de 6-7 jugadores) se enseña, pero no decide
# si un error separa elos ni a qué elo te pareces: con 21 partidas un 14 % frente a un 7 % es ruido.
SOLID_ELO_GAMES = 30


def elo_tiers(profile: dict | None, role: str | None, champ: int | None = None) -> tuple[dict, str]:
    """Bloques por elo de tu campeón (si hay al menos 2 elos con muestra) o de tu rol: ``(tiers, "champ"|"role")``."""
    if not profile or not role:
        return {}, "role"
    if champ:
        tiers = ((profile.get("champs") or {}).get(f"{champ}:{role}") or {}).get("tiers") or {}
        if len(tiers) >= 2:
            return tiers, "champ"
    return (profile.get("roles") or {}).get(role) or {}, "role"


def _known(block: dict, mistake_id: str) -> bool:
    """¿Se sabe cuánto comete ese elo este tipo de error? No si se detecta desde un análisis posterior a sus partidas."""
    return mistake_id not in (block.get("unknown") or ())


def separation(tiers: dict, mistake_id: str) -> dict | None:
    """% de partidas con ese error en el elo más bajo y en el más alto con datos, y si de verdad separa elos (por
    errores por partida: morir aislado 3 veces por partida o 1 es muy distinto aunque ambos lo hagan en todas)."""
    present = [g for g in ELO_GROUPS if g in tiers and _known(tiers[g], mistake_id)]
    solid = [g for g in present if (tiers[g].get("games") or 0) >= SOLID_ELO_GAMES]
    if len(solid) >= 2:
        present = solid
    if len(present) < 2:
        return None
    low, top = present[0], present[-1]
    get = lambda g, k: ((tiers[g].get("types") or {}).get(mistake_id) or {}).get(k, 0.0)  # noqa: E731
    lo, hi = get(low, "per_game"), get(top, "per_game")
    return {
        "low": low, "top": top, "low_pct": get(low, "pct"), "top_pct": get(top, "pct"),
        "flat": lo <= 0 or hi >= FLAT_RATIO * lo, "rises": lo > 0 and hi >= RISE_RATIO * lo,
    }


def recurring_mistakes(games: list[dict], elo: dict | None = None) -> dict:
    """Tipos de error que se repiten en tus partidas: en cuántas, cuántas veces y ejemplos para verlos.

    ``elo``: rol -> bloques por elo (``elo_tiers``). Con ellos cada error dice cuántas partidas lo tienen en cada elo, y
    el que comete igual un Aspirante que un Platino pesa la mitad (no es lo que te separa de subir)."""
    pool = [g for g in games if reliable(g) and g.get("has_timeline") and g.get("mistakes") is not None]
    groups: dict[str, dict] = {}
    for g in pool:
        for kind, severity, t, tags, title in g["mistakes"]:
            mt = mistake_type(kind, set(tags or []), g.get("role"))
            if mt is not None:
                _add_mistake(groups, mt, g, severity, t, title)
    n = len(pool)
    out = []
    for grp in groups.values():
        item = _recurring_item(grp, n, elo)
        if item is not None:
            out.append(item)
    out.sort(key=lambda x: x["score"], reverse=True)
    return {"items": out, "sample": n}


def _add_mistake(groups: dict[str, dict], mt: dict, g: dict, severity: str, t, title: str) -> None:
    """Suma un error de la partida ``g`` al grupo de su tipo (con un ejemplo por partida, hasta ``RECURRING_EXAMPLES``)."""
    grp = groups.setdefault(mt["id"], {"type": mt, "count": 0, "games": set(), "weight": 0.0, "examples": [], "roles": Counter()})
    grp["roles"][g.get("role")] += 1
    grp["count"] += 1
    grp["games"].add(g["match_id"])
    grp["weight"] += SEVERITY_WEIGHT.get(severity, 1.0)
    if len(grp["examples"]) < RECURRING_EXAMPLES and not any(e["match_id"] == g["match_id"] for e in grp["examples"]):
        grp["examples"].append({"match_id": g["match_id"], "t": t, "title": title, "champ": g["champ"], "win": g["win"]})


def _tier_pcts(tiers: dict, mistake_id: str) -> dict:
    """% de partidas con ese tipo de error en cada elo con datos (de más bajo a más alto); sin los que no lo saben."""
    return {
        g: ((tiers[g].get("types") or {}).get(mistake_id) or {}).get("pct", 0.0)
        for g in ELO_GROUPS
        if g in tiers and _known(tiers[g], mistake_id)
    }


def _elo_weight(sep: dict | None) -> float:
    """Peso de un error repetido según los elos: un cuarto si el más alto lo comete más, la mitad si igual, 1 si separa."""
    if not sep:
        return 1.0
    if sep["rises"]:
        return RISE_WEIGHT
    return FLAT_WEIGHT if sep["flat"] else 1.0


def _recurring_item(grp: dict, n: int, elo: dict | None) -> dict | None:
    """Tipo de error repetido listo para la web (None si no llega al mínimo de partidas o de porcentaje)."""
    games_with = len(grp["games"])
    rate = games_with / n if n else 0.0
    if games_with < RECURRING_MIN_GAMES or rate < RECURRING_MIN_RATE:
        return None
    mt = grp["type"]
    rule = RULE_BY_METRIC.get(mt["metric"]) if mt["metric"] else None
    role = grp["roles"].most_common(1)[0][0]
    tiers = (elo or {}).get(role) or {}
    sep = separation(tiers, mt["id"]) if tiers else None
    weight = _elo_weight(sep)
    return {
        "id": mt["id"],
        "title": mt["title"],
        "tip": mt.get("tip") or (rule or {}).get("tip") or "",
        "metric": mt["metric"],
        "games": games_with,
        "count": grp["count"],
        "of": n,
        "rate": round(rate * 100),
        "severity": round(grp["weight"] / grp["count"], 2),
        "score": round(weight * rate * grp["weight"] / grp["count"], 3),
        "elo": _tier_pcts(tiers, mt["id"]) or None,
        "separates": (not sep["flat"]) if sep else None,
        "rises": bool(sep and sep["rises"]),
        "examples": grp["examples"],
        "learn": learn.for_mistake(mt["id"]),
    }


def elo_errors(games: list[dict], profile: dict | None) -> dict | None:
    """Tus errores por partida frente a los de cada elo (mismo análisis, mismas reglas), en tu rol más jugado y, si hay
    datos de varios elos, con tu campeón más jugado."""
    pool = [g for g in games if reliable(g) and g.get("has_timeline") and g.get("mistakes") is not None and g.get("role")]
    if not pool or not profile:
        return None
    role = Counter(g["role"] for g in pool).most_common(1)[0][0]
    mine = [g for g in pool if g["role"] == role]
    champ = Counter(g["champ"] for g in mine).most_common(1)[0][0]
    tiers, scope = elo_tiers(profile, role, champ)
    if scope == "champ":
        mine = [g for g in mine if g["champ"] == champ]
    if len(mine) < MIN_ELO_GAMES or not tiers:
        return None
    n = len(mine)
    count, with_type, sev, total = _count_mistakes(mine, role)
    all_ids = set(count) | {k for t in tiers.values() for k in (t.get("types") or {})}
    # Los tipos que ningún elo sabe (la lente sin usar: sus partidas son de antes) no suman a tu total, que si no saldría
    # inflado frente al de ellos.
    unknown = {mid for mid in all_ids if not any(_known(t, mid) for t in tiers.values())}
    per_game = (total - sum(count[mid] for mid in unknown)) / n
    sep_ids = {mid for mid in all_ids if (sep := separation(tiers, mid)) and not sep["flat"]}
    rows = _elo_type_rows(all_ids, tiers, count, with_type, n)
    levels = _elo_levels(tiers)
    # Solo los tipos que separan elos (el más alto los comete claramente menos): lo que de verdad te falta para subir.
    sep_mine = sum(count[mid] for mid in sep_ids) / n
    for lv in levels:
        types = tiers[lv["tier"]].get("types") or {}
        lv["per_game_sep"] = round(sum((types.get(mid) or {}).get("per_game", 0.0) for mid in sep_ids), 2)
    closest = _closest(levels, "per_game", per_game)
    closest_sep = _closest(levels, "per_game_sep", sep_mine)
    normals = sum(1 for g in mine if g.get("queue") not in (420, 440))
    return {
        "role": role,
        "champ": champ if scope == "champ" else None,
        "games": n,
        "normals": normals,
        "per_game": round(per_game, 2),
        "severity": {s: round(sev[s] / n, 2) for s in ("high", "medium", "low")},
        "levels": levels,
        "closest": closest,
        "per_game_sep": round(sep_mine, 2),
        "closest_sep": closest_sep,
        "types": rows,
    }


def _count_mistakes(mine: list[dict], role: str) -> tuple[Counter, Counter, Counter, int]:
    """Tus errores por tipo: veces, partidas con ese tipo, veces por gravedad y total de errores."""
    count, with_type, sev = Counter(), Counter(), Counter()
    total = 0
    for g in mine:
        ids = []
        for kind, severity, _, tags, _ in g["mistakes"]:
            mt = mistake_type(kind, set(tags or []), role)
            if mt is None:
                continue
            ids.append(mt["id"])
            sev[severity] += 1
        total += len(ids)
        count.update(ids)
        with_type.update(set(ids))
    return count, with_type, sev, total


def _elo_status(mine_pct: float, top: float | None, games_with: int, n: int) -> str | None:
    """Cómo quedas en un tipo de error frente al elo más alto: «worse», «better» o «even» (None sin datos del elo)."""
    if top is None:
        return None
    # Ruido: con n partidas, una diferencia menor que ~1,5 errores típicos de una proporción no dice nada.
    p = max(min((top or 0) / 100, 0.99), 0.01)
    noise = 150 * math.sqrt(p * (1 - p) / n)
    if games_with >= 2 and mine_pct - top > max(5.0, noise):
        return "worse"
    return "better" if top - mine_pct > max(5.0, noise) else "even"


def _elo_type_rows(all_ids: set, tiers: dict, count: Counter, with_type: Counter, n: int) -> list[dict]:
    """Una fila por tipo de error: tu % de partidas con él frente al de cada elo (primero en lo que estás peor)."""
    titles = {mt["id"]: mt["title"] for mt in MISTAKE_TYPES}
    rows = []
    for mid in all_ids:
        sep = separation(tiers, mid)
        mine_pct = 100 * with_type[mid] / n
        top = sep["top_pct"] if sep else None
        status = _elo_status(mine_pct, top, with_type[mid], n)
        if not with_type[mid] and (top is None or top < 5):
            continue
        rows.append(
            {
                "id": mid,
                "title": titles.get(mid, mid),
                "mine_pct": round(mine_pct, 1),
                "mine_per_game": round(count[mid] / n, 2),
                "tiers": _tier_pcts(tiers, mid),
                "separates": (not sep["flat"]) if sep else None,
                "rises": bool(sep and sep["rises"]),
                "status": status,
            }
        )
    order = {"worse": 0, "even": 1, None: 2, "better": 3}
    rows.sort(key=lambda r: (order[r["status"]], not r["separates"], -r["mine_pct"]))
    return rows


def _elo_levels(tiers: dict) -> list[dict]:
    """Cada elo con datos (de más bajo a más alto): partidas, jugadores, errores por partida y su gravedad."""
    return [
        {
            "tier": g, "label": ELO_LABEL[g], "games": tiers[g]["games"], "players": tiers[g].get("players"),
            "per_game": tiers[g]["per_game"], "severity": tiers[g].get("severity"),
            "few": tiers[g]["games"] < SOLID_ELO_GAMES,
        }
        for g in ELO_GROUPS if g in tiers
    ]


def _closest(levels: list[dict], key: str, value: float) -> str | None:
    """Elo cuyo ``key`` (errores por partida) queda más cerca de tu valor (None si no hay elos); los de pocas partidas,
    solo si no hay otros."""
    pool = [lv for lv in levels if not lv.get("few")] or levels
    return min(pool, key=lambda lv: abs(lv[key] - value))["tier"] if pool else None


# ------------------------------------------------------------------ movimiento (posiciones reconstruidas)
# (clave, fase, campo del movimiento, etiqueta, roles; None = todos). Las de elo salen de las métricas mv_* de los OTPs.
MOVE_ROWS = [
    ("lane_pre14", "early", "lane", "En tu línea (antes del 14)", ("TOP", "MID", "ADC", "SUPPORT"), "mv_lane_pre14"),
    ("other_lane_pre14", "early", "other_lane", "En otras líneas (antes del 14)", None, None),
    ("river_pre14", "early", "river", "En el río (antes del 14)", None, "mv_river_pre14"),
    ("own_jungle_pre14", "early", "own_jungle", "En tu jungla (antes del 14)", ("JUNGLE",), None),
    ("enemy_jungle_pre14", "early", "enemy_jungle", "En la jungla rival (antes del 14)", None, "mv_enemy_jungle_pre14"),
    ("base", "both", "base", "En base", None, "mv_base_pct"),
    ("dead", "both", "dead", "Muerto", None, None),
    ("grouped_late", "late", "grouped_late", "Agrupado con 2+ aliados (después del 14)", None, "mv_grouped_late"),
    ("side_alone_late", "late", "side_alone_late", "Solo en una línea lateral (después del 14)", None, "mv_side_alone_late"),
]
MIN_MOVE_GAMES = 5
# Roams, contraganks… (``route_macro``): (clave, etiqueta, roles, unidad). Tendencias, no errores: se comparan con cada elo.
ROUTE_ROWS = [
    ("rm_roams", "Roams antes del 14 (20 s o más fuera de tu línea)", ("TOP", "MID", "ADC", "SUPPORT"), ""),
    ("rm_roam_success_pct", "Roams con kill o asistencia", ("TOP", "MID", "ADC", "SUPPORT"), "%"),
    ("rm_roam_cs_cost", "CS que pierdes frente a tu rival por roamear", ("TOP", "MID", "ADC"), ""),
    ("rm_counter_near_pct", "Cerca cuando actúa el jungla rival (antes del 14)", ("JUNGLE",), "%"),
    ("rm_counter_pct", "Respondes a lo que hace el jungla rival (kill o asistencia allí en 15 s)", ("JUNGLE",), "%"),
    ("rm_mid_with_jg_pct", "Con tu jungla en sus peleas del río o la jungla", ("MID",), "%"),
    ("rm_adc_alone_deaths", "Muertes de tu ADC estando tú lejos (antes del 14)", ("SUPPORT",), ""),
    # Adónde vas cuando sales de tu línea (antes del 14): el roam «bueno» depende del rol.
    ("rm_roam_lane", "Roams a otra línea", ("TOP", "MID", "ADC", "SUPPORT"), ""),
    ("rm_roam_river", "Salidas al río (visión, cangrejo, ayudar a tu jungla)", ("MID", "SUPPORT"), ""),
    ("rm_roam_own_jg", "Visitas a tu jungla (ayudar a tu jungla, wards)", ("MID", "SUPPORT"), ""),
    ("rm_roam_enemy_jg", "Entradas en la jungla rival", ("SUPPORT",), ""),
    # Visión útil: para los objetivos y para seguir al jungla rival.
    ("rm_wards_pre14", "Wards antes del 14", ("JUNGLE", "SUPPORT"), ""),
    ("rm_obj_ward_pct", "Objetivos con un ward tuyo cerca en los 2 min previos", ("SUPPORT",), "%"),
    ("rm_pink_obj", "Pinks junto a dragón o barón", ("JUNGLE", "SUPPORT"), ""),
    ("rm_ward_enemy_jg10", "Wards en la jungla rival antes del 10 (seguir al jungla rival)", ("JUNGLE", "SUPPORT"), ""),
    ("rm_ward_enemy_jg_pct", "Wards en la jungla rival (antes del 14, de todos los tuyos)", ("TOP", "JUNGLE", "SUPPORT"), "%"),
    ("rm_ward_lane_pct", "Wards en tu línea (antes del 14, de todos los tuyos)", ("SUPPORT",), "%"),
    ("rm_farsight10", "Farsight (trinket azul) cada 10 min", ("TOP", "MID", "ADC"), ""),
    # De macro/macro_internet_por_elo.md (validado el 25-11-2026 con 48.229 partidas, Hierro-Bronce → Aspirante): jungla,
    # full clear 4,15 → 3,5 min, primera vuelta a base 4,7 → 3,4 y minutos parado del 4 al 14 0,27 → 0,05; Farsight cada
    # 10 min, top 0,02 → 0,50, mid 0,17 → 0,89 y ADC 0,26 → 0,95 (jungla y support no lo usan); objetivos de tu equipo
    # que acaban en torre o inhibidor, 31 % → 35 % (poco, pero siempre hacia arriba). Medido y sin diferencia (no sale):
    # morir bajo su torre justo después de una kill de tu equipo allí (el «disparo de más» de un dive), 0,06-0,11 en todos.
    ("rm_obj_convert_pct", "Objetivos de tu equipo que acaban en torre o inhibidor (90 s; 3 min tras el barón)", ("TOP", "JUNGLE", "MID", "ADC", "SUPPORT"), "%"),
    ("rm_jg_clear_min", "Minuto en que acabas el primer full clear (24 de CS)", ("JUNGLE",), ""),
    ("rm_first_back_min", "Minuto de tu primera vuelta a base (sin haber muerto)", ("JUNGLE",), ""),
    ("rm_jg_idle_min", "Minutos parado del 4 al 14 (sin CS, kills ni objetivos)", ("JUNGLE",), ""),
    # Muertes que cuestan caro (validado el 25-11-2026 con 50.328 partidas, Hierro-Bronce → Aspirante): reaparecer después
    # de que salga el dragón, el ancestral o el barón 0,68-0,79 → 0,45-0,61 por partida en los cinco roles (y que el
    # rival se lo lleve antes de que llegues, 0,24-0,31 → 0,14-0,20); morir ante tu rival justo después de que suba a 6,
    # 11 o 16, top 0,43 → 0,28, mid 0,32 → 0,15, jungla 0,15 → 0,09; morir solo tras perder vuestra primera torre, jungla
    # 0,31 → 0,19, support 0,27 → 0,17, mid 0,40 → 0,20; ADC sin desplazamientos muerto en una lateral pasado el río,
    # 0,53 → 0,27; muertes cada 10 min yendo 3.000 por detrás, jungla 3,5 → 2,9 y mid 3,0 → 2,4. Medido y sin diferencia
    # (no sale): la dispersión de las peleas tardías (C-3, 18-24 % en todos), quedarte farmeando cuando tu rival roamea
    # (C-8) y los objetivos que cogéis yendo por detrás (C-10, 14-19 %).
    ("rm_dead_spawn", "Muertes en las que reapareces después de que salga el dragón o el barón", ("TOP", "JUNGLE", "MID", "ADC", "SUPPORT"), ""),
    ("rm_dead_spawn_lost", "De ellas, el rival se lo lleva antes de que llegues", ("TOP", "JUNGLE", "MID", "ADC", "SUPPORT"), ""),
    ("rm_spike_deaths", "Muertes ante tu rival justo después de que suba a 6, 11 o 16 (60 s)", ("TOP", "JUNGLE", "MID", "ADC"), ""),
    ("rm_alone_after_tower", "Muertes solo (sin aliados en la jugada) en los 3 min tras perder vuestra primera torre", ("JUNGLE", "MID", "SUPPORT"), ""),
    ("rm_adc_side_deaths", "Muertes en una lateral pasado el río tras la primera torre (ADC sin desplazamientos)", ("ADC",), ""),
    ("rm_behind_deaths10", "Muertes cada 10 min yendo 3.000 de oro por detrás (desde el 14)", ("JUNGLE", "MID"), ""),
]
VISION_KEYS = {"rm_wards_pre14", "rm_obj_ward_pct", "rm_pink_obj", "rm_ward_enemy_jg10", "rm_ward_enemy_jg_pct", "rm_ward_lane_pct", "rm_farsight10"}
MACRO_KEYS = {"rm_obj_convert_pct", "rm_jg_clear_min", "rm_first_back_min", "rm_jg_idle_min"}
DEATH_KEYS = {"rm_dead_spawn", "rm_dead_spawn_lost", "rm_spike_deaths", "rm_alone_after_tower", "rm_adc_side_deaths", "rm_behind_deaths10"}


def _move_value(mv: dict | None, phase: str, field: str) -> float | None:
    if not mv:
        return None
    if phase == "late" and field in ("grouped_late", "side_alone_late"):
        return mv.get(field)
    if phase == "both":
        vals = [(mv.get(ph) or {}).get(field, 0.0) for ph in ("early", "late") if mv.get(ph)]
        return sum(vals) / len(vals) if vals else None
    block = mv.get(phase)
    return block.get(field, 0.0) if block else None


def movement_summary(games: list[dict], profile: dict | None) -> dict | None:
    """Dónde pasas el tiempo (posiciones reconstruidas cada 5 s) frente a tu rival directo y, si hay partidas analizadas
    con el movimiento, frente a cada elo: tu rol más jugado y, si hay varios elos con datos, tu campeón."""
    pool = [g for g in games if reliable(g) and (g.get("movement") or {}).get("me") and g.get("role")]
    if len(pool) < MIN_MOVE_GAMES:
        return None
    role = Counter(g["role"] for g in pool).most_common(1)[0][0]
    mine = [g for g in pool if g["role"] == role]
    champ = Counter(g["champ"] for g in mine).most_common(1)[0][0]
    tiers, scope = elo_tiers(profile, role, champ)
    tiers = {t: b for t, b in tiers.items() if b.get("move")}
    rows = _move_rows(mine, role, tiers) + _route_rows(mine, role, tiers)
    for r in rows:
        r.update(_move_trend(r))
    return {"role": role, "champ": champ if scope == "champ" and tiers else None, "games": len(mine), "rows": rows, "elo": bool(tiers)}


def _elo_move(tiers: dict, key: str) -> dict | None:
    """Valor de esa medida en cada elo que la tiene (None si no la tiene ninguno)."""
    return {g: tiers[g]["move"].get(key) for g in ELO_GROUPS if g in tiers and tiers[g]["move"].get(key) is not None} or None


def _move_rows(mine: list[dict], role: str, tiers: dict) -> list[dict]:
    """Filas de dónde pasas el tiempo (``MOVE_ROWS`` de tu rol): tu %, el de tu rival directo y el de cada elo."""
    rows = []
    for key, phase, field, label, roles, elo_key in MOVE_ROWS:
        if roles is not None and role not in roles:
            continue
        me_vals = [v for g in mine if (v := _move_value(g["movement"].get("me"), phase, field)) is not None]
        opp_vals = [v for g in mine if (v := _move_value(g["movement"].get("opp"), phase, field)) is not None]
        if len(me_vals) < MIN_MOVE_GAMES:
            continue
        rows.append({
            "key": key,
            "label": label,
            "mine": round(sum(me_vals) / len(me_vals), 1),
            "opp": round(sum(opp_vals) / len(opp_vals), 1) if len(opp_vals) >= MIN_MOVE_GAMES else None,
            "tiers": _elo_move(tiers, elo_key) if elo_key else None,
            "unit": "%",
        })
    return rows


def _route_group(field: str, key: str) -> str:
    """Grupo de una fila: «rules» (reglas medibles), «vision», «macro» (ritmo y objetivos), «deaths» (muertes caras) o
    «routes» (roams)."""
    if field == "rules":
        return "rules"
    for group, keys in (("vision", VISION_KEYS), ("macro", MACRO_KEYS), ("deaths", DEATH_KEYS)):
        if key in keys:
            return group
    return "routes"


def _route_rows(mine: list[dict], role: str, tiers: dict) -> list[dict]:
    """Filas de roams, visión y reglas medibles de tu rol (``ROUTE_ROWS`` y ``rule_metrics.ROWS``): tu media y la de cada elo."""
    rows = []
    for key, label, roles, unit, field in [(*r, "route_macro") for r in ROUTE_ROWS] + [(*r, "rules") for r in RULE_ROWS]:
        if role not in roles:
            continue
        vals = [v for g in mine if isinstance(v := (g.get(field) or {}).get(key), (int, float))]
        if len(vals) < MIN_MOVE_GAMES:
            continue
        rows.append({
            "key": key,
            "label": label,
            "unit": unit,
            "group": _route_group(field, key),
            "mine": round(sum(vals) / len(vals), 1 if unit == "%" else 2),
            "opp": None,
            "tiers": _elo_move(tiers, key),
        })
    return rows


MOVE_TREND_REL = 0.10  # diferencia del elo más alto frente al más bajo para decir que «va en una dirección»…
MOVE_TREND_ABS = 1.0  # …y en puntos de porcentaje, para no leer ruido en valores pequeños…
MOVE_TREND_ABS_COUNT = 0.05  # …o en recuentos por partida (wards destruidos, kills en otras líneas: 0,04 → 0,13)


def _move_trend(row: dict) -> dict:
    """Hacia dónde van los elos altos en esa medida (``up``/``down``/None) y dónde quedas tú: ``low`` si estás más allá
    del elo más bajo en la dirección mala, ``top`` si estás a la altura del más alto o mejor. Así cada fila se lee sola:
    estar solo en una lateral es bueno en top (los Aspirante hacen más split) y malo en ADC."""
    tiers = row.get("tiers") or {}
    present = [g for g in ELO_GROUPS if g in tiers]
    if len(present) < 2:
        return {"trend": None, "standing": None}
    lo, hi = tiers[present[0]], tiers[present[-1]]
    diff = hi - lo
    min_abs = MOVE_TREND_ABS if row.get("unit") == "%" else MOVE_TREND_ABS_COUNT
    if abs(diff) < max(min_abs, MOVE_TREND_REL * max(abs(lo), abs(hi))):
        return {"trend": None, "standing": None}
    trend = "up" if diff > 0 else "down"
    mine = row["mine"]
    if (trend == "up" and mine <= lo) or (trend == "down" and mine >= lo):
        standing = "low"
    elif (trend == "up" and mine >= hi) or (trend == "down" and mine <= hi):
        standing = "top"
    else:
        standing = "mid"
    return {"trend": trend, "standing": standing}


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
    after, by_index = _after_losses(sessions)
    results = [bool(g["win"]) for s in sessions for g in s]
    overall = sum(results) / len(results)
    wr_after = sum(after) / len(after) if after else None
    se = math.sqrt(max(overall * (1 - overall), 0.05) / len(after)) if after else None
    drop = (overall - wr_after) * 100 if wr_after is not None else None
    tilted = bool(after) and len(after) >= TILT_MIN_GAMES and drop >= max(TILT_MIN_DROP, Z_80 * se * 100)
    long_sessions = [s for s in sessions if len(s) >= LONG_SESSION]
    message = _tilt_message(tilted, after, wr_after, overall)
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


def _after_losses(sessions: list[list[dict]]) -> tuple[list[bool], dict[int, list[bool]]]:
    """Victorias en las partidas jugadas tras ``TILT_STREAK`` derrotas seguidas y por número de partida en la sesión."""
    after: list[bool] = []
    by_index: dict[int, list[bool]] = defaultdict(list)
    for s in sessions:
        streak = 0
        for i, g in enumerate(s):
            by_index[min(i + 1, 4)].append(bool(g["win"]))
            if streak >= TILT_STREAK:
                after.append(bool(g["win"]))
            streak = 0 if g["win"] else streak + 1
    return after, by_index


def _tilt_message(tilted: bool, after: list[bool], wr_after: float | None, overall: float) -> str:
    """Texto del aviso de tilt: caída clara, sin caída clara o aún pocas partidas tras 2 derrotas seguidas."""
    if tilted:
        return (
            f"Tras {TILT_STREAK} derrotas seguidas ganas el {round(wr_after * 100)} % ({len(after)} partidas), frente al {round(overall * 100)} % "
            "general: después de 2 derrotas, para al menos 15 minutos antes de la siguiente."
        )
    if len(after) >= TILT_MIN_GAMES:
        return f"Tras {TILT_STREAK} derrotas seguidas: {round(wr_after * 100)} % de victorias en {len(after)} partidas (general {round(overall * 100)} %). No hay una caída clara."
    return f"Aún hay pocas partidas jugadas tras {TILT_STREAK} derrotas seguidas ({len(after)}) para saber si te afecta."


def _pool_message(champions: int, games: int, wide: bool) -> str:
    """Texto del pool de un rol: aviso si juegas demasiados campeones distintos o cuántos juegas."""
    if wide:
        return (
            f"Has jugado {champions} campeones distintos en tus últimas {games} partidas de este rol: los coaches recomiendan un núcleo "
            "de 2-3 para dominar sus matchups y picos de poder."
        )
    noun = "campeón" if champions == 1 else "campeones"
    return f"Juegas {champions} {noun} en tus últimas {games} partidas de este rol."


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
                "message": _pool_message(len(champs), len(items), wide),
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


def habits(games: list[dict], profile: dict | None = None) -> dict:
    """Hábitos entre partidas (tilt, sesiones, pool de campeones, constancia) y ventajas que se escapan."""
    return {
        "tilt": tilt_report(games),
        "pool": pool_report(games),
        "leads": leads_report(games),
        "consistency": consistency_report(games, profile),
    }


CONSISTENCY_ROWS = (
    ("deaths_sd", "Cuánto cambian tus muertes de una partida a otra (desviación)"),
    ("worst_deaths", "Muertes en tu 20 % de partidas peores"),
)


def consistency_report(games: list[dict], profile: dict | None) -> dict | None:
    """C-11: tus últimas 20 partidas fiables de tu rol más jugado (``games``, de la más reciente a la más antigua) frente
    a la mediana de los jugadores de cada elo con sus últimas 20 (``consistency``)."""
    pool = [g for g in games if reliable(g) and g.get("role") and isinstance((g.get("metrics") or {}).get("deaths"), (int, float))]
    if len(pool) < consistency.MIN_GAMES:
        return None
    role = Counter(g["role"] for g in pool).most_common(1)[0][0]
    deaths = [float(g["metrics"]["deaths"]) for g in pool if g["role"] == role][: consistency.RECENT]
    mine = consistency.summary(list(reversed(deaths)))
    if mine is None:
        return None
    tiers, _ = elo_tiers(profile, role)
    rows = []
    for key, label in CONSISTENCY_ROWS:
        elo = {t: b["consistency"][key] for t, b in tiers.items() if (b.get("consistency") or {}).get(key) is not None}
        row = {"key": key, "label": label, "unit": "", "mine": mine[key], "tiers": elo or None}
        row.update(_move_trend(row))
        rows.append(row)
    return {"role": role, "games": len(deaths), "rows": rows}


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


# Prioridad de línea en 1.818 partidas de junglas OTP (data/otps y data/partidas_campeon, noviembre de 2026): dragones
# normales que coge tu equipo según el CS de mid y bot frente a sus rivales (las dos ≥ +5, mezcla, las dos ≤ −5) y la
# primera torre. Son partidas de OTPs (ganan algo más de la mitad): sirven para ver cuánto pesa la prioridad, no como nota.
PRIORITY_REFS = {  # con el análisis 12 (CS minuto a minuto) de 1.012 partidas de Challenger y 551 de Esmeralda sin AFK
    "dragons": {"prio": {"Challenger": 72, "Esmeralda": 75}, "mixed": {"Challenger": 56, "Esmeralda": 55}, "noprio": {"Challenger": 47, "Esmeralda": 41}},
    "first_tower": {"Challenger": {"rate": 55, "wr_with": 78, "wr_without": 36, "median_min": 14.5},
                    "Esmeralda": {"rate": 52, "wr_with": 69, "wr_without": 34, "median_min": 14.8}},
    # CS de tu línea a los 10 → vuestra torre de esa línea cae antes (Challenger, top / mid / bot).
    "lane_tower": {"ahead": (84, 75, 72), "behind": (31, 44, 24)},
}
PRIORITY_MIN_GAMES = 5


def priority_summary(games: list[dict]) -> dict | None:
    """Tus dragones según la prioridad de mid y bot y tu primera torre, frente a Challenger y Esmeralda."""
    pool = [g for g in games if reliable(g) and g.get("has_timeline") and (g.get("metrics") or {}).get("drag_prio_n") is not None]
    if len(pool) < PRIORITY_MIN_GAMES:
        return None
    rows = []
    for key, label in (("prio", "Con prioridad en mid y bot"), ("mixed", "Prioridad repartida"), ("noprio", "Sin prioridad en mid y bot")):
        n = sum(g["metrics"].get(f"drag_{key}_n") or 0 for g in pool)
        won = sum(g["metrics"].get(f"drag_{key}_won") or 0 for g in pool)
        rows.append({"key": key, "label": label, "n": n, "won": won, "pct": round(won / n * 100) if n else None,
                     "ref": PRIORITY_REFS["dragons"][key]})
    towers = [g for g in pool if g["metrics"].get("first_tower_ours") is not None]
    first = [g for g in towers if g["metrics"]["first_tower_ours"]]
    without = [g for g in towers if not g["metrics"]["first_tower_ours"]]
    mins = sorted(g["metrics"]["our_first_tower_min"] for g in pool if g["metrics"].get("our_first_tower_min") is not None)
    tower = {
        "games": len(towers),
        "rate": round(len(first) / len(towers) * 100) if towers else None,
        "wr_with": round(sum(g["win"] for g in first) / len(first) * 100) if first else None,
        "wr_without": round(sum(g["win"] for g in without) / len(without) * 100) if without else None,
        "median_min": mins[len(mins) // 2] if mins else None,
        "ref": PRIORITY_REFS["first_tower"],
    }
    return {"games": len(pool), "dragons": rows, "first_tower": tower, "lane_tower": PRIORITY_REFS["lane_tower"],
            "learn": learn.topic("objectives")}


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


ELO_OF_TARGET = {"SILVER": "GOLD", "GOLD": "GOLD", "PLATINUM": "PLATINUM", "EMERALD": "EMERALD", "DIAMOND": "DIAMOND", "MASTER": "MASTER"}


def errors_score(elo: dict | None, target_tier: str | None) -> dict | None:
    """Tus errores que separan elos frente a los de tu elo objetivo, en 0-100 (70 = como ellos; más, menos errores)."""
    if not elo or not elo.get("levels"):
        return None
    want = ELO_OF_TARGET.get((target_tier or "").upper())
    level = next((lv for lv in elo["levels"] if lv["tier"] == want), None)
    if level is None or not level.get("per_game_sep"):
        return None
    ratio = elo["per_game_sep"] / level["per_game_sep"]
    return {"score": round(max(0.0, min(100.0, 100 - (ratio - 0.7) * 100))), "mine": elo["per_game_sep"], "target": level["per_game_sep"], "tier": want}


def readiness(games: list[dict], refs: References, goals: list[dict], elo: dict | None = None) -> dict:
    """Nota 0-100 para jugar clasificatorias: propósitos que cumples, fundamentos (medias frente a tu elo objetivo) y
    errores que separan elos frente a los de tu elo objetivo. Sin propósitos, mitad fundamentos y mitad errores."""
    games = [g for g in games if reliable(g)]
    recent = games[:RECENT]
    scores = [fundamentals_score(g["metrics"] or {}, game_refs(refs, g), g["role"]) for g in recent]
    scores = [s for s in scores if s is not None]
    fundamentals = round(sum(scores) / len(scores), 1) if scores else None
    rated = [g["rate"] for g in goals if g.get("active", True) and g.get("rate") is not None and g.get("games", 0) >= MIN_GOAL_GAMES]
    goals_rate = round(sum(rated) / len(rated)) if rated else None
    errs = errors_score(elo, getattr(refs, "tier", None))
    parts = _readiness_parts(goals_rate, fundamentals, errs)  # (peso, nota 0-100)
    total = sum(w for w, _ in parts)
    score = round(sum(w * v for w, v in parts) / total) if parts else None
    missing = max(0, MIN_GAMES_READY - len(games))
    ready = score is not None and score >= READY_SCORE and not missing
    tier = ELO_LABEL.get(ELO_OF_TARGET.get(getattr(refs, "tier", "") or "", ""), "tu elo objetivo")
    if score is None:
        message = "Juega unas partidas con el seguimiento activo para calcularlo."
    elif missing:
        message = f"Te faltan {missing} partidas analizadas para valorarlo con fiabilidad."
    elif ready:
        message = _ready_message(goals_rate, elo)
    else:
        message = _not_ready_message(goals_rate, errs, fundamentals, tier)
    return {
        "score": score,
        "ready": ready,
        "fundamentals": fundamentals,
        "goals_rate": goals_rate,
        "errors": errs,
        "games": len(recent),
        "missing_games": missing,
        "message": message,
        "threshold": READY_SCORE,
    }


def _readiness_parts(goals_rate: int | None, fundamentals: float | None, errs: dict | None) -> list[tuple[float, float]]:
    """Partes de la nota (peso, nota 0-100) que haya: propósitos, fundamentos y errores que separan elos."""
    parts = []
    if goals_rate is not None:
        parts.append((0.5 if errs else 0.6, goals_rate))
    if fundamentals is not None:
        parts.append((_fundamentals_weight(goals_rate is not None, errs), fundamentals * 10))
    if errs:
        parts.append((_errors_weight(goals_rate is not None, fundamentals is not None), errs["score"]))
    return parts


def _fundamentals_weight(has_goals: bool, errs: dict | None) -> float:
    """Peso de los fundamentos en la nota, según haya propósitos y errores por elo."""
    if has_goals:
        return 0.25 if errs else 0.4
    return 0.5 if errs else 1.0


def _errors_weight(has_goals: bool, has_fundamentals: bool) -> float:
    """Peso de los errores que separan elos en la nota, según haya propósitos y fundamentos."""
    if has_goals:
        return 0.25
    return 0.5 if has_fundamentals else 1.0


def _ready_message(goals_rate: int | None, elo: dict | None) -> str:
    """Texto cuando estás listo para clasificatorias (con un aviso si casi todas tus partidas son normales)."""
    if goals_rate is not None:
        message = "Cumples tus propósitos y juegas al nivel de tu elo objetivo: es buen momento para jugar clasificatorias."
    else:
        message = "Juegas al nivel de tu elo objetivo: es buen momento para jugar clasificatorias. Ponte un propósito para seguir mejorando."
    if elo and elo.get("games") and (elo.get("normals") or 0) >= 0.8 * elo["games"]:
        message += " Ojo: casi todas tus partidas son normales, donde los rivales suelen ser más flojos; confírmalo con unas pocas clasificatorias."
    return message


def _not_ready_message(goals_rate: int | None, errs: dict | None, fundamentals: float | None, tier: str) -> str:
    """Qué te falta para jugar clasificatorias: cumplir tus propósitos, cometer menos errores que separan elos o tus medias."""
    if goals_rate is not None and goals_rate < READY_SCORE:
        return f"Cumples tus propósitos en el {goals_rate} % de tus últimas partidas. Llega al 70 % antes de saltar a clasificatorias."
    if errs and errs["score"] < READY_SCORE:
        return (
            f"Cometes {_fmt(errs['mine'], 1)} errores que separan elos por partida y en {tier} se cometen {_fmt(errs['target'], 1)}: "
            "baja de ahí con los que más repites (mira «Tus errores frente a cada elo»)."
        )
    if fundamentals is not None and fundamentals >= 5:
        return "Tus medias ya están a la altura de tu elo objetivo, pero aún sin margen: sigue unas partidas más o súbelo un elo para ver qué te falta."
    return "Tus medias aún están por debajo de tu elo objetivo: céntrate en tus puntos débiles (abajo)."


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
    first, overlap, notes = _item_notes(legendary, core, popular)
    boots = mine.get("boots")
    ref_boots = ref.get("boots") or []
    if boots and ref_boots and boots not in ref_boots:
        notes.append({"ok": None, "text": "Botas distintas a las más habituales (cámbialas si es por el daño rival).", "items": [boots, ref_boots[0]]})
    skill_max = "".join(mine.get("skill_max") or [])[:3]
    ref_max = "".join(ref.get("skill_max") or [])[:3]
    if skill_max and ref_max:
        notes.append(_skills_note(skill_max, ref_max))
    return {"core": core, "boots": ref_boots, "skill_max": list(ref_max), "popular": ref.get("popular") or [], "overlap": overlap, "first": first, "notes": notes}


def _item_notes(legendary: list, core: list, popular: dict) -> tuple:
    """Tu primer objeto, cuántos de tus 3 primeros están en el núcleo de la build más jugada y las notas de ambos."""
    first = legendary[0] if legendary else None
    notes: list[dict] = []
    overlap = len(set(legendary[:3]) & set(core)) if core else None
    if first and core:
        notes.append(_first_item_note(first, core, popular))
    if overlap is not None and len(legendary) >= 3:
        notes.append({"ok": overlap >= 2, "text": f"Coincides en {overlap} de {len(core)} objetos clave con la build más jugada."})
    return first, overlap, notes


def _first_item_note(first, core: list, popular: dict) -> dict:
    """Nota sobre tu primer objeto frente al de la build más jugada (y lo habitual que es en otras builds)."""
    if first == core[0]:
        return {"ok": True, "text": "Primer objeto: el mismo que la build más jugada."}
    if first in core:
        return {"ok": True, "text": "Empiezas por otro de los objetos clave de la build más jugada (cambia el orden).", "items": [first, core[0]]}
    if first in popular:
        return {"ok": True, "text": f"Primer objeto distinto al más habitual, pero lo lleva un {round((popular[first] or 0) * 100)} % de las builds.", "items": [first, core[0]]}
    return {"ok": False, "text": "Tu primer objeto no está entre los habituales de tu campeón en este rol.", "items": [first, core[0]]}


def _skills_note(skill_max: str, ref_max: str) -> dict:
    """Nota sobre el orden en que maximizas las habilidades frente al habitual."""
    same = skill_max == ref_max
    return {"ok": same, "text": f"Habilidades: maximizas {' > '.join(skill_max)}" + ("" if same else f"; lo habitual es {' > '.join(ref_max)}") + "."}


def build_summary(games: list[dict], refs: dict) -> list[dict]:
    """Por campeón: tu primer objeto, tu núcleo de build y tiempos, frente a la build más jugada."""
    by: dict[tuple[int, str | None], list[dict]] = defaultdict(list)
    for g in games:
        if g.get("build") and g["build"].get("items") is not None:
            by[(g["champ"], g["role"])].append(g)
    out = []
    for (champ, role), items in sorted(by.items(), key=lambda kv: len(kv[1]), reverse=True)[:4]:
        out.append(_champ_build(champ, role, items, refs))
    return out


def _count_build(b: dict, firsts: Counter, cores: Counter, boots: Counter, maxes: Counter) -> None:
    """Suma una build (objetos terminados, botas y orden de habilidades) a los recuentos de su campeón."""
    legend = list(b.get("items") or [])
    if legend:
        firsts[legend[0]] += 1
    if len(legend) >= 3:
        cores[tuple(legend[:3])] += 1
    if b.get("boots"):
        boots[b["boots"]] += 1
    if b.get("skill_max"):
        maxes["".join(b["skill_max"])[:3]] += 1


def _my_items(core: tuple | None, first: tuple | None) -> list:
    """Tus objetos más repetidos: el núcleo de 3 si lo hay; si no, solo el primero."""
    if core:
        return list(core[0])
    return [first[0]] if first else []


def _champ_build(champ: int, role: str | None, items: list[dict], refs: dict) -> dict:
    """Tu build más repetida con un campeón y rol (primer objeto, núcleo, botas, habilidades y tiempos) frente a la más jugada."""
    firsts: Counter = Counter()
    cores: Counter = Counter()
    boots: Counter = Counter()
    maxes: Counter = Counter()
    minutes = []
    for g in items:
        _count_build(g["build"], firsts, cores, boots, maxes)
        if (g["metrics"] or {}).get("first_item_min") is not None:
            minutes.append(g["metrics"]["first_item_min"])
    first = firsts.most_common(1)[0] if firsts else None
    core = cores.most_common(1)[0] if cores else None
    mine = {
        "items": _my_items(core, first),
        "boots": boots.most_common(1)[0][0] if boots else None,
        "skill_max": list(maxes.most_common(1)[0][0]) if maxes else [],
    }
    return {
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


def summarize(games: list[dict], refs: References, goals: list[dict], build_refs: dict | None = None, elo_profile: dict | None = None) -> dict:
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
    elo = {r: elo_tiers(elo_profile, r)[0] for r in {g.get("role") for g in games if g.get("role")}} if elo_profile else None
    elo_block = elo_errors(games, elo_profile)
    recurring = recurring_mistakes(games, elo)
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
        "elo_errors": elo_block,
        "movement": movement_summary(games, elo_profile),
        "deaths": deaths,
        "jungle": jungle_summary(games),
        "priority": priority_summary(games),
        "goals": evaluated,
        "readiness": readiness(games, refs, evaluated, elo_block),
        "champions": champion_stats(games, refs),
        "builds": build_summary(games, build_refs or {}),
        "categories": CATEGORIES,
    }
