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

**Plan de partida.** Del draft sale si vuestro equipo gana en partidas cortas o largas (curva por duración) y qué
líneas gana cada uno (OP.GG: quién gana la línea; si no, el WR del cruce). Con eso cada jugador tiene un plan
(p. ej. jungla con su mid ganando, que gana a la jungla rival, en un draft que pierde el late: invadir y build de bola
de nieve) y, si el campeón tiene **de verdad** esa otra build (≥ 10 % de sus builds completas empiezan así y no gana más
de 1,5 pp menos), se cambia de camino. Nunca se inventa una build: solo se elige entre las que se juegan.

**Reglas que nunca se rompen** (auditadas con 910 builds reales de 41 drafts, 25 de ellos por escenario: rival mágico,
físico, con curanderos, con tanques o con asesinos, cada uno con plan normal, de bola de nieve y para escalar):

* Un objeto que se compra de primero (posición media < 1,5, p. ej. Soberbia) no se recomienda de 3.º a 5.º.
* El uso de cada objeto se mide en las builds que llevan tu núcleo; lo que casi nunca va con él (< 1 %) no entra.
* Dos objetos que casi nunca van juntos en sus builds reales (< 10 % de lo esperado) no se recomiendan juntos
  (caminos distintos, como Kayn rojo y azul). Y siempre, aunque no haya datos del campeón, los límites del juego:
  mismo componente limitado (Últimas palabras: Dominik + Recordatorio letal; Tiamat: dos hidras) o misma pasiva única
  (Salvavidas: Sterak + Fauces).
* Las Heridas graves que le tocan a un jugador nunca chocan con su núcleo.
* Los objetos transformados (Muramana, Abrazo del serafín) se recomiendan como el que se compra.
* El resto de la build va en el orden en que se compra de verdad.
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
# Orden de compra (Lolalytics, posición media 1-5 en sus builds completas). Un objeto que se compra de primero (Soberbia
# en Hecarim: 1,08 de media) es de bola de nieve: de 4.º o 5.º no tiene sentido aunque lo lleve un tercio de las builds.
RUSH_POS = 1.5
MIN_POS_GAMES = 100
MIN_CORE_GAMES = 300  # builds con tu núcleo para medir qué se compra junto a él (si no, se usa el total)
MIN_WITH_CORE = 0.01  # lo que casi nunca se compra con tu núcleo no entra (ni como situacional): es de otra build
# Dos objetos que casi nunca van juntos en sus builds reales (menos del 10 % de lo esperado si fueran independientes)
# comparten pasiva única (Recuerdos de lord Dominik + Recordatorio letal) o son de caminos distintos (Kayn rojo y azul).
EXCLUSIVE_RATIO = 0.1
# Límites del juego, comprobados con las builds de 119 campeones y líneas (Lolalytics): los objetos hechos con Últimas
# palabras (Dominik, Recordatorio letal, Serylda) van juntos el 0,7 % de lo esperado y las hidras (Tiamat) el 1,6 %;
# los que comparten pasiva única (Salvavidas: Sterak, Fauces, Arcoescudo) casi nunca. Se aplican aunque no haya datos
# del campeón. Si los datos sí los muestran juntos (Manamune + Bastón del arcángel, 98 %), se respetan los datos.
LIMITED_COMPONENTS = {3035: "Últimas palabras", 3077: "Tiamat"}
SEEN_TOGETHER = 0.3
EXCLUSIVE_MIN_SHARE = 0.03
EXCLUSIVE_MIN_EXPECTED = 30
# Plan de partida: diferencia de WR del equipo entre partidas cortas (< 25 min) y largas (≥ 35 min) para hablar de
# «partida corta» o «larga»; y una build alternativa real (≥ 10 % de sus builds empiezan así) para cambiar de camino.
TEMPO_PP = 4.0
PATH_MIN_SHARE = 0.10
PATH_MAX_WR_LOSS = 0.015
# Piedras angulares: de bola de nieve (dañar y rematar pronto) y de pelea larga o para aguantar. El plan elige entre las
# páginas reales del campeón (OP.GG contra ese rival y la más jugada y la que más gana en Lolalytics), nunca inventa una.
AGGRESSIVE_KEYSTONES = {8112, 8128, 9923, 8369, 8124, 8230}  # Electrocutar, Cosecha, Granizo, Primer golpe, Depredador, azules
DURABLE_KEYSTONES = {8010, 8437, 8008, 8021, 8439, 8005, 8465}  # Conquistador, Garras, Compás, Pies veloces, Réplica, Ataque, Guardián
RUNES_MAX_WR_LOSS = 1.5  # puntos de WR que se aceptan perder para seguir el plan
MIN_MATCHUP_RUNE_GAMES = 100
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


