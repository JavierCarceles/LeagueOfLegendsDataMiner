"""«Aprende esto»: material gratuito de coaches para cada punto débil y cada error.

Cada tema trae 1-3 vídeos gratis (primero en español), un ejercicio concreto y su propósito. Los vídeos
salen de la investigación de ``docs/coaches.md`` (apartado D): autor y título comprobados con el oEmbed
público de YouTube; no se añade ninguna URL que no esté verificada allí.
"""

from __future__ import annotations

VIDEOS: dict[str, dict] = {
    "cs_impact": {"title": "FARMEAR o IMPACTAR el MAPA", "channel": "PochiPoom", "lang": "ES", "url": "https://www.youtube.com/watch?v=vHNSfy3mTkU"},
    "cs_truth": {"title": "The TRUTH Behind 10 CS Per Minute", "channel": "Skill Capped", "lang": "EN", "url": "https://www.youtube.com/watch?v=BhLOZeO9tGM"},
    "track_jungler": {"title": "How to Track the Enemy Jungler as a LANER", "channel": "Skill Capped", "lang": "EN", "url": "https://www.youtube.com/watch?v=MOmyQIs2Am4"},
    "warding_ganks": {"title": "COMPLETE WARDING GUIDE - Never Die to Ganks Again", "channel": "Coach Chippys", "lang": "EN", "url": "https://www.youtube.com/watch?v=8b-PM_PR5Js"},
    "vision_master": {"title": "EVERYTHING you NEED to KNOW to become a VISION MASTER!", "channel": "Skill Capped", "lang": "EN", "url": "https://www.youtube.com/watch?v=6cXqzH2vMH8"},
    "ward_mistakes": {"title": "The 10 WORST WARD MISTAKES", "channel": "Skill Capped", "lang": "EN", "url": "https://www.youtube.com/watch?v=U-uju2fG15k"},
    "jungle_mistakes": {"title": "#1 Jungle Coaching - Fixing the Biggest Jungle Mistakes", "channel": "Coach Kirei", "lang": "EN", "url": "https://www.youtube.com/watch?v=pr9GOmrhpWQ"},
    "jungle_s16": {"title": "Season 16 Jungle guide (Faelights, Objectives, Midgame)", "channel": "Coach Kirei", "lang": "EN", "url": "https://www.youtube.com/watch?v=wHWcWtbWc1c"},
    "macro_dishake": {"title": "Cómo subir de elo jugando ADC, Support y Jungla", "channel": "Dishake", "lang": "ES", "url": "https://www.youtube.com/watch?v=VifY8Eucr2Y"},
    "macro_pochi": {"title": "Coach profesional EXPLICA Macro y Micro avanzado", "channel": "PochiPoom", "lang": "ES", "url": "https://www.youtube.com/watch?v=gCOM9xQf3wo"},
    "mid_game": {"title": "The Ultimate Guide To Mid Game", "channel": "Coach Curtis", "lang": "EN", "url": "https://www.youtube.com/watch?v=udMlKeYGq1w"},
    "waves_mauro": {"title": "ENSEÑO CONTROL DE OLEADAS a ZELING", "channel": "Mauro Garih", "lang": "ES", "url": "https://www.youtube.com/watch?v=j1Vc_ByA8jY"},
    "waves_dishake": {"title": "Tu ELO está estancado por esto", "channel": "Dishake", "lang": "ES", "url": "https://www.youtube.com/watch?v=YLgMgbuZ0Zk"},
    "waves_iceberg": {"title": "The Wave Management Iceberg", "channel": "Coach Rogue", "lang": "EN", "url": "https://www.youtube.com/watch?v=qaZGJAADRsk"},
    "back_waves": {"title": "GUÍA de CONTROL de OLEADAS para el BACK PERFECTO", "channel": "PochiPoom", "lang": "ES", "url": "https://www.youtube.com/watch?v=GVeV7BWykic"},
    "recall": {"title": "How to Recall like a CHALLENGER", "channel": "Skill Capped", "lang": "EN", "url": "https://www.youtube.com/watch?v=8CAAejEoGNg"},
    "trades_koi": {"title": "GUÍA DEFINITIVA DE TRADES #1", "channel": "Movistar KOI", "lang": "ES", "url": "https://www.youtube.com/watch?v=pSe5rUcc_0k"},
    "trades": {"title": "The ONLY TRADING GUIDE You'll EVER NEED", "channel": "Skill Capped", "lang": "EN", "url": "https://www.youtube.com/watch?v=L7qPSGmS9ik"},
    "tilt_pochi": {"title": "Sigue la CIENCIA para NO sentir TILT", "channel": "PochiPoom", "lang": "ES", "url": "https://www.youtube.com/watch?v=k_8NLsjQC5U"},
    "tilt_bbc": {"title": "Why Emerald players Tilt and Rage so easily", "channel": "Broken By Concept", "lang": "EN", "url": "https://www.youtube.com/shorts/W2CmZxHSO7Q"},
}

