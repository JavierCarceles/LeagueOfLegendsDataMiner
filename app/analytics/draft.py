"""Modelo de evaluación de drafts.

Modelo logístico aditivo (en log-odds) sobre la evidencia combinada
(OP.GG + tu base de datos + estadísticas de campeón):

    L = Σ_líneas logit(WR estimado del enfrentamiento)
      + Σ_parejas_aliadas sinergia − Σ_parejas_enemigas sinergia
    P(victoria aliada) = σ(α · L)

* **Líneas**: WR del aliado contra su rival directo, encogido hacia el valor
  esperado por log5 (fuerza general de ambos campeones en ese rol). Si una
  línea solo tiene un campeón elegido se usa su WR general (draft a medias).
* **Sinergias**: WR de la pareja frente al esperado si fueran independientes,
  encogido hacia 0 (las sinergias reales son pequeñas).
* **α** se calibra con *backtesting* (leave-one-out) sobre partidas
  guardadas, para que la probabilidad no sea más optimista de lo que
  justifican los datos.

La composición (perfil de daño, frontline, CC, curva por duración) se
muestra como análisis cualitativo; no se suma a P para no contar dos veces
lo que ya recoge el winrate.
"""

from __future__ import annotations

import json
import math
import time
from collections import defaultdict

from app.analytics.evidence import LIGHT_PRIOR, MIN_ROLE_GAMES, Evidence
from app.analytics.filters import Filters
from app.analytics.matchups import _source, pair_view
from app.analytics.stats import clamp, log5, logit, pct, round_or_none, safe_div, sample_confidence, shrink, sigmoid
from app.riot.routing import ROLES

Z90 = 1.6448536269514722
ROLE_PAIRS = [(ROLES[i], ROLES[j]) for i in range(len(ROLES)) for j in range(i + 1, len(ROLES))]
# Parejas en las que merece la pena pedir sinergias a OP.GG (las de más peso).
KEY_PAIRS = {("ADC", "SUPPORT"), ("JUNGLE", "MID"), ("TOP", "JUNGLE"), ("JUNGLE", "SUPPORT"), ("MID", "SUPPORT")}
DAMAGE_SPLIT = {"AD": (0.88, 0.08), "AP": (0.08, 0.88), "HYBRID": (0.5, 0.45), "MIXED": (0.5, 0.45), "TRUE": (0.45, 0.2)}
# Datos del enfrentamiento (``pair_view``) que se muestran en cada línea del draft.
LANE_VIEW_KEYS = ("games", "wins", "wr", "wr_adj", "expected", "delta", "ci", "verdict", "counter", "confidence", "sources", "lane")


def grade(p: float) -> dict:
    if p >= 0.60:
        return {"letter": "S", "label": "Ventaja muy clara"}
    if p >= 0.56:
        return {"letter": "A", "label": "Ventaja clara"}
    if p >= 0.525:
        return {"letter": "B", "label": "Ligera ventaja"}
    if p > 0.475:
        return {"letter": "C", "label": "Igualado"}
    if p > 0.44:
        return {"letter": "D", "label": "Ligera desventaja"}
    return {"letter": "F", "label": "Desventaja clara"}


def get_alpha(svc) -> float:
    try:
        return clamp(float(svc.db.meta_get("draft_alpha") or 1.0), 0.2, 2.0)
    except ValueError:
        return 1.0


def _clean(team: dict | None) -> dict[str, int]:
    return {role: int(champ) for role, champ in (team or {}).items() if role in ROLES and champ}


def _lane_jobs(svc, ev: Evidence, role: str, a: int | None, b: int | None) -> list:
    """Descargas de una línea: counters e info de cada campeón y, si están los dos, las guías del enfrentamiento."""
    jobs = []
    for champ in (a, b):
        if champ:
            jobs.append(lambda r=role, c=champ: ev.counters(r, c))
            jobs.append(lambda r=role, c=champ: svc.opgg.champion_info(c, r))
    if a and b:
        jobs.append(lambda r=role, x=a, y=b: svc.opgg.matchup_guide(x, y, r))
        jobs.append(lambda r=role, x=a, y=b: svc.opgg.matchup_guide(y, x, r))
    return jobs


def _team_synergy_jobs(svc, team: dict[str, int]) -> list:
    """Descargas de las sinergias de las parejas con más peso del equipo."""
    return [
        lambda a=r1, c=team[r1], b=r2: svc.opgg.synergies(c, a, b)
        for r1, r2 in ROLE_PAIRS
        if (r1, r2) in KEY_PAIRS and team.get(r1) and team.get(r2)
    ]


