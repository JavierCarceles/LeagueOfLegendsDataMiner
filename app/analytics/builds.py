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


def _candidates(cat, base: dict, general: dict | None, lola: dict | None, otp: dict | None, own: dict | None, with_core: dict | None = None) -> dict[int, dict]:
    """Objetos completos que se le compran a ese campeón, con lo que dice cada fuente.

    ``with_core``: lo que se compra junto a su núcleo (Lolalytics). Si lo hay, el uso de cada objeto se mide ahí.
    """
    out: dict[int, dict] = {}

    def add(item, **data):
        try:
            item = int(item)
        except (TypeError, ValueError):
            return
        if cat and item:
            item = cat.buyable_form(item)  # Muramana -> Manamune: lo que se puede comprar
        if not item or (cat and not cat.is_legendary(item)):
            return
        out.setdefault(item, {}).update(data)

    for rank, i in enumerate(base.get("core") or []):
        add(i, core=rank)
    for key in ("popular", "fourth", "fifth", "sixth"):
        for o in (general or {}).get(key) or []:
            if o.get("id"):
                extra = {"opgg_after_core": True} if key != "popular" else {}
                add(o["id"], opgg=max(out.get(int(o["id"]), {}).get("opgg", 0.0), float(o.get("pick_rate") or 0)), **extra)
    if lola and lola.get("games"):
        total = lola["games"]
        pos = lola.get("pos") or {}
        for i, (games, wins) in (lola.get("items") or {}).items():
            if games / total >= 0.01:
                add(i, lola=games / total, lola_games=games, lola_wins=wins, lola_order=pos.get(str(i)),
                    lola_pos=pos.get(str(i)) if games >= MIN_POS_GAMES else None)
        if with_core:
            for i, (games, _wins) in with_core["items"].items():
                if int(i) in out and "lola" in out[int(i)]:
                    out[int(i)]["lola"] = games / with_core["games"]  # uso junto a su núcleo
                    out[int(i)]["with_core"] = True
            for data in out.values():
                if "lola" not in data and "core" not in data:
                    data["lola"] = 0.0  # solo lo lista OP.GG: en las builds con este núcleo no aparece
                if "lola" in data and not data.get("with_core"):
                    data["lola"] = 0.0  # nunca va con este núcleo
                if "lola" in data and data["lola"] < MIN_WITH_CORE and "core" not in data:
                    data["off_core"] = True  # con datos de su núcleo, mandan esos (las listas de OP.GG van tras el suyo)
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
        of = "de sus builds con ese núcleo" if data.get("with_core") and use == data.get("lola") else "de sus builds"
        reasons.append((0.1, f"De los más comprados con él ({use * 100:.0f} % {of}{_wr_text(delta, data.get('lola_games') or 0)})"))
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
                "runes": (guide.get("runes") or [None])[0], "rune_pages": guide.get("runes") or [],
                "skill_order": ((guide.get("skills") or [{}])[0]).get("order"),
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


def dd_item(cat, item: int) -> str:
    return cat.name(item) if cat else f"#{item}"


class _Exclusive:
    """¿Estos dos objetos casi nunca van juntos en las builds reales del campeón? (pasiva única o caminos distintos)."""

    def __init__(self, lola: dict | None, cat=None) -> None:
        self.cat = cat
        self.sets = [(set(ids), g) for ids, g, _w in (lola or {}).get("sets") or []]
        self.total = sum(g for _ids, g in self.sets)
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


def _paths(cat, lola: dict | None) -> list[dict]:
    """Las builds reales del campeón según su primer objeto (Lolalytics): cuántas empiezan así, su 2.º objeto y su WR."""
    sets = (lola or {}).get("sets") or []
    total = sum(g for _ids, g, _w in sets)
    if not total:
        return []
    by_first: dict[int, list] = {}
    for ids, g, w in sets:
        if ids:
            by_first.setdefault(int(ids[0]), []).append((ids, g, w))
    out = []
    for first, rows in by_first.items():
        games = sum(g for _i, g, _w in rows)
        if games / total < PATH_MIN_SHARE:
            continue
        seconds = Counter()
        for ids, g, _w in rows:
            if len(ids) > 1:
                seconds[int(ids[1])] += g
        out.append({"first": first, "second": seconds.most_common(1)[0][0] if seconds else None, "share": games / total,
                    "wr": sum(w for _i, _g, w in rows) / games, "aggressive": _aggressive_item(cat, first)})
    return sorted(out, key=lambda x: -x["share"])


