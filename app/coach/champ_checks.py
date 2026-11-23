"""Errores de micro que dependen del campeón: orden de habilidades, primera compra y ritmo del primer objeto.

La referencia es lo que hacen los OTPs de ese campeón y rol de Diamante en adelante (``data/otps``), no una regla
general: subir E a nivel 3 con Hecarim es normal (la mitad de los Diamante lo hacen) y con otro campeón puede ser un
error. Solo se marca lo que esos OTPs casi nunca hacen, con muestra suficiente; si no la hay, no se juzga.

Todo trabaja sobre lo que ya guarda el análisis (``build.skills``, ``build.backs``, ``first_item_min``…), así que se
aplica igual a tus partidas y a las de los OTPs de cualquier elo (``elo_profile``) sin volver a analizar nada.
"""

from __future__ import annotations

from collections import Counter

REF_TIERS = frozenset({"DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"})
MIN_REF_GAMES = 40  # partidas de referencia del campeón para juzgar habilidades o ritmo de objetos
MIN_BACK_GAMES = 60  # primeras vueltas de referencia para juzgar una compra
SKILL_LEVELS = 9  # hasta nivel 9: después el orden ya está decidido (maximizar una habilidad)
SKILL_RARE = 0.08  # tu reparto de puntos lo tienen menos del 8 % de los OTPs…
SKILL_USUAL = 0.6  # …y uno concreto, al menos el 60 %
BACK_RARE = 0.04  # objeto que menos del 4 % de los OTPs compra en su primera vuelta
FIRST_BACK_MIN_S = 90
FIRST_BACK_MAX_S = 10 * 60
ITEM_LATE_MARGIN = 1.0  # minutos por encima del percentil 75 de los OTPs
ITEM_LATE_BEHIND = -250  # con más desventaja de oro a los 10, el retraso no es (solo) culpa del ritmo
SEVERITY_ORDER = {"high": 3, "medium": 2, "low": 1}
# Métricas de la partida que usan las comprobaciones (también van en el resumen ligero de tus partidas).
METRIC_KEYS = (
    "first_item_min", "second_item_min", "gold_diff10", "gold_diff15", "cs10", "kp", "kills", "assists", "minutes",
    "control_wards", "wards_pre14",
)
# Errores de partida entera que separan elos (OTPs de jungla, % de partidas: Platino → Aspirante, noviembre de 2026):
# ninguna pink en 20+ min 44 → 14 (top Diamante 50 → Aspirante 26); KP < 40 % 15 → 7 (en top no separa: 54 → 56);
# 0-1 wards antes del 14 36 → 2; segundo objeto pasado el 21: 15 → 3.
NO_PINK_MIN = 20
NO_PINK_ROLES = ("TOP", "JUNGLE", "MID", "ADC")
LOW_KP = 40
LOW_KP_TEAM_KILLS = 10  # con menos kills en la partida, el porcentaje no dice nada
FEW_WARDS = 1
FEW_WARDS_ROLES = ("TOP", "JUNGLE", "MID", "ADC")
SECOND_LATE_MARGIN = 1.5
SECOND_LATE_BEHIND = -500


def skill_vector(letters: str, level: int) -> str:
    """Reparto de puntos a un nivel («Q2W1E0R0»)."""
    c = Counter(letters[:level])
    return "".join(f"{k}{c[k]}" for k in "QWER")


def _vector_label(vec: str) -> str:
    parts = [f"{vec[i]} {vec[i + 1]}" for i in range(0, 8, 2) if vec[i + 1] != "0"]
    return ", ".join(parts)


def first_back(backs: list[dict] | None) -> dict | None:
    """Primera vuelta a base de verdad (no la compra inicial): la primera con compras entre el 1:30 y el 10:00."""
    for b in backs or []:
        t = b.get("t") or 0
        if t < FIRST_BACK_MIN_S:
            continue
        return b if t <= FIRST_BACK_MAX_S else None
    return None