def _prefetch(svc, ev: Evidence, ally: dict[str, int], enemy: dict[str, int]) -> None:
    """Descarga en paralelo lo que falte de OP.GG para este draft."""
    if not ev.scope:
        return
    jobs = []
    for role in ROLES:
        jobs.extend(_lane_jobs(svc, ev, role, ally.get(role), enemy.get(role)))
    for team in (ally, enemy):
        jobs.extend(_team_synergy_jobs(svc, team))
    jobs.extend(lambda r=role: ev.tier_list(r) for role in ROLES)
    svc.opgg.prefetch(jobs, timeout=25)


def _synergy_terms(ev: Evidence, team: dict[str, int]) -> list[dict]:
    terms = []
    for r1, r2 in ROLE_PAIRS:
        c1, c2 = team.get(r1), team.get(r2)
        if not c1 or not c2:
            continue
        s = ev.synergy(r1, c1, r2, c2)
        terms.append(
            {
                "roles": [r1, r2],
                "champs": [c1, c2],
                "games": int(s["games"]),
                "wr": pct(safe_div(s["wins"], s["games"])),
                "wr_adj": pct(s["adjusted"]),
                "expected": pct(s["expected"]),
                "sources": {"opgg": _source(*s["ext"]), "own": _source(*s["own"])},
                "logit": s["logit"],
                "var": s["var"],
            }
        )
    return terms


def _dd_info(dd, champ: int) -> dict:
    """Valores de Data Dragon del campeón (ataque, magia, defensa…)."""
    return (dd.champions.get(champ) or {}).get("info") or {}


def _tankiness(row: dict) -> float:
    """Daño recibido + mitigado."""
    return (row.get("taken") or 0) + (row.get("mitigated") or 0)


def _damage_split(dd, champ: int, damage_type: str | None) -> tuple[float, float, str]:
    """(parte física, parte mágica, fuente): el tipo de daño de OP.GG o, si no, los valores de Data Dragon."""
    if damage_type and damage_type.upper() in DAMAGE_SPLIT:
        share_phys, share_magic = DAMAGE_SPLIT[damage_type.upper()]
        return share_phys, share_magic, "opgg"
    stats = _dd_info(dd, champ)
    attack, magic_score = stats.get("attack", 5), stats.get("magic", 5)
    share_magic = magic_score / max(1, attack + magic_score)
    return 1 - share_magic, share_magic, "ddragon"


def _add_reference(acc: dict[str, float], ref: dict) -> None:
    """Suma el aguante y el CC por minuto medios de la línea (la referencia de un equipo normal)."""
    ref_minutes = ref.get("minutes") or 0
    if ref_minutes:
        acc["ref_tank"] += _tankiness(ref) / ref_minutes
        acc["ref_cc"] += (ref.get("cc") or 0) / ref_minutes


def _add_measured(acc: dict[str, float], row: dict, minutes: float) -> None:
    """Suma lo medido en tu BD: daño por tipo, aguante y CC por minuto."""
    acc["phys"] += (row.get("dmg_phys") or 0) / minutes
    acc["magic"] += (row.get("dmg_magic") or 0) / minutes
    acc["true"] += (row.get("dmg_true") or 0) / minutes
    acc["tank"] += _tankiness(row) / minutes
    acc["cc"] += (row.get("cc") or 0) / minutes


def _add_estimated(acc: dict[str, float], dd, champ: int, ref: dict, share_phys: float, share_magic: float) -> None:
    """Suma lo estimado con la media de la línea: su daño repartido según el tipo del campeón y su aguante según
    la defensa de Data Dragon."""
    ref_minutes = ref.get("minutes") or 0
    role_dpm = (ref.get("dmg") or 0) / ref_minutes if ref_minutes else 650.0
    acc["phys"] += role_dpm * share_phys
    acc["magic"] += role_dpm * share_magic
    acc["true"] += role_dpm * max(0.0, 1 - share_phys - share_magic)
    if ref_minutes:
        defense = _dd_info(dd, champ).get("defense", 5)
        acc["tank"] += _tankiness(ref) / ref_minutes * (0.6 + defense / 12.5)
        acc["cc"] += (ref.get("cc") or 0) / ref_minutes


def _add_champion(acc: dict[str, float], dd, champ: int, row: dict, ref: dict, damage_type: str | None) -> str:
    """Suma la aportación del campeón y devuelve de dónde sale: «data» (tu BD), «opgg» o «ddragon»."""
    minutes = row.get("minutes") or 0
    if (row.get("dgames") or 0) >= 20 and minutes:
        _add_measured(acc, row, minutes)
        return "data"
    share_phys, share_magic, source = _damage_split(dd, champ, damage_type)
    _add_estimated(acc, dd, champ, ref, share_phys, share_magic)
    return source


