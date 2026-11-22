"""Build completa recomendada para cada jugador del draft, contra todo el equipo rival y coordinada con su equipo.

Para cada campeón elegido se reúnen **los objetos que tienen sentido para él** (los que se le compran de verdad):

* OP.GG: su núcleo contra su rival de línea (``matchup_guide``) y sus opciones de 4.º, 5.º y 6.º objeto, botas,
  runas, hechizos y habilidades (``champion_build``).
* Lolalytics: todos los objetos de sus builds completas, con cuántas builds los llevan y su winrate (``item_sets``).
* Sus OTPs (``data/otps``): qué compran **según cómo es el equipo rival** (mucho AP, mucho AD, mixto, con curaciones,
  con tanques, con asesinos), con su winrate. Es lo que más pesa para adaptar la build al draft.
* Tu base de datos: objetos con ese campeón y su winrate.

Cada objeto se puntúa por lo que se usa y lo bien que va, más lo que pide **todo el equipo rival** (su daño
físico/mágico, curaciones, primera línea, asesinos, control) y lo que pide **tu equipo** (quién lleva las Heridas
graves —como mucho 2—, poca primera línea, objetos de equipo del support). Se monta una build de 6: botas + el núcleo
(los 2 primeros objetos que definen al campeón) + los 3 mejores para este draft, cada uno con su porqué.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from app.analytics.draft import _composition
from app.analytics.evidence import Evidence
from app.ingest.otp_details import FILES_DIR, HEAVY_HEAL, TIER_ORDER, _slug, champ_info_from
from app.riot.routing import ROLES

ITEMS = 5  # objetos completos además de las botas
CORE = 2  # los primeros que definen al campeón (su núcleo) se respetan
ANTI_HEAL_PER_TEAM = 2  # con 2 jugadores con Heridas graves basta; más es oro tirado
ANTI_HEAL_ORDER = ("ADC", "TOP", "MID", "JUNGLE")  # quién la compra antes (pega más y más seguido)
STEELCAPS, MERCURY = 3047, 3111
# Objetos de equipo del support según lo que pide el draft (si existen en el parche).
LOCKET, MIKAEL, CHEMTECH = 3190, 3222, 3011
MIN_OWN_GAMES = 30
MIN_MATCHUP_GAMES = 100  # con menos partidas, el núcleo contra ese rival es ruido: se usa su build general
STYLE_TAG = {"ap": "SpellDamage", "ad": "Damage", "tank": "Health"}


def _style(cat, items: list[int], tags: list[str]) -> str:
    """ap / ad / tank según sus objetos (y sus clases de Data Dragon si no hay objetos)."""
    score = Counter()
    for i in items:
        t = set(cat.tags(i)) if cat else set()
        if "SpellDamage" in t:
            score["ap"] += 1
        if t & {"Damage", "CriticalStrike", "AttackSpeed", "ArmorPenetration"}:
            score["ad"] += 1
        if t & {"Health", "Armor", "SpellBlock"} and not t & {"SpellDamage", "Damage", "CriticalStrike"}:
            score["tank"] += 1
    if score:
        return score.most_common(1)[0][0]
    if "Tank" in tags:
        return "tank"
    return "ap" if "Mage" in tags else "ad"


def _squishy(style: str, tags: list[str]) -> bool:
    """Carries blandos (tiradores, magos, asesinos): no se les piden resistencias salvo casos extremos."""
    return style in ("ap", "ad") and not ({"Tank", "Fighter"} & set(tags))


def _otp_items(svc, champ: int, role: str) -> dict | None:
    dd = svc.dd
    try:
        summary = json.loads((Path(svc.settings.data_dir) / FILES_DIR / _slug(dd.name(champ)) / "resumen.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    role_data = (summary.get("roles") or {}).get(role) or {}
    groups = (role_data.get("all") or {}).get("items_by_enemy")
    best = None
    for tier in TIER_ORDER:  # el elo más alto con datos (para mostrar su build más jugada)
        block = (role_data.get("by_tier") or {}).get(tier)
        if block and block.get("games", 0) >= 20:
            best = {"tier": tier, "games": block["games"], "players": block.get("players"),
                    "path": (block.get("build_paths") or [None])[0], "skill_max": (block.get("skill_max") or [None])[0]}
            break
    if not groups and not best:
        return None
    return {"groups": groups or {}, "best": best}


def _own_items(svc, cat, champ: int, role: str) -> dict | None:
    rows = svc.db.query("SELECT items, win FROM participants WHERE champ = ? AND role = ? AND items IS NOT NULL", (champ, role))
    if len(rows) < MIN_OWN_GAMES:
        return None
    count, wins = Counter(), Counter()
    for r in rows:
        items = {int(x) for x in str(r["items"]).split(",") if x.strip().isdigit() and int(x)}
        for i in items:
            if cat and cat.is_legendary(i):
                count[i] += 1
                wins[i] += int(r["win"] or 0)
    return {"games": len(rows), "items": {i: (n, wins[i]) for i, n in count.items() if n >= 10}}


class _Context:
    """Lo que pide el equipo rival (para un jugador concreto)."""

    def __init__(self, svc, enemies: list[int], comp: dict | None, champ_info) -> None:
        dd = svc.dd
        self.enemies = enemies
        self.names = {c: dd.name(c) for c in enemies}
        profile = (comp or {}).get("damage_profile") or {}
        self.phys, self.magic = profile.get("phys") or 0, profile.get("magic") or 0
        self.cc = (comp or {}).get("cc") or 0
        self.frontline = (comp or {}).get("frontline") or 0
        tags = {c: ((dd.champions.get(c) or {}).get("tags") or []) for c in enemies}
        self.healers = [c for c in enemies if self.names[c] in HEAVY_HEAL]
        self.tanks = [c for c in enemies if "Tank" in tags[c]]
        self.assassins = [c for c in enemies if "Assassin" in tags[c]]
        self.complete = len(enemies) >= 3  # con menos, el perfil de daño aún no es fiable
        # Grupos de las estadísticas de los OTPs que coinciden con este equipo rival.
        self.groups: list[str] = []
        if self.complete and champ_info:
            infos = [i for i in (champ_info(c) for c in enemies) if i]
            if infos:
                m = sum(i["magic"] for i in infos) / len(infos)
                self.groups.append("ap" if m >= 0.5 else "ad" if m <= 0.3 else "mixed")
        if self.healers:
            self.groups.append("healers")
        if len(self.tanks) >= 2:
            self.groups.append("tanks")
        if len(self.assassins) >= 2:
            self.groups.append("assassins")

    def who(self, champs: list[int]) -> str:
        return ", ".join(self.names[c] for c in champs)


def _candidates(cat, base: dict, general: dict | None, lola: dict | None, otp: dict | None, own: dict | None) -> dict[int, dict]:
    """Objetos completos que se le compran a ese campeón, con lo que dice cada fuente."""
    out: dict[int, dict] = {}

    def add(item, **data):
        try:
            item = int(item)
        except (TypeError, ValueError):
            return
        if not item or (cat and not cat.is_legendary(item)):
            return
        out.setdefault(item, {}).update(data)

    for rank, i in enumerate(base.get("core") or []):
        add(i, core=rank)
    for key in ("popular", "fourth", "fifth", "sixth"):
        for o in (general or {}).get(key) or []:
            if o.get("id"):
                add(o["id"], opgg=max(out.get(int(o["id"]), {}).get("opgg", 0.0), float(o.get("pick_rate") or 0)))
    if lola and lola.get("games"):
        total = lola["games"]
        for i, (games, wins) in (lola.get("items") or {}).items():
            if games / total >= 0.01:
                add(i, lola=games / total, lola_games=games, lola_wins=wins)
    for i in ((otp or {}).get("groups", {}).get("all", {}).get("items") or {}):
        add(i, otp_seen=True)
    for i, (games, wins) in ((own or {}).get("items") or {}).items():
        add(i, own_games=games, own_wins=wins)
    return out


def _wr_text(delta: float | None, games: int) -> str:
    """El WR de las builds de 5 objetos es alto para todos (son partidas largas): solo se dice si va claramente mejor
    o peor que sus otras opciones, nunca el número suelto (un «65 % WR» engaña)."""
    if delta is None or games < 300 or abs(delta) < 2:
        return ""
    return f", {abs(delta):.0f} pp {'mejor' if delta > 0 else 'peor'} que sus otras opciones en Lolalytics"


def _score(item: int, data: dict, cat, style: str, squishy: bool, ctx: _Context, otp: dict | None, lola: dict | None, team: dict) -> tuple[float, str]:
    """Puntuación del objeto para este draft y la razón principal."""
    reasons: list[tuple[float, str]] = []
    use = max(data.get("lola", 0.0), data.get("opgg", 0.0))
    score = 2.0 * use
    # Winrate en Lolalytics frente a la media de sus otros objetos (encogido hacia 0 con pocas partidas).
    delta = None
    if lola and data.get("lola_games"):
        totals = list(lola["items"].values())
        mean = sum(w for _g, w in totals) / max(1, sum(g for g, _w in totals))
        delta = (data["lola_wins"] / data["lola_games"] - mean) * 100
        score += 0.04 * delta * data["lola_games"] / (data["lola_games"] + 300)
    if use >= 0.1:
        reasons.append((0.1, f"De los más comprados con él ({use * 100:.0f} % de sus builds{_wr_text(delta, data.get('lola_games') or 0)})"))
    # Sus OTPs: cuánto más (o menos) lo compran contra equipos como este.
    groups = (otp or {}).get("groups") or {}
    all_g = groups.get("all") or {}
    if all_g.get("games"):
        base_share = (all_g.get("items") or {}).get(str(item), [0, 0])[0] / all_g["games"]
        for name in ctx.groups:
            g = groups.get(name) or {}
            if (g.get("games") or 0) < 25:
                continue
            games, wins = (g.get("items") or {}).get(str(item), [0, 0])
            lift = games / g["games"] - base_share
            if abs(lift) >= 0.03:
                score += 4.0 * lift
                if lift > 0 and games >= 8:
                    reasons.append((lift * 10, f"Sus OTPs lo compran más contra equipos así ({100 * games / g['games']:.0f} % frente a {base_share * 100:.0f} %, {100 * wins / games:.0f} % WR)"))
    if cat:
        resist = cat.resist(item)
        tags = set(cat.tags(item))
        if ctx.complete and "mr" in resist and (not squishy or ctx.magic >= 70):
            fit = max(-0.5, min(1.2, (ctx.magic - 50) / 20))
            score += fit
            if fit > 0.3:
                reasons.append((fit, f"Resistencia mágica: el {ctx.magic:.0f} % del daño rival es mágico"))
        if ctx.complete and "armor" in resist and (not squishy or ctx.phys >= 70):
            fit = max(-0.5, min(1.2, (ctx.phys - 50) / 20))
            score += fit
            if fit > 0.3:
                reasons.append((fit, f"Armadura: el {ctx.phys:.0f} % del daño rival es físico"))
        if squishy and len(ctx.assassins) >= 2 and resist:
            score += 0.6
            reasons.append((0.6, f"Para sobrevivir a sus asesinos ({ctx.who(ctx.assassins)})"))
        pen = "MagicPenetration" if style == "ap" else "ArmorPenetration"
        if style in ("ap", "ad") and pen in tags and (len(ctx.tanks) >= 2 or ctx.frontline >= 1.15):
            score += 0.7
            reasons.append((0.7, f"Penetración contra su primera línea ({ctx.who(ctx.tanks) or 'mucha vida y resistencias'})"))
        if cat.anti_heal(item):
            if team.get("anti_heal") is not None:
                score += 2.5
                reasons.append((3.0, f"Heridas graves contra {ctx.who(ctx.healers)}{team['anti_heal']}"))
            else:
                score -= 1.0  # no te toca: ya la llevan otros (o no hace falta)
        if team.get("needs_front") and not squishy and tags & {"Health", "Armor", "SpellBlock"}:
            score += 0.3
            reasons.append((0.3, "Tu equipo tiene poca primera línea: aguantar es parte de tu trabajo"))
    return score, (max(reasons)[1] if reasons else "")


def _boots(cat, base: dict, general: dict | None, lola: dict | None, otp: dict | None, squishy: bool, ctx: _Context) -> dict | None:
    """Las botas: las suyas de siempre salvo que el draft pida otras (blindadas o de mercurio)."""
    usual = (base.get("boots") or (general or {}).get("boots") or [None])[0]
    if not usual and lola:
        boots = [(g, int(i)) for i, (g, _w) in (lola.get("boots") or {}).items() if cat and cat.is_boots(int(i))]
        usual = max(boots)[1] if boots else None
    want, why = None, ""
    if ctx.complete:
        limit = 70 if squishy else 60
        if ctx.phys >= limit:
            want, why = STEELCAPS, f"El {ctx.phys:.0f} % del daño rival es físico"
        elif ctx.magic >= limit or (not squishy and ctx.cc >= 1.25):
            want, why = MERCURY, f"El {ctx.magic:.0f} % del daño rival es mágico" + (" y tienen mucho control" if ctx.cc >= 1.25 else "")
    groups = (otp or {}).get("groups") or {}
    if want and usual and want != usual:
        # Si sus OTPs, contra equipos así, casi nunca se cambian las botas, se respetan las suyas.
        g = next((groups[n] for n in ctx.groups if (groups.get(n) or {}).get("games", 0) >= 25), None)
        if g and (g.get("boots") or {}).get(str(want), [0])[0] / g["games"] < 0.1:
            want = None
    if want and (not cat or want in cat):
        return {"item": want, "reason": why, "situational": want != usual, "replaces": usual if want != usual else None}
    if usual:
        return {"item": usual, "reason": "Sus botas de siempre", "situational": False}
    return None


def _base(svc, ev, opgg, champ: int, role: str, opp: int | None) -> tuple[dict, dict | None]:
    base: dict = {}
    core: dict = {}
    if opgg is not None and opp:
        guide = opgg.matchup_guide(champ, opp, role, allow_fetch=ev.fetch) or {}
        core = (guide.get("core_items") or [{}])[0]
        if core.get("ids") and (core.get("games") or 0) >= MIN_MATCHUP_GAMES:
            base = {
                "source": "matchup", "opponent": opp, "core": [int(i) for i in core.get("ids") or []],
                "games": core.get("games"), "wins": core.get("wins"),
                "boots": [int(i) for i in ((guide.get("boots") or [{}])[0].get("ids") or [])][:1],
                "starter": [int(i) for i in ((guide.get("starter_items") or [{}])[0].get("ids") or [])],
                "spells": [int(i) for i in ((guide.get("spells") or [{}])[0].get("ids") or [])],
                "runes": (guide.get("runes") or [None])[0], "skill_order": ((guide.get("skills") or [{}])[0]).get("order"),
            }
    general = opgg.champion_build(champ, role, allow_fetch=ev.fetch) if opgg is not None else None
    if not base and general and general.get("core"):
        base = {
            "source": "general", "core": general["core"], "games": general.get("core_games"), "wins": general.get("core_wins"),
            "boots": (general.get("boots") or [])[:1], "starter": general.get("starter") or [], "spells": general.get("spells") or [],
            "runes": general.get("runes"), "skill_max": general.get("skill_max"),
        }
        if opp and core.get("ids"):
            base["thin_matchup"] = core.get("games") or 0  # había núcleo contra ese rival, pero con pocas partidas
    return base, general


def _player(svc, ev, cat, champ_info, side: str, role: str, champ: int, theirs: dict, enemy_comp: dict | None, team: dict) -> dict:
    dd = svc.dd
    tags = list(((dd.champions.get(champ) or {}).get("tags")) or [])
    opp = theirs.get(role)
    opgg = svc.opgg if getattr(svc, "opgg", None) is not None and svc.opgg.enabled else None
    lola_src = getattr(svc, "lolalytics", None)
    base, general = _base(svc, ev, opgg, champ, role, opp)
    lola = None
    if lola_src is not None and getattr(lola_src, "enabled", False) and ev.scope:
        lola = lola_src.item_sets(champ, role, ev.scope.tier, ev.scope.region, ev.scope.patches[0], allow_fetch=ev.fetch)
    if not base.get("core") and lola and lola.get("first"):
        firsts = [(g, int(i)) for i, (g, _w) in lola["first"].items() if not cat or cat.is_legendary(int(i))]
        if firsts:
            base = {**base, "source": base.get("source") or "lolalytics", "core": [max(firsts)[1]]}
    otp = _otp_items(svc, champ, role)
    own = _own_items(svc, cat, champ, role)
    pool = _candidates(cat, base, general, lola, otp, own)
    style = _style(cat, (base.get("core") or list(pool)[:3]), tags)
    squishy = _squishy(style, tags)
    ctx = _Context(svc, [c for c in theirs.values() if c], enemy_comp, champ_info)
    plan = team.get(champ) or {}

    scored = {i: _score(i, data, cat, style, squishy, ctx, otp, lola, plan) for i, data in pool.items()}
    chosen: list[dict] = []
    for i in (base.get("core") or [])[:CORE]:
        if i in pool:
            why = (f"Núcleo contra {dd.name(opp)} (OP.GG, {base['games']} partidas)" if base.get("source") == "matchup" and base.get("games")
                   else f"Núcleo de su build (contra {dd.name(opp)} aún hay pocas partidas: {base['thin_matchup']})" if base.get("thin_matchup")
                   else "Núcleo de su build (lo que define al campeón)")
            chosen.append({"item": i, "kind": "core", "reason": why})
    anti = sum(1 for c in chosen if cat and cat.anti_heal(c["item"]))
    resist = 0
    for i, (_score_value, why) in sorted(scored.items(), key=lambda kv: -kv[1][0]):
        if len(chosen) >= ITEMS:
            break
        if any(c["item"] == i for c in chosen):
            continue
        if cat and cat.anti_heal(i):
            if anti or plan.get("anti_heal") is None:
                continue
            anti += 1
        if cat and cat.resist(i) and squishy:
            if resist >= 2:
                continue
            resist += 1
        situational = bool(why) and not why.startswith("De los más comprados")
        chosen.append({"item": i, "kind": "situational" if situational else "popular", "reason": why or "De los más comprados con él"})
    if plan.get("anti_heal") is not None and not anti and cat:
        # Le toca Heridas graves y ningún objeto suyo la da: la más barata de su tipo de daño.
        option = next(
            (i for i in sorted(cat.items, key=cat.cost)
             if cat.anti_heal(i) and cat.is_legendary(i) and cat.get(i).get("buyable") and not cat.get(i).get("champion") and STYLE_TAG[style] in cat.tags(i)),
            None,
        )
        if option:
            entry = {"item": option, "kind": "situational", "reason": f"Heridas graves contra {ctx.who(ctx.healers)}{plan['anti_heal']}"}
            if len(chosen) >= ITEMS:
                chosen[-1] = entry
            else:
                chosen.append(entry)
    alternatives = [
        {"item": i, "reason": why or "Otra opción frecuente"}
        for i, (_s, why) in sorted(scored.items(), key=lambda kv: -kv[1][0])
        if not any(c["item"] == i for c in chosen)
    ][:4]
    sources = [name for name, ok in (("OP.GG", bool(base.get("source") in ("matchup", "general") or general)), ("Lolalytics", bool(lola)),
                                     ("OTPs", bool(otp)), ("tu BD", bool(own))) if ok]
    return {
        "side": side,
        "role": role,
        "champ": champ,
        "style": style,
        "opponent": opp,
        "build": chosen,
        "boots": _boots(cat, base, general, lola, otp, squishy, ctx),
        "alternatives": alternatives,
        "runes": base.get("runes"),
        "starter": base.get("starter") or [],
        "spells": base.get("spells") or [],
        "skills": base.get("skill_order") or (">".join(base["skill_max"]) if base.get("skill_max") else None),
        "matchup": {"opponent": opp, "games": base.get("games"), "wins": base.get("wins")} if base.get("source") == "matchup" else None,
        "otp": (otp or {}).get("best"),
        "enemy": {"phys": ctx.phys, "magic": ctx.magic, "groups": ctx.groups},
        "sources": sources,
    }


def _team_plan(svc, players: dict[int, str], enemies: list[int], ally_comp: dict | None) -> dict[int, dict]:
    """Coordinación del equipo: quién lleva las Heridas graves (2 como mucho) y si hace falta más primera línea."""
    dd = svc.dd
    healers = [c for c in enemies if dd.name(c) in HEAVY_HEAL]
    plan: dict[int, dict] = {c: {} for c in players}
    if healers:
        order = sorted((c for c in players if players[c] != "SUPPORT"), key=lambda c: ANTI_HEAL_ORDER.index(players[c]) if players[c] in ANTI_HEAL_ORDER else 9)
        holders = order[:ANTI_HEAL_PER_TEAM]
        names = " y ".join(dd.name(c) for c in holders)
        for c in holders:
            plan[c]["anti_heal"] = f" (en tu equipo la llevan {names}: con dos basta)" if len(holders) > 1 else ""
    if len(players) >= 4 and (ally_comp or {}).get("frontline") is not None and ally_comp["frontline"] < 0.82:
        for c, role in players.items():
            if role in ("TOP", "JUNGLE", "SUPPORT"):
                plan[c]["needs_front"] = True
    return plan


def _support_items(cat, ctx: _Context, anti_heal_holders: int = 0) -> list[dict]:
    """Objetos de equipo del support según el draft rival (Heridas graves solo si no la llevan ya dos aliados)."""
    out = []
    if not cat or not ctx.complete:
        return out
    if ctx.magic >= 55 and LOCKET in cat:
        out.append({"item": LOCKET, "reason": f"Escudo para el equipo: el {ctx.magic:.0f} % del daño rival es mágico"})
    if ctx.cc >= 1.25 and MIKAEL in cat:
        out.append({"item": MIKAEL, "reason": "Quita el control a un aliado: tienen mucho control"})
    if ctx.healers and anti_heal_holders < ANTI_HEAL_PER_TEAM and CHEMTECH in cat:
        out.append({"item": CHEMTECH, "reason": f"Heridas graves desde el support contra {ctx.who(ctx.healers)}"})
    return out


def recommend(svc, f, ally: dict[str, int], enemy: dict[str, int], sources: str = "all") -> dict:
    """Build completa para cada campeón elegido de los dos equipos, contra todo el equipo rival."""
    ev = Evidence(svc, f, sources)
    opgg = svc.opgg if getattr(svc, "opgg", None) is not None and svc.opgg.enabled else None
    lola = getattr(svc, "lolalytics", None)
    lola = lola if lola is not None and getattr(lola, "enabled", False) else None
    if ev.scope and (opgg is not None or lola is not None):
        jobs = []
        for role in ROLES:
            for mine, theirs in ((ally, enemy), (enemy, ally)):
                champ = mine.get(role)
                if not champ:
                    continue
                if opgg is not None:
                    jobs.append(lambda c=champ, r=role: opgg.champion_build(c, r))
                    if theirs.get(role):
                        jobs.append(lambda c=champ, o=theirs[role], r=role: opgg.matchup_guide(c, o, r))
                if lola is not None:
                    jobs.append(lambda c=champ, r=role: lola.item_sets(c, r, ev.scope.tier, ev.scope.region, ev.scope.patches[0]))
        if opgg is not None:
            opgg.prefetch(jobs, timeout=10)
        else:
            for job in jobs:
                job()
    ev.fetch = False  # a partir de aquí, solo lo que ya esté en caché (lo que falte llega en segundo plano)
    cat = getattr(svc.dd, "item_catalog", None) or None
    champ_info = champ_info_from(svc.dd)
    comp = {"ally": _composition(svc, ev, ally), "enemy": _composition(svc, ev, enemy)}
    out: dict[str, list] = {"ally": [], "enemy": []}
    for side, mine, theirs in (("ally", ally, enemy), ("enemy", enemy, ally)):
        players = {c: r for r, c in mine.items() if c}
        rival_comp = comp["enemy" if side == "ally" else "ally"]
        plan = _team_plan(svc, players, [c for c in theirs.values() if c], comp[side])
        for role in ROLES:
            if mine.get(role):
                p = _player(svc, ev, cat, champ_info, side, role, mine[role], theirs, rival_comp, plan)
                if role == "SUPPORT":
                    holders = sum(1 for v in plan.values() if v.get("anti_heal") is not None)
                    p["team_items"] = _support_items(cat, _Context(svc, [c for c in theirs.values() if c], rival_comp, champ_info), holders)
                out[side].append(p)
    return out
