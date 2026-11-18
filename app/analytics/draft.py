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

from app.analytics.evidence import LIGHT_PRIOR, Evidence
from app.analytics.filters import Filters
from app.analytics.matchups import _source, pair_view
from app.analytics.stats import clamp, log5, logit, pct, safe_div, sample_confidence, shrink, sigmoid
from app.riot.routing import ROLES

Z90 = 1.6448536269514722
ROLE_PAIRS = [(ROLES[i], ROLES[j]) for i in range(len(ROLES)) for j in range(i + 1, len(ROLES))]
# Parejas en las que merece la pena pedir sinergias a OP.GG (las de más peso).
KEY_PAIRS = {("ADC", "SUPPORT"), ("JUNGLE", "MID"), ("TOP", "JUNGLE"), ("JUNGLE", "SUPPORT"), ("MID", "SUPPORT")}
DAMAGE_SPLIT = {"AD": (0.88, 0.08), "AP": (0.08, 0.88), "HYBRID": (0.5, 0.45), "MIXED": (0.5, 0.45), "TRUE": (0.45, 0.2)}


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


def _prefetch(svc, ev: Evidence, ally: dict[str, int], enemy: dict[str, int]) -> None:
    """Descarga en paralelo lo que falte de OP.GG para este draft."""
    if not ev.scope:
        return
    jobs = []
    for role in ROLES:
        for champ in (ally.get(role), enemy.get(role)):
            if champ:
                jobs.append(lambda r=role, c=champ: ev.counters(r, c))
                jobs.append(lambda r=role, c=champ: svc.opgg.champion_info(c, r))
        a, b = ally.get(role), enemy.get(role)
        if a and b:
            jobs.append(lambda r=role, x=a, y=b: svc.opgg.matchup_guide(x, y, r))
            jobs.append(lambda r=role, x=a, y=b: svc.opgg.matchup_guide(y, x, r))
    for team in (ally, enemy):
        for r1, r2 in ROLE_PAIRS:
            if (r1, r2) in KEY_PAIRS and team.get(r1) and team.get(r2):
                jobs.append(lambda a=r1, c=team[r1], b=r2: svc.opgg.synergies(c, a, b))
    for role in ROLES:
        jobs.append(lambda r=role: ev.tier_list(r))
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


def _composition(svc, ev: Evidence, team: dict[str, int]) -> dict | None:
    if not team:
        return None
    dd = svc.dd
    role_totals = svc.agg.role_totals(ev.f) if ev.use_own else {}
    phys = magic = true = tank = cc = 0.0
    ref_tank = ref_cc = 0.0
    sources = []
    types = {}
    for role, champ in team.items():
        row = ev.own_role(role).get(champ) or {}
        ref = role_totals.get(role) or {}
        ref_minutes = ref.get("minutes") or 0
        role_dpm = (ref.get("dmg") or 0) / ref_minutes if ref_minutes else 650.0
        if ref_minutes:
            ref_tank += ((ref.get("taken") or 0) + (ref.get("mitigated") or 0)) / ref_minutes
            ref_cc += (ref.get("cc") or 0) / ref_minutes
        minutes = row.get("minutes") or 0
        info = svc.opgg.champion_info(champ, role, allow_fetch=False) if ev.scope else None
        damage_type = (info or {}).get("damage_type")
        types[str(champ)] = damage_type
        if (row.get("dgames") or 0) >= 20 and minutes:
            phys += (row.get("dmg_phys") or 0) / minutes
            magic += (row.get("dmg_magic") or 0) / minutes
            true += (row.get("dmg_true") or 0) / minutes
            tank += ((row.get("taken") or 0) + (row.get("mitigated") or 0)) / minutes
            cc += (row.get("cc") or 0) / minutes
            sources.append("data")
            continue
        if damage_type and damage_type.upper() in DAMAGE_SPLIT:
            share_phys, share_magic = DAMAGE_SPLIT[damage_type.upper()]
            sources.append("opgg")
        else:
            stats = (dd.champions.get(champ) or {}).get("info") or {}
            attack, magic_score = stats.get("attack", 5), stats.get("magic", 5)
            share_magic = magic_score / max(1, attack + magic_score)
            share_phys = 1 - share_magic
            sources.append("ddragon")
        phys += role_dpm * share_phys
        magic += role_dpm * share_magic
        true += role_dpm * max(0.0, 1 - share_phys - share_magic)
        if ref_minutes:
            defense = ((dd.champions.get(champ) or {}).get("info") or {}).get("defense", 5)
            tank += ((ref.get("taken") or 0) + (ref.get("mitigated") or 0)) / ref_minutes * (0.6 + defense / 12.5)
            cc += (ref.get("cc") or 0) / ref_minutes
    total = phys + magic + true
    profile = {"phys": pct(safe_div(phys, total)), "magic": pct(safe_div(magic, total)), "true": pct(safe_div(true, total))}
    tank_ratio = safe_div(tank, ref_tank) if ref_tank else None
    cc_ratio = safe_div(cc, ref_cc) if ref_cc else None
    warnings = []
    if len(team) >= 4 and total:
        if (profile["magic"] or 0) < 20:
            warnings.append("Daño casi todo físico: al rival le basta con comprar armadura.")
        elif (profile["phys"] or 0) < 25:
            warnings.append("Daño casi todo mágico: al rival le basta con comprar resistencia mágica.")
    if len(team) >= 4 and tank_ratio is not None and tank_ratio < 0.82:
        warnings.append("Poca primera línea: cuesta iniciar y aguantar peleas.")
    if len(team) >= 4 and cc_ratio is not None and cc_ratio < 0.75:
        warnings.append("Poco control de masas: difícil asegurar objetivos y castigar errores.")
    source = "data" if sources and all(s == "data" for s in sources) else ("opgg" if "opgg" in sources else "ddragon")
    return {
        "damage_profile": profile,
        "damage_types": types,
        "frontline": round(tank_ratio, 2) if tank_ratio is not None else None,
        "cc": round(cc_ratio, 2) if cc_ratio is not None else None,
        "source": source,
        "warnings": warnings,
    }