def _composition_warnings(
    team_size: int, total: float, profile: dict, tank_ratio: float | None, cc_ratio: float | None
) -> list[str]:
    """Avisos de composición (solo con al menos 4 campeones elegidos)."""
    if team_size < 4:
        return []
    warnings = []
    if total:
        if (profile["magic"] or 0) < 20:
            warnings.append("Daño casi todo físico: al rival le basta con comprar armadura.")
        elif (profile["phys"] or 0) < 25:
            warnings.append("Daño casi todo mágico: al rival le basta con comprar resistencia mágica.")
    if tank_ratio is not None and tank_ratio < 0.82:
        warnings.append("Poca primera línea: cuesta iniciar y aguantar peleas.")
    if cc_ratio is not None and cc_ratio < 0.75:
        warnings.append("Poco control de masas: difícil asegurar objetivos y castigar errores.")
    return warnings


def _composition_source(sources: list[str]) -> str:
    """«data» si todo sale de tu BD; si no, «opgg» si alguno usa OP.GG, o «ddragon»."""
    if sources and all(s == "data" for s in sources):
        return "data"
    return "opgg" if "opgg" in sources else "ddragon"


def _composition(svc, ev: Evidence, team: dict[str, int]) -> dict | None:
    if not team:
        return None
    dd = svc.dd
    role_totals = svc.agg.role_totals(ev.f) if ev.use_own else {}
    acc = dict.fromkeys(("phys", "magic", "true", "tank", "cc", "ref_tank", "ref_cc"), 0.0)
    sources = []
    types = {}
    for role, champ in team.items():
        row = ev.own_role(role).get(champ) or {}
        ref = role_totals.get(role) or {}
        _add_reference(acc, ref)
        info = svc.opgg.champion_info(champ, role, allow_fetch=False) if ev.scope else None
        damage_type = (info or {}).get("damage_type")
        types[str(champ)] = damage_type
        sources.append(_add_champion(acc, dd, champ, row, ref, damage_type))
    total = acc["phys"] + acc["magic"] + acc["true"]
    profile = {key: pct(safe_div(acc[key], total)) for key in ("phys", "magic", "true")}
    tank_ratio = safe_div(acc["tank"], acc["ref_tank"]) if acc["ref_tank"] else None
    cc_ratio = safe_div(acc["cc"], acc["ref_cc"]) if acc["ref_cc"] else None
    return {
        "damage_profile": profile,
        "damage_types": types,
        "frontline": round_or_none(tank_ratio, 2),
        "cc": round_or_none(cc_ratio, 2),
        "source": _composition_source(sources),
        "warnings": _composition_warnings(len(team), total, profile, tank_ratio, cc_ratio),
    }


def _guide_curve(svc, ev: Evidence, role: str, champ: int, opp: int) -> list[float] | None:
    """WR por tramo de duración según la guía del enfrentamiento de OP.GG (si tiene todos los tramos)."""
    guide = svc.opgg.matchup_guide(champ, opp, role, allow_fetch=False) or {}
    by_minute = {g["minute"]: g["wr"] for g in guide.get("game_lengths") or [] if g.get("wr")}
    if not by_minute:
        return None
    late = [by_minute[m] for m in (35, 40) if m in by_minute]
    values = [by_minute.get(0), by_minute.get(25), by_minute.get(30), sum(late) / len(late) if late else None]
    if any(v is None for v in values):
        return None
    # Datos del parche en curso (muestras pequeñas por tramo): se
    # encogen a medio camino hacia el WR general del campeón.
    p_all = ev.base(role, champ)[0]
    return [p_all + 0.5 * (v - p_all) for v in values]


def _champ_curve(svc, ev: Evidence, role: str, champ: int, opp: int | None) -> list[float] | None:
    """WR por tramo de duración: tu base de datos si hay, si no OP.GG."""
    row = ev.own_role(role).get(champ) or {}
    if sum(row.get(f"g{b}") or 0 for b in range(4)) >= 80:
        p_all = ev.base(role, champ)[0]
        return [shrink(row.get(f"w{b}") or 0, row.get(f"g{b}") or 0, p_all, 60.0) for b in range(4)]
    if ev.scope and opp:
        return _guide_curve(svc, ev, role, champ, opp)
    return None