def _lane_state(svc, ev, opgg, role: str, mine: int | None, theirs: int | None) -> int:
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
    except Exception:  # noqa: BLE001  (sin datos del cruce: igualada)
        return 0
    return 1 if wr >= 0.53 else -1 if wr <= 0.47 else 0


def _game_plans(svc, ev, opgg, ally: dict[str, int], enemy: dict[str, int]) -> dict[str, dict]:
    """Plan de partida de cada equipo según el draft: si gana en partidas cortas o largas (curva por duración) y qué
    líneas gana (prioridad). De ahí sale el plan de cada jugador (p. ej. jungla con prioridad en mid y que gana a su
    jungla en un draft que pierde el late: invadir y build agresiva)."""
    from app.analytics.draft import _duration_curve, get_alpha

    try:
        curve = _duration_curve(svc, ev, ally, enemy, get_alpha(svc)) if ally and enemy else None
    except Exception:  # noqa: BLE001  (sin curva: tempo igualado)
        curve = None
    out = {}
    # Quién gana cada línea se decide una vez (desde tu lado) y el rival tiene justo lo contrario: así los dos planes
    # nunca se contradicen (la guía de A contra B y la de B contra A pueden no coincidir).
    ally_lanes = {role: _lane_state(svc, ev, opgg, role, ally.get(role), enemy.get(role)) for role in ROLES}
    for side, mine, theirs in (("ally", ally, enemy), ("enemy", enemy, ally)):
        early = late = None
        if curve:
            early, late = curve[0]["p"], curve[-1]["p"]
            if side == "enemy":
                early, late = 100 - early, 100 - late
        diff = (early - late) if early is not None else 0.0
        tempo = "early" if diff >= TEMPO_PP else "late" if diff <= -TEMPO_PP else "even"
        lanes = ally_lanes if side == "ally" else {role: -v for role, v in ally_lanes.items()}
        out[side] = {"tempo": tempo, "early": early, "late": late, "lanes": lanes}
    return out


LANE_NAME = {"TOP": "top", "JUNGLE": "jungla", "MID": "mid", "ADC": "ADC", "SUPPORT": "support"}


def _player_plan(team_plan: dict | None, role: str) -> dict:
    """Plan de un jugador: agresivo (partida corta y ventaja propia), a escalar o normal, con el porqué."""
    if not team_plan:
        return {"style": "normal", "text": None}
    tempo, lanes = team_plan["tempo"], team_plan["lanes"]
    own = lanes.get("ADC" if role == "SUPPORT" else role, 0)
    why = []
    if tempo == "early":
        why.append(f"vuestro draft es mejor en partidas cortas ({team_plan['early']:.0f} % antes del 25 frente a {team_plan['late']:.0f} % pasado el 35)")
    elif tempo == "late":
        why.append(f"vuestro draft es mejor en partidas largas ({team_plan['late']:.0f} % pasado el 35 frente a {team_plan['early']:.0f} % antes del 25)")
    if role == "JUNGLE":
        mid = lanes.get("MID", 0)
        why.append("ganas a su jungla" if own > 0 else "su jungla te gana" if own < 0 else "")
        why.append("tu mid gana su línea" if mid > 0 else "tu mid pierde la suya" if mid < 0 else "")
        aggressive = tempo == "early" and own > 0 and mid > 0
        if aggressive:
            action = "invade su jungla con tu mid y fuerza peleas pronto, con build de bola de nieve"
        elif own > 0 and mid > 0:
            action = "puedes invadir su jungla con tu mid"
        elif own < 0:
            action = "no le invadas; juega cerca de tus líneas y cubre su jungla cuando aparezca"
        elif tempo == "late":
            action = "farmea seguro y juega los objetivos con el equipo, con build para escalar"
        else:
            action = None
    else:
        aggressive = tempo == "early" and own > 0
        duo = role == "SUPPORT"
        why.append(("tu dúo gana la línea" if duo else "ganas tu línea") if own > 0 else ("tu dúo pierde la línea" if duo else "pierdes tu línea") if own < 0 else "")
        if aggressive:
            action = "presiona la línea y conviértelo en placas y objetivos, con build de bola de nieve"
        elif own < 0 and tempo != "early":
            action = "juega seguro en línea, sin forzar intercambios, y escala"
        elif tempo == "late":
            action = "farmea y escala, con build para pelear tarde"
        else:
            action = None
    why = [w for w in why if w]
    style = "aggressive" if aggressive else "scaling" if tempo == "late" or (own < 0 and tempo != "early") else "normal"
    text = None
    if why or action:
        sentences = ["; ".join(why)] if why else []
        if action:
            sentences.append(action)
        text = " ".join(x[0].upper() + x[1:] + "." for x in sentences)
    return {"style": style, "text": text, "tempo": tempo, "lane": own}