def _otp_role(svc, champ: int, role: str) -> dict:
    """Lo agregado de los OTPs de ese campeón en ese rol (``resumen.json``), o ``{}``."""
    try:
        summary = json.loads((Path(svc.settings.data_dir) / FILES_DIR / _slug(svc.dd.name(champ)) / "resumen.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return (summary.get("roles") or {}).get(role) or {}


OTP_PLAN_MIN_GAMES = 60  # partidas de OTPs de ese campeón y rol para sacar de ellas el plan
OTP_PLAN_MIN_SIDE = 25  # …y de cada lado de una comparación (por delante / por detrás, primero / no)
# Solo los dos que salen a la vez (minuto 8-9): el heraldo cae hacia el 16, cuando quien lo coge suele ir ya por
# delante, y saldría siempre como «el que más pesa» con cualquier campeón.
OBJ_LABEL = {"dragon": "el primer dragón", "horde": "las primeras larvas"}
OBJ_GRAMMAR = {"dragon": ("lo", "pesa"), "horde": ("las", "pesan")}
CAMP_LABEL = {"blue": "azul", "red": "rojo", "raptors": "aviarios", "wolves": "lobos", "krugs": "krugs", "gromp": "gromp"}


def _otp_lane_line(block: dict, name: str) -> dict | None:
    """Jungla: la línea que más decide las partidas de sus OTPs (su WR si va por delante o por detrás a los 15)."""
    swings = []
    for lane in ("TOP", "MID", "ADC"):
        li = (block.get("lanes_impact") or {}).get(lane) or {}
        a, b = li.get("ahead") or {}, li.get("behind") or {}
        if (a.get("games") or 0) >= OTP_PLAN_MIN_SIDE and (b.get("games") or 0) >= OTP_PLAN_MIN_SIDE:
            swings.append((a["wr"] - b["wr"], lane, a["wr"], b["wr"]))
    if len(swings) < 2:
        return None
    swings.sort(reverse=True)
    top, second = swings[0], swings[1]
    if top[0] - second[0] >= 5:
        return {"topic": f"OTPs de {name}", "text": (
            f"La línea que más decide sus partidas es {LANE_NAME[top[1]]}: ganan el {top[2]:.0f} % si va por delante a los 15 "
            f"y el {top[3]:.0f} % si va por detrás (en {LANE_NAME[second[1]]}, {second[2]:.0f} % frente a {second[3]:.0f} %). "
            "Si puedes elegir, tus primeros ganks y contraganks, ahí.")}
    return None


def _otp_objective_line(block: dict) -> dict | None:
    """El primer objetivo (dragón o larvas) que más pesa en las partidas de sus OTPs."""
    objs = []
    for key, label in OBJ_LABEL.items():
        o = (block.get("objectives_first") or {}).get(key) or {}
        games, share = o.get("games") or 0, (o.get("ally_first_pct") or 0) / 100
        if games * share >= OTP_PLAN_MIN_SIDE and games * (1 - share) >= OTP_PLAN_MIN_SIDE and o.get("wr_ally_first") is not None:
            objs.append((o["wr_ally_first"] - o["wr_enemy_first"], label, o, key))
    if len(objs) != 2:
        return None
    objs.sort(key=lambda x: -x[0])
    (gap, label, o, key), (gap2, label2, _, _) = objs
    if gap - gap2 >= 5:
        pron, verb = OBJ_GRAMMAR[key]
        return {"topic": "Primer objetivo", "text": (
            f"Entre {label} y {label2}, a sus OTPs les {verb} más {label}: ganan el {o['wr_ally_first']:.0f} % cuando {pron} coge su "
            f"equipo y el {o['wr_enemy_first']:.0f} % cuando {pron} coge el rival ({gap:.0f} puntos de diferencia; {label2}, {gap2:.0f}). "
            "Si tenéis que elegir lado al empezar, ese.")}
    return None


def _otp_rhythm_line(block: dict) -> dict | None:
    """Jungla: el ritmo de sus OTPs (primer campamento, primer gank y primera vuelta a base)."""
    m = block.get("metrics") or {}
    camp = (block.get("first_camp") or [None])[0]
    gank, recall = (m.get("first_gank_min") or {}).get("mean"), (m.get("first_recall_min") or {}).get("mean")
    bits = []
    if camp and camp.get("pct", 0) >= 30:
        bits.append(f"empiezan por {CAMP_LABEL.get(camp['key'], camp['key'])} ({camp['pct']:.0f} %)")
    if gank:
        bits.append(f"primer gank hacia el {gank:.1f}".replace(".", ","))
    if recall:
        bits.append(f"primera vuelta a base hacia el {recall:.1f}".replace(".", ","))
    if bits:
        return {"topic": "Ritmo", "text": f"Sus OTPs {_join_names(bits)}."}
    return None


def _otp_plan_lines(svc, champ: int, role: str) -> list[dict]:
    """Lo que dicen las partidas de los OTPs de ese campeón: qué línea decide más sus partidas (para el jungla, a quién
    ayudar primero), qué primer objetivo pesa más y, de jungla, su ritmo (primer campamento, primer gank, primera vuelta).

    Son correlaciones (ir por delante en una línea también es consecuencia de ganar), así que solo se dicen cuando las
    muestras son grandes y la diferencia clara."""
    block = (_otp_role(svc, champ, role) or {}).get("all") or {}
    if (block.get("games") or 0) < OTP_PLAN_MIN_GAMES:
        return []
    name = svc.dd.name(champ)
    jungle = role == "JUNGLE"
    lines = [_otp_lane_line(block, name) if jungle else None, _otp_objective_line(block), _otp_rhythm_line(block) if jungle else None]
    return [line for line in lines if line]


def _otp_items(svc, champ: int, role: str) -> dict | None:
    role_data = _otp_role(svc, champ, role)
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


def _damage_group(magic: float) -> str:
    """Grupo de las estadísticas de los OTPs según la parte media de daño mágico del equipo rival (0-1): ``ap``,
    ``ad`` o ``mixed``."""
    if magic >= 0.5:
        return "ap"
    return "ad" if magic <= 0.3 else "mixed"


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
        self.groups: list[str] = self._otp_groups(champ_info)

    def _otp_groups(self, champ_info) -> list[str]:
        """Grupos de las estadísticas de los OTPs que coinciden con este equipo rival (por su daño, curaciones, tanques…)."""
        groups: list[str] = []
        if self.complete and champ_info:
            infos = [i for i in (champ_info(c) for c in self.enemies) if i]
            if infos:
                groups.append(_damage_group(sum(i["magic"] for i in infos) / len(infos)))
        if self.healers:
            groups.append("healers")
        if len(self.tanks) >= 2:
            groups.append("tanks")
        if len(self.assassins) >= 2:
            groups.append("assassins")
        return groups

    def who(self, champs: list[int]) -> str:
        return ", ".join(self.names[c] for c in champs)


def _with_core(lola: dict | None, core: list[int]) -> dict | None:
    """Qué se compra en las builds de Lolalytics que llevan el núcleo recomendado: ``{"items": {objeto: [partidas,
    victorias]}, "games": N}``. Mezclar caminos distintos (el de letalidad y el de luchador) daba builds sin sentido."""
    sets = (lola or {}).get("sets") or []
    need = {int(i) for i in core if i}
    if not sets or not need:
        return None
    items: dict[str, list[int]] = {}
    total = 0
    for ids, games, wins in sets:
        if not need <= set(ids):
            continue
        total += games
        for i in set(ids):
            cur = items.setdefault(str(i), [0, 0])
            cur[0] += games
            cur[1] += wins
    return {"items": items, "games": total} if total >= MIN_CORE_GAMES else None


def _add_candidate(out: dict[int, dict], cat, item, **data) -> None:
    """Añade a los candidatos (o completa) un objeto completo, en la forma en que se compra."""
    try:
        item = int(item)
    except (TypeError, ValueError):
        return
    if cat and item:
        item = cat.buyable_form(item)  # Muramana -> Manamune: lo que se puede comprar
    if not item or (cat and not cat.is_legendary(item)):
        return
    out.setdefault(item, {}).update(data)


def _add_opgg_candidates(out: dict[int, dict], cat, general: dict | None) -> None:
    """Las opciones de OP.GG (objetos populares y de 4.º, 5.º y 6.º hueco) con su tasa de elección."""
    for key in ("popular", "fourth", "fifth", "sixth"):
        extra = {"opgg_after_core": True} if key != "popular" else {}
        for o in (general or {}).get(key) or []:
            if o.get("id"):
                _add_candidate(out, cat, o["id"], opgg=max(out.get(int(o["id"]), {}).get("opgg", 0.0), float(o.get("pick_rate") or 0)), **extra)


def _add_lola_candidates(out: dict[int, dict], cat, lola: dict) -> None:
    """Los objetos de sus builds completas en Lolalytics (en al menos el 1 %), con su uso, WR y posición de compra."""
    total = lola["games"]
    pos = lola.get("pos") or {}
    for i, (games, wins) in (lola.get("items") or {}).items():
        if games / total >= 0.01:
            _add_candidate(out, cat, i, lola=games / total, lola_games=games, lola_wins=wins, lola_order=pos.get(str(i)),
                           lola_pos=pos.get(str(i)) if games >= MIN_POS_GAMES else None)


def _off_core(data: dict) -> None:
    """Ajusta un candidato a su uso con el núcleo: lo que no aparece con él cuenta 0 y, si casi nunca va con él y no es
    del núcleo, queda fuera de la build."""
    if "lola" not in data and "core" not in data:
        data["lola"] = 0.0  # solo lo lista OP.GG: en las builds con este núcleo no aparece
    if "lola" in data and not data.get("with_core"):
        data["lola"] = 0.0  # nunca va con este núcleo
    if "lola" in data and data["lola"] < MIN_WITH_CORE and "core" not in data:
        data["off_core"] = True  # con datos de su núcleo, mandan esos (las listas de OP.GG van tras el suyo)


def _core_usage(out: dict[int, dict], with_core: dict) -> None:
    """Con datos de las builds que llevan su núcleo, el uso de cada objeto se mide ahí (y lo que casi nunca va con él
    queda fuera)."""
    for i, (games, _) in with_core["items"].items():
        if int(i) in out and "lola" in out[int(i)]:
            out[int(i)]["lola"] = games / with_core["games"]  # uso junto a su núcleo
            out[int(i)]["with_core"] = True
    for data in out.values():
        _off_core(data)


def _candidates(cat, base: dict, general: dict | None, lola: dict | None, otp: dict | None, own: dict | None, with_core: dict | None = None) -> dict[int, dict]:
    """Objetos completos que se le compran a ese campeón, con lo que dice cada fuente.

    ``with_core``: lo que se compra junto a su núcleo (Lolalytics). Si lo hay, el uso de cada objeto se mide ahí.
    """
    out: dict[int, dict] = {}
    for rank, i in enumerate(base.get("core") or []):
        _add_candidate(out, cat, i, core=rank)
    _add_opgg_candidates(out, cat, general)
    if lola and lola.get("games"):
        _add_lola_candidates(out, cat, lola)
        if with_core:
            _core_usage(out, with_core)
    for i in ((otp or {}).get("groups", {}).get("all", {}).get("items") or {}):
        _add_candidate(out, cat, i, otp_seen=True)
    for i, (games, wins) in ((own or {}).get("items") or {}).items():
        _add_candidate(out, cat, i, own_games=games, own_wins=wins)
    return out


def _wr_text(delta: float | None, games: int) -> str:
    """El WR de las builds de 5 objetos es alto para todos (son partidas largas): solo se dice si va claramente mejor
    o peor que sus otras opciones, nunca el número suelto (un «65 % WR» engaña)."""
    if delta is None or games < 300 or abs(delta) < 2:
        return ""
    return f", {abs(delta):.0f} pp {'mejor' if delta > 0 else 'peor'} que sus otras opciones en Lolalytics"


def _otp_lift(score: float, key: str, g: dict, base_share: float, reasons: list[tuple[float, str]]) -> float:
    """Lo que suma (o resta) un grupo de las estadísticas de sus OTPs: cuánto más (o menos) lo compran contra ese tipo
    de equipo que en general."""
    games, wins = (g.get("items") or {}).get(key, [0, 0])
    lift = games / g["games"] - base_share
    if abs(lift) >= 0.03:
        score += 4.0 * lift
        if lift > 0 and games >= 8:
            reasons.append((lift * 10, f"Sus OTPs lo compran más contra equipos así ({100 * games / g['games']:.0f} % frente a {base_share * 100:.0f} %, {100 * wins / games:.0f} % WR)"))
    return score


def _otp_score(score: float, item: int, otp: dict | None, ctx: _Context, reasons: list[tuple[float, str]]) -> float:
    """Sus OTPs: cuánto más (o menos) lo compran contra equipos como este."""
    groups = (otp or {}).get("groups") or {}
    all_g = groups.get("all") or {}
    if not all_g.get("games"):
        return score
    base_share = (all_g.get("items") or {}).get(str(item), [0, 0])[0] / all_g["games"]
    for name in ctx.groups:
        g = groups.get(name) or {}
        if (g.get("games") or 0) < 25:
            continue
        score = _otp_lift(score, str(item), g, base_share, reasons)
    return score


def _resist_fit(share: float) -> float:
    """Cuánto pide el draft una resistencia según la parte del daño rival de ese tipo (de −0,5 a 1,2)."""
    return max(-0.5, min(1.2, (share - 50) / 20))


def _resist_score(score: float, item: int, cat, squishy: bool, ctx: _Context, reasons: list[tuple[float, str]]) -> float:
    """Resistencias según el daño rival (a un carry blando solo si es extremo) y sobrevivir a sus asesinos."""
    resist = cat.resist(item)
    if ctx.complete and "mr" in resist and (not squishy or ctx.magic >= 70):
        fit = _resist_fit(ctx.magic)
        score += fit
        if fit > 0.3:
            reasons.append((fit, f"Resistencia mágica: el {ctx.magic:.0f} % del daño rival es mágico"))
    if ctx.complete and "armor" in resist and (not squishy or ctx.phys >= 70):
        fit = _resist_fit(ctx.phys)
        score += fit
        if fit > 0.3:
            reasons.append((fit, f"Armadura: el {ctx.phys:.0f} % del daño rival es físico"))
    if squishy and len(ctx.assassins) >= 2 and resist:
        score += 0.6
        reasons.append((0.6, f"Para sobrevivir a sus asesinos ({ctx.who(ctx.assassins)})"))
    return score


def _team_score(score: float, item: int, cat, style: str, squishy: bool, ctx: _Context, team: dict, reasons: list[tuple[float, str]]) -> float:
    """Penetración contra su primera línea, Heridas graves (solo si le tocan) y aguante si a tu equipo le falta primera
    línea."""
    tags = set(cat.tags(item))
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
    return score


def _score(item: int, data: dict, cat, style: str, squishy: bool, ctx: _Context, otp: dict | None, lola: dict | None, team: dict) -> tuple[float, str]:
    """Puntuación del objeto para este draft y la razón principal."""
    reasons: list[tuple[float, str]] = []
    use = max(data.get("lola", 0.0), data.get("opgg", 0.0))
    score = 2.0 * use
    # Winrate en Lolalytics frente a la media de sus otros objetos (encogido hacia 0 con pocas partidas).
    delta = None
    if lola and data.get("lola_games"):
        totals = list(lola["items"].values())
        mean = sum(w for _, w in totals) / max(1, sum(g for g, _ in totals))
        delta = (data["lola_wins"] / data["lola_games"] - mean) * 100
        score += 0.04 * delta * data["lola_games"] / (data["lola_games"] + 300)
    if use >= 0.1:
        of = "de sus builds con ese núcleo" if data.get("with_core") and use == data.get("lola") else "de sus builds"
        reasons.append((0.1, f"De los más comprados con él ({use * 100:.0f} % {of}{_wr_text(delta, data.get('lola_games') or 0)})"))
    score = _otp_score(score, item, otp, ctx, reasons)
    if cat:
        score = _resist_score(score, item, cat, squishy, ctx, reasons)
        score = _team_score(score, item, cat, style, squishy, ctx, team, reasons)
    return score, (max(reasons)[1] if reasons else "")


def _usual_boots(cat, base: dict, general: dict | None, lola: dict | None) -> int | None:
    """Sus botas de siempre: las de OP.GG (contra ese rival o en su build general) o las más compradas en Lolalytics."""
    usual = (base.get("boots") or (general or {}).get("boots") or [None])[0]
    if not usual and lola:
        boots = [(g, int(i)) for i, (g, _) in (lola.get("boots") or {}).items() if cat and cat.is_boots(int(i))]
        usual = max(boots)[1] if boots else None
    return usual


def _draft_boots(squishy: bool, ctx: _Context) -> tuple[int | None, str]:
    """Las botas que pide el daño rival (blindadas o de mercurio) y por qué; ``(None, "")`` si no pide ningunas."""
    if not ctx.complete:
        return None, ""
    limit = 70 if squishy else 60
    if ctx.phys >= limit:
        return STEELCAPS, f"El {ctx.phys:.0f} % del daño rival es físico"
    if ctx.magic >= limit or (not squishy and ctx.cc >= 1.25):
        return MERCURY, f"El {ctx.magic:.0f} % del daño rival es mágico" + (" y tienen mucho control" if ctx.cc >= 1.25 else "")
    return None, ""


def _otps_keep_boots(want: int, otp: dict | None, ctx: _Context) -> bool:
    """¿Sus OTPs, contra equipos así, casi nunca se cambian a esas botas? Entonces se respetan las suyas."""
    groups = (otp or {}).get("groups") or {}
    g = next((groups[n] for n in ctx.groups if (groups.get(n) or {}).get("games", 0) >= 25), None)
    return bool(g and (g.get("boots") or {}).get(str(want), [0])[0] / g["games"] < 0.1)


def _boots(cat, base: dict, general: dict | None, lola: dict | None, otp: dict | None, squishy: bool, ctx: _Context) -> dict | None:
    """Las botas: las suyas de siempre salvo que el draft pida otras (blindadas o de mercurio)."""
    usual = _usual_boots(cat, base, general, lola)
    want, why = _draft_boots(squishy, ctx)
    if want and usual and want != usual and _otps_keep_boots(want, otp, ctx):
        want = None
    if want and (not cat or want in cat):
        return {"item": want, "reason": why, "situational": want != usual, "replaces": usual if want != usual else None}
    if usual:
        return {"item": usual, "reason": "Sus botas de siempre", "situational": False}
    return None


def _first_ids(rows: list[dict] | None) -> list[int]:
    """Los ids de la primera opción de una lista de la guía de OP.GG (botas, objetos iniciales, hechizos)."""
    return [int(i) for i in ((rows or [{}])[0].get("ids") or [])]


def _matchup_guide_core(ev, opgg, champ: int, role: str, opp: int | None) -> tuple[dict, dict]:
    """La guía de OP.GG contra ese rival y su núcleo más jugado (``{}`` si no hay)."""
    if opgg is None or not opp:
        return {}, {}
    guide = opgg.matchup_guide(champ, opp, role, allow_fetch=ev.fetch) or {}
    return guide, (guide.get("core_items") or [{}])[0]


def _matchup_base(guide: dict, core: dict, opp: int | None) -> dict:
    """La build contra ese rival (guía de OP.GG) como base."""
    return {
        "source": "matchup", "opponent": opp, "core": [int(i) for i in core.get("ids") or []],
        "games": core.get("games"), "wins": core.get("wins"),
        "boots": _first_ids(guide.get("boots"))[:1],
        "starter": _first_ids(guide.get("starter_items")),
        "spells": _first_ids(guide.get("spells")),
        "runes": (guide.get("runes") or [None])[0], "rune_pages": guide.get("runes") or [],
        "skill_order": ((guide.get("skills") or [{}])[0]).get("order"),
    }


def _general_base(general: dict) -> dict:
    """Su build general de OP.GG como base."""
    return {
        "source": "general", "core": general["core"], "games": general.get("core_games"), "wins": general.get("core_wins"),
        "boots": (general.get("boots") or [])[:1], "starter": general.get("starter") or [], "spells": general.get("spells") or [],
        "runes": general.get("runes"), "skill_max": general.get("skill_max"),
    }


def _base(ev, opgg, champ: int, role: str, opp: int | None) -> tuple[dict, dict | None]:
    """La build de partida (la de OP.GG contra ese rival si tiene partidas suficientes; si no, su build general) y su
    build general."""
    guide, core = _matchup_guide_core(ev, opgg, champ, role, opp)
    base: dict = {}
    if core.get("ids") and (core.get("games") or 0) >= MIN_MATCHUP_GAMES:
        base = _matchup_base(guide, core, opp)
    general = opgg.champion_build(champ, role, allow_fetch=ev.fetch) if opgg is not None else None
    if not base and general and general.get("core"):
        base = _general_base(general)
        if opp and core.get("ids"):
            base["thin_matchup"] = core.get("games") or 0  # había núcleo contra ese rival, pero con pocas partidas
    return base, general


def dd_item(cat, item: int) -> str:
    return cat.name(item) if cat else f"#{item}"


class _Exclusive:
    """¿Estos dos objetos casi nunca van juntos en las builds reales del campeón? (pasiva única o caminos distintos)."""

    def __init__(self, lola: dict | None, cat=None) -> None:
        self.cat = cat
        self.sets = [(set(ids), g) for ids, g, _ in (lola or {}).get("sets") or []]
        self.total = sum(g for _, g in self.sets)
        self._single: dict[int, int] = {}
        self._cache: dict[tuple[int, int], bool] = {}

    def _games(self, item: int) -> int:
        if item not in self._single:
            self._single[item] = sum(g for ids, g in self.sets if item in ids)
        return self._single[item]

    def __call__(self, a: int, b: int) -> bool:
        if a == b:
            return False
        key = (min(a, b), max(a, b))
        if key not in self._cache:
            self._cache[key] = self._decide(a, b)
        return self._cache[key]

    def _decide(self, a: int, b: int) -> bool:
        expected = together = 0.0
        if self.total:
            sa, sb = self._games(a) / self.total, self._games(b) / self.total
            expected = sa * sb * self.total
            together = sum(g for ids, g in self.sets if a in ids and b in ids)
            if expected >= EXCLUSIVE_MIN_EXPECTED and together >= SEEN_TOGETHER * expected:
                return False  # los datos los muestran juntos: se pueden llevar
            if sa >= EXCLUSIVE_MIN_SHARE and sb >= EXCLUSIVE_MIN_SHARE and expected >= EXCLUSIVE_MIN_EXPECTED and together < EXCLUSIVE_RATIO * expected:
                return True
        return self._game_limit(a, b)

    def _game_limit(self, a: int, b: int) -> bool:
        """Límite del juego: mismo componente limitado (Últimas palabras, Tiamat) o misma pasiva única."""
        cat = self.cat
        if not cat:
            return False
        ia, ib = cat.get(a), cat.get(b)
        if set(ia.get("from") or []) & set(ib.get("from") or []) & set(LIMITED_COMPONENTS):
            return True
        return bool(set(ia.get("passives") or []) & set(ib.get("passives") or []))


def _aggressive_item(cat, item: int) -> bool:
    """Objeto de bola de nieve: daño con penetración o letalidad, sin defensas ni crítico (Soberbia, Youmuu, Rencor de
    Serylda, Arco axiomático, Llamasombría). Que se compre de primero no lo hace agresivo: el primer objeto de cualquier
    build se compra de primero."""
    if not cat:
        return False
    tags = set(cat.tags(item))
    return (bool(tags & {"Damage", "SpellDamage"}) and bool(tags & {"ArmorPenetration", "MagicPenetration"})
            and not tags & {"Health", "Armor", "SpellBlock", "CriticalStrike"})


def _second_item(rows: list) -> int | None:
    """El 2.º objeto más comprado en las builds que empiezan igual."""
    seconds = Counter()
    for ids, g, _ in rows:
        if len(ids) > 1:
            seconds[int(ids[1])] += g
    return seconds.most_common(1)[0][0] if seconds else None


def _paths(cat, lola: dict | None) -> list[dict]:
    """Las builds reales del campeón según su primer objeto (Lolalytics): cuántas empiezan así, su 2.º objeto y su WR."""
    sets = (lola or {}).get("sets") or []
    total = sum(g for _, g, _ in sets)
    if not total:
        return []
    by_first: dict[int, list] = {}
    for ids, g, w in sets:
        if ids:
            by_first.setdefault(int(ids[0]), []).append((ids, g, w))
    out = []
    for first, rows in by_first.items():
        games = sum(g for _, g, _ in rows)
        if games / total < PATH_MIN_SHARE:
            continue
        out.append({"first": first, "second": _second_item(rows), "share": games / total,
                    "wr": sum(w for _, _, w in rows) / games, "aggressive": _aggressive_item(cat, first)})
    return sorted(out, key=lambda x: -x["share"])


def _wr_state(wr: float) -> int:
    """+1 si el WR del cruce es claramente favorable (≥ 53 %), −1 si es claramente desfavorable (≤ 47 %) y 0 si no."""
    if wr >= 0.53:
        return 1
    return -1 if wr <= 0.47 else 0


def _lane_state(ev, opgg, role: str, mine: int | None, theirs: int | None) -> int:
    """+1 si tu campeón gana esa línea, −1 si la pierde, 0 si está igualada o no se sabe (OP.GG; si no, el WR del cruce)."""
    if not mine or not theirs:
        return 0
    guide = opgg.matchup_guide(mine, theirs, role, allow_fetch=False) if opgg is not None else None
    adv = (guide or {}).get("lane_advantage")
    if adv == mine:
        return 1
    if adv == theirs:
        return -1
    try:
        wr = ev.pair(role, mine, theirs, fetch=False)["adjusted"]
    # sin datos del cruce: igualada
    except Exception:  # noqa: BLE001
        return 0
    return _wr_state(wr)


def _tempo(diff: float) -> str:
    """``early``, ``late`` o ``even`` según la diferencia de WR entre partidas cortas y largas (en puntos)."""
    if diff >= TEMPO_PP:
        return "early"
    return "late" if diff <= -TEMPO_PP else "even"


def _side_plan(curve: list[dict] | None, lanes: dict[str, int], enemy_side: bool) -> dict:
    """Plan de un equipo: su WR en partidas cortas y largas (visto desde su lado), su tempo y quién gana cada línea."""
    early = late = None
    if curve:
        early, late = curve[0]["p"], curve[-1]["p"]
        if enemy_side:
            early, late = 100 - early, 100 - late
    diff = (early - late) if early is not None else 0.0
    return {"tempo": _tempo(diff), "early": early, "late": late, "lanes": lanes}


def _game_plans(svc, ev, opgg, ally: dict[str, int], enemy: dict[str, int]) -> dict[str, dict]:
    """Plan de partida de cada equipo según el draft: si gana en partidas cortas o largas (curva por duración) y qué
    líneas gana (prioridad). De ahí sale el plan de cada jugador (p. ej. jungla con prioridad en mid y que gana a su
    jungla en un draft que pierde el late: invadir y build agresiva)."""
    from app.analytics.draft import _duration_curve, get_alpha

    try:
        curve = _duration_curve(svc, ev, ally, enemy, get_alpha(svc)) if ally and enemy else None
    # sin curva: tempo igualado
    except Exception:  # noqa: BLE001
        curve = None
    out = {}
    # Quién gana cada línea se decide una vez (desde tu lado) y el rival tiene justo lo contrario: así los dos planes
    # nunca se contradicen (la guía de A contra B y la de B contra A pueden no coincidir).
    ally_lanes = {role: _lane_state(ev, opgg, role, ally.get(role), enemy.get(role)) for role in ROLES}
    for side in ("ally", "enemy"):
        lanes = ally_lanes if side == "ally" else {role: -v for role, v in ally_lanes.items()}
        out[side] = _side_plan(curve, lanes, side == "enemy")
    return out


LANE_NAME = {"TOP": "top", "JUNGLE": "jungla", "MID": "mid", "ADC": "ADC", "SUPPORT": "support"}


def _by_sign(value: int, positive: str, negative: str, zero: str = "") -> str:
    """El texto que toca según el signo de ``value`` (quién gana una línea: +1, −1 o 0)."""
    if value > 0:
        return positive
    return negative if value < 0 else zero


def _tempo_reason(team_plan: dict) -> str:
    """Por qué el draft es de partida corta o larga («» si está igualado)."""
    if team_plan["tempo"] == "early":
        return f"vuestro draft es mejor en partidas cortas ({team_plan['early']:.0f} % antes del 25 frente a {team_plan['late']:.0f} % pasado el 35)"
    if team_plan["tempo"] == "late":
        return f"vuestro draft es mejor en partidas largas ({team_plan['late']:.0f} % pasado el 35 frente a {team_plan['early']:.0f} % antes del 25)"
    return ""


def _jungle_action(tempo: str, own: int, mid: int) -> str | None:
    """Qué hacer de jungla según el tempo del draft, el cruce de junglas y la línea de tu mid."""
    if tempo == "early" and own > 0 and mid > 0:
        return "invade su jungla con tu mid y fuerza peleas pronto, con build de bola de nieve"
    if own > 0 and mid > 0:
        return "puedes invadir su jungla con tu mid"
    if own < 0:
        return "no le invadas; juega cerca de tus líneas y cubre su jungla cuando aparezca"
    if tempo == "late":
        return "farmea seguro y juega los objetivos con el equipo, con build para escalar"
    return None


def _lane_action(tempo: str, own: int) -> str | None:
    """Qué hacer en línea según el tempo del draft y quién gana la línea."""
    if tempo == "early" and own > 0:
        return "presiona la línea y conviértelo en placas y objetivos, con build de bola de nieve"
    if own < 0 and tempo != "early":
        return "juega seguro en línea, sin forzar intercambios, y escala"
    if tempo == "late":
        return "farmea y escala, con build para pelear tarde"
    return None


def _lane_reason(own: int, duo: bool) -> str:
    """Quién gana la línea, dicho al jugador de línea o al support (de su dúo)."""
    if duo:
        return _by_sign(own, "tu dúo gana la línea", "tu dúo pierde la línea")
    return _by_sign(own, "ganas tu línea", "pierdes tu línea")


def _plan_style(aggressive: bool, tempo: str, own: int) -> str:
    """Estilo del plan: de bola de nieve, para escalar o normal."""
    if aggressive:
        return "aggressive"
    return "scaling" if tempo == "late" or (own < 0 and tempo != "early") else "normal"


def _plan_text(why: list[str], action: str | None) -> str | None:
    """El porqué y la acción del plan en frases (cada una con mayúscula y punto); ``None`` si no hay nada que decir."""
    if not why and not action:
        return None
    sentences = ["; ".join(why)] if why else []
    if action:
        sentences.append(action)
    return " ".join(x[0].upper() + x[1:] + "." for x in sentences)


def _player_plan(team_plan: dict | None, role: str) -> dict:
    """Plan de un jugador: agresivo (partida corta y ventaja propia), a escalar o normal, con el porqué."""
    if not team_plan:
        return {"style": "normal", "text": None}
    tempo, lanes = team_plan["tempo"], team_plan["lanes"]
    own = lanes.get("ADC" if role == "SUPPORT" else role, 0)
    why = [_tempo_reason(team_plan)]
    if role == "JUNGLE":
        mid = lanes.get("MID", 0)
        why += [_by_sign(own, "ganas a su jungla", "su jungla te gana"), _by_sign(mid, "tu mid gana su línea", "tu mid pierde la suya")]
        aggressive = tempo == "early" and own > 0 and mid > 0
        action = _jungle_action(tempo, own, mid)
    else:
        why.append(_lane_reason(own, role == "SUPPORT"))
        aggressive = tempo == "early" and own > 0
        action = _lane_action(tempo, own)
    return {"style": _plan_style(aggressive, tempo, own), "text": _plan_text([w for w in why if w], action), "tempo": tempo, "lane": own}


def _rune_class(keystone: int | None) -> str | None:
    """``aggressive``, ``durable`` o ``None`` según la piedra angular."""
    if keystone in AGGRESSIVE_KEYSTONES:
        return "aggressive"
    return "durable" if keystone in DURABLE_KEYSTONES else None


def _matchup_rune_pages(guide_pages: list[dict]) -> list[dict]:
    """Las páginas de runas de OP.GG contra ese rival que están completas y tienen partidas suficientes, con su WR."""
    out: list[dict] = []
    for page in guide_pages or []:
        games = page.get("games") or 0
        if page.get("keystone") and len(page.get("ids") or []) == 6 and games >= MIN_MATCHUP_RUNE_GAMES:
            out.append({**page, "source": "matchup", "wr": 100 * (page.get("wins") or 0) / games})
    return out


def _general_rune_page(base: dict) -> dict | None:
    """La página de su build (si la base no es la del cruce y la página está completa), con su WR si se sabe."""
    general = base.get("runes") if base.get("source") != "matchup" else None
    if not (general and general.get("keystone") and len(general.get("ids") or []) == 6):
        return None
    games = general.get("games") or 0
    return {**general, "source": "general", "wr": 100 * (general.get("wins") or 0) / games if games else None}


def _pages_by_source(options: list[dict]) -> dict[str, list[dict]]:
    """Las páginas de cada fuente (OP.GG contra ese rival, Lolalytics, su build), de más a menos jugada."""
    by_source: dict[str, list[dict]] = {}
    for o in options:
        by_source.setdefault(o["source"], []).append(o)
    for rows in by_source.values():
        rows.sort(key=lambda o: -(o.get("games") or 0))
    return by_source


def _plan_rune(by_source: dict[str, list[dict]], want: str) -> dict | None:
    """La página del tipo que pide el plan (la más jugada) de la primera fuente que tenga una que no gane más de 1,5 pp
    menos que su página más jugada; ``None`` si ninguna."""
    for rows in by_source.values():
        ref = rows[0].get("wr")
        fits = [o for o in rows if _rune_class(o["keystone"]) == want and o.get("wr") is not None and ref is not None and o["wr"] >= ref - RUNES_MAX_WR_LOSS]
        if fits:
            return max(fits, key=lambda o: o.get("games") or 0)
    return None


def _rune_name(dd, keystone) -> str:
    """Nombre de una runa en Data Dragon (o su id si no está)."""
    return (dd.runes.get(int(keystone)) or {}).get("name") or str(keystone)


def _baseline_rune_why(dd, baseline: dict, opp: int | None) -> str:
    """Por qué la página base: la más jugada contra ese rival (OP.GG), la más jugada con él (Lolalytics) o la de su build."""
    if baseline["source"] == "matchup":
        return f"{_rune_name(dd, baseline['keystone'])}: la más jugada contra {dd.name(opp)} ({baseline.get('games')} partidas en OP.GG)"
    if baseline["source"] == "lola":
        share = baseline.get("share")
        return f"{_rune_name(dd, baseline['keystone'])}: la más jugada con él" + (f" ({share * 100:.0f} % de sus partidas)" if share else "")
    return f"{_rune_name(dd, baseline['keystone'])}: la de su build más jugada"


def _round_wr(page: dict) -> float | None:
    """El WR de una página redondeado a un decimal (``None`` si no se sabe)."""
    return round(page["wr"], 1) if page.get("wr") is not None else None


def _rune_result(dd, chosen: dict, options: list[dict], why: str) -> dict:
    """La página elegida (con sus nombres, fragmentos y su porqué) y las opciones que había."""
    shards = chosen.get("shards") or next((o.get("shards") for o in options if o["keystone"] == chosen["keystone"] and o.get("shards")), None)
    ids = [int(i) for i in chosen["ids"]]
    return {
        "keystone": int(chosen["keystone"]), "primary": int(chosen["primary"]), "secondary": int(chosen["secondary"]), "ids": ids,
        "names": [(dd.runes.get(i) or {}).get("name") or str(i) for i in ids], "shards": shards or [], "why": why,
        "games": chosen.get("games"), "wr": _round_wr(chosen),
        "options": [{"keystone": o["keystone"], "source": o["source"], "games": o.get("games"), "wr": _round_wr(o)} for o in options],
    }


def _choose_runes(dd, base: dict, guide_pages: list[dict], lola_pages: list[dict] | None, opp: int | None, want: str | None) -> dict | None:
    """Página de runas para esta partida, con su porqué.

    Base: la más jugada contra ese rival (OP.GG, con ≥ 100 partidas) o, si no, la más jugada con él (Lolalytics) o la de
    su build general. Si el plan pide otra cosa (``want``: ``aggressive`` o ``durable``) y el campeón tiene una página
    real de ese tipo que gana como mucho 1,5 pp menos que la base de su misma fuente, se usa esa (Kayn: Cosecha o
    Conquistador; Hecarim: las azules o Conquistador).
    """
    options = _matchup_rune_pages(guide_pages)
    options += [{**page, "source": "lola"} for page in lola_pages or []]
    general = _general_rune_page(base)
    if general is not None:
        options.append(general)
    if not options:
        return base.get("runes")
    by_source = _pages_by_source(options)
    baseline = (by_source.get("matchup") or by_source.get("lola") or by_source.get("general"))[0]
    planned = _plan_rune(by_source, want) if want and _rune_class(baseline["keystone"]) != want else None
    if planned is None:
        chosen, why = baseline, _baseline_rune_why(dd, baseline, opp)
    else:
        chosen = planned
        label = "de bola de nieve" if want == "aggressive" else "para pelear largo"
        why = f"{_rune_name(dd, chosen['keystone'])}: piedra angular {label} por el plan de partida"
    return _rune_result(dd, chosen, options, why)


def _side_priority(a: int, b: int) -> str:
    """Prioridad en un lado del mapa con las dos líneas que lo controlan: ``ours``, ``theirs`` o ``split``."""
    if a >= 0 and b >= 0 and (a > 0 or b > 0):
        return "ours"
    if a <= 0 and b <= 0 and (a < 0 or b < 0):
        return "theirs"
    return "split"


def team_priorities(team_plan: dict) -> dict:
    lanes = team_plan["lanes"]
    return {"dragon": _side_priority(lanes.get("MID", 0), lanes.get("ADC", 0)), "top_side": _side_priority(lanes.get("TOP", 0), lanes.get("MID", 0))}


def _join_names(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " y " + names[-1]


def _short_game_text(early: float, late: float, short: str, long_: str) -> str:
    """El draft va mejor (o pierde menos) en partidas cortas."""
    if round(early) >= 50:
        return (f"Vuestro draft es mejor en partidas cortas ({short} frente a {late:.0f} % {'pasado el 35' if round(late) < 50 else 'en las largas'})"
                ": busca ventajas pronto y cierra antes del 30.")
    return (f"Vuestro draft va por detrás en cualquier duración, pero sufre más si la partida se alarga ({short}, {long_}): "
            "juega las primeras peleas y objetivos con ventaja numérica y no le des tiempo a escalar.")


def _long_game_text(early: float, late: float, short: str, long_: str) -> str:
    """El draft va mejor (o pierde menos) en partidas largas."""
    if round(late) >= 50:
        return (f"Vuestro draft es mejor en partidas largas ({long_} frente a {early:.0f} % {'antes del 25' if round(early) < 50 else 'en las cortas'})"
                ": no regales nada pronto y pelea cuando tengáis objetos.")
    return (f"Vuestro draft va por detrás en cualquier duración, pero sufre menos si la partida se alarga ({short}, {long_}): "
            "aguanta las primeras fases sin regalar nada y busca las peleas tarde.")


def _tempo_line(team_plan: dict) -> list[dict]:
    """La partida según la duración: si el draft gana (o pierde menos) en partidas cortas o largas. Nunca dice «gana»
    si en las dos fases está por debajo del 50 %."""
    tempo, early, late = team_plan["tempo"], team_plan["early"], team_plan["late"]
    if tempo == "even" or early is None:
        return []
    short, long_ = f"{early:.0f} % antes del 25", f"{late:.0f} % pasado el 35"
    if tempo == "early":
        text = _short_game_text(early, late, short, long_)
    else:
        text = _long_game_text(early, late, short, long_)
    return [{"topic": "Partida", "text": text}]


class _Lanes:
    """Quién gana cada línea y qué campeón la juega en cada equipo (``mine`` / ``theirs``: nombre por rol)."""

    def __init__(self, lanes: dict[str, int], mine: dict[str, str], theirs: dict[str, str]) -> None:
        self.lanes = lanes
        self.mine = mine
        self.theirs = theirs

    def vs(self, r: str) -> str:
        """«Tu campeón contra el suyo» en esa línea."""
        return f"{self.mine.get(r, '?')} contra {self.theirs.get(r, '?')}"

    def known(self, r: str) -> bool:
        """Con el draft a medias, una línea sin los dos campeones no está «igualada»: no se sabe."""
        return bool(self.mine.get(r) and self.theirs.get(r))

    def state(self, r: str) -> str:
        """Cómo va esa línea: la gana, la pierde, está igualada o aún no tiene rival."""
        if not self.known(r):
            return f"{LANE_NAME[r]} aún sin rival"
        return f"{LANE_NAME[r]} {_by_sign(self.lanes.get(r, 0), 'gana su línea', 'pierde su línea', 'está igualado')}"

    def why(self, a: str, b: str) -> str:
        """Por qué hay (o no) prioridad en un lado del mapa: cómo van las dos líneas que lo controlan."""
        va, vb = self.lanes.get(a, 0), self.lanes.get(b, 0)
        if va == vb and self.known(a) and self.known(b):
            return f"{LANE_NAME[a]} y {LANE_NAME[b]} {_by_sign(va, 'ganan su línea', 'pierden su línea', 'están igualados')}"
        return f"{self.state(a)} y {self.state(b)}"


def _pending_side(lx: _Lanes, label: str, a: str, b: str, prio: str) -> str | None:
    """La frase de un lado del mapa con alguna de sus dos líneas aún sin rival (draft a medias): sin dar la prioridad
    por segura, que depende de la que falta. ``None`` si ya se conocen las dos."""
    known = [r for r in (a, b) if lx.known(r)]
    if len(known) == 2:
        return None
    if not known:
        return f"{label}: {LANE_NAME[a]} y {LANE_NAME[b]} aún sin rival; se sabrá al elegirlos."
    other = LANE_NAME[b if known[0] == a else a]
    why = lx.why(a, b)
    return {
        "ours": f"{label}: {why}; tendréis prioridad si {other} no pierde la suya.",
        "theirs": f"{label}: {why}; no tendréis prioridad salvo que {other} gane la suya.",
        "split": f"{label}: {why}; la prioridad dependerá de {other}.",
    }[prio]


def _objective_texts(lx: _Lanes, prio: dict[str, str]) -> tuple[str, str]:
    """Las frases del dragón y de larvas y heraldo según la prioridad en cada lado del mapa."""
    pending_dragon = _pending_side(lx, "Dragón", "MID", "ADC", prio["dragon"])
    pending_top = _pending_side(lx, "Larvas y heraldo", "TOP", "MID", prio["top_side"])
    dragon = {"ours": f"Dragón: tenéis prioridad ({lx.why('MID', 'ADC')}); preparadlo juntos 1 minuto antes.",
              "theirs": f"Dragón: no tenéis prioridad ({lx.why('MID', 'ADC')}); no lo fuercéis, cambiadlo por larvas, heraldo o una torre arriba.",
              "split": f"Dragón: prioridad repartida ({lx.why('MID', 'ADC')}); disputadlo solo si mid y bot empujan antes que los suyos."}[prio["dragon"]]
    top_side = {"ours": f"Larvas y heraldo: tenéis prioridad ({lx.why('TOP', 'MID')}); jugad ese lado.",
                "theirs": f"Larvas y heraldo: no tenéis prioridad ({lx.why('TOP', 'MID')}); cededlos y jugad el lado del dragón.",
                "split": f"Larvas y heraldo: prioridad repartida ({lx.why('TOP', 'MID')}); van al que empuje primero top y mid."}[prio["top_side"]]
    return pending_dragon or dragon, pending_top or top_side


def _jungle_text(lanes: dict[str, int], theirs: dict[str, str]) -> str:
    """Jungla: invadir o no según el cruce de junglas y la línea de tu mid."""
    jg, mid = lanes.get("JUNGLE", 0), lanes.get("MID", 0)
    if jg > 0:
        return f"Ganas a {theirs.get('JUNGLE', 'su jungla')}: " + ("invade su jungla con tu mid, que gana su línea." if mid > 0
                                                                   else "puedes robarle campamentos, pero sin tu mid con prioridad, invade solo si sabes dónde está.")
    if jg < 0:
        return f"{theirs.get('JUNGLE', 'Su jungla')} te gana en la jungla: no le invadas; sigue su ruta, cubre las líneas a las que va y contragankea."
    return "Jungla igualada: " + ("con tu mid ganando puedes invadir su lado de la jungla." if mid > 0 else "farmea tu jungla y juega con tus líneas.")


def _ganks_text(lx: _Lanes) -> str:
    """Jungla: a qué líneas ganquear (las que ganan o están igualadas) y a cuáles cubrir en defensa."""
    sides = [r for r in ("TOP", "MID", "ADC") if lx.known(r)]
    winning = [r for r in sides if lx.lanes.get(r, 0) > 0]
    even = [r for r in sides if lx.lanes.get(r, 0) == 0]
    losing = [r for r in sides if lx.lanes.get(r, 0) < 0]
    pending = [r for r in ("TOP", "MID", "ADC") if not lx.known(r)]
    gank = []
    if winning:
        gank.append(f"ganquea {_join_names([LANE_NAME[r] for r in winning])} ({_join_names([lx.vs(r) for r in winning])}): ganan su línea y con tu ayuda hacen bola de nieve")
    if even:
        gank.append(f"{_join_names([LANE_NAME[r] for r in even])} {'está igualado' if len(even) == 1 else 'están igualados'}: un gank decide la línea")
    if losing and not winning and not even:
        gank.append("todas tus líneas pierden su línea: no fuerces ganks; farmea, cúbrelas con contraganks cuando empujen y juega para el final de la partida")
    elif losing:
        gank.append(f"{_join_names([LANE_NAME[r] for r in losing])} {'pierde' if len(losing) == 1 else 'pierden'} su línea: no fuerces ganks ahí, juega a contragankear y a cubrirlos en defensa")
    if pending:
        gank.append(f"{_join_names([LANE_NAME[r] for r in pending])}: aún sin rival")
    return "; ".join(g[0].upper() + g[1:] if i == 0 else g for i, g in enumerate(gank)) + "."


def _lane_objectives(role: str, duo: bool, dragon: str, top_side: str) -> str:
    """Los objetivos del lado del mapa de su línea: top, larvas y heraldo; el dúo de bot, el dragón; mid, los dos."""
    if role == "TOP":
        return top_side
    return dragon if duo else f"{dragon} {top_side}"


def _lane_text(own: int, duo: bool, who: str) -> str:
    """Cómo jugar la línea según quién la gana (en plural para el dúo de bot)."""
    if own > 0:
        text = f"Ganáis la línea ({who})" if duo else f"Ganas tu línea ({who})"
        return text + ": presiona, coge placas y llega primero al río para ayudar a tu jungla."
    if own < 0:
        text = f"Perdéis la línea ({who})" if duo else f"Pierdes tu línea ({who})"
        return text + ": farmea seguro bajo torre, no gastes el Destello y avisa a tu jungla cuando empujen."
    return f"Línea igualada ({who}): la decide quién juega mejor los niveles y los ganks; mira el minimapa."


def _mid_text(own: int, jg: int) -> str:
    """Mid: cuándo moverse con su jungla según quién gana su línea (y si su jungla gana a la suya)."""
    if own > 0:
        return "Con prioridad en mid, mueve primero al río y acompaña a tu jungla" + (" a invadir: gana a la suya." if jg > 0 else ".")
    if own < 0:
        return "Sin prioridad en mid, no sigas a tu jungla a su lado: empuja tu oleada primero."
    return "Mid igualado: muévete con tu jungla solo cuando tu oleada esté empujada contra su torre."


def _lane_lines(lx: _Lanes, role: str, dragon: str, top_side: str) -> list[dict]:
    """Plan de un jugador de línea: cómo jugar su línea, de mid cuándo moverse con su jungla, y los objetivos de su lado."""
    lane_of = {"TOP": "TOP", "MID": "MID", "ADC": "ADC", "SUPPORT": "ADC"}
    own = lx.lanes.get(lane_of.get(role, role), 0)
    duo = role in ("ADC", "SUPPORT")
    if not lx.known(lane_of[role]):
        return [{"topic": "Línea", "text": "Aún no se sabe tu rival de línea: el plan se completa cuando elija."},
                {"topic": "Objetivos", "text": _lane_objectives(role, duo, dragon, top_side)}]
    who = lx.vs(lane_of[role]) if not duo else f"{lx.mine.get('ADC', '?')} y {lx.mine.get('SUPPORT', '?')} contra {lx.theirs.get('ADC', '?')} y {lx.theirs.get('SUPPORT', '?')}"
    out = [{"topic": "Línea", "text": _lane_text(own, duo, who)}]
    if role == "MID":
        out.append({"topic": "Jungla", "text": _mid_text(own, lx.lanes.get("JUNGLE", 0))})
    out.append({"topic": "Objetivos", "text": _lane_objectives(role, duo, dragon, top_side)})
    return out


def _plan_lines(team_plan: dict | None, role: str, mine: dict[str, str], theirs: dict[str, str]) -> list[dict]:
    """Cómo jugar la partida, en frases cortas: partida, jungla, ganks, objetivos y línea (según el rol).

    ``mine`` / ``theirs``: nombre del campeón de cada rol. Todo sale de la curva por duración y de quién gana cada
    línea (OP.GG o el WR del cruce): no hay frases hechas que no dependan del draft.
    """
    if not team_plan:
        return []
    lx = _Lanes(team_plan["lanes"], mine, theirs)
    prio = team_priorities(team_plan)
    out: list[dict] = []
    out += _tempo_line(team_plan)
    dragon, top_side = _objective_texts(lx, prio)
    if role == "JUNGLE":
        out.extend(
            [
                {"topic": "Jungla", "text": _jungle_text(lx.lanes, theirs)},
                {"topic": "Ganks", "text": _ganks_text(lx)},
                {"topic": "Objetivos", "text": f"{dragon} {top_side}"},
            ]
        )
    else:
        out += _lane_lines(lx, role, dragon, top_side)
    return out


def _choose_path(cat, base: dict, paths: list[dict], pplan: dict) -> dict:
    """Cambia de camino (primer objeto y su 2.º) si el plan lo pide y el campeón tiene esa build de verdad.

    Agresivo: una build de bola de nieve (letalidad, Soberbia…) que empiece al menos el 10 % de sus partidas y no gane
    más de 1,5 pp menos que la habitual. A escalar: al revés, si la habitual empieza por un objeto de bola de nieve.
    """
    core = base.get("core") or []
    if not paths or not core or pplan.get("style") not in ("aggressive", "scaling"):
        return base
    main = next((x for x in paths if x["first"] == core[0]), None) or paths[0]
    current_aggr = _aggressive_item(cat, core[0])
    want_aggr = pplan["style"] == "aggressive"
    if current_aggr == want_aggr:
        return base
    options = [x for x in paths if x["aggressive"] == want_aggr and x["second"] and x["wr"] >= main["wr"] - PATH_MAX_WR_LOSS]
    if not options:
        return base
    best = options[0]
    new_core = [best["first"], best["second"]] + [i for i in core if i not in (best["first"], best["second"])]
    return {**base, "core": new_core, "source": "path", "path": best}


def _opgg(svc):
    """OP.GG si está activo (``None`` si no)."""
    return svc.opgg if getattr(svc, "opgg", None) is not None and svc.opgg.enabled else None


def _champ_tags(dd, champ: int) -> list[str]:
    """Clases del campeón en Data Dragon (Fighter, Mage…)."""
    return list(((dd.champions.get(champ) or {}).get("tags")) or [])


def _lola_item_sets(ev, lola_src, champ: int, role: str) -> dict | None:
    """Las builds completas del campeón en Lolalytics (``None`` si no está activo o no hay alcance)."""
    if lola_src is not None and getattr(lola_src, "enabled", False) and ev.scope:
        return lola_src.item_sets(champ, role, ev.scope.tier, ev.scope.region, ev.scope.patches[0], allow_fetch=ev.fetch)
    return None


def _lola_rune_pages(ev, lola_src, champ: int, role: str) -> list[dict] | None:
    """Las páginas de runas del campeón en Lolalytics (``None`` si no está activo, no hay alcance o no las da)."""
    if lola_src is not None and getattr(lola_src, "enabled", False) and ev.scope and hasattr(lola_src, "rune_pages"):
        return lola_src.rune_pages(champ, role, ev.scope.tier, ev.scope.region, ev.scope.patches[0], allow_fetch=ev.fetch)
    return None


def _lola_core(cat, base: dict, lola: dict | None) -> dict:
    """Sin núcleo de OP.GG, hace de núcleo el primer objeto más comprado en Lolalytics."""
    if not base.get("core") and lola and lola.get("first"):
        firsts = [(g, int(i)) for i, (g, _) in lola["first"].items() if not cat or cat.is_legendary(int(i))]
        if firsts:
            return {**base, "source": base.get("source") or "lolalytics", "core": [max(firsts)[1]]}
    return base


def _want_runes(pplan: dict, base: dict) -> str | None:
    """Qué runas pide el plan: de bola de nieve (``aggressive``), para pelear largo (``durable``) o ninguna en especial."""
    if pplan.get("style") == "aggressive" or (base.get("path") or {}).get("aggressive"):
        return "aggressive"
    return "durable" if pplan.get("style") == "scaling" else None


def _core_reason(dd, cat, base: dict, opp: int | None) -> str:
    """Por qué ese núcleo: el camino que pide el plan, el núcleo contra ese rival o el de su build."""
    path = base.get("path")
    if path:
        return (f"{'Build de bola de nieve' if path['aggressive'] else 'Build para escalar'} por el plan de partida: "
                f"{dd_item(cat, path['first'])} de primer objeto ({path['share'] * 100:.0f} % de sus builds empiezan así)")
    if base.get("source") == "matchup" and base.get("games"):
        return f"Núcleo contra {dd.name(opp)} (OP.GG, {base['games']} partidas)"
    if base.get("thin_matchup"):
        return f"Núcleo de su build (contra {dd.name(opp)} aún hay pocas partidas: {base['thin_matchup']})"
    return "Núcleo de su build (lo que define al campeón)"


def _core_entries(dd, cat, base: dict, opp: int | None, pool: dict) -> list[dict]:
    """El núcleo (los 2 primeros objetos que definen al campeón) que está entre sus objetos, con su porqué."""
    return [{"item": i, "kind": "core", "reason": _core_reason(dd, cat, base, opp)} for i in (base.get("core") or [])[:CORE] if i in pool]


def _rush_items(pool: dict, chosen: list[dict]) -> dict[int, str]:
    """Objetos que se compran de primero (bola de nieve): no se pueden meter de 3.º a 5.º; quedan como otra forma de
    empezar."""
    return {
        i: f"Otra forma de empezar: se compra de primer objeto (posición media {str(d['lola_pos']).replace('.', ',')} de 5), no al final"
        for i, d in pool.items()
        if d.get("lola_pos") is not None and d["lola_pos"] < RUSH_POS and not any(c["item"] == i for c in chosen)
    }


class _BuildLimits:
    """Límites al completar la build: Heridas graves solo si le tocan (y una sola) y, a un carry blando, como mucho 2
    objetos con resistencias."""

    def __init__(self, cat, plan: dict, squishy: bool, chosen: list[dict]) -> None:
        self.cat = cat
        self.plan = plan
        self.squishy = squishy
        self.anti = sum(1 for c in chosen if cat and cat.anti_heal(c["item"]))
        self.resist = 0

    def allow(self, item: int) -> bool:
        """¿Cabe este objeto? Cuenta cada límite que pasa (uno con Heridas graves cuenta aunque luego no quepa por las
        resistencias)."""
        cat = self.cat
        if cat and cat.anti_heal(item):
            if self.anti or self.plan.get("anti_heal") is None:
                return False
            self.anti += 1
        if cat and cat.resist(item) and self.squishy:
            if self.resist >= 2:
                return False
            self.resist += 1
        return True

    def owes_anti_heal(self) -> bool:
        """Le tocan Heridas graves y ningún objeto de su build las da."""
        return self.plan.get("anti_heal") is not None and not self.anti and bool(self.cat)


def _clashes(i: int, chosen: list[dict], rush: dict, pool: dict, exclusive: _Exclusive) -> bool:
    """Ya está en la build, se compra de primero, no se compra con este núcleo o casi nunca va con lo que ya lleva
    (pasiva única o camino distinto)."""
    if any(c["item"] == i for c in chosen) or i in rush or pool[i].get("off_core"):
        return True
    return any(exclusive(i, c["item"]) for c in chosen)


def _fill_build(chosen: list[dict], order: list[tuple[int, str]], pool: dict, rush: dict, exclusive: _Exclusive, limits: _BuildLimits) -> None:
    """Completa la build (hasta 5 objetos) con los que más encajan en este draft y caben."""
    for i, why in order:
        if len(chosen) >= ITEMS:
            break
        if _clashes(i, chosen, rush, pool, exclusive) or not limits.allow(i):
            continue
        situational = bool(why) and not why.startswith("De los más comprados")
        chosen.append({"item": i, "kind": "situational" if situational else "popular", "reason": why or "De los más comprados con él"})


def _anti_heal_duty(chosen: list[dict], cat, style: str, exclusive: _Exclusive, ctx: _Context, plan: dict) -> None:
    """Le tocan Heridas graves y ningún objeto suyo las da: la más barata de su tipo de daño que pueda ir con su núcleo
    (Recordatorio letal no puede ir con la Cuchilla negra de Smolder: se busca otra; si ninguna puede, que la lleve otro
    aliado)."""
    options = [
        i for i in sorted(cat.items, key=cat.cost)
        if cat.anti_heal(i) and cat.is_legendary(i) and cat.get(i).get("buyable") and not cat.get(i).get("champion") and STYLE_TAG[style] in cat.tags(i)
        and not any(c["kind"] == "core" and exclusive(i, c["item"]) for c in chosen)
    ]
    option = options[0] if options else None
    if not option:
        return
    entry = {"item": option, "kind": "situational", "reason": f"Heridas graves contra {ctx.who(ctx.healers)}{plan['anti_heal']}"}
    clash = next((k for k, c in enumerate(chosen) if c["kind"] != "core" and exclusive(option, c["item"])), None)
    if clash is not None:
        chosen[clash] = entry  # sustituye al que no puede ir con ella (Recordatorio letal en vez de Dominik)
    elif len(chosen) >= ITEMS:
        chosen[-1] = entry
    else:
        chosen.append(entry)


def _buy_order(chosen: list[dict], pool: dict) -> list[dict]:
    """El núcleo primero y el resto en el orden en que se compra de verdad (posición media en Lolalytics)."""
    core_n = sum(1 for c in chosen if c["kind"] == "core")
    return chosen[:core_n] + sorted(chosen[core_n:], key=lambda c: pool.get(c["item"], {}).get("lola_order") or 9.0)


def _alternatives(order: list[tuple[int, str]], chosen: list[dict], rush: dict, pool: dict) -> list[dict]:
    """Otras opciones frecuentes que no están en la build (hasta 4) y, al final, otra forma de empezar."""
    ranked = [(i, why) for i, why in order if not any(c["item"] == i for c in chosen)]
    first_alt = [{"item": i, "reason": rush[i]} for i, _ in ranked if i in rush][:1]
    return [
        {"item": i, "reason": why or "Otra opción frecuente"} for i, why in ranked if i not in rush and not pool[i].get("off_core")
    ][: 4 - len(first_alt)] + first_alt


def _skills(base: dict) -> str | None:
    """Orden de habilidades contra ese rival o, si no, el orden en que se suben al máximo."""
    return base.get("skill_order") or (">".join(base["skill_max"]) if base.get("skill_max") else None)


def _sources(base: dict, general: dict | None, lola: dict | None, otp: dict | None, own: dict | None) -> list[str]:
    """Las fuentes con datos para esta build."""
    return [name for name, ok in (("OP.GG", bool(base.get("source") in ("matchup", "general") or general)), ("Lolalytics", bool(lola)),
                                  ("OTPs", bool(otp)), ("tu BD", bool(own))) if ok]


def _player(svc, ev, cat, champ_info, side: str, role: str, champ: int, theirs: dict, enemy_comp: dict | None, team: dict,
            pplan: dict | None = None) -> dict:
    """Build completa de un jugador: núcleo y objetos para este draft, botas, runas, alternativas y qué cambiar si la
    partida cambia."""
    dd = svc.dd
    tags = _champ_tags(dd, champ)
    opp = theirs.get(role)
    opgg = _opgg(svc)
    lola_src = getattr(svc, "lolalytics", None)
    base, general = _base(ev, opgg, champ, role, opp)
    lola = _lola_item_sets(ev, lola_src, champ, role)
    base = _lola_core(cat, base, lola)
    pplan = pplan or {"style": "normal", "text": None}
    base = _choose_path(cat, base, _paths(cat, lola), pplan)
    lola_runes = _lola_rune_pages(ev, lola_src, champ, role)
    runes = _choose_runes(dd, base, base.get("rune_pages") or [], lola_runes, opp, _want_runes(pplan, base))
    otp = _otp_items(svc, champ, role)
    own = _own_items(svc, cat, champ, role)
    pool = _candidates(cat, base, general, lola, otp, own, _with_core(lola, (base.get("core") or [])[:CORE]))
    exclusive = _Exclusive(lola, cat)
    style = _style(cat, (base.get("core") or list(pool)[:3]), tags)
    squishy = _squishy(style, tags)
    ctx = _Context(svc, [c for c in theirs.values() if c], enemy_comp, champ_info)
    plan = team.get(champ) or {}

    scored = {i: _score(i, data, cat, style, squishy, ctx, otp, lola, plan) for i, data in pool.items()}
    order = [(i, why) for i, (_, why) in sorted(scored.items(), key=lambda kv: -kv[1][0])]
    chosen = _core_entries(dd, cat, base, opp, pool)
    limits = _BuildLimits(cat, plan, squishy, chosen)
    rush = _rush_items(pool, chosen)
    _fill_build(chosen, order, pool, rush, exclusive, limits)
    if limits.owes_anti_heal():
        _anti_heal_duty(chosen, cat, style, exclusive, ctx, plan)
    chosen = _buy_order(chosen, pool)
    alternatives = _alternatives(order, chosen, rush, pool)
    situations = _situations(svc, cat, champ_info, chosen, pool, scored, style, theirs, _paths(cat, lola), base) if len([c for c in theirs.values() if c]) >= 3 else []
    return {
        "side": side,
        "role": role,
        "champ": champ,
        "style": style,
        "opponent": opp,
        "build": chosen,
        "boots": _boots(cat, base, general, lola, otp, squishy, ctx),
        "alternatives": alternatives,
        "situations": situations,
        "runes": runes,
        "starter": base.get("starter") or [],
        "spells": base.get("spells") or [],
        "skills": _skills(base),
        "matchup": {"opponent": opp, "games": base.get("games"), "wins": base.get("wins")} if base.get("source") == "matchup" else None,
        "otp": (otp or {}).get("best"),
        "enemy": {"phys": ctx.phys, "magic": ctx.magic, "groups": ctx.groups},
        "sources": _sources(base, general, lola, otp, own),
        "plan": pplan,
    }


THREAT_TAGS = {"Assassin", "Mage", "Marksman", "Fighter"}
THREAT_ROLE_ORDER = ("ADC", "MID", "JUNGLE", "TOP", "SUPPORT")  # a igualdad, el que más daño suele hacer


def _threat_kind(magic: float) -> str | None:
    """``ap`` / ``ad`` según la parte de daño mágico del campeón (0-1); ``None`` si es mixto."""
    if magic >= 0.55:
        return "ap"
    return "ad" if magic <= 0.4 else None


def _threat_rank(svc, champ_info, role: str, champ: int | None) -> tuple[str, tuple[int, int]] | None:
    """Tipo de amenaza de ese rival (``ap`` / ``ad``) y su prioridad; ``None`` si no es una amenaza de daño clara."""
    info = champ_info(champ) if champ and champ_info else None
    tags = set(((svc.dd.champions.get(champ) or {}).get("tags")) or []) if champ else set()
    if not info or not tags & THREAT_TAGS or (role == "SUPPORT" and "Mage" not in tags):
        return None
    kind = _threat_kind(info["magic"])
    if kind is None:
        return None
    # Primero los que viven de matar (asesinos y tiradores), luego el orden de roles.
    return kind, (0 if tags & {"Assassin", "Marksman"} else 1, THREAT_ROLE_ORDER.index(role))


def _threats(svc, theirs: dict[str, int], champ_info) -> list[tuple[str, int, str]]:
    """Su mayor amenaza de daño físico y la de daño mágico: [(tipo, campeón, rol)] (``ad`` / ``ap``)."""
    out: dict[str, tuple[int, int, str]] = {}
    for role in THREAT_ROLE_ORDER:
        champ = theirs.get(role)
        threat = _threat_rank(svc, champ_info, role, champ)
        if threat is None:
            continue
        kind, rank = threat
        if kind not in out or rank < out[kind][0]:
            out[kind] = (rank, champ, role)
    return [(k, v[1], v[2]) for k, v in sorted(out.items(), key=lambda kv: kv[1][0])]


def _defensive_pick(cat, pool: dict, scored: dict, style: str, need: str, taken: set[int]) -> int | None:
    """El objeto con esa resistencia que más encaja con el campeón: primero de los que de verdad compra (su pool), y si
    no, el más barato de la tienda de su tipo de daño."""
    options = [i for i in pool if i not in taken and need in cat.resist(i) and cat.is_legendary(i)]
    if options:
        return max(options, key=lambda i: scored.get(i, (0, ""))[0])
    tag = STYLE_TAG.get(style)
    shop = [
        i for i in sorted(cat.items, key=cat.cost)
        if i not in taken and need in cat.resist(i) and cat.is_legendary(i) and cat.get(i).get("buyable")
        and not cat.get(i).get("champion") and (tag is None or tag in cat.tags(i))
    ]
    return shop[0] if shop else None


def _threat_situation(svc, cat, kind: str, champ: int, role: str, item: int, swap: int | None) -> dict:
    """«Si X se pone fuerte»: adelantar ese objeto (con la resistencia que pide su daño) y dejar para después el último
    que no es del núcleo."""
    name = svc.dd.name(champ)
    what = "armadura" if kind == "ad" else "resistencia mágica"
    return {
        "when": f"Si {name} se pone fuerte",
        "item": item,
        "replaces": swap,
        "reason": f"{name} ({LANE_NAME.get(role, role)}) es su mayor amenaza de daño {'físico' if kind == 'ad' else 'mágico'}: si va por "
                  f"delante (2+ kills de ventaja o te mata en una pelea), adelanta {cat.name(item)} ({what})"
                  + (f" y deja {cat.name(swap)} para después." if swap else "."),
    }


def _threat_situations(svc, cat, champ_info, chosen: list[dict], pool: dict, scored: dict, style: str, theirs: dict[str, int]) -> list[dict]:
    """Si su mayor amenaza física o mágica se pone fuerte: el objeto con esa resistencia que más encaja con el campeón
    (si su build aún no la lleva)."""
    out: list[dict] = []
    taken = {c["item"] for c in chosen}
    swap = next((c["item"] for c in reversed(chosen) if c["kind"] != "core"), None)  # lo que se cambia: el último no núcleo
    for kind, champ, role in _threats(svc, theirs, champ_info):
        need = "armor" if kind == "ad" else "mr"
        if any(need in cat.resist(i) for i in taken):
            continue  # ya lo llevas en la build
        item = _defensive_pick(cat, pool, scored, style, need, taken)
        if not item:
            continue
        out.append(_threat_situation(svc, cat, kind, champ, role, item, swap))
        taken.add(item)
    return out


def _path_situation(cat, alt: dict, first: int, ahead: bool) -> dict:
    """Cambiar el primer objeto por el de otra build real del campeón (de bola de nieve o para escalar)."""
    return {
        "when": "Si vas por delante" if ahead else "Si vas por detrás",
        "item": alt["first"],
        "replaces": first,
        "reason": (f"{'Build de bola de nieve' if ahead else 'Build para escalar'}: {cat.name(alt['first'])} de primer objeto "
                   f"({alt['share'] * 100:.0f} % de sus builds empiezan así, {alt['wr'] * 100:.1f} % WR)"
                   + (f" y después {cat.name(alt['second'])}" if alt.get("second") else "")
                   + (". Con ventaja, cierra la partida antes de que escalen." if ahead else ". Sin ventaja, aguanta y llega fuerte al final.")),
    }


def _path_situations(cat, paths: list[dict], base: dict) -> list[dict]:
    """Si vas por delante o por detrás: la otra forma de empezar que su campeón juega de verdad (y no gana más de
    1,5 pp menos)."""
    core = base.get("core") or []
    if not paths or not core:
        return []
    out: list[dict] = []
    main = next((x for x in paths if x["first"] == core[0]), None) or paths[0]
    for ahead in (True, False):
        options = [x for x in paths if x["first"] != core[0] and x["aggressive"] == ahead and x["wr"] >= main["wr"] - PATH_MAX_WR_LOSS]
        if not options or _aggressive_item(cat, core[0]) == ahead:
            continue
        out.append(_path_situation(cat, options[0], core[0], ahead))
    return out


def _situations(svc, cat, champ_info, chosen: list[dict], pool: dict, scored: dict, style: str,
                theirs: dict[str, int], paths: list[dict], base: dict) -> list[dict]:
    """Cómo cambiar la build si la partida cambia: si un rival concreto se pone fuerte (su amenaza física y su amenaza
    mágica) y si vas por delante o por detrás (la otra forma de empezar que su campeón juega de verdad)."""
    if not cat or not chosen:
        return []
    return _threat_situations(svc, cat, champ_info, chosen, pool, scored, style, theirs) + _path_situations(cat, paths, base)


def _assign_anti_heal(dd, players: dict[int, str], plan: dict[int, dict]) -> None:
    """Quién lleva las Heridas graves: los 2 primeros en el orden ADC, top, mid y jungla (nunca el support)."""
    order = sorted((c for c in players if players[c] != "SUPPORT"), key=lambda c: ANTI_HEAL_ORDER.index(players[c]) if players[c] in ANTI_HEAL_ORDER else 9)
    holders = order[:ANTI_HEAL_PER_TEAM]
    names = " y ".join(dd.name(c) for c in holders)
    for c in holders:
        plan[c]["anti_heal"] = f" (en tu equipo la llevan {names}: con dos basta)" if len(holders) > 1 else ""


def _team_plan(svc, players: dict[int, str], enemies: list[int], ally_comp: dict | None) -> dict[int, dict]:
    """Coordinación del equipo: quién lleva las Heridas graves (2 como mucho) y si hace falta más primera línea."""
    dd = svc.dd
    healers = [c for c in enemies if dd.name(c) in HEAVY_HEAL]
    plan: dict[int, dict] = {c: {} for c in players}
    if healers:
        _assign_anti_heal(dd, players, plan)
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


def _lolalytics(svc):
    """Lolalytics si está activo (``None`` si no)."""
    lola = getattr(svc, "lolalytics", None)
    return lola if lola is not None and getattr(lola, "enabled", False) else None


def _champ_jobs(ev, opgg, lola, role: str, champ: int, opp: int | None) -> list:
    """Las descargas que precargar para un campeón (cada una, una función sin argumentos)."""
    jobs = []
    if opgg is not None:
        jobs.append(lambda c=champ, r=role: opgg.champion_build(c, r))
        if opp:
            jobs.append(lambda c=champ, o=opp, r=role: opgg.matchup_guide(c, o, r))
    if lola is not None:
        jobs.append(lambda c=champ, r=role: lola.item_sets(c, r, ev.scope.tier, ev.scope.region, ev.scope.patches[0]))
        if hasattr(lola, "rune_pages"):
            jobs.append(lambda c=champ, r=role: lola.rune_pages(c, r, ev.scope.tier, ev.scope.region, ev.scope.patches[0]))
    return jobs


def _prefetch(ev, opgg, lola, ally: dict[str, int], enemy: dict[str, int]) -> None:
    """Precarga de OP.GG y Lolalytics lo que necesita el draft (en paralelo con OP.GG; si no, una descarga tras otra)."""
    jobs = []
    for role in ROLES:
        for mine, theirs in ((ally, enemy), (enemy, ally)):
            champ = mine.get(role)
            if champ:
                jobs += _champ_jobs(ev, opgg, lola, role, champ, theirs.get(role))
    if opgg is not None:
        opgg.prefetch(jobs, timeout=10)
    else:
        for job in jobs:
            job()


class _Draft:
    """Lo que comparten todas las builds de un draft: servicios, evidencia, catálogo, planes, nombres y composiciones."""

    def __init__(self, svc, ev, cat, champ_info, plans: dict, names: dict, comp: dict) -> None:
        self.svc = svc
        self.ev = ev
        self.cat = cat
        self.champ_info = champ_info
        self.plans = plans
        self.names = names
        self.comp = comp

    def team_builds(self, side: str, mine: dict[str, int], theirs: dict[str, int]) -> list[dict]:
        """Las builds de un equipo (en el orden de los roles), coordinadas entre ellos."""
        players = {c: r for r, c in mine.items() if c}
        plan = _team_plan(self.svc, players, [c for c in theirs.values() if c], self.comp[side])
        return [self._build(side, role, mine[role], theirs, plan) for role in ROLES if mine.get(role)]

    def _build(self, side: str, role: str, champ: int, theirs: dict[str, int], plan: dict) -> dict:
        """Build y plan de partida de un jugador (y, del support, sus objetos de equipo)."""
        svc = self.svc
        rival = "enemy" if side == "ally" else "ally"
        pplan = _player_plan(self.plans.get(side), role)
        pplan["lines"] = _plan_lines(self.plans.get(side), role, self.names[side], self.names[rival]) + _otp_plan_lines(svc, champ, role)
        p = _player(svc, self.ev, self.cat, self.champ_info, side, role, champ, theirs, self.comp[rival], plan, pplan)
        if role == "SUPPORT":
            holders = sum(1 for v in plan.values() if v.get("anti_heal") is not None)
            p["team_items"] = _support_items(self.cat, _Context(svc, [c for c in theirs.values() if c], self.comp[rival], self.champ_info), holders)
        return p


def recommend(svc, f, ally: dict[str, int], enemy: dict[str, int], sources: str = "all") -> dict:
    """Build completa para cada campeón elegido de los dos equipos, contra todo el equipo rival."""
    ev = Evidence(svc, f, sources)
    opgg = _opgg(svc)
    lola = _lolalytics(svc)
    if ev.scope and (opgg is not None or lola is not None):
        _prefetch(ev, opgg, lola, ally, enemy)
    ev.fetch = False  # a partir de aquí, solo lo que ya esté en caché (lo que falte llega en segundo plano)
    plans = _game_plans(svc, ev, opgg, ally, enemy)
    names = {side: {role: svc.dd.name(c) for role, c in team.items() if c} for side, team in (("ally", ally), ("enemy", enemy))}
    cat = getattr(svc.dd, "item_catalog", None) or None
    champ_info = champ_info_from(svc.dd)
    comp = {"ally": _composition(svc, ev, ally), "enemy": _composition(svc, ev, enemy)}
    draft = _Draft(svc, ev, cat, champ_info, plans, names, comp)
    out: dict[str, list] = {"ally": draft.team_builds("ally", ally, enemy), "enemy": draft.team_builds("enemy", enemy, ally)}
    out["plans"] = {
        side: ({"tempo": tp["tempo"], "early": tp["early"], "late": tp["late"], "lanes": tp["lanes"], "priorities": team_priorities(tp)} if tp else None)
        for side, tp in plans.items()
    }
    return out