def _duration_curve(svc, ev: Evidence, ally: dict[str, int], enemy: dict[str, int], alpha: float) -> list[dict] | None:
    if not ally or not enemy:
        return None
    labels = ["< 25 min", "25-30 min", "30-35 min", "≥ 35 min"]
    totals = [0.0] * 4
    used = 0
    for sign, team, other in ((1, ally, enemy), (-1, enemy, ally)):
        for role, champ in team.items():
            curve = _champ_curve(svc, ev, role, champ, other.get(role))
            if not curve:
                continue
            used += 1
            for b in range(4):
                totals[b] += sign * logit(curve[b])
    if used < 4:
        return None
    return [{"bucket": b, "label": labels[b], "p": pct(sigmoid(alpha * totals[b]))} for b in range(4)]


def _guide_tips(guide: dict | None) -> dict | None:
    """Ventajas y consejo de la guía del enfrentamiento de OP.GG."""
    if not guide:
        return None
    return {
        "lane_advantage": guide.get("lane_advantage"),
        "solo_kill_advantage": guide.get("solo_kill_advantage"),
        "play_style": guide.get("play_style"),
        "tip": guide.get("tip"),
    }


def _matchup_lane(svc, ev: Evidence, role: str, a: int, b: int) -> dict:
    """Línea con los dos campeones elegidos: el enfrentamiento directo."""
    p = ev.pair(role, a, b)
    view = pair_view(p)
    guide = svc.opgg.matchup_guide(a, b, role, allow_fetch=False) if ev.scope else None
    return {
        "kind": "matchup",
        **{k: view[k] for k in LANE_VIEW_KEYS},
        "ally_wr": pct(p["pa"]),
        "enemy_wr": pct(p["pb"]),
        "opgg": _guide_tips(guide),
        "guide": svc.guides.exists(role, a, b),
        "logit": p["logit"],
        "var": p["var"],
    }


def _solo_lane(ev: Evidence, role: str, a: int | None, b: int | None) -> dict:
    """Línea con un solo campeón elegido: su WR general, a favor si es aliado y en contra si es rival."""
    p, var, games = ev.base(role, a or b)
    return {
        "kind": "solo",
        "games": int(games),
        "wr_adj": pct(p if a else 1 - p),
        "confidence": sample_confidence(games),
        "logit": logit(p) if a else -logit(p),
        "var": var,
    }


def _lane(svc, ev: Evidence, role: str, a: int | None, b: int | None) -> dict:
    """Término de una línea en el modelo (``logit`` y su varianza ``var``) con lo que se muestra de ella."""
    lane: dict = {"role": role, "ally": a, "enemy": b}
    if a and b:
        lane.update(_matchup_lane(svc, ev, role, a, b))
    elif a or b:
        lane.update(_solo_lane(ev, role, a, b))
    else:
        lane.update({"kind": "empty", "logit": 0.0, "var": 0.0})
    return lane


def _impact_pp(p_win: float, alpha: float, total: float, term: float) -> float:
    """Puntos de probabilidad que aporta un término (lo que cambiaría P sin él)."""
    return round((p_win - sigmoid(alpha * (total - term))) * 100, 2)


def _team_strength(ev: Evidence, alpha: float, team: dict[str, int], syn: float) -> dict | None:
    """Fuerza del equipo frente a uno medio: WR generales de sus campeones más sus sinergias."""
    if not team:
        return None
    score = sum(logit(ev.base(role, champ)[0]) for role, champ in team.items()) + syn
    strength = sigmoid(alpha * score)
    return {
        "wr_vs_avg": pct(strength),
        "grade": grade(strength),
        "picks": len(team),
        "synergy_pp": round((strength - sigmoid(alpha * (score - syn))) * 100, 2),
    }


def _confidence(se: float, lanes: list[dict]) -> dict:
    """Confianza del pronóstico: su error típico y cuántas líneas tienen muestra (≥ 100 partidas)."""
    with_data = sum(1 for lane in lanes if lane["kind"] == "matchup" and lane["games"] >= 100)
    if se < 0.09 and with_data >= 4:
        return {"level": 3, "label": "Alta"}
    if se < 0.16 and with_data >= 2:
        return {"level": 2, "label": "Media"}
    return {"level": 1, "label": "Baja"}


def _public(items: list[dict]) -> list[dict]:
    """Términos para la respuesta: sin la varianza y con el logit redondeado."""
    return [{k: (round(v, 4) if k == "logit" else v) for k, v in item.items() if k != "var"} for item in items]