def _rune_class(keystone: int | None) -> str | None:
    return "aggressive" if keystone in AGGRESSIVE_KEYSTONES else "durable" if keystone in DURABLE_KEYSTONES else None


def _choose_runes(dd, base: dict, guide_pages: list[dict], lola_pages: list[dict] | None, opp: int | None, want: str | None) -> dict | None:
    """Página de runas para esta partida, con su porqué.

    Base: la más jugada contra ese rival (OP.GG, con ≥ 100 partidas) o, si no, la más jugada con él (Lolalytics) o la de
    su build general. Si el plan pide otra cosa (``want``: ``aggressive`` o ``durable``) y el campeón tiene una página
    real de ese tipo que gana como mucho 1,5 pp menos que la base de su misma fuente, se usa esa (Kayn: Cosecha o
    Conquistador; Hecarim: las azules o Conquistador).
    """
    options: list[dict] = []
    for page in guide_pages or []:
        games = page.get("games") or 0
        if page.get("keystone") and len(page.get("ids") or []) == 6 and games >= MIN_MATCHUP_RUNE_GAMES:
            options.append({**page, "source": "matchup", "wr": 100 * (page.get("wins") or 0) / games})
    for page in lola_pages or []:
        options.append({**page, "source": "lola"})
    general = base.get("runes") if base.get("source") != "matchup" else None
    if general and general.get("keystone") and len(general.get("ids") or []) == 6:
        games = general.get("games") or 0
        options.append({**general, "source": "general", "wr": 100 * (general.get("wins") or 0) / games if games else None})
    if not options:
        return base.get("runes")
    by_source = {}
    for o in options:
        by_source.setdefault(o["source"], []).append(o)
    for rows in by_source.values():
        rows.sort(key=lambda o: -(o.get("games") or 0))
    baseline = (by_source.get("matchup") or by_source.get("lola") or by_source.get("general"))[0]
    chosen, why = baseline, None
    def name(keystone) -> str:
        return (dd.runes.get(int(keystone)) or {}).get("name") or str(keystone)

    if want and _rune_class(baseline["keystone"]) != want:
        for rows in by_source.values():
            ref = rows[0].get("wr")
            fits = [o for o in rows if _rune_class(o["keystone"]) == want and o.get("wr") is not None and ref is not None and o["wr"] >= ref - RUNES_MAX_WR_LOSS]
            if fits:
                chosen = max(fits, key=lambda o: o.get("games") or 0)
                label = "de bola de nieve" if want == "aggressive" else "para pelear largo"
                why = f"{name(chosen['keystone'])}: piedra angular {label} por el plan de partida"
                break
    if why is None:
        if baseline["source"] == "matchup":
            why = f"{name(baseline['keystone'])}: la más jugada contra {dd.name(opp)} ({baseline.get('games')} partidas en OP.GG)"
        elif baseline["source"] == "lola":
            share = baseline.get("share")
            why = f"{name(baseline['keystone'])}: la más jugada con él" + (f" ({share * 100:.0f} % de sus partidas)" if share else "")
        else:
            why = f"{name(baseline['keystone'])}: la de su build más jugada"
    shards = chosen.get("shards") or next((o.get("shards") for o in options if o["keystone"] == chosen["keystone"] and o.get("shards")), None)
    ids = [int(i) for i in chosen["ids"]]
    return {
        "keystone": int(chosen["keystone"]), "primary": int(chosen["primary"]), "secondary": int(chosen["secondary"]), "ids": ids,
        "names": [(dd.runes.get(i) or {}).get("name") or str(i) for i in ids], "shards": shards or [], "why": why,
        "games": chosen.get("games"), "wr": round(chosen["wr"], 1) if chosen.get("wr") is not None else None,
        "options": [{"keystone": o["keystone"], "source": o["source"], "games": o.get("games"), "wr": round(o["wr"], 1) if o.get("wr") is not None else None}
                    for o in options],
    }


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