TOPICS: dict[str, dict] = {
    "farm": {
        "label": "Farm",
        "videos": ["cs_impact", "cs_truth"],
        "drill": "10 minutos al día en la herramienta de práctica: solo últimos golpes, sin habilidades, hasta pasar de 80 CS en el minuto 10.",
        "purpose": "Los coaches piden 85-90 CS en el minuto 10 y unos 8 CS por minuto en Platino y Esmeralda: el farm es oro seguro.",
    },
    "ganks": {
        "label": "Que no te ganqueen",
        "videos": ["warding_ganks", "track_jungler"],
        "drill": "En tus próximas 3 partidas, di en voz alta dónde crees que está el jungla rival a las 2:30, 3:30 y 5:00 y compruébalo en el minimapa.",
        "purpose": "Saber dónde está el jungla rival te dice cuándo puedes empujar y cuándo tienes que jugar atrás.",
    },
    "vision": {
        "label": "Visión",
        "videos": ["vision_master", "ward_mistakes"],
        "drill": "Compra una pink en cada vuelta a base y ponla antes de salir de tu jungla o del río; usa el trinket en cuanto se recargue.",
        "purpose": "La visión es información: con ella eliges las peleas en vez de que te las elijan.",
    },
    "jungle": {
        "label": "Jungla",
        "videos": ["jungle_mistakes", "jungle_s16", "macro_dishake"],
        "drill": "En la pantalla de carga decide tu lado de inicio, tu primer gank (una línea con control de masas y empuje) y qué harás cuando salga el dragón a las 5:00.",
        "purpose": "Un plan para el early evita ganks a ciegas y minutos sin impacto.",
    },
    "objectives": {
        "label": "Objetivos",
        "videos": ["macro_dishake", "jungle_s16"],
        "drill": "Un minuto antes de cada objetivo: empuja tu oleada o acaba el campamento, vuelve a base si hace falta y llega al foso con una pink. Cuenta los rivales vivos antes de pelearlo.",
        "purpose": "Los objetivos se ganan antes de que aparezcan: llegar a tiempo, con visión y sin uno menos.",
    },
    "macro": {
        "label": "Macro y mid game",
        "videos": ["macro_pochi", "mid_game"],
        "drill": "Cada vez que muera un rival o caiga una torre, pregúntate en voz alta cuál es el objetivo más cercano; mira el minimapa cada 3-5 segundos.",
        "purpose": "En Platino y Esmeralda manda la macro: convertir ventajas y no regalar muertes sueltas.",
    },
    "waves": {
        "label": "Oleadas",
        "videos": ["waves_mauro", "waves_dishake", "waves_iceberg"],
        "drill": "Antes de cada vuelta a base o rotación, empuja la oleada hasta su torre; si no da tiempo, congélala cerca de la tuya.",
        "purpose": "Las oleadas deciden cuándo puedes moverte sin perder oro ni experiencia.",
    },
    "backs": {
        "label": "Vueltas a base",
        "videos": ["back_waves", "recall"],
        "drill": "Vuelve a base cuando te llegue para un componente de tu siguiente objeto y tras empujar la oleada; nunca con el cañón llegando a tu torre.",
        "purpose": "Gastar el oro a tiempo es pelear con un objeto más que el rival.",
    },
    "trades": {
        "label": "Intercambios",
        "videos": ["trades_koi", "trades"],
        "drill": "Tradea solo cuando el rival gaste una habilidad clave o vaya a por un súbdito, y retírate antes de que vuelva a tenerla.",
        "purpose": "Ganar la línea es ganar intercambios pequeños, no buscar el all-in a ciegas.",
    },
    "tilt": {
        "label": "Tilt",
        "videos": ["tilt_pochi", "tilt_bbc"],
        "drill": "Tras 2 derrotas seguidas, 15 minutos de pausa lejos del ordenador; juega con el chat silenciado.",
        "purpose": "Jugar enfadado tira partidas: la pausa protege tu elo.",
    },
}