def analyze(svc, f: Filters, ally: dict, enemy: dict, sources: str = "all") -> dict:
    ev = Evidence(svc, f, sources)
    alpha = get_alpha(svc)
    ally, enemy = _clean(ally), _clean(enemy)
    _prefetch(svc, ev, ally, enemy)
    ev.fetch = False  # a partir de aquí, solo lo que ya esté en caché

    lanes = [_lane(svc, ev, role, ally.get(role), enemy.get(role)) for role in ROLES]
    total = 0.0
    variance = 0.0
    for lane in lanes:
        total += lane["logit"]
        variance += lane["var"]

    ally_syn = _synergy_terms(ev, ally)
    enemy_syn = _synergy_terms(ev, enemy)
    syn_ally = sum(t["logit"] for t in ally_syn)
    syn_enemy = sum(t["logit"] for t in enemy_syn)
    total += syn_ally - syn_enemy
    # El intervalo refleja la incertidumbre de los enfrentamientos de línea;
    # las sinergias se suman como ajuste (ya muy encogidas hacia 0).

    p_win = sigmoid(alpha * total)
    se = math.sqrt(variance)
    low, high = sigmoid(alpha * (total - Z90 * se)), sigmoid(alpha * (total + Z90 * se))
    for lane in lanes:
        lane["impact_pp"] = _impact_pp(p_win, alpha, total, lane["logit"])
    for terms, sign in ((ally_syn, 1), (enemy_syn, -1)):
        for t in terms:
            t["impact_pp"] = _impact_pp(p_win, alpha, total, sign * t["logit"])

    return {
        "filters": f.public(),
        "evidence": ev.public(),
        "alpha": alpha,
        "priors": ev.priors,
        "win_probability": pct(p_win),
        "interval": [pct(low), pct(high)],
        "grade": grade(p_win),
        "confidence": _confidence(se, lanes),
        "lanes_filled": sum(1 for lane in lanes if lane["kind"] == "matchup"),
        "picks": {"ally": len(ally), "enemy": len(enemy)},
        "lanes": _public(lanes),
        "synergies": {"ally": _public(ally_syn), "enemy": _public(enemy_syn)},
        "teams": {"ally": _team_strength(ev, alpha, ally, syn_ally), "enemy": _team_strength(ev, alpha, enemy, syn_enemy)},
        "composition": {"ally": _composition(svc, ev, ally), "enemy": _composition(svc, ev, enemy)},
        "duration_curve": _duration_curve(svc, ev, ally, enemy, alpha),
    }


def _slot_jobs(svc, ev: Evidence, mine: dict[str, int], theirs: dict[str, int], role: str) -> dict:
    """Descargas de OP.GG que necesita un hueco (clave -> trabajo, sin duplicados)."""
    jobs = {("tier", role): lambda r=role: ev.tier_list(r)}
    opponent = theirs.get(role)
    if opponent:
        jobs[("counters", role, opponent)] = lambda r=role, c=opponent: ev.counters(r, c)
    for r, c in mine.items():
        if r != role:
            jobs[("synergy", c, r, role)] = lambda r=r, c=c, slot=role: svc.opgg.synergies(c, r, slot)
    return jobs


def _pick_synergy(ev: Evidence, role: str, champ: int, mine: dict[str, int]) -> float:
    """Suma (en log-odds) de las sinergias del candidato con los ya elegidos de su equipo."""
    synergy = 0.0
    for partner_role, partner in mine.items():
        if partner_role == role:
            continue
        s = ev.synergy(role, champ, partner_role, partner)
        synergy += s["logit"]
    return synergy


def _candidate(
    ev: Evidence, alpha: float, mine: dict[str, int], role: str, champ: int, opponent: int | None, min_lane_games: int
) -> dict | None:
    """Puntuación de un candidato para el hueco (None si no llega al mínimo de partidas contra el rival de línea)."""
    p_c, _, games_c = ev.base(role, champ)
    if opponent:
        p = ev.pair(role, champ, opponent, fetch=False)
        lane_p, lane_games = p["adjusted"], p["games"]
        if lane_games < min_lane_games:
            return None
    else:
        lane_p, lane_games = p_c, 0
    lane_term = logit(lane_p)
    score = lane_term + _pick_synergy(ev, role, champ, mine)
    return {
        "champ": champ,
        "staple": ev.staple(role, champ),
        "score_pp": round((sigmoid(alpha * score) - 0.5) * 100, 2),
        "lane_wr": pct(lane_p),
        "lane_games": int(lane_games),
        "synergy_pp": round((sigmoid(alpha * score) - sigmoid(alpha * lane_term)) * 100, 2),
        "base_wr": pct(p_c),
        "games": int(games_c),
        "confidence": sample_confidence(lane_games if opponent else games_c),
    }