def _tempo_line(team_plan: dict) -> list[dict]:
    """La partida según la duración: si el draft gana (o pierde menos) en partidas cortas o largas. Nunca dice «gana»
    si en las dos fases está por debajo del 50 %."""
    tempo, early, late = team_plan["tempo"], team_plan["early"], team_plan["late"]
    if tempo == "even" or early is None:
        return []
    short, long_ = f"{early:.0f} % antes del 25", f"{late:.0f} % pasado el 35"
    if tempo == "early":
        if round(early) >= 50:
            text = (f"Vuestro draft es mejor en partidas cortas ({short} frente a {late:.0f} % {'pasado el 35' if round(late) < 50 else 'en las largas'})"
                    ": busca ventajas pronto y cierra antes del 30.")
        else:
            text = (f"Vuestro draft va por detrás en cualquier duración, pero sufre más si la partida se alarga ({short}, {long_}): "
                    "juega las primeras peleas y objetivos con ventaja numérica y no le des tiempo a escalar.")
    else:
        if round(late) >= 50:
            text = (f"Vuestro draft es mejor en partidas largas ({long_} frente a {early:.0f} % {'antes del 25' if round(early) < 50 else 'en las cortas'})"
                    ": no regales nada pronto y pelea cuando tengáis objetos.")
        else:
            text = (f"Vuestro draft va por detrás en cualquier duración, pero sufre menos si la partida se alarga ({short}, {long_}): "
                    "aguanta las primeras fases sin regalar nada y busca las peleas tarde.")
    return [{"topic": "Partida", "text": text}]