def _champ_curve(svc, ev: Evidence, role: str, champ: int, opp: int | None) -> list[float] | None:
    """WR por tramo de duración: tu base de datos si hay, si no OP.GG."""
    row = ev.own_role(role).get(champ) or {}
    if sum(row.get(f"g{b}") or 0 for b in range(4)) >= 80:
        p_all = ev.base(role, champ)[0]
        return [shrink(row.get(f"w{b}") or 0, row.get(f"g{b}") or 0, p_all, 60.0) for b in range(4)]
    if ev.scope and opp:
        guide = svc.opgg.matchup_guide(champ, opp, role, allow_fetch=False) or {}
        by_minute = {g["minute"]: g["wr"] for g in guide.get("game_lengths") or [] if g.get("wr")}
        if by_minute:
            late = [by_minute[m] for m in (35, 40) if m in by_minute]
            values = [by_minute.get(0), by_minute.get(25), by_minute.get(30), sum(late) / len(late) if late else None]
            if all(v is not None for v in values):
                # Datos del parche en curso (muestras pequeñas por tramo): se
                # encogen a medio camino hacia el WR general del campeón.
                p_all = ev.base(role, champ)[0]
                return [p_all + 0.5 * (v - p_all) for v in values]
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


def analyze(svc, f: Filters, ally: dict, enemy: dict, sources: str = "all") -> dict:
    ev = Evidence(svc, f, sources)
    alpha = get_alpha(svc)
    ally, enemy = _clean(ally), _clean(enemy)
    _prefetch(svc, ev, ally, enemy)
    ev.fetch = False  # a partir de aquí, solo lo que ya esté en caché

    lanes = []
    total = 0.0
    variance = 0.0
    for role in ROLES:
        a, b = ally.get(role), enemy.get(role)
        lane: dict = {"role": role, "ally": a, "enemy": b}
        if a and b:
            p = ev.pair(role, a, b)
            term, var = p["logit"], p["var"]
            view = pair_view(p)
            guide = svc.opgg.matchup_guide(a, b, role, allow_fetch=False) if ev.scope else None
            lane.update(
                {
                    "kind": "matchup",
                    **{k: view[k] for k in ("games", "wins", "wr", "wr_adj", "expected", "delta", "ci", "verdict", "counter", "confidence", "sources", "lane")},
                    "ally_wr": pct(p["pa"]),
                    "enemy_wr": pct(p["pb"]),
                    "opgg": {
                        "lane_advantage": guide.get("lane_advantage"),
                        "solo_kill_advantage": guide.get("solo_kill_advantage"),
                        "play_style": guide.get("play_style"),
                        "tip": guide.get("tip"),
                    }
                    if guide
                    else None,
                    "guide": svc.guides.exists(role, a, b),
                }
            )
        elif a or b:
            champ = a or b
            p, var, games = ev.base(role, champ)
            term = logit(p) if a else -logit(p)
            lane.update({"kind": "solo", "games": int(games), "wr_adj": pct(p if a else 1 - p), "confidence": sample_confidence(games)})
        else:
            term, var = 0.0, 0.0
            lane.update({"kind": "empty"})
        lane["logit"] = term
        lane["var"] = var
        total += term
        variance += var
        lanes.append(lane)

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
        lane["impact_pp"] = round((p_win - sigmoid(alpha * (total - lane["logit"]))) * 100, 2)
    for terms, sign in ((ally_syn, 1), (enemy_syn, -1)):
        for t in terms:
            t["impact_pp"] = round((p_win - sigmoid(alpha * (total - sign * t["logit"]))) * 100, 2)

    def team_strength(team: dict[str, int], syn: float) -> dict | None:
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

    filled = sum(1 for lane in lanes if lane["kind"] == "matchup")
    with_data = sum(1 for lane in lanes if lane["kind"] == "matchup" and lane["games"] >= 100)
    if se < 0.09 and with_data >= 4:
        confidence = {"level": 3, "label": "Alta"}
    elif se < 0.16 and with_data >= 2:
        confidence = {"level": 2, "label": "Media"}
    else:
        confidence = {"level": 1, "label": "Baja"}

    def public(items):
        return [{k: (round(v, 4) if k == "logit" else v) for k, v in item.items() if k != "var"} for item in items]

    return {
        "filters": f.public(),
        "evidence": ev.public(),
        "alpha": alpha,
        "priors": ev.priors,
        "win_probability": pct(p_win),
        "interval": [pct(low), pct(high)],
        "grade": grade(p_win),
        "confidence": confidence,
        "lanes_filled": filled,
        "picks": {"ally": len(ally), "enemy": len(enemy)},
        "lanes": public(lanes),
        "synergies": {"ally": public(ally_syn), "enemy": public(enemy_syn)},
        "teams": {"ally": team_strength(ally, syn_ally), "enemy": team_strength(enemy, syn_enemy)},
        "composition": {"ally": _composition(svc, ev, ally), "enemy": _composition(svc, ev, enemy)},
        "duration_curve": _duration_curve(svc, ev, ally, enemy, alpha),
    }