def _suggest_slot(
    ev: Evidence,
    alpha: float,
    mine: dict[str, int],
    theirs: dict[str, int],
    taken: set[int],
    role: str,
    side: str,
    limit: int,
    min_games: int,
    min_lane_games: int,
) -> dict:
    opponent = theirs.get(role)
    results = []
    # Solo campeones que se juegan en esa línea con muestra real suficiente (un Zilean top suelto no se recomienda).
    for champ in ev.champions_in_role(role, min_games):
        if champ in taken:
            continue
        result = _candidate(ev, alpha, mine, role, champ, opponent, min_lane_games)
        if result is not None:
            results.append(result)
    results.sort(key=lambda r: r["score_pp"], reverse=True)
    # Recomendados: solo los habituales de la línea (los secundarios, con el WR inflado, solo en el desplegable).
    staples = [r for r in results if r["staple"]]
    return {
        "role": role,
        "side": side,
        "opponent": opponent,
        "best": staples[:limit],
        "worst": list(reversed(staples[-min(5, len(staples)) :])) if len(staples) > limit else [],
        "evaluated": len(results),
        "scores": {r["champ"]: r["score_pp"] for r in results},
    }


def suggest(
    svc,
    f: Filters,
    ally: dict,
    enemy: dict,
    role: str,
    side: str = "ally",
    limit: int = 12,
    min_games: int = MIN_ROLE_GAMES,
    sources: str = "all",
    min_lane_games: int = 100,
) -> dict:
    """Mejores picks para el hueco ``role`` del lado ``side`` según el draft actual.

    Si el rival de línea ya está elegido solo se proponen campeones con al
    menos ``min_lane_games`` partidas de ese enfrentamiento concreto.
    """
    ev = Evidence(svc, f, sources)
    alpha = get_alpha(svc)
    ally, enemy = _clean(ally), _clean(enemy)
    mine, theirs = (ally, enemy) if side == "ally" else (enemy, ally)
    if ev.scope:
        svc.opgg.prefetch(list(_slot_jobs(svc, ev, mine, theirs, role).values()), timeout=20)
    ev.fetch = False
    taken = set(ally.values()) | set(enemy.values())
    result = _suggest_slot(ev, alpha, mine, theirs, taken, role, side, limit, min_games, min_lane_games)
    return {**result, "filters": f.public(), "evidence": ev.public()}


def suggest_many(
    svc,
    f: Filters,
    ally: dict,
    enemy: dict,
    slots: list[tuple[str, str]],
    limit: int = 8,
    min_games: int = MIN_ROLE_GAMES,
    sources: str = "all",
    min_lane_games: int = 100,
) -> dict:
    """``suggest`` para varios huecos a la vez (comparten evidencia y descargas)."""
    ev = Evidence(svc, f, sources)
    alpha = get_alpha(svc)
    ally, enemy = _clean(ally), _clean(enemy)
    taken = set(ally.values()) | set(enemy.values())
    teams = {"ally": (ally, enemy), "enemy": (enemy, ally)}
    slots = list(dict.fromkeys((side, role) for side, role in slots if side in teams and role in ROLES))
    if ev.scope:
        jobs: dict = {}
        for side, role in slots:
            jobs.update(_slot_jobs(svc, ev, *teams[side], role))
        svc.opgg.prefetch(list(jobs.values()), timeout=25)
    ev.fetch = False
    out = {}
    for side, role in slots:
        mine, theirs = teams[side]
        out[f"{side}:{role}"] = _suggest_slot(ev, alpha, mine, theirs, taken, role, side, limit, min_games, min_lane_games)
    return {"filters": f.public(), "evidence": ev.public(), "slots": out}


# ------------------------------------------------------------------ backtest
def _load_teams(svc, ids: list) -> dict[str, dict[int, dict[str, int]]]:
    """Campeón de cada rol de cada equipo (100/200) en esas partidas."""
    teams: dict[str, dict[int, dict[str, int]]] = defaultdict(lambda: {100: {}, 200: {}})
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        marks = ",".join("?" * len(chunk))
        sql = f"SELECT match_id, team, role, champ FROM participants WHERE match_id IN ({marks}) AND role IS NOT NULL"
        for r in svc.db.query(sql, chunk):
            teams[r["match_id"]][r["team"]][r["role"]] = r["champ"]
    return teams


def _load_champs(svc) -> dict[tuple, tuple[int, int]]:
    """Partidas/victorias de cada (rol, campeón) en tus agregados."""
    rows = svc.db.query("SELECT role, champ, SUM(games), SUM(wins) FROM agg_champ GROUP BY role, champ")
    return {(r[0], r[1]): (r[2], r[3]) for r in rows}


def _lane_champs(teams: dict) -> dict[str, set[int]]:
    """Campeones de cada rol que tienen enfrentamiento de línea en esas partidas."""
    needed: dict[str, set[int]] = defaultdict(set)
    for team_map in teams.values():
        for role in ROLES:
            if team_map[100].get(role) and team_map[200].get(role):
                needed[role].add(team_map[100][role])
                needed[role].add(team_map[200][role])
    return needed


