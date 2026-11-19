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

from app.coach.benchmarks import CATEGORIES, METRICS, References, applies

RECENT = 10
READY_SCORE = 70
MIN_GAMES_READY = 10

# Métrica, peso, plantilla de título, detalle, consejo y propósito sugerido.
RULES: list[dict] = [
    {
        "metric": "deaths_to_jungler_pre14",
        "weight": 1.35,
        "title": "Te pilla el jungla rival",
        "detail": "{v} muertes por partida antes del 14 en las que participa el jungla rival (referencia {ref}).",
        "tip": "Antes de empujar, piensa dónde está el jungla rival: fíjate por qué lado empezó y cuenta ~3:15 para su primer gank. Si no lo has visto en 30-40 s, juega como si viniera a tu línea. Pon un ward en el río o el tribush a partir del 2:45.",
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
        "detail": "{v} muertes por partida antes del 14 sin haber puesto un ward en los 2 minutos previos (referencia {ref}).",
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
        "tip": "Deduce su ruta por dónde empezó: si empieza arriba, a los 3:15 estará abajo. Ve a su mismo lado para contraganquear, o al contrario para invadir o hacer objetivo.",
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
        "detail": "{v} veces por partida subes la R más de 30 s después de llegar a nivel 6, 11 o 16 (referencia {ref}).",
        "tip": "Usa el atajo de subir habilidad (Ctrl + tecla) en cuanto suba el nivel, o activa la subida automática de la R en las opciones.",
        "goal": ("<=", 0, "Subir la R en cuanto llegues a 6, 11 y 16"),
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
    {"metric": "r_late", "op": "<=", "target": 0, "label": "Subir la R en cuanto llegues a 6, 11 y 16"},
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
}


# ------------------------------------------------------------------ utilidades
def _mean(values: list[float]) -> float | None:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


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
def metric_table(games: list[dict], refs: References) -> list[dict]:
    rows = []
    for key, m in METRICS.items():
        pairs = []
        for g in games:
            if not applies(m, g["role"]):
                continue
            value = (g["metrics"] or {}).get(key)
            ref = refs.for_role(g["role"]).get(key)
            if value is None or ref is None:
                continue
            pairs.append((value, ref))
        if not pairs:
            continue
        value = _mean([v for v, _ in pairs])
        ref = _mean([r for _, r in pairs])
        dev = deviation(key, value, ref)
        status = "good" if dev <= -0.05 else "bad" if dev >= 0.15 else "ok"
        trend = None
        if len(pairs) >= 6:
            half = len(pairs) // 2
            recent, older = _mean([v for v, _ in pairs[:half]]), _mean([v for v, _ in pairs[half:]])
            better = (recent < older) if m.better == "lower" else (recent > older)
            trend = {"recent": _round(recent, m.decimals), "older": _round(older, m.decimals), "improving": better if abs(recent - older) > 1e-9 else None}
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
                "trend": trend,
                "series": [_round(v, m.decimals + 1) for v, _ in pairs[:20]],
            }
        )
    return rows


def weaknesses(table: list[dict], death_tags: dict, limit: int = 8) -> list[dict]:
    by_key = {row["key"]: row for row in table}
    found: dict[str, dict] = {}
    for rule in RULES:
        row = by_key.get(rule["metric"])
        if not row or row["games"] < 3:
            continue
        m = METRICS[rule["metric"]]
        value, ref = row["value"], row["ref"]
        dev = row["dev"]
        if "min_abs" in rule:
            if (m.better == "higher" and value > -rule["min_abs"]) or (m.better == "lower" and value < rule["min_abs"]):
                continue
        elif dev < 0.15:
            continue
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
        }
    return sorted(found.values(), key=lambda w: w["severity"], reverse=True)[:limit]


def _death_context(metric: str, tags: dict) -> str:
    early = tags.get("early_total") or 0
    if metric not in ("deaths_to_jungler_pre14", "deaths_ganked", "deaths_pre14") or early < 3:
        return ""
    parts = []
    for tag, text in (("no_ward", "sin ward en los 2 minutos previos"), ("overextended", "adelantado en la línea")):
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
    for g in games[:limit_games]:
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
    for goal in goals:
        applicable = [
            g
            for g in games
            if (not goal.get("role") or g["role"] == goal["role"]) and (g["metrics"] or {}).get(goal["metric"]) is not None
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
    recent = games[:RECENT]
    scores = [fundamentals_score(g["metrics"] or {}, refs.for_role(g["role"]), g["role"]) for g in recent]
    scores = [s for s in scores if s is not None]
    fundamentals = round(sum(scores) / len(scores), 1) if scores else None
    rated = [g["rate"] for g in goals if g.get("active", True) and g.get("rate") is not None and g.get("games", 0) >= 3]
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
        message = "Cumples tus propósitos y tus fundamentos están al nivel de tu elo objetivo: es buen momento para jugar clasificatorias."
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
        scores = [fundamentals_score(g["metrics"] or {}, refs.for_role(role), role) for g in items]
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
    for g in games:
        g["score"] = fundamentals_score(g["metrics"] or {}, refs.for_role(g["role"]), g["role"])
    return {
        "games": len(games),
        "wins": wins,
        "wr": round(wins / len(games) * 100, 1) if games else None,
        "roles": [{"role": r, "games": n} for r, n in roles.most_common()],
        "metrics": table,
        "weaknesses": weaknesses(table, deaths),
        "deaths": deaths,
        "jungle": jungle_summary(games),
        "goals": evaluated,
        "readiness": readiness(games, refs, evaluated),
        "champions": champion_stats(games, refs),
        "builds": build_summary(games, build_refs or {}),
        "categories": CATEGORIES,
    }
