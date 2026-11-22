"""Build recomendada para cada jugador del draft, según su rival de línea y el equipo rival.

Para cada campeón elegido:

1. **Base**: la build de OP.GG **contra su rival de línea** si ya está elegido (objetos clave, botas, inicial, runas,
   hechizos y habilidades); si no, su build más jugada en ese rol.
2. **Ajustes por el draft rival** (cada uno con su porqué y los campeones que lo provocan): Heridas graves contra
   curaciones fuertes, botas y resistencias según el daño rival (físico/mágico) y su control, penetración contra
   mucha primera línea y un objeto defensivo contra asesinos. Los objetos salen, siempre que se pueda, de los que ese
   campeón compra de verdad (los más jugados de OP.GG); si no hay ninguno, del catálogo de objetos del parche.
3. **Respaldo**: lo que construyen sus OTPs (``data/otps``) y los objetos con su winrate en tu base de datos.

Sin datos no se inventa nada: cada parte que falta se omite.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from app.analytics.draft import _composition
from app.analytics.evidence import Evidence
from app.ingest.otp_details import FILES_DIR, TIER_ORDER, _slug
from app.riot.routing import ROLES

# Campeones con curaciones o robo de vida que deciden peleas (los que justifican Heridas graves pronto).
HEAVY_HEAL = {
    "Aatrox", "Soraka", "Yuumi", "Vladimir", "Sylas", "Warwick", "Dr. Mundo", "Swain", "Briar", "Illaoi", "Nami",
    "Sona", "Seraphine", "Olaf", "Trundle", "Volibear", "Gwen", "Nasus", "Maokai", "Zac", "Fiddlesticks", "Kayn",
    "Senna", "Milio", "Renata Glasc", "Viego", "Darius", "Sett", "Irelia", "Aphelios", "Samira", "Kindred",
}
# Ids de objetos conocidos como respaldo cuando el campeón no tiene ninguno parecido entre sus más jugados.
STEELCAPS, MERCURY = 3047, 3111
ZHONYA, GUARDIAN_ANGEL, MAW = 3157, 3026, 3156
PHYS_HEAVY, MAGIC_HEAVY = 60, 60  # % del daño rival para pedir armadura o resistencia mágica
EXTREME = 70  # para los carries (blandos), solo si es así de extremo
TANKY_FRONT = 1.15  # primera línea rival frente a la media
CC_HEAVY = 1.25
MIN_OWN_GAMES = 30
ANTI_HEAL_PER_TEAM = 2  # con 2 jugadores con Heridas graves basta; más es oro tirado
ANTI_HEAL_ORDER = ("ADC", "TOP", "MID", "JUNGLE")  # quién la compra antes (pega más y más seguido)


def _style(cat, items: list[int], tags: list[str]) -> str:
    """ap / ad / tank según sus objetos clave (y sus clases de Data Dragon si no hay objetos)."""
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


def _pick(cat, pool: list[int], test, style: str, exclude: set[int]) -> int | None:
    """El primero de los más jugados por ese campeón que cumpla ``test``; si no, uno completo del catálogo."""
    for i in pool:
        if i not in exclude and cat and cat.is_legendary(i) and test(i):
            return i
    if not cat:
        return None
    want = {"ap": "SpellDamage", "ad": "Damage", "tank": "Health"}[style]
    options = [
        (cat.cost(i), i)
        for i, it in cat.items.items()
        if i not in exclude and it.get("buyable") and not it.get("champion") and cat.is_legendary(i) and want in it["tags"] and test(i)
    ]
    return min(options)[1] if options else None


def _otp_build(svc, champ: int, role: str) -> dict | None:
    dd = svc.dd
    try:
        summary = json.loads((Path(svc.settings.data_dir) / FILES_DIR / _slug(dd.name(champ)) / "resumen.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    by_tier = (((summary.get("roles") or {}).get(role) or {}).get("by_tier")) or {}
    for tier in TIER_ORDER:  # el elo más alto con datos
        block = by_tier.get(tier)
        if block and block.get("games", 0) >= 20:
            first = {key: (block.get(key) or [None])[0] for key in ("first_legendary", "build_paths", "skill_max", "keystones", "summoner_spells")}
            return {
                "tier": tier, "games": block["games"], "players": block.get("players"),
                "first_item": first["first_legendary"], "path": first["build_paths"], "skill_max": first["skill_max"],
                "keystone": first["keystones"], "spells": first["summoner_spells"],
            }
    return None


def _own_items(svc, cat, champ: int, role: str) -> dict | None:
    """Objetos completos que más se compran con ese campeón en ese rol en tu base de datos, con su winrate."""
    rows = svc.db.query("SELECT items, win FROM participants WHERE champ = ? AND role = ? AND items IS NOT NULL", (champ, role))
    if len(rows) < MIN_OWN_GAMES:
        return None
    count, wins = Counter(), Counter()
    for r in rows:
        try:
            items = {int(x) for x in str(r["items"]).split(",") if x.strip().isdigit() and int(x)}
        except ValueError:
            continue
        for i in items:
            if cat and cat.is_legendary(i):
                count[i] += 1
                wins[i] += int(r["win"] or 0)
    top = [{"id": i, "games": n, "wr": round(100 * wins[i] / n, 1)} for i, n in count.most_common(6) if n >= 10]
    return {"games": len(rows), "items": top} if top else None


def _player(svc, ev, cat, side: str, role: str, champ: int, mine: dict, theirs: dict, enemy_comp: dict | None) -> dict:
    dd = svc.dd
    tags = list(((dd.champions.get(champ) or {}).get("tags")) or [])
    opp = theirs.get(role)
    opgg = svc.opgg if getattr(svc, "opgg", None) is not None and svc.opgg.enabled else None
    base: dict = {}
    if opgg is not None and opp:
        guide = opgg.matchup_guide(champ, opp, role, allow_fetch=ev.fetch) or {}
        core = (guide.get("core_items") or [{}])[0]
        if core.get("ids"):
            rune = (guide.get("runes") or [{}])[0]
            skills = (guide.get("skills") or [{}])[0]
            base = {
                "source": "matchup", "opponent": opp,
                "core": [int(i) for i in core.get("ids") or []],
                "games": core.get("games"), "wins": core.get("wins"),
                "boots": [int(i) for i in ((guide.get("boots") or [{}])[0].get("ids") or [])][:1],
                "starter": [int(i) for i in ((guide.get("starter_items") or [{}])[0].get("ids") or [])],
                "spells": [int(i) for i in ((guide.get("spells") or [{}])[0].get("ids") or [])],
                "runes": {"keystone": rune.get("keystone"), "primary": rune.get("primary"), "secondary": rune.get("secondary"), "ids": rune.get("ids"), "names": rune.get("names")} if rune else None,
                "skill_order": skills.get("order"),
            }
    general = opgg.champion_build(champ, role, allow_fetch=ev.fetch) if opgg is not None else None
    if not base and general and general.get("core"):
        base = {
            "source": "general", "core": general["core"], "games": general.get("core_games"), "wins": general.get("core_wins"),
            "boots": general.get("boots")[:1] if general.get("boots") else [], "starter": general.get("starter") or [],
            "skill_max": general.get("skill_max"),
        }
    pool = [i for i in [*(base.get("core") or []), *[o["id"] for o in (general or {}).get("fourth") or []], *[o["id"] for o in (general or {}).get("popular") or []]] if i]
    style = _style(cat, base.get("core") or pool[:3], tags)
    squishy = _squishy(style, tags)

    # Ajustes por el draft rival.
    situational = []
    used = set(base.get("core") or [])
    enemies = [c for c in theirs.values() if c]
    names = {c: dd.name(c) for c in enemies}
    profile = (enemy_comp or {}).get("damage_profile") or {}
    phys, magic = profile.get("phys") or 0, profile.get("magic") or 0
    complete = len(enemies) >= 3  # con menos de 3 rivales, el perfil de daño aún no es fiable

    healers = [c for c in enemies if names[c] in HEAVY_HEAL]
    if healers and role != "SUPPORT":
        item = _pick(cat, pool, cat.anti_heal if cat else (lambda i: False), style, used)
        if item:
            situational.append({"item": item, "kind": "anti_heal", "reason": f"Heridas graves contra {', '.join(names[c] for c in healers)}: se curan mucho y sin ellas las peleas largas las ganan ellos.", "vs": healers})
            used.add(item)

    base_boots = (base.get("boots") or [None])[0]
    if complete and not squishy:
        want = STEELCAPS if phys >= PHYS_HEAVY else MERCURY if (magic >= MAGIC_HEAVY or ((enemy_comp or {}).get("cc") or 0) >= CC_HEAVY) else None
    elif complete:
        want = STEELCAPS if phys >= EXTREME else MERCURY if magic >= EXTREME else None
    else:
        want = None
    if want and want != base_boots and (not cat or want in cat):
        why = (
            f"El {phys:.0f} % del daño rival es físico." if want == STEELCAPS
            else f"El {magic:.0f} % del daño rival es mágico" + (" y tienen mucho control" if ((enemy_comp or {}).get("cc") or 0) >= CC_HEAVY else "") + "."
        )
        situational.append({"item": want, "kind": "boots", "replaces": base_boots, "reason": why})

    if complete and not squishy:
        if phys >= PHYS_HEAVY:
            item = _pick(cat, pool, lambda i: "armor" in cat.resist(i), style, used) if cat else None
            if item:
                situational.append({"item": item, "kind": "armor", "reason": f"Armadura: el {phys:.0f} % del daño rival es físico."})
                used.add(item)
        elif magic >= MAGIC_HEAVY:
            item = _pick(cat, pool, lambda i: "mr" in cat.resist(i), style, used) if cat else None
            if item:
                situational.append({"item": item, "kind": "mr", "reason": f"Resistencia mágica: el {magic:.0f} % del daño rival es mágico."})
                used.add(item)

    tanks = [c for c in enemies if "Tank" in ((dd.champions.get(c) or {}).get("tags") or [])]
    if complete and style in ("ad", "ap") and (len(tanks) >= 2 or ((enemy_comp or {}).get("frontline") or 0) >= TANKY_FRONT):
        pen = "MagicPenetration" if style == "ap" else "ArmorPenetration"
        item = _pick(cat, pool, lambda i: pen in cat.tags(i), style, used) if cat else None
        if item:
            situational.append({"item": item, "kind": "penetration", "reason": f"Penetración contra su primera línea ({', '.join(names[c] for c in tanks) or 'mucha vida y resistencias'}).", "vs": tanks})
            used.add(item)

    assassins = [c for c in enemies if "Assassin" in ((dd.champions.get(c) or {}).get("tags") or [])]
    if squishy and len(assassins) >= 2 and role != "SUPPORT":
        options = [ZHONYA] if style == "ap" else [GUARDIAN_ANGEL, MAW]
        item = next((i for i in options if (not cat or i in cat) and i not in used), None)
        if item:
            situational.append({"item": item, "kind": "defensive", "reason": f"Defensivo contra sus asesinos ({', '.join(names[c] for c in assassins)}): te buscarán a ti.", "vs": assassins})

    return {
        "side": side,
        "role": role,
        "champ": champ,
        "style": style,
        "opponent": opp,
        "base": base or None,
        "situational": situational,
        "otp": _otp_build(svc, champ, role),
        "own": _own_items(svc, cat, champ, role),
    }


def recommend(svc, f, ally: dict[str, int], enemy: dict[str, int], sources: str = "all") -> dict:
    """Build recomendada para cada campeón elegido de los dos equipos."""
    ev = Evidence(svc, f, sources)
    opgg = svc.opgg if getattr(svc, "opgg", None) is not None and svc.opgg.enabled else None
    if opgg is not None and ev.scope:
        jobs = []
        for role in ROLES:
            for mine, theirs in ((ally, enemy), (enemy, ally)):
                champ = mine.get(role)
                if champ:
                    jobs.append(lambda c=champ, r=role: opgg.champion_build(c, r))
                    if theirs.get(role):
                        jobs.append(lambda c=champ, o=theirs[role], r=role: opgg.matchup_guide(c, o, r))
        opgg.prefetch(jobs, timeout=10)
    ev.fetch = False  # a partir de aquí, solo lo que ya esté en caché (lo que falte llega en segundo plano)
    cat = getattr(svc.dd, "item_catalog", None) or None
    comp_ally, comp_enemy = _composition(svc, ev, ally), _composition(svc, ev, enemy)
    out = {"ally": [], "enemy": []}
    for role in ROLES:
        if ally.get(role):
            out["ally"].append(_player(svc, ev, cat, "ally", role, ally[role], ally, enemy, comp_enemy))
        if enemy.get(role):
            out["enemy"].append(_player(svc, ev, cat, "enemy", role, enemy[role], enemy, ally, comp_ally))
    for side in ("ally", "enemy"):
        _share_anti_heal(out[side], svc)
    return out


def _share_anti_heal(players: list[dict], svc) -> None:
    """Heridas graves solo para los 2 mejor colocados del equipo (ADC, top, mid, jungla); al resto se le quita."""
    holders = [p for p in players if any(s["kind"] == "anti_heal" for s in p["situational"])]
    holders.sort(key=lambda p: ANTI_HEAL_ORDER.index(p["role"]) if p["role"] in ANTI_HEAL_ORDER else 9)
    keep = holders[:ANTI_HEAL_PER_TEAM]
    names = " y ".join(svc.dd.name(p["champ"]) for p in keep)
    for p in holders:
        if p in keep:
            for s in p["situational"]:
                if s["kind"] == "anti_heal" and len(keep) > 1:
                    s["reason"] += f" En tu equipo la llevan {names}: con dos basta."
        else:
            p["situational"] = [s for s in p["situational"] if s["kind"] != "anti_heal"]