def _load_matchups(svc, teams: dict) -> dict[tuple, tuple[int, int]]:
    """Partidas/victorias de tus agregados en los enfrentamientos de línea de esas partidas."""
    matchup: dict[tuple, tuple[int, int]] = {}
    for role, champs in _lane_champs(teams).items():
        champ_list = sorted(champs)
        for i in range(0, len(champ_list), 400):
            chunk = champ_list[i : i + 400]
            marks = ",".join("?" * len(chunk))
            sql = (
                "SELECT champ, opp, SUM(games), SUM(wins) FROM agg_matchup "
                f"WHERE role = ? AND champ IN ({marks}) GROUP BY champ, opp"
            )
            for r in svc.db.query(sql, [role, *chunk]):
                matchup[(role, r[0], r[1])] = (r[2], r[3])
    return matchup


def _load_synergies(svc) -> dict[tuple, tuple[int, int]]:
    """Partidas/victorias de cada pareja (rol1, campeón1, rol2, campeón2) en tus agregados."""
    rows = svc.db.query("SELECT role1, champ1, role2, champ2, SUM(games), SUM(wins) FROM agg_synergy GROUP BY 1, 2, 3, 4")
    return {(r[0], r[1], r[2], r[3]): (r[4], r[5]) for r in rows}


class _LeaveOneOut:
    """El modelo del draft con tus agregados sin la partida que se predice (y las webs solo desde la caché)."""

    def __init__(self, priors: dict, base: dict, matchup: dict, synergy: dict, ext: Evidence | None) -> None:
        self.kc, self.km, self.ks = priors["champ"], priors["matchup"], priors["synergy"]
        self.base = base
        self.matchup = matchup
        self.synergy = synergy
        self.ext = ext

    def p_base(self, role: str, champ: int, win: int, k: float | None = None) -> float:
        """WR general del campeón en el rol sin esta partida (``win``: 1 si la ganó)."""
        games, wins = self.base.get((role, champ), (0, 0))
        games, wins = max(0, games - 1), max(0, wins - win)
        if self.ext:
            n_e, w_e = self.ext.base_counts(role, champ)["ext"]
            games, wins = games + n_e, wins + w_e
        return shrink(wins, games, 0.5, self.kc if k is None else k)

    def lane_term(self, role: str, a: int, b: int, y: int) -> float:
        """Logit del enfrentamiento de línea del azul (``a``) contra el rojo (``b``); ``y`` = 1 si ganó el azul."""
        pa, pb = self.p_base(role, a, y), self.p_base(role, b, 1 - y)
        games, wins = self.matchup.get((role, a, b), (0, 0))
        games, wins = max(0, games - 1), max(0, wins - y)
        if self.ext:
            n_e, w_e = self.ext.pair_counts(role, a, b, fetch=False)["ext"]
            games, wins = games + n_e, wins + w_e
        return logit(shrink(wins, games, log5(pa, pb), self.km))

    def synergy_term(self, r1: str, c1: int, r2: str, c2: int, win: int) -> float:
        """Sinergia (log-odds sobre lo esperado) de una pareja del mismo equipo; ``win`` = 1 si ganó ese equipo."""
        games, wins = self.synergy.get((r1, c1, r2, c2), (0, 0))
        games, wins = max(0, games - 1), max(0, wins - win)
        if self.ext:
            n_e, w_e = self.ext.synergy_counts(r1, c1, r2, c2)["ext"]
            games, wins = games + n_e, wins + w_e
        expected = sigmoid(logit(self.p_base(r1, c1, win, LIGHT_PRIOR)) + logit(self.p_base(r2, c2, win, LIGHT_PRIOR)))
        return logit(shrink(wins, games, expected, self.ks)) - logit(expected)

    def match_logit(self, blue: dict[str, int], red: dict[str, int], y: int) -> float:
        """L de la partida a favor del azul (líneas + sinergias propias − sinergias rivales)."""
        total = 0.0
        for role in ROLES:
            total += self.lane_term(role, blue[role], red[role], y)
        for team, sign, win in ((blue, 1, y), (red, -1, 1 - y)):
            for r1, r2 in ROLE_PAIRS:
                total += sign * self.synergy_term(r1, team[r1], r2, team[r2], win)
        return total