def suggest(
    svc,
    f: Filters,
    ally: dict,
    enemy: dict,
    role: str,
    side: str = "ally",
    limit: int = 12,
    min_games: int = 200,
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
    taken = set(ally.values()) | set(enemy.values())
    opponent = theirs.get(role)
    if ev.scope:
        jobs = [lambda: ev.tier_list(role)]
        if opponent:
            jobs.append(lambda: ev.counters(role, opponent))
        jobs += [lambda r=r, c=c: svc.opgg.synergies(c, r, role) for r, c in mine.items() if r != role]
        svc.opgg.prefetch(jobs, timeout=20)
    ev.fetch = False

    results = []
    for champ in ev.champions_in_role(role, min_games):
        if champ in taken:
            continue
        p_c, _, games_c = ev.base(role, champ)
        if games_c < min_games:
            continue
        if opponent:
            p = ev.pair(role, champ, opponent, fetch=False)
            lane_p, lane_games = p["adjusted"], p["games"]
            if lane_games < min_lane_games:
                continue
        else:
            lane_p, lane_games = p_c, 0
        lane_term = logit(lane_p)
        synergy = 0.0
        for partner_role, partner in mine.items():
            if partner_role == role:
                continue
            s = ev.synergy(role, champ, partner_role, partner)
            synergy += s["logit"]
        score = lane_term + synergy
        results.append(
            {
                "champ": champ,
                "score_pp": round((sigmoid(alpha * score) - 0.5) * 100, 2),
                "lane_wr": pct(lane_p),
                "lane_games": int(lane_games),
                "synergy_pp": round((sigmoid(alpha * score) - sigmoid(alpha * lane_term)) * 100, 2),
                "base_wr": pct(p_c),
                "games": int(games_c),
                "confidence": sample_confidence(lane_games if opponent else games_c),
            }
        )
    results.sort(key=lambda r: r["score_pp"], reverse=True)
    return {
        "role": role,
        "side": side,
        "opponent": opponent,
        "filters": f.public(),
        "evidence": ev.public(),
        "best": results[:limit],
        "worst": list(reversed(results[-min(5, len(results)) :])) if len(results) > limit else [],
        "evaluated": len(results),
    }


# ------------------------------------------------------------------ backtest
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
    teams: dict[str, dict[int, dict[str, int]]] = defaultdict(lambda: {100: {}, 200: {}})
    ids = list(winners)
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        marks = ",".join("?" * len(chunk))
        for r in svc.db.query(f"SELECT match_id, team, role, champ FROM participants WHERE match_id IN ({marks}) AND role IS NOT NULL", chunk):
            teams[r["match_id"]][r["team"]][r["role"]] = r["champ"]

    priors = svc.priors.get()
    kc, km, ks = priors["champ"], priors["matchup"], priors["synergy"]
    base = {(r[0], r[1]): (r[2], r[3]) for r in svc.db.query("SELECT role, champ, SUM(games), SUM(wins) FROM agg_champ GROUP BY role, champ")}
    pairs_needed: dict[str, set[int]] = defaultdict(set)
    for team_map in teams.values():
        for role in ROLES:
            if team_map[100].get(role) and team_map[200].get(role):
                pairs_needed[role].add(team_map[100][role])
                pairs_needed[role].add(team_map[200][role])
    matchup: dict[tuple, tuple[int, int]] = {}
    for role, champs in pairs_needed.items():
        champ_list = sorted(champs)
        for i in range(0, len(champ_list), 400):
            chunk = champ_list[i : i + 400]
            marks = ",".join("?" * len(chunk))
            for r in svc.db.query(
                f"SELECT champ, opp, SUM(games), SUM(wins) FROM agg_matchup WHERE role = ? AND champ IN ({marks}) GROUP BY champ, opp",
                [role, *chunk],
            ):
                matchup[(role, r[0], r[1])] = (r[2], r[3])
    synergy = {
        (r[0], r[1], r[2], r[3]): (r[4], r[5])
        for r in svc.db.query("SELECT role1, champ1, role2, champ2, SUM(games), SUM(wins) FROM agg_synergy GROUP BY 1, 2, 3, 4")
    }

    ext = Evidence(svc, Filters(), "opgg", fetch=False) if sources == "all" else None
    if ext is not None and not ext.scope:
        ext = None

    def p_base(role, champ, win, k=None):
        games, wins = base.get((role, champ), (0, 0))
        games, wins = max(0, games - 1), max(0, wins - win)
        if ext:
            n_e, w_e = ext.base_counts(role, champ)["ext"]
            games, wins = games + n_e, wins + w_e
        return shrink(wins, games, 0.5, kc if k is None else k)

    logits: list[float] = []
    outcomes: list[int] = []
    for match_id, team_map in teams.items():
        blue, red = team_map[100], team_map[200]
        if len(blue) != 5 or len(red) != 5:
            continue
        y = 1 if winners[match_id] == 100 else 0
        total = 0.0
        for role in ROLES:
            a, b = blue[role], red[role]
            pa, pb = p_base(role, a, y), p_base(role, b, 1 - y)
            games, wins = matchup.get((role, a, b), (0, 0))
            games, wins = max(0, games - 1), max(0, wins - y)
            if ext:
                n_e, w_e = ext.pair_counts(role, a, b, fetch=False)["ext"]
                games, wins = games + n_e, wins + w_e
            total += logit(shrink(wins, games, log5(pa, pb), km))
        for team, sign, win in ((blue, 1, y), (red, -1, 1 - y)):
            for r1, r2 in ROLE_PAIRS:
                c1, c2 = team[r1], team[r2]
                games, wins = synergy.get((r1, c1, r2, c2), (0, 0))
                games, wins = max(0, games - 1), max(0, wins - win)
                if ext:
                    n_e, w_e = ext.synergy_counts(r1, c1, r2, c2)["ext"]
                    games, wins = games + n_e, wins + w_e
                expected = sigmoid(logit(p_base(r1, c1, win, LIGHT_PRIOR)) + logit(p_base(r2, c2, win, LIGHT_PRIOR)))
                total += sign * (logit(shrink(wins, games, expected, ks)) - logit(expected))
        logits.append(total)
        outcomes.append(y)

    alpha = 1.0
    for _ in range(30):  # Newton-Raphson sobre la log-verosimilitud
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

    def evaluate(a: float) -> dict:
        n = len(logits)
        correct = logloss = brier = 0.0
        for x, y in zip(logits, outcomes):
            p = sigmoid(a * x)
            correct += (p >= 0.5) == (y == 1)
            logloss -= math.log(clamp(p if y else 1 - p, 1e-9, 1))
            brier += (p - y) ** 2
        return {"accuracy": pct(correct / n), "log_loss": round(logloss / n, 4), "brier": round(brier / n, 4)}

    bins = [[0, 0.0, 0] for _ in range(10)]
    for x, y in zip(logits, outcomes):
        p = sigmoid(alpha * x)
        idx = min(9, int(p * 10))
        bins[idx][0] += 1
        bins[idx][1] += p
        bins[idx][2] += y
    report = {
        "status": "ok",
        "matches": len(logits),
        "sources": "opgg+own" if ext else "own",
        "alpha": round(alpha, 4),
        "raw": evaluate(1.0),
        "calibrated": evaluate(alpha),
        "baseline": {
            "accuracy": pct(max(sum(outcomes), len(outcomes) - sum(outcomes)) / len(outcomes)),
            "log_loss": round(math.log(2), 4),
        },
        "calibration": [
            {"bin": f"{i * 10}-{i * 10 + 10}%", "n": n, "predicted": pct(s / n) if n else None, "observed": pct(o / n) if n else None}
            for i, (n, s, o) in enumerate(bins)
            if n
        ],
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