def _plan_lines(team_plan: dict | None, role: str, mine: dict[str, str], theirs: dict[str, str]) -> list[dict]:
    """Cómo jugar la partida, en frases cortas: partida, jungla, ganks, objetivos y línea (según el rol).

    ``mine`` / ``theirs``: nombre del campeón de cada rol. Todo sale de la curva por duración y de quién gana cada
    línea (OP.GG o el WR del cruce): no hay frases hechas que no dependan del draft.
    """
    if not team_plan:
        return []
    lanes = team_plan["lanes"]
    prio = team_priorities(team_plan)
    out: list[dict] = []
    out += _tempo_line(team_plan)
    lane_of = {"TOP": "TOP", "MID": "MID", "ADC": "ADC", "SUPPORT": "ADC"}

    def vs(r: str) -> str:
        return f"{mine.get(r, '?')} contra {theirs.get(r, '?')}"

    def known(r: str) -> bool:  # con el draft a medias, una línea sin los dos campeones no está «igualada»: no se sabe
        return bool(mine.get(r) and theirs.get(r))

    def state(r: str) -> str:
        if not known(r):
            return f"{LANE_NAME[r]} aún sin rival"
        v = lanes.get(r, 0)
        return f"{LANE_NAME[r]} {'gana su línea' if v > 0 else 'pierde su línea' if v < 0 else 'está igualado'}"

    def why(a: str, b: str) -> str:
        va, vb = lanes.get(a, 0), lanes.get(b, 0)
        if va == vb and known(a) and known(b):
            verb = "ganan su línea" if va > 0 else "pierden su línea" if va < 0 else "están igualados"
            return f"{LANE_NAME[a]} y {LANE_NAME[b]} {verb}"
        return f"{state(a)} y {state(b)}"

    dragon = {"ours": f"Dragón: tenéis prioridad ({why('MID', 'ADC')}); preparadlo juntos 1 minuto antes.",
              "theirs": f"Dragón: no tenéis prioridad ({why('MID', 'ADC')}); no lo fuercéis, cambiadlo por larvas, heraldo o una torre arriba.",
              "split": f"Dragón: prioridad repartida ({why('MID', 'ADC')}); disputadlo solo si mid y bot empujan antes que los suyos."}[prio["dragon"]]
    top_side = {"ours": f"Larvas y heraldo: tenéis prioridad ({why('TOP', 'MID')}); jugad ese lado.",
                "theirs": f"Larvas y heraldo: no tenéis prioridad ({why('TOP', 'MID')}); cededlos y jugad el lado del dragón.",
                "split": f"Larvas y heraldo: prioridad repartida ({why('TOP', 'MID')}); van al que empuje primero top y mid."}[prio["top_side"]]
    if role == "JUNGLE":
        jg, mid = lanes.get("JUNGLE", 0), lanes.get("MID", 0)
        if jg > 0:
            text = f"Ganas a {theirs.get('JUNGLE', 'su jungla')}: " + ("invade su jungla con tu mid, que gana su línea." if mid > 0
                                                                       else "puedes robarle campamentos, pero sin tu mid con prioridad, invade solo si sabes dónde está.")
        elif jg < 0:
            text = f"{theirs.get('JUNGLE', 'Su jungla')} te gana en la jungla: no le invadas; sigue su ruta, cubre las líneas a las que va y contragankea."
        else:
            text = "Jungla igualada: " + ("con tu mid ganando puedes invadir su lado de la jungla." if mid > 0 else "farmea tu jungla y juega con tus líneas.")
        out.append({"topic": "Jungla", "text": text})
        sides = [r for r in ("TOP", "MID", "ADC") if known(r)]
        winning = [r for r in sides if lanes.get(r, 0) > 0]
        even = [r for r in sides if lanes.get(r, 0) == 0]
        losing = [r for r in sides if lanes.get(r, 0) < 0]
        pending = [r for r in ("TOP", "MID", "ADC") if not known(r)]
        gank = []
        if winning:
            gank.append(f"ganquea {_join_names([LANE_NAME[r] for r in winning])} ({_join_names([vs(r) for r in winning])}): ganan su línea y con tu ayuda hacen bola de nieve")
        if even:
            gank.append(f"{_join_names([LANE_NAME[r] for r in even])} {'está igualado' if len(even) == 1 else 'están igualados'}: un gank decide la línea")
        if losing and not winning and not even:
            gank.append("todas tus líneas pierden su línea: no fuerces ganks; farmea, cúbrelas con contraganks cuando empujen y juega para el final de la partida")
        elif losing:
            gank.append(f"{_join_names([LANE_NAME[r] for r in losing])} {'pierde' if len(losing) == 1 else 'pierden'} su línea: no fuerces ganks ahí, juega a contragankear y a cubrirlos en defensa")
        if pending:
            gank.append(f"{_join_names([LANE_NAME[r] for r in pending])}: aún sin rival")
        out.append({"topic": "Ganks", "text": "; ".join(g[0].upper() + g[1:] if i == 0 else g for i, g in enumerate(gank)) + "."})
        out.append({"topic": "Objetivos", "text": f"{dragon} {top_side}"})
    else:
        own = lanes.get(lane_of.get(role, role), 0)
        duo = role in ("ADC", "SUPPORT")
        if not known(lane_of[role]):
            out.append({"topic": "Línea", "text": "Aún no se sabe tu rival de línea: el plan se completa cuando elija."})
            out.append({"topic": "Objetivos", "text": top_side if role == "TOP" else dragon if duo else f"{dragon} {top_side}"})
            return out
        who = vs(lane_of[role]) if not duo else f"{mine.get('ADC', '?')} y {mine.get('SUPPORT', '?')} contra {theirs.get('ADC', '?')} y {theirs.get('SUPPORT', '?')}"
        if own > 0:
            text = f"Ganáis la línea ({who})" if duo else f"Ganas tu línea ({who})"
            text += ": presiona, coge placas y llega primero al río para ayudar a tu jungla."
        elif own < 0:
            text = f"Perdéis la línea ({who})" if duo else f"Pierdes tu línea ({who})"
            text += ": farmea seguro bajo torre, no gastes el Destello y avisa a tu jungla cuando empujen."
        else:
            text = f"Línea igualada ({who}): la decide quién juega mejor los niveles y los ganks; mira el minimapa."
        out.append({"topic": "Línea", "text": text})
        jg = lanes.get("JUNGLE", 0)
        if role == "MID":
            if own > 0:
                mid_text = "Con prioridad en mid, mueve primero al río y acompaña a tu jungla" + (" a invadir: gana a la suya." if jg > 0 else ".")
            elif own < 0:
                mid_text = "Sin prioridad en mid, no sigas a tu jungla a su lado: empuja tu oleada primero."
            else:
                mid_text = "Mid igualado: muévete con tu jungla solo cuando tu oleada esté empujada contra su torre."
            out.append({"topic": "Jungla", "text": mid_text})
            out.append({"topic": "Objetivos", "text": f"{dragon} {top_side}"})
        elif role == "TOP":
            out.append({"topic": "Objetivos", "text": top_side})
        else:
            out.append({"topic": "Objetivos", "text": dragon})
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