def back_items(back: dict | None, catalog) -> list[int]:
    """Lo que cuenta de una vuelta: ni consumibles, ni wards, ni abalorios."""
    out = []
    for item in (back or {}).get("items") or []:
        if catalog is not None and catalog and item in catalog:
            if catalog.is_consumable(item) or catalog.is_trinket(item):
                continue
        out.append(int(item))
    return out


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    pos = (len(s) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


class RefBuilder:
    """Acumula las partidas de referencia de un campeón y rol (OTPs de Diamante+) y da su ``ref``."""

    def __init__(self) -> None:
        self.games = 0
        self.levels: list[Counter] = [Counter() for _ in range(SKILL_LEVELS)]
        self.skill_games = 0
        self.backs: Counter = Counter()
        self.back_games = 0
        self.first_items: Counter = Counter()
        self.first_min: list[float] = []
        self.second_min: list[float] = []

    def add(self, rec: dict, catalog) -> None:
        self.games += 1
        letters = rec.get("skills") or ""
        if len(letters) >= SKILL_LEVELS:
            self.skill_games += 1
            for lvl in range(1, SKILL_LEVELS + 1):
                self.levels[lvl - 1][skill_vector(letters, lvl)] += 1
        back = first_back(rec.get("backs"))
        if back is not None:
            self.back_games += 1
            for item in set(back_items(back, catalog)):
                self.backs[item] += 1
        if rec.get("first_item"):
            self.first_items[rec["first_item"]] += 1
        if isinstance(rec.get("first_item_min"), (int, float)):
            self.first_min.append(float(rec["first_item_min"]))
        if isinstance(rec.get("second_item_min"), (int, float)):
            self.second_min.append(float(rec["second_item_min"]))

    def ref(self) -> dict:
        return {
            "games": self.games,
            "skill_games": self.skill_games,
            "levels": [dict(c.most_common(6)) for c in self.levels] if self.skill_games else [],
            "back_games": self.back_games,
            "backs": dict(self.backs.most_common(30)),
            "first_items": [i for i, _ in self.first_items.most_common(3)],
            "first_item_p50": round(_percentile(self.first_min, 0.5), 2) if len(self.first_min) >= MIN_REF_GAMES else None,
            "first_item_p75": round(_percentile(self.first_min, 0.75), 2) if len(self.first_min) >= MIN_REF_GAMES else None,
            "second_item_p50": round(_percentile(self.second_min, 0.5), 2) if len(self.second_min) >= MIN_REF_GAMES else None,
            "second_item_p75": round(_percentile(self.second_min, 0.75), 2) if len(self.second_min) >= MIN_REF_GAMES else None,
        }


def record_from_analysis(analysis: dict, skill_t: list[float] | None = None) -> dict:
    """Lo que necesitan las comprobaciones, sacado de un análisis (tuyo o del dossier de un OTP)."""
    build = analysis.get("build") or {}
    metrics = analysis.get("metrics") or {}
    legend = [o for o in build.get("order") or [] if o.get("kind") == "legendary"]
    return {
        "champ": analysis.get("champ"),
        "role": analysis.get("role"),
        "skills": build.get("skills") or "",
        "skill_t": skill_t if skill_t is not None else build.get("skill_t"),
        "backs": build.get("backs") or [],
        "first_item": legend[0]["item"] if legend else None,
        "first_item_t": legend[0]["t"] if legend else None,
        "first_item_min": metrics.get("first_item_min"),
        "second_item_t": legend[1]["t"] if len(legend) > 1 else None,
        "items_reliable": analysis.get("items_reliable", True),
        **{k: metrics.get(k) for k in METRIC_KEYS},
    }


def _name(catalog, item: int) -> str:
    return catalog.name(item) if catalog is not None and catalog else f"#{item}"


def _pct(n: int, total: int) -> str:
    return f"{round(100 * n / total)} %" if total else "—"


def _fmt_min(m: float) -> str:
    return f"{m:.1f}".replace(".", ",")


def check(rec: dict, ref: dict | None, catalog=None, champ_name: str = "tu campeón") -> list[dict]:
    """Momentos de error (mismo formato que los del análisis) de una partida frente a la referencia de su campeón."""
    out: list[dict] = _game_checks(rec)
    if not ref:
        return out
    out += _skills(rec, ref, champ_name)
    out += _first_back(rec, ref, catalog, champ_name)
    out += _item_tempo(rec, ref, catalog, champ_name)
    out += _second_item(rec, ref, catalog, champ_name)
    return out


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _game_checks(rec: dict) -> list[dict]:
    """Errores de la partida entera (visión, participación): no dependen del campeón, sí del rol."""
    role = rec.get("role")
    minutes = _num(rec.get("minutes")) or 0
    out = []
    pinks = _num(rec.get("control_wards"))
    if role in NO_PINK_ROLES and minutes >= NO_PINK_MIN and pinks == 0:
        out.append(_moment(
            NO_PINK_MIN * 60, "low", "Ninguna pink en toda la partida",
            f"Jugaste {round(minutes)} minutos sin comprar un guardián de control. Cuesta 75 de oro y es la visión que más "
            "decide los objetivos: en Aspirante solo 1 de cada 7 junglas acaba una partida sin ninguna (en Platino, casi la mitad). "
            "Compra una en cada vuelta a base y ponla en el río o junto al siguiente objetivo.",
            ["vision", "no_pinks"], kind="vision",
        ))
    wards = _num(rec.get("wards_pre14"))
    if role in FEW_WARDS_ROLES and wards is not None and wards <= FEW_WARDS and minutes >= 15:
        out.append(_moment(
            14 * 60, "low", f"{int(wards)} {'ward' if wards == 1 else 'wards'} antes del minuto 14",
            "Casi sin visión en la fase de líneas: el abalorio se recarga solo, úsalo cada vez que esté listo (río, entrada de tu "
            "jungla, el campamento que el rival suele hacer). En Aspirante solo el 2 % de los junglas pone tan pocos.",
            ["vision", "few_wards"], kind="vision",
        ))
    kp, kills, assists = _num(rec.get("kp")), _num(rec.get("kills")) or 0, _num(rec.get("assists")) or 0
    if role == "JUNGLE" and kp is not None and kp < LOW_KP and kp > 0:
        team_kills = (kills + assists) * 100 / kp
        if team_kills >= LOW_KP_TEAM_KILLS:
            out.append(_moment(
                minutes * 60, "medium" if kp < 30 else "low", f"Participaste en el {round(kp)} % de las kills",
                f"Tu equipo hizo unas {round(team_kills)} kills y estuviste en {int(kills + assists)}. Un jungla de Aspirante baja del "
                "40 % en 1 de cada 14 partidas (en Platino, 1 de cada 7): sigue las peleas de tus líneas, llega a los ganks del "
                "rival (contragank) y agrupa con tu equipo antes de cada objetivo.",
                ["jungle", "low_kp"], kind="jungle",
            ))
    return out


def _moment(t: float | None, severity: str, title: str, detail: str, tags: list[str], kind: str = "build") -> dict:
    return {"t": round(float(t or 0), 1), "kind": kind, "severity": severity, "title": title, "detail": detail, "x": None, "y": None, "tags": tags}


def _skills(rec: dict, ref: dict, champ_name: str) -> list[dict]:
    letters = rec.get("skills") or ""
    levels = ref.get("levels") or []
    total = ref.get("skill_games") or 0
    if total < MIN_REF_GAMES or len(letters) < 2:
        return []
    for lvl in range(1, min(SKILL_LEVELS, len(letters), len(levels)) + 1):
        dist = levels[lvl - 1]
        mine = skill_vector(letters, lvl)
        n = sum(dist.values()) or total
        usual, usual_n = max(dist.items(), key=lambda kv: kv[1])
        share = dist.get(mine, 0) / n
        if mine == usual or share >= SKILL_RARE or usual_n / n < SKILL_USUAL:
            continue
        times = rec.get("skill_t") or []
        t = times[lvl - 1] if len(times) >= lvl else None
        picked = letters[lvl - 1]
        severity = "medium" if lvl <= 3 and share < 0.03 and usual_n / n >= 0.8 else "low"
        return [
            _moment(
                t, severity, f"Nivel {lvl}: subiste la {picked} (orden poco habitual)",
                f"A nivel {lvl} llevabas {_vector_label(mine)}; el {_pct(usual_n, n)} de los OTPs de {champ_name} "
                f"(Diamante o más) llevan {_vector_label(usual)} y solo el {_pct(dist.get(mine, 0), n)} lo hacen como tú. "
                "Los primeros niveles deciden la velocidad del despeje y los duelos: copia su orden salvo que tengas un motivo concreto.",
                ["build", "skill_order"],
            )
        ] if t is not None else []
    return []


def _first_back(rec: dict, ref: dict, catalog, champ_name: str) -> list[dict]:
    total = ref.get("back_games") or 0
    back = first_back(rec.get("backs"))
    if total < MIN_BACK_GAMES or back is None or catalog is None or not catalog:
        return []
    usual_paths: set[int] = set()
    for item in ref.get("first_items") or []:
        usual_paths.add(int(item))
        usual_paths.update(catalog.tree(int(item)))
    backs = {int(k): v for k, v in (ref.get("backs") or {}).items()}
    rare = []
    for item in dict.fromkeys(back_items(back, catalog)):
        if item not in catalog or catalog.is_boots(item) or catalog.resist(item) or catalog.anti_heal(item):
            continue  # botas, defensas y Heridas graves dependen de la partida
        if item in usual_paths or backs.get(item, 0) / total >= BACK_RARE:
            continue
        rare.append(item)
    if not rare:
        return []
    usual = [(i, n) for i, n in sorted(backs.items(), key=lambda kv: -kv[1]) if catalog and i in catalog][:3]
    usual_txt = ", ".join(f"{_name(catalog, i)} ({_pct(n, total)})" for i, n in usual)
    names = ", ".join(_name(catalog, i) for i in rare)
    return [
        _moment(
            back.get("t"), "low", f"Primera vuelta: {names}",
            f"Los OTPs de {champ_name} (Diamante o más) casi nunca lo compran en su primera vuelta "
            f"({', '.join(_pct(backs.get(i, 0), total) for i in rare)}) y no forma parte de sus primeros objetos. "
            f"Lo habitual: {usual_txt}. Gasta la primera vuelta en piezas de tu primer objeto o en botas.",
            ["build", "first_back"],
        )
    ]


def _item_tempo(rec: dict, ref: dict, catalog, champ_name: str) -> list[dict]:
    p50, p75 = ref.get("first_item_p50"), ref.get("first_item_p75")
    mine = rec.get("first_item_min")
    if p75 is None or not isinstance(mine, (int, float)) or not rec.get("items_reliable", True):
        return []
    gd = rec.get("gold_diff10")
    if gd is not None and gd < ITEM_LATE_BEHIND:
        return []  # ibas por detrás: el retraso viene de ahí (ya se ve en la fase de líneas)
    late = mine - p75
    if late < ITEM_LATE_MARGIN:
        return []
    item = rec.get("first_item")
    what = _name(catalog, item) if item else "Tu primer objeto"
    return [
        _moment(
            rec.get("first_item_t") or mine * 60, "medium" if late >= 2.5 else "low",
            f"Primer objeto tarde (minuto {_fmt_min(mine)})",
            f"{what} en el minuto {_fmt_min(mine)}: los OTPs de {champ_name} (Diamante o más) lo terminan hacia el "
            f"{_fmt_min(p50)} y el 75 % antes del {_fmt_min(p75)}. Ibas igualado o por delante, así que el tiempo se fue en "
            "farmear más lento, volver tarde a base o gastar en otras cosas: limpia campamentos entre jugada y jugada y vuelve "
            "en cuanto te llegue para una pieza grande.",
            ["build", "item_tempo"],
        )
    ]


def _second_item(rec: dict, ref: dict, catalog, champ_name: str) -> list[dict]:
    p50, p75 = ref.get("second_item_p50"), ref.get("second_item_p75")
    mine = _num(rec.get("second_item_min"))
    if p75 is None or mine is None or not rec.get("items_reliable", True):
        return []
    gd = _num(rec.get("gold_diff15"))
    if gd is not None and gd < SECOND_LATE_BEHIND:
        return []
    late = mine - p75
    if late < SECOND_LATE_MARGIN:
        return []
    return [
        _moment(
            rec.get("second_item_t") or mine * 60, "medium" if late >= 3 else "low",
            f"Segundo objeto tarde (minuto {_fmt_min(mine)})",
            f"Los OTPs de {champ_name} (Diamante o más) tienen el segundo objeto hacia el {_fmt_min(p50)} y el 75 % antes del "
            f"{_fmt_min(p75)}; no ibas por detrás a los 15. Entre el primer y el segundo objeto es donde más se nota no farmear "
            "entre objetivos: no te quedes parado esperando, limpia campamentos u oleadas laterales.",
            ["build", "item_tempo"],
        )
    ]