def _match_logits(model: _LeaveOneOut, teams: dict, winners: dict) -> tuple[list[float], list[int]]:
    """L de cada partida completa (5 contra 5) y su resultado (1 si ganó el azul)."""
    logits: list[float] = []
    outcomes: list[int] = []
    for match_id, team_map in teams.items():
        blue, red = team_map[100], team_map[200]
        if len(blue) != 5 or len(red) != 5:
            continue
        y = 1 if winners[match_id] == 100 else 0
        logits.append(model.match_logit(blue, red, y))
        outcomes.append(y)
    return logits, outcomes


def _fit_alpha(logits: list[float], outcomes: list[int]) -> float:
    """α que maximiza la log-verosimilitud (Newton-Raphson, acotado entre 0,2 y 2)."""
    alpha = 1.0
    for _ in range(30):
        grad = hess = 0.0
        for x, y in zip(logits, outcomes):
            p = sigmoid(alpha * x)
            grad += (y - p) * x
            hess -= p * (1 - p) * x * x
        if hess >= -1e-9:
            break
        step = grad / hess
        alpha = clamp(alpha - step, 0.2, 2.0)
        if abs(step) < 1e-5:
            break
    return alpha


def _evaluate(logits: list[float], outcomes: list[int], a: float) -> dict:
    """Acierto, log-loss y Brier de las predicciones con α = ``a``."""
    n = len(logits)
    correct = logloss = brier = 0.0
    for x, y in zip(logits, outcomes):
        p = sigmoid(a * x)
        correct += (p >= 0.5) == (y == 1)
        logloss -= math.log(clamp(p if y else 1 - p, 1e-9, 1))
        brier += (p - y) ** 2
    return {"accuracy": pct(correct / n), "log_loss": round(logloss / n, 4), "brier": round(brier / n, 4)}


def _calibration(logits: list[float], outcomes: list[int], alpha: float) -> list[dict]:
    """Probabilidad predicha frente a la observada, por tramos de 10 puntos."""
    bins = [[0, 0.0, 0] for _ in range(10)]
    for x, y in zip(logits, outcomes):
        p = sigmoid(alpha * x)
        idx = min(9, int(p * 10))
        bins[idx][0] += 1
        bins[idx][1] += p
        bins[idx][2] += y
    return [
        {
            "bin": f"{i * 10}-{i * 10 + 10}%",
            "n": n,
            "predicted": pct(s / n) if n else None,
            "observed": pct(o / n) if n else None,
        }
        for i, (n, s, o) in enumerate(bins)
        if n
    ]


def backtest(svc, sample: int = 3000, save: bool = True, sources: str = "all") -> dict:
    """Valida y calibra el modelo con partidas reales guardadas (leave-one-out).

    De cada partida se resta su propia contribución a tus agregados antes de
    predecirla. Los datos de OP.GG se usan solo desde la caché (no se
    descargan aquí) y apenas contienen cada partida concreta.
    """
    started = time.time()
    matches = svc.db.query(
        "SELECT match_id, winner FROM matches WHERE aggregated = 1 AND full_roles = 1 AND winner IN (100, 200) "
        "ORDER BY game_start DESC LIMIT ?",
        (sample,),
    )
    if len(matches) < 200:
        return {"status": "insufficient", "matches": len(matches), "needed": 200}

    winners = {m["match_id"]: m["winner"] for m in matches}
    teams = _load_teams(svc, list(winners))
    priors = svc.priors.get()
    base = _load_champs(svc)
    matchup = _load_matchups(svc, teams)
    synergy = _load_synergies(svc)
    ext = Evidence(svc, Filters(), "opgg", fetch=False) if sources == "all" else None
    if ext is not None and not ext.scope:
        ext = None

    logits, outcomes = _match_logits(_LeaveOneOut(priors, base, matchup, synergy, ext), teams, winners)
    alpha = _fit_alpha(logits, outcomes)
    report = {
        "status": "ok",
        "matches": len(logits),
        "sources": "opgg+own" if ext else "own",
        "alpha": round(alpha, 4),
        "raw": _evaluate(logits, outcomes, 1.0),
        "calibrated": _evaluate(logits, outcomes, alpha),
        "baseline": {
            "accuracy": pct(max(sum(outcomes), len(outcomes) - sum(outcomes)) / len(outcomes)),
            "log_loss": round(math.log(2), 4),
        },
        "calibration": _calibration(logits, outcomes, alpha),
        "priors": priors,
        "seconds": round(time.time() - started, 1),
        "at": int(time.time()),
    }
    if save:
        svc.db.meta_set("draft_alpha", str(round(alpha, 4)))
        svc.db.meta_set("draft_backtest", json.dumps(report))
    return report


def last_backtest(svc) -> dict | None:
    raw = svc.db.meta_get("draft_backtest")
    return json.loads(raw) if raw else None