def _player(svc, ev, cat, champ_info, side: str, role: str, champ: int, theirs: dict, enemy_comp: dict | None, team: dict,
            pplan: dict | None = None) -> dict:
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
    pplan = pplan or {"style": "normal", "text": None}
    base = _choose_path(cat, base, _paths(cat, lola), pplan)
    lola_runes = None
    if lola_src is not None and getattr(lola_src, "enabled", False) and ev.scope and hasattr(lola_src, "rune_pages"):
        lola_runes = lola_src.rune_pages(champ, role, ev.scope.tier, ev.scope.region, ev.scope.patches[0], allow_fetch=ev.fetch)
    want_runes = "aggressive" if pplan.get("style") == "aggressive" or (base.get("path") or {}).get("aggressive") else (
        "durable" if pplan.get("style") == "scaling" else None)
    runes = _choose_runes(dd, base, base.get("rune_pages") or [], lola_runes, opp, want_runes)
    otp = _otp_items(svc, champ, role)
    own = _own_items(svc, cat, champ, role)
    pool = _candidates(cat, base, general, lola, otp, own, _with_core(lola, (base.get("core") or [])[:CORE]))
    exclusive = _Exclusive(lola, cat)
    style = _style(cat, (base.get("core") or list(pool)[:3]), tags)
    squishy = _squishy(style, tags)
    ctx = _Context(svc, [c for c in theirs.values() if c], enemy_comp, champ_info)
    plan = team.get(champ) or {}

    scored = {i: _score(i, data, cat, style, squishy, ctx, otp, lola, plan) for i, data in pool.items()}
    chosen: list[dict] = []
    for i in (base.get("core") or [])[:CORE]:
        if i in pool:
            path = base.get("path")
            why = (f"{'Build de bola de nieve' if path['aggressive'] else 'Build para escalar'} por el plan de partida: "
                   f"{dd_item(cat, path['first'])} de primer objeto ({path['share'] * 100:.0f} % de sus builds empiezan así)" if path
                   else f"Núcleo contra {dd.name(opp)} (OP.GG, {base['games']} partidas)" if base.get("source") == "matchup" and base.get("games")
                   else f"Núcleo de su build (contra {dd.name(opp)} aún hay pocas partidas: {base['thin_matchup']})" if base.get("thin_matchup")
                   else "Núcleo de su build (lo que define al campeón)")
            chosen.append({"item": i, "kind": "core", "reason": why})
    anti = sum(1 for c in chosen if cat and cat.anti_heal(c["item"]))
    resist = 0
    # Objetos que se compran de primero (bola de nieve): no se pueden meter de 3.º a 5.º; quedan como otra forma de empezar.
    rush = {
        i: f"Otra forma de empezar: se compra de primer objeto (posición media {str(d['lola_pos']).replace('.', ',')} de 5), no al final"
        for i, d in pool.items()
        if d.get("lola_pos") is not None and d["lola_pos"] < RUSH_POS and not any(c["item"] == i for c in chosen)
    }
    for i, (_score_value, why) in sorted(scored.items(), key=lambda kv: -kv[1][0]):
        if len(chosen) >= ITEMS:
            break
        if any(c["item"] == i for c in chosen) or i in rush or pool[i].get("off_core"):
            continue  # ya está, se compra de primero o no se compra con este núcleo
        if any(exclusive(i, c["item"]) for c in chosen):
            continue  # casi nunca va con lo que ya lleva (pasiva única o camino distinto)
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
        # La más barata de su tipo de daño que pueda ir con su núcleo (Recordatorio letal no puede ir con la Cuchilla
        # negra de Smolder: se busca otra; si ninguna puede, que la lleve otro aliado).
        options = [
            i for i in sorted(cat.items, key=cat.cost)
            if cat.anti_heal(i) and cat.is_legendary(i) and cat.get(i).get("buyable") and not cat.get(i).get("champion") and STYLE_TAG[style] in cat.tags(i)
            and not any(c["kind"] == "core" and exclusive(i, c["item"]) for c in chosen)
        ]
        option = options[0] if options else None
        if option:
            entry = {"item": option, "kind": "situational", "reason": f"Heridas graves contra {ctx.who(ctx.healers)}{plan['anti_heal']}"}
            clash = next((k for k, c in enumerate(chosen) if c["kind"] != "core" and exclusive(option, c["item"])), None)
            if clash is not None:
                chosen[clash] = entry  # sustituye al que no puede ir con ella (Recordatorio letal en vez de Dominik)
            elif len(chosen) >= ITEMS:
                chosen[-1] = entry
            else:
                chosen.append(entry)
    # El núcleo primero y el resto en el orden en que se compra de verdad (posición media en Lolalytics).
    core_n = sum(1 for c in chosen if c["kind"] == "core")
    chosen = chosen[:core_n] + sorted(chosen[core_n:], key=lambda c: pool.get(c["item"], {}).get("lola_order") or 9.0)
    ranked = [(i, why) for i, (_s, why) in sorted(scored.items(), key=lambda kv: -kv[1][0]) if not any(c["item"] == i for c in chosen)]
    first_alt = [{"item": i, "reason": rush[i]} for i, _why in ranked if i in rush][:1]
    alternatives = [
        {"item": i, "reason": why or "Otra opción frecuente"} for i, why in ranked if i not in rush and not pool[i].get("off_core")
    ][: 4 - len(first_alt)] + first_alt
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
        "runes": runes,
        "starter": base.get("starter") or [],
        "spells": base.get("spells") or [],
        "skills": base.get("skill_order") or (">".join(base["skill_max"]) if base.get("skill_max") else None),
        "matchup": {"opponent": opp, "games": base.get("games"), "wins": base.get("wins")} if base.get("source") == "matchup" else None,
        "otp": (otp or {}).get("best"),
        "enemy": {"phys": ctx.phys, "magic": ctx.magic, "groups": ctx.groups},
        "sources": sources,
        "plan": pplan,
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
                    if hasattr(lola, "rune_pages"):
                        jobs.append(lambda c=champ, r=role: lola.rune_pages(c, r, ev.scope.tier, ev.scope.region, ev.scope.patches[0]))
        if opgg is not None:
            opgg.prefetch(jobs, timeout=10)
        else:
            for job in jobs:
                job()
    ev.fetch = False  # a partir de aquí, solo lo que ya esté en caché (lo que falte llega en segundo plano)
    plans = _game_plans(svc, ev, opgg, ally, enemy)
    names = {side: {role: svc.dd.name(c) for role, c in team.items() if c} for side, team in (("ally", ally), ("enemy", enemy))}
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
                pplan = _player_plan(plans.get(side), role)
                pplan["lines"] = _plan_lines(plans.get(side), role, names[side], names["enemy" if side == "ally" else "ally"])
                p = _player(svc, ev, cat, champ_info, side, role, mine[role], theirs, rival_comp, plan, pplan)
                if role == "SUPPORT":
                    holders = sum(1 for v in plan.values() if v.get("anti_heal") is not None)
                    p["team_items"] = _support_items(cat, _Context(svc, [c for c in theirs.values() if c], rival_comp, champ_info), holders)
                out[side].append(p)
    out["plans"] = {
        side: ({"tempo": tp["tempo"], "early": tp["early"], "late": tp["late"], "lanes": tp["lanes"], "priorities": team_priorities(tp)} if tp else None)
        for side, tp in plans.items()
    }
    return out