# Punto débil (métrica de ``insights.RULES``) -> tema.
METRIC_TOPIC = {
    "deaths_to_jungler_pre14": "ganks",
    "deaths_ganked": "ganks",
    "deaths_no_vision_pre14": "vision",
    "deaths_overextended": "ganks",
    "deaths_pre14": "trades",
    "deaths_solo": "trades",
    "deaths_duel_behind": "trades",
    "deaths_enemy_jungle": "vision",
    "deaths_isolated_late": "macro",
    "deaths_unspent_gold": "backs",
    "gold_hoard_min": "backs",
    "idle_pct": "macro",
    "time_dead_pct": "macro",
    "cs_min": "farm",
    "cs10": "farm",
    "cs_diff10": "farm",
    "gold_diff15": "waves",
    "vision_min": "vision",
    "control_wards": "vision",
    "obj_participation": "objectives",
    "obj_lost_far": "objectives",
    "deaths_before_objective": "objectives",
    "kp": "macro",
    "impact_pre10": "jungle",
    "enemy_ganks_far": "jungle",
    "jungle_cs10": "jungle",
    "first_item_min": "backs",
    "deaths": "trades",
}
# Error que repites (``insights.MISTAKE_TYPES``) -> tema.
MISTAKE_TOPIC = {
    "death_outnumbered_objective": "objectives",
    "death_before_objective": "objectives",
    "death_gank_no_ward": "ganks",
    "death_overextended": "ganks",
    "death_enemy_jungle": "vision",
    "death_duel_behind": "trades",
    "death_unspent_gold": "backs",
    "death_collapsed": "macro",
    "death_isolated": "macro",
    "death_solo": "trades",
    "death_gank": "ganks",
    "objective_far": "objectives",
    "objective_late_recall": "objectives",
    "objective_late": "objectives",
    "objective_dead": "objectives",
    "fight_unconverted": "macro",
    "baron_wasted": "macro",
    "gold_hoard": "backs",
    "enemy_jungler_far": "jungle",
    "pink_unused": "vision",
    "skill_order": "jungle",
    "first_back": "backs",
    "item_tempo": "farm",
    "no_pinks": "vision",
    "few_wards": "vision",
    "no_sweep": "vision",
    "low_kp": "macro",
    "slow_clear": "jungle",
}
# Etiquetas de un momento de la repetición -> tema (la primera que encaje).
TAG_TOPIC = [
    ("fight_outnumbered", "objectives"), ("before_objective", "objectives"), ("late_recall", "objectives"), ("late", "objectives"),
    ("far", "objectives"), ("duel_behind", "trades"), ("unconverted", "macro"), ("baron_wasted", "macro"), ("collapsed", "macro"), ("isolated", "macro"),
    ("no_ward", "vision"), ("pink", "vision"), ("enemy_jungle", "vision"), ("gank", "ganks"), ("jungler", "ganks"),
    ("overextended", "ganks"), ("skill_order", "jungle"), ("first_back", "backs"), ("item_tempo", "farm"), ("no_pinks", "vision"), ("few_wards", "vision"), ("no_sweep", "vision"), ("low_kp", "macro"), ("slow_clear", "jungle"), ("solo", "trades"), ("unspent_gold", "backs"), ("gold", "backs"), ("tracking", "jungle"),
    ("jungle", "jungle"), ("lane", "waves"), ("idle", "macro"),
]


def topic(key: str | None) -> dict | None:
    """Tema listo para la web: etiqueta, vídeos (con título, canal, idioma y URL), ejercicio y propósito."""
    t = TOPICS.get(key or "")
    if not t:
        return None
    return {"topic": key, "label": t["label"], "videos": [VIDEOS[v] for v in t["videos"]], "drill": t["drill"], "purpose": t["purpose"]}


def for_metric(metric: str | None) -> dict | None:
    return topic(METRIC_TOPIC.get(metric or ""))


def for_mistake(mistake_id: str | None) -> dict | None:
    return topic(MISTAKE_TOPIC.get(mistake_id or ""))


def catalog() -> dict:
    """Todo el material (para la repetición: elige el tema por las etiquetas de cada momento)."""
    return {"topics": {k: topic(k) for k in TOPICS}, "by_tag": TAG_TOPIC}
