"""Factorías de datos sintéticos (pero con la forma real de la API) para los tests.

* Un Data Dragon mínimo (``champion.json``) para trabajar sin red.
* Payloads de match-v5 completos: metadata, info, equipos con bans/objetivos
  y 10 participantes con ``challenges``.
* Un generador de muchas partidas deterministas donde un campeón es
  claramente más fuerte (para probar el modelo de drafts).
* Un cliente de Riot falso para el recolector.
* Un constructor de ``Services`` aislado en carpetas temporales.
"""

from __future__ import annotations

import copy
import json
import math
import random
import threading
from pathlib import Path

DD_VERSION = "16.22.1"
GAME_VERSION = "16.22.1.2"
GAME_START_MS = 1_763_000_000_000  # ~13/11/2025
TEST_KEY = "RGAPI-00000000-0000-0000-0000-000000000000"

# ----------------------------------------------------------------- campeones
AATROX, DARIUS, GAREN = 266, 122, 86
LEE_SIN, VIEGO, WUKONG = 64, 234, 62
AHRI, ZED, SYNDRA, ORIANNA = 103, 238, 134, 61
JINX, KAISA, EZREAL = 222, 145, 81
THRESH, NAUTILUS, LEONA = 412, 111, 89

# id de Data Dragon -> (key numérica, nombre, tags, info) — valores reales del parche 16.x
CHAMPIONS: dict[str, tuple[int, str, list[str], dict]] = {
    "Aatrox": (AATROX, "Aatrox", ["Fighter"], {"attack": 8, "defense": 4, "magic": 3, "difficulty": 4}),
    "Darius": (DARIUS, "Darius", ["Fighter", "Tank"], {"attack": 9, "defense": 5, "magic": 1, "difficulty": 2}),
    "Garen": (GAREN, "Garen", ["Fighter", "Tank"], {"attack": 7, "defense": 7, "magic": 1, "difficulty": 5}),
    "LeeSin": (LEE_SIN, "Lee Sin", ["Fighter", "Assassin"], {"attack": 8, "defense": 5, "magic": 3, "difficulty": 6}),
    "Viego": (VIEGO, "Viego", ["Fighter", "Assassin"], {"attack": 6, "defense": 4, "magic": 2, "difficulty": 5}),
    "MonkeyKing": (WUKONG, "Wukong", ["Fighter", "Tank"], {"attack": 8, "defense": 5, "magic": 2, "difficulty": 3}),
    "Ahri": (AHRI, "Ahri", ["Mage", "Assassin"], {"attack": 3, "defense": 4, "magic": 8, "difficulty": 5}),
    "Zed": (ZED, "Zed", ["Assassin"], {"attack": 9, "defense": 2, "magic": 1, "difficulty": 7}),
    "Syndra": (SYNDRA, "Syndra", ["Mage"], {"attack": 2, "defense": 3, "magic": 9, "difficulty": 8}),
    "Orianna": (ORIANNA, "Orianna", ["Mage", "Support"], {"attack": 4, "defense": 3, "magic": 9, "difficulty": 7}),
    "Jinx": (JINX, "Jinx", ["Marksman"], {"attack": 9, "defense": 2, "magic": 4, "difficulty": 6}),
    "Kaisa": (KAISA, "Kai'Sa", ["Marksman", "Mage"], {"attack": 8, "defense": 5, "magic": 3, "difficulty": 6}),
    "Ezreal": (EZREAL, "Ezreal", ["Marksman", "Mage"], {"attack": 7, "defense": 2, "magic": 6, "difficulty": 7}),
    "Thresh": (THRESH, "Thresh", ["Support", "Tank"], {"attack": 5, "defense": 6, "magic": 6, "difficulty": 7}),
    "Nautilus": (NAUTILUS, "Nautilus", ["Tank", "Support"], {"attack": 4, "defense": 6, "magic": 6, "difficulty": 6}),
    "Leona": (LEONA, "Leona", ["Tank", "Support"], {"attack": 4, "defense": 8, "magic": 3, "difficulty": 4}),
}
CHAMPION_NAME = {key: ddid for ddid, (key, *_rest) in CHAMPIONS.items()}
ALL_CHAMPION_IDS = [key for key, *_rest in CHAMPIONS.values()]

ROLES = ("TOP", "JUNGLE", "MID", "ADC", "SUPPORT")
ROLE_TO_POSITION = {"TOP": "TOP", "JUNGLE": "JUNGLE", "MID": "MIDDLE", "ADC": "BOTTOM", "SUPPORT": "UTILITY"}
ROLE_POOLS = {
    "TOP": [AATROX, DARIUS, GAREN],
    "JUNGLE": [LEE_SIN, VIEGO, WUKONG],
    "MID": [AHRI, ZED, SYNDRA, ORIANNA],
    "ADC": [JINX, KAISA, EZREAL],
    "SUPPORT": [THRESH, NAUTILUS, LEONA],
}
DEFAULT_BLUE = {"TOP": AATROX, "JUNGLE": LEE_SIN, "MID": AHRI, "ADC": JINX, "SUPPORT": THRESH}
DEFAULT_RED = {"TOP": DARIUS, "JUNGLE": VIEGO, "MID": ZED, "ADC": KAISA, "SUPPORT": NAUTILUS}
# Campeones libres en el draft por defecto (GAREN repetido: en SoloQ ambos equipos pueden banear lo mismo).
DEFAULT_BANS = ([GAREN, SYNDRA, -1, EZREAL, LEONA], [WUKONG, ORIANNA, GAREN, -1, -1])


# --------------------------------------------------------------- Data Dragon
def champion_json(version: str = DD_VERSION) -> dict:
    data = {}
    for ddid, (key, name, tags, info) in CHAMPIONS.items():
        data[ddid] = {
            "version": version,
            "id": ddid,
            "key": str(key),
            "name": name,
            "title": f"título de {name}",
            "blurb": "",
            "info": dict(info),
            "image": {"full": f"{ddid}.png", "sprite": "champion0.png", "group": "champion", "x": 0, "y": 0, "w": 48, "h": 48},
            "tags": list(tags),
            "partype": "Maná",
            "stats": {},
        }
    return {"type": "champion", "format": "standAloneComplex", "version": version, "data": data}


# Subconjunto real de ``item.json`` (parche 16.23, es_ES) con las recetas que usan los tests.
ITEMS: dict[str, dict] = {
    "1001": {'name': 'Botas', 'gold': {'base': 300, 'total': 300, 'sell': 210, 'purchasable': True}, 'tags': ['Boots'], 'maps': {'11': True}, 'into': ['3047', '3006', '3111']},
    "1029": {'name': 'Armadura de tela', 'gold': {'base': 300, 'total': 300, 'sell': 210, 'purchasable': True}, 'tags': ['Armor'], 'maps': {'11': True}, 'into': ['3047']},
    "3047": {'name': 'Botas blindadas', 'gold': {'base': 600, 'total': 1200, 'sell': 840, 'purchasable': True}, 'tags': ['Armor', 'Boots'], 'maps': {'11': True}, 'from': ['1001', '1029'], 'into': [], 'depth': 2},
    "1033": {'name': 'Manto de anulación de magia', 'gold': {'base': 400, 'total': 400, 'sell': 280, 'purchasable': True}, 'tags': ['SpellBlock'], 'maps': {'11': True}, 'into': ['3111']},
    "3111": {'name': 'Botas de mercurio', 'gold': {'base': 550, 'total': 1250, 'sell': 875, 'purchasable': True}, 'tags': ['Boots', 'SpellBlock', 'Tenacity'], 'maps': {'11': True}, 'from': ['1001', '1033'], 'into': [], 'depth': 2},
    "1036": {'name': 'Espada larga', 'gold': {'base': 350, 'total': 350, 'sell': 245, 'purchasable': True}, 'tags': ['Damage', 'Lane'], 'maps': {'11': True}, 'into': ['2021', '3133', '3035', '3044', '3123']},
    "1037": {'name': 'Pico', 'gold': {'base': 875, 'total': 875, 'sell': 613, 'purchasable': True}, 'tags': ['Damage'], 'maps': {'11': True}, 'into': ['3071', '3053', '3161']},
    "1028": {'name': 'Cristal de rubí', 'gold': {'base': 400, 'total': 400, 'sell': 280, 'purchasable': True}, 'tags': ['Health'], 'maps': {'11': True}, 'into': ['2021', '3067', '3044', '3053', '3161']},
    "2021": {'name': 'Tuneladora', 'gold': {'base': 400, 'total': 1150, 'sell': 805, 'purchasable': True}, 'tags': ['Health', 'Damage'], 'maps': {'11': True}, 'from': ['1036', '1028'], 'into': ['3053', '3161'], 'depth': 2},
    "3044": {'name': 'Bacteriófago', 'gold': {'base': 350, 'total': 1100, 'sell': 770, 'purchasable': True}, 'tags': ['Health', 'Damage', 'NonbootsMovement'], 'maps': {'11': True}, 'from': ['1028', '1036'], 'into': ['3071'], 'depth': 2},
    "3067": {'name': 'Gema avivadora', 'gold': {'base': 150, 'total': 800, 'sell': 560, 'purchasable': True}, 'tags': ['Health', 'CooldownReduction', 'AbilityHaste'], 'maps': {'11': True}, 'from': ['1028', '2022'], 'into': ['3071'], 'depth': 2},
    "2022": {'name': 'Mota brillante', 'gold': {'base': 250, 'total': 250, 'sell': 175, 'purchasable': True}, 'tags': ['CooldownReduction'], 'maps': {'11': True}, 'into': ['3133', '3067']},
    "3071": {'name': 'Cuchilla negra', 'gold': {'base': 225, 'total': 3000, 'sell': 2100, 'purchasable': True}, 'tags': ['Health', 'Damage', 'CooldownReduction', 'OnHit', 'NonbootsMovement', 'ArmorPenetration', 'AbilityHaste'], 'maps': {'11': True}, 'from': ['3044', '3067', '1037'], 'depth': 3},
    "3133": {'name': 'Martillo de guerra de Caulfield', 'gold': {'base': 100, 'total': 1050, 'sell': 735, 'purchasable': True}, 'tags': ['Damage', 'CooldownReduction', 'AbilityHaste'], 'maps': {'11': True}, 'from': ['1036', '2022', '1036'], 'into': ['6694'], 'depth': 2},
    "3035": {'name': 'Últimas palabras', 'gold': {'base': 750, 'total': 1450, 'sell': 1015, 'purchasable': True}, 'tags': ['ArmorPenetration', 'Damage'], 'maps': {'11': True}, 'from': ['1036', '1036'], 'into': ['6694'], 'depth': 2},
    "6694": {'name': 'Rencor de Serylda', 'gold': {'base': 500, 'total': 3000, 'sell': 2100, 'purchasable': True}, 'tags': ['Damage', 'CooldownReduction', 'ArmorPenetration', 'AbilityHaste'], 'maps': {'11': True}, 'from': ['3133', '3035'], 'depth': 3},
    "1055": {'name': 'Espada de Doran', 'gold': {'base': 450, 'total': 450, 'sell': 180, 'purchasable': True}, 'tags': ['Health', 'Damage', 'LifeSteal', 'SpellVamp', 'Lane'], 'maps': {'11': True}},
    "2003": {'name': 'Poción de vida', 'gold': {'base': 50, 'total': 50, 'sell': 20, 'purchasable': True}, 'tags': ['HealthRegen', 'Consumable', 'Lane', 'Jungle'], 'maps': {'11': True}, 'consumed': True},
    "2055": {'name': 'Guardián de control', 'gold': {'base': 75, 'total': 75, 'sell': 30, 'purchasable': True}, 'tags': ['Consumable', 'Lane', 'Stealth', 'Vision'], 'maps': {'11': True}, 'consumed': True},
    "3340": {'name': 'Guardián invisible', 'gold': {'base': 0, 'total': 0, 'sell': 0, 'purchasable': True}, 'tags': ['Active', 'Jungle', 'Lane', 'Trinket', 'Vision'], 'maps': {'11': True}},
    "3123": {'name': 'Llamada del verdugo', 'gold': {'base': 450, 'total': 800, 'sell': 560, 'purchasable': True}, 'tags': ['Damage'], 'maps': {'11': True}, 'from': ['1036'], 'into': [], 'depth': 2, 'description': 'Aplica Heridas graves.'},
    "3053": {'name': 'Calibrador de Sterak', 'gold': {'base': 775, 'total': 3200, 'sell': 2240, 'purchasable': True}, 'tags': ['Health', 'Damage', 'Tenacity'], 'maps': {'11': True}, 'from': ['1037', '2021', '1028'], 'depth': 3},
    "3161": {'name': 'Lanza de Shojin', 'gold': {'base': 675, 'total': 3100, 'sell': 2170, 'purchasable': True}, 'tags': ['Health', 'Damage', 'AbilityHaste'], 'maps': {'11': True}, 'from': ['1037', '2021', '1028'], 'depth': 3},
    "1102": {'name': 'Cría de Caminabrisas', 'gold': {'base': 450, 'total': 450, 'sell': 0, 'purchasable': True}, 'tags': ['Jungle'], 'maps': {'11': True}},
    "2138": {'name': 'Elixir de hierro', 'gold': {'base': 500, 'total': 500, 'sell': 200, 'purchasable': True}, 'tags': ['Health', 'Consumable', 'NonbootsMovement', 'Tenacity'], 'maps': {'11': True}, 'consumed': True},
    "1042": {'name': 'Daga', 'gold': {'base': 250, 'total': 250, 'sell': 175, 'purchasable': True}, 'tags': ['AttackSpeed'], 'maps': {'11': True}, 'into': ['3006']},
    "3006": {'name': 'Grebas de berserker', 'gold': {'base': 300, 'total': 1100, 'sell': 770, 'purchasable': True}, 'tags': ['AttackSpeed', 'Boots'], 'maps': {'11': True}, 'from': ['1001', '1042', '1042'], 'into': [], 'depth': 2},
    "1052": {'name': 'Tomo amplificador', 'gold': {'base': 400, 'total': 400, 'sell': 280, 'purchasable': True}, 'tags': ['SpellDamage'], 'maps': {'11': True}, 'into': ['3916']},
    "3916": {'name': 'Orbe del olvido', 'gold': {'base': 400, 'total': 800, 'sell': 560, 'purchasable': True}, 'tags': ['SpellDamage'], 'maps': {'11': True}, 'from': ['1052'], 'into': [], 'depth': 2, 'description': 'Aplica Heridas graves.'},
    "1082": {'name': 'Sello oscuro', 'gold': {'base': 350, 'total': 350, 'sell': 140, 'purchasable': True}, 'tags': ['Health', 'SpellDamage', 'Lane'], 'maps': {'11': True}, 'into': []},
    "3865": {'name': 'Atlas mundial', 'gold': {'base': 400, 'total': 400, 'sell': 160, 'purchasable': True}, 'tags': ['Health', 'ManaRegen', 'Vision', 'GoldPer', 'Lane'], 'maps': {'11': True}},
    "3866": {'name': 'Brújula rúnica', 'gold': {'base': 400, 'total': 400, 'sell': 160, 'purchasable': False}, 'tags': ['SpellDamage', 'ManaRegen', 'Vision', 'GoldPer', 'Lane'], 'maps': {'11': True}, 'inStore': False},
    "3867": {'name': 'Botín universal', 'gold': {'base': 400, 'total': 400, 'sell': 160, 'purchasable': False}, 'tags': ['SpellDamage', 'ManaRegen', 'Vision', 'GoldPer', 'Lane'], 'maps': {'11': True}, 'into': ['3870'], 'inStore': False},
    "2422": {'name': 'Calzado ligeramente mágico', 'gold': {'base': 300, 'total': 300, 'sell': 90, 'purchasable': False}, 'tags': ['Boots'], 'maps': {'11': True}, 'into': ['3006', '3047', '3111'], 'inStore': False},
    "2010": {'name': 'Galleta de la voluntad eterna', 'gold': {'base': 50, 'total': 50, 'sell': 5, 'purchasable': False}, 'tags': [], 'maps': {'11': True}, 'consumed': True, 'inStore': False},
    "3870": {'name': 'Tejesueños', 'gold': {'base': 0, 'total': 400, 'sell': 160, 'purchasable': True}, 'tags': ['Health', 'HealthRegen', 'ManaRegen', 'Vision', 'GoldPer', 'Lane'], 'maps': {'11': True}, 'from': ['3867'], 'depth': 2},
}


def item_json(version: str = DD_VERSION) -> dict:
    return {"type": "item", "version": version, "data": json.loads(json.dumps(ITEMS))}


def item_catalog():
    from app.coach.items import ItemCatalog

    return ItemCatalog.from_ddragon(item_json())


def write_ddragon_cache(cache_dir: Path, version: str = DD_VERSION) -> Path:
    """Escribe ``champion.json`` e ``item.json`` en ``<cache_dir>/<version>/`` (lo que lee ``DDragon.load(allow_network=False)``)."""
    target = Path(cache_dir) / version / "champion.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(champion_json(version), ensure_ascii=False), encoding="utf-8")
    (target.parent / "item.json").write_text(json.dumps(item_json(version), ensure_ascii=False), encoding="utf-8")
    return target


def offline_ddragon(cache_dir: Path):
    from app.ddragon import DDragon

    write_ddragon_cache(cache_dir)
    dd = DDragon(cache_dir)
    assert dd.load(allow_network=False), "no se pudo cargar el Data Dragon sintético"
    return dd


# ------------------------------------------------------------------ match-v5
def _participant(pid: int, team: int, position: str, champ: int, win: bool, puuid: str) -> dict:
    jungle = position == "JUNGLE"
    support = position == "UTILITY"
    k = pid % 5
    kills = (7 if win else 3) + k
    deaths = (3 if win else 6) + (k % 2)
    assists = (9 if win else 5) + k
    dmg = (21_000 if win else 16_500) + 211 * pid
    phys = int(dmg * 0.6)
    magic = int(dmg * 0.3)
    return {
        "participantId": pid,
        "puuid": puuid,
        "riotIdGameName": f"Player{pid}",
        "riotIdTagline": "EUW",
        "summonerName": "",
        "summonerId": f"summoner-{puuid}",
        "profileIcon": 5000 + pid,
        "summonerLevel": 300 + pid,
        "championId": champ,
        "championName": CHAMPION_NAME.get(champ, str(champ)),
        "teamId": team,
        "teamPosition": position,
        "individualPosition": position or "Invalid",
        "lane": "JUNGLE" if jungle else ("BOTTOM" if position in ("BOTTOM", "UTILITY") else position or "NONE"),
        "role": "SUPPORT" if support else ("CARRY" if position == "BOTTOM" else "SOLO"),
        "win": win,
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "champLevel": 16 if win else 15,
        "totalMinionsKilled": 30 if support else (40 if jungle else 190 + pid),
        "neutralMinionsKilled": 160 if jungle else 8,
        "goldEarned": (12_800 if win else 10_400) + 37 * pid,
        "totalDamageDealtToChampions": dmg,
        "physicalDamageDealtToChampions": phys,
        "magicDamageDealtToChampions": magic,
        "trueDamageDealtToChampions": dmg - phys - magic,
        "totalDamageTaken": 24_000 + 100 * pid,
        "damageSelfMitigated": 14_000 + 50 * pid,
        "timeCCingOthers": 18 + pid,
        "visionScore": 75 if support else 28,
        "wardsPlaced": 40 if support else 11,
        "wardsKilled": 6 if support else 3,
        "visionWardsBoughtInGame": 6 if support else 3,
        "firstBloodKill": False,
        "gameEndedInEarlySurrender": False,
        "gameEndedInSurrender": False,
        "teamEarlySurrendered": False,
        "item0": 3071,
        "item1": 3047,
        "item2": 6333,
        "item3": 3053,
        "item4": 3742,
        "item5": 0,
        "item6": 3364,
        "summoner1Id": 4,
        "summoner2Id": 11 if jungle else (14 if support else 12),
        "perks": {
            "statPerks": {"defense": 5001, "flex": 5008, "offense": 5005},
            "styles": [
                {
                    "description": "primaryStyle",
                    "style": 8000,
                    "selections": [{"perk": 8010, "var1": 0, "var2": 0, "var3": 0}, {"perk": 9111}, {"perk": 9104}, {"perk": 8299}],
                },
                {"description": "subStyle", "style": 8400, "selections": [{"perk": 8444}, {"perk": 8453}]},
            ],
        },
        "challenges": {
            "laneMinionsFirst10Minutes": 4 if jungle else (12 if support else 68 + k + (6 if win else 0)),
            "jungleCsBefore10Minutes": (54 + k) if jungle else 0,
            "laningPhaseGoldExpAdvantage": 1 if win else 0,
            "earlyLaningPhaseGoldExpAdvantage": 1 if win else 0,
            "maxCsAdvantageOnLaneOpponent": 14.0 if win else 3.0,
            "soloKills": 2 if win else 1,
            "turretPlatesTaken": 3 if win else 1,
            "killParticipation": round(0.55 + 0.02 * k, 4),
            "teamDamagePercentage": round(0.18 + 0.01 * k, 4),
            "kda": round((kills + assists) / max(1, deaths), 4),
        },
    }


def _objectives(blue: bool, kills: int) -> dict:
    """Por defecto: azul primera sangre/torre/heraldo/inhibidor; rojo dragón/larvas/barón."""
    return {
        "atakhan": {"first": False, "kills": 0},
        "baron": {"first": not blue, "kills": 0 if blue else 1},
        "champion": {"first": blue, "kills": kills},
        "dragon": {"first": not blue, "kills": 1 if blue else 3},
        "horde": {"first": not blue, "kills": 2 if blue else 4},
        "inhibitor": {"first": blue, "kills": 1 if blue else 0},
        "riftHerald": {"first": blue, "kills": 1 if blue else 0},
        "tower": {"first": blue, "kills": 8 if blue else 4},
    }


def make_match(
    match_id: str = "EUW1_7000000001",
    *,
    blue: dict[str, int] | None = None,
    red: dict[str, int] | None = None,
    blue_win: bool = True,
    duration: int = 1800,
    queue: int = 420,
    map_id: int = 11,
    game_version: str = GAME_VERSION,
    game_start: int = GAME_START_MS,
    positions: dict[int, str] | None = None,
    puuids: dict[int, str] | None = None,
    bans: tuple[list[int], list[int]] | None = None,
    objectives: dict[int, dict] | None = None,
    overrides: dict[int, dict] | None = None,
    end_timestamp: bool = True,
    early_surrender: bool = False,
    surrender: bool = False,
) -> dict:
    """Payload de ``/lol/match/v5/matches/{id}``.

    * ``blue``/``red``: {rol interno: campeón}, se mezcla con la alineación por defecto.
    * ``positions``: {participantId: teamPosition} para forzar roles duplicados o vacíos.
    * ``overrides``: {participantId: campos} (``challenges`` se mezcla, no se sustituye).
    * ``end_timestamp=False`` simula un payload anterior a 11.20 (``gameDuration`` en ms).
    """
    lineups = {100: {**DEFAULT_BLUE, **(blue or {})}, 200: {**DEFAULT_RED, **(red or {})}}
    platform_id, _, suffix = match_id.partition("_")
    winners = {100: blue_win, 200: not blue_win}
    surrendering_team = 200 if blue_win else 100

    participants = []
    pid = 0
    for team in (100, 200):
        for role in ROLES:
            pid += 1
            position = (positions or {}).get(pid, ROLE_TO_POSITION[role])
            puuid = (puuids or {}).get(pid) or f"puuid-{match_id}-{pid}"
            p = _participant(pid, team, position, lineups[team][role], winners[team], puuid)
            if early_surrender:
                p["gameEndedInEarlySurrender"] = True
                p["teamEarlySurrendered"] = team == surrendering_team
            if surrender:
                p["gameEndedInSurrender"] = True
            participants.append(p)
    participants[0]["firstBloodKill"] = True
    for target_pid, extra in (overrides or {}).items():
        target = participants[target_pid - 1]
        extra = copy.deepcopy(extra)
        challenges = extra.pop("challenges", None)
        target.update(extra)
        if challenges:
            target["challenges"].update(challenges)

    kills = {team: sum(p["kills"] for p in participants if p["teamId"] == team) for team in (100, 200)}
    ban_lists = bans if bans is not None else DEFAULT_BANS
    teams = []
    for index, team in enumerate((100, 200)):
        objs = _objectives(team == 100, kills[team])
        for name, values in ((objectives or {}).get(team) or {}).items():
            objs[name] = {**objs.get(name, {}), **values}
        teams.append(
            {
                "teamId": team,
                "win": winners[team],
                "bans": [{"championId": champ, "pickTurn": turn + 1 + 5 * index} for turn, champ in enumerate(ban_lists[index])],
                "objectives": objs,
                "feats": {"EPIC_MONSTER_KILL": {"featState": 1}, "FIRST_BLOOD": {"featState": 1}, "FIRST_TURRET": {"featState": 1}},
            }
        )

    game_id = int(suffix) if suffix.isdigit() else 0
    info = {
        "endOfGameResult": "GameComplete",
        "gameCreation": game_start - 95_000,
        "gameDuration": duration,
        "gameId": game_id,
        "gameMode": "CLASSIC" if map_id == 11 else "ARAM",
        "gameName": f"teambuilder-match-{game_id}",
        "gameStartTimestamp": game_start,
        "gameType": "MATCHED_GAME",
        "gameVersion": game_version,
        "mapId": map_id,
        "participants": participants,
        "platformId": platform_id,
        "queueId": queue,
        "teams": teams,
        "tournamentCode": "",
    }
    if end_timestamp:
        info["gameEndTimestamp"] = game_start + duration * 1000
    return {
        "metadata": {"dataVersion": "2", "matchId": match_id, "participants": [p["puuid"] for p in participants]},
        "info": info,
    }


def participant(payload: dict, pid: int) -> dict:
    """Participante crudo del payload por ``participantId``."""
    return next(p for p in payload["info"]["participants"] if p["participantId"] == pid)


def generate_draft_matches(
    n: int,
    *,
    seed: int = 20251118,
    strong: int = DARIUS,
    edge: float = 1.5,
    first_id: int = 7_100_000_000,
    platform: str = "EUW1",
) -> list[dict]:
    """``n`` partidas deterministas: picks aleatorios por rol y ``strong`` gana mucho más.

    P(gana el equipo con ``strong``) = σ(edge) ≈ 82 % con edge=1.5; el resto es una moneda.
    """
    rng = random.Random(seed)
    payloads = []
    for i in range(n):
        blue, red = {}, {}
        for role in ROLES:
            blue[role], red[role] = rng.sample(ROLE_POOLS[role], 2)
        diff = edge * ((strong in blue.values()) - (strong in red.values()))
        blue_win = rng.random() < 1.0 / (1.0 + math.exp(-diff))
        picked = set(blue.values()) | set(red.values())
        free = [c for c in ALL_CHAMPION_IDS if c not in picked]
        rng.shuffle(free)
        payloads.append(
            make_match(
                f"{platform}_{first_id + i}",
                blue=blue,
                red=red,
                blue_win=blue_win,
                duration=rng.randint(1250, 2450),
                game_start=GAME_START_MS + i * 3_600_000,
                bans=(free[:3], free[3:6]),
            )
        )
    return payloads


# ---------------------------------------------------------- servicios aislados
def make_settings(base: Path):
    """Settings con todas las rutas en ``base`` y OP.GG desactivado (sin red)."""
    from app.config import Settings

    data = Path(base) / "data"
    return Settings(
        data_dir=data,
        db_path=data / "lol.db",
        legacy_dir=data / "legacy",
        coachings_dir=data / "coachings",
        ddragon_dir=data / "ddragon",
        opgg_enabled=False,
        lolalytics_enabled=False,
        patchnotes_enabled=False,
        warmer_enabled=False,
        calibrate_enabled=False,
        low_priority=False,
    )


def write_env(base: Path, key: str | None = None) -> Path:
    env_path = Path(base) / ".env"
    env_path.write_text(f"RIOT_API_KEY={key}\n" if key else "# sin RIOT_API_KEY\n", encoding="utf-8")
    return env_path


def build_services(base: Path, monkeypatch, *, key: str | None = None):
    """``Services`` real sobre carpetas temporales, .env temporal y Data Dragon offline."""
    import app.services
    from app.services import Services

    monkeypatch.setattr(app.services, "ENV_PATH", write_env(base, key))
    svc = Services(make_settings(base))
    write_ddragon_cache(svc.settings.ddragon_dir)
    assert svc.dd.load(allow_network=False)
    return svc


def store_payloads(svc, payloads: list[dict], seed_tier: str | None = "DIAMOND") -> list[str]:
    """Guarda payloads con el repositorio real e invalida las cachés de agregados/priors."""
    from app.data.repository import store_match
    from app.ingest.extractor import parse_match

    results = [
        store_match(svc.db, parse_match(p), seed_tier=seed_tier, stats_queues=svc.settings.stats_queues) for p in payloads
    ]
    svc.agg.bump()
    svc.priors.invalidate()
    return results


# --------------------------------------------------------------- OP.GG falso
def opgg_row(wr: float, *, pick: float = 5.0, role_rate: float = 0.9) -> dict:
    """Fila de tier list de OP.GG (como la devuelve ``Opgg.tier_list``)."""
    return {"wr": wr, "pick": pick, "ban": 1.0, "role_rate": role_rate, "tier": 2, "rank": 10, "counters": []}


class FakeOpgg:
    """Sustituto en memoria de ``app.external.opgg.Opgg``: sin red ni caché.

    Permite probar cómo ``Evidence`` combina datos externos y propios sin
    activar ``opgg_enabled`` (que descargaría de op.gg).
    """

    enabled = True

    def __init__(self, *, versions=("16.22",), tier_lists=None, counters=None, synergies=None, builds=None) -> None:
        self._versions = list(versions)
        self._builds = builds or {}  # (campeón, rol) -> build de referencia
        self._tier = tier_lists or {}  # rol -> {campeón: fila}
        self._counters = counters or {}  # (rol, campeón) -> {rival: (partidas, victorias)}
        self._synergies = synergies or {}  # (campeón, rol, rol_compañero) -> {compañero: (partidas, victorias)}
        self.calls: list[tuple] = []

    def versions(self, allow_fetch=True):
        return list(self._versions)

    def tier_list(self, role, tier, region, patch, allow_fetch=True):
        self.calls.append(("tier_list", role, tier, region, patch))
        data = self._tier.get(role)
        return {int(k): dict(v) for k, v in data.items()} if data else None

    def counters(self, champ, role, tier, region, patch, allow_fetch=True):
        self.calls.append(("counters", champ, role, tier, region, patch))
        data = self._counters.get((role, champ))
        return dict(data) if data is not None else None

    def synergies(self, champ, role, partner_role, allow_fetch=True):
        data = self._synergies.get((champ, role, partner_role))
        return dict(data) if data else None

    def champion_info(self, champ, role, allow_fetch=True):
        return None

    def matchup_guide(self, champ, opp, role, lang="es_ES", allow_fetch=True):
        return None

    def champion_build(self, champ, role, allow_fetch=True):
        self.calls.append(("champion_build", champ, role, allow_fetch))
        build = self._builds.get((champ, role))
        return dict(build) if build else None

    def prefetch(self, jobs, timeout=15.0, workers=6):
        for job in jobs:
            job()
        return len(jobs)

    def cached_entries(self, source):
        return iter(())


def install_opgg(svc, fake: FakeOpgg) -> FakeOpgg:
    svc.opgg = fake
    svc.priors.opgg = fake
    svc.priors.invalidate()
    return fake


# ------------------------------------------------------------ Riot falso
class FakeRiotClient:
    """Sustituto de ``RiotClient`` para el recolector: sirve payloads de factoría y anota las llamadas."""

    def __init__(self, ladder: dict[str, list[str]], payloads: dict[str, dict | None]) -> None:
        self.ladder = ladder  # puuid -> ids de partidas (más reciente primero, como Riot)
        self.payloads = payloads
        self.calls: list[tuple] = []
        self._lock = threading.Lock()

    def _record(self, *call) -> None:
        with self._lock:
            self.calls.append(call)

    def calls_to(self, name: str) -> list[tuple]:
        with self._lock:
            return [c for c in self.calls if c[0] == name]

    def _entries(self) -> list[dict]:
        return [
            {
                "puuid": puuid,
                "leaguePoints": 1400 - 25 * i,
                "rank": "I",
                "wins": 210 + i,
                "losses": 170 + i,
                "veteran": True,
                "inactive": False,
                "freshBlood": False,
                "hotStreak": False,
            }
            for i, puuid in enumerate(self.ladder)
        ]

    def apex_league(self, platform, tier, queue=420, **kw):
        self._record("apex_league", platform, tier, queue)
        return {"tier": tier, "leagueId": "fake-league", "queue": "RANKED_SOLO_5x5", "name": "Liga de pruebas", "entries": self._entries()}

    def league_entries(self, platform, tier, division, page=1, queue=420, **kw):
        self._record("league_entries", platform, tier, division, page)
        if division == "I" and page == 1:
            return [dict(e, tier=tier, queueType="RANKED_SOLO_5x5", leagueId="fake-league") for e in self._entries()]
        return []

    def match_ids(self, platform, puuid, *, queue=None, ranked_only=False, start_time=None, end_time=None, start=0, count=20, **kw):
        self._record("match_ids", platform, puuid, queue, ranked_only, start_time, count)
        return list(self.ladder.get(puuid, []))[start : start + count]

    def match(self, match_id, **kw):
        self._record("match", match_id)
        payload = self.payloads.get(match_id)
        return copy.deepcopy(payload) if payload is not None else None

    def timeline(self, match_id, **kw):
        self._record("timeline", match_id)
        payload = self.payloads.get(match_id)
        return make_timeline(payload) if payload is not None else None


# ------------------------------------------------------------ timeline-v5
# Escenario del timeline de pruebas (partida de 30 min, ``make_match`` por defecto):
# * pid 1 = top azul (Aatrox) vs pid 6 = top rojo (Darius); junglas: pid 2 (azul) y pid 7 (rojo).
# * 5:10 gank del jungla rojo en top: pid 1 adelantado y sin ward; el rojo coge las larvas a las 6:20.
# * 8:00 pid 1 mata a su rival · 11:30 pid 1 muere 1 contra 1 · 24:00 muere aislado en la jungla roja
#   con 1.750-1.800 de oro sin gastar desde el minuto 23 (le llegaba para sus botas blindadas, que compra
#   muerto a las 24:20) · 25:45 muere en mid y el rojo coge el barón a las 26:10 (pid 1 muerto).
# * Minutos 16-18 con +1.500 de oro (le llegaba para un Bacteriófago de su Cuchilla negra, que compra a
#   las 18:30) y 19-21 sin farmear ni pelear (en el 19 vuelve a base: cuenta como actividad).
# * 6:30 gank del jungla azul (pid 2) en bot · 9:00 dragón azul (pid 2, 4 y 5).
TL_DURATION_MIN = 30
TL_HOME = {
    1: (1300, 11500), 3: (6300, 6700), 4: (11500, 1300), 5: (11300, 1650),
    6: (3300, 13700), 8: (8300, 8000), 9: (13500, 3200), 10: (13250, 3500),
}
TL_JUNGLE_PATHS = {
    2: [(7765, 4020), (8394, 2641), (6823, 5508), (10400, 5300), (11000, 1500), (11000, 1300), (3000, 3000), (6823, 5508), (9500, 4600), (5000, 7000)],
    7: [(10979, 6799), (12512, 6452), (11020, 8457), (7977, 9392), (2600, 13000), (5007, 10471), (7035, 10880), (6406, 12259), (8000, 9500), (9000, 9000)],
}
TL_LATE = {100: (6000, 6500), 200: (9000, 8800)}


def _tl_position(pid: int, minute: int) -> tuple[int, int]:
    team = 100 if pid <= 5 else 200
    if minute == 0:
        return (400, 420) if team == 100 else (14340, 14390)
    if pid in TL_JUNGLE_PATHS and minute <= len(TL_JUNGLE_PATHS[pid]):
        return TL_JUNGLE_PATHS[pid][minute - 1]
    if minute < 14 and pid in TL_HOME:
        x, y = TL_HOME[pid]
        return x + (minute % 3) * 40, y - (minute % 2) * 40
    if pid == 1 and minute == 24:
        return (10600, 6900)  # entrando en la jungla roja
    x, y = TL_LATE[team]
    return x + 120 * (pid % 5), y + 90 * (pid % 5)


def _tl_frame(pid: int, minute: int) -> dict:
    jungle = pid in (2, 7)
    support = pid in (5, 10)
    minions = 0 if jungle else (minute if support else minute * 7)
    jungle_cs = minute * 6 if jungle else 0
    total_gold = 500 + minute * 400 + 10 * pid
    damage = minute * 500
    current = 300 if minute else 500
    if pid == 1:
        if minute <= 10:
            minions = minute * 6
        elif minute <= 18:
            minions = 60 + (minute - 10) * 7
        elif minute <= 21:
            minions = 116  # minutos 19-21 sin farmear
        else:
            minions = 116 + (minute - 21) * 7
        damage = min(minute, 18) * 500 + max(0, minute - 21) * 500
        total_gold = 500 + minute * 300
        current = {16: 1700, 17: 1900, 18: 2100, 23: 1750, 24: 1800}.get(minute, current)
    elif pid == 6:
        minions = int(minute * 7.5)
        total_gold = 500 + minute * 380
    x, y = _tl_position(pid, minute)
    return {
        "championStats": {},
        "currentGold": current,
        "damageStats": {"totalDamageDoneToChampions": damage},
        "goldPerSecond": 0,
        "jungleMinionsKilled": jungle_cs,
        "level": min(18, 1 + minute * 2 // 3),
        "minionsKilled": minions,
        "participantId": pid,
        "position": {"x": x, "y": y},
        "timeEnemySpentControlled": 0,
        "totalGold": total_gold,
        "xp": minute * 650,
    }


def _tl_events() -> list[dict]:
    ev: list[dict] = [{"timestamp": 0, "type": "PAUSE_END", "realTimestamp": 0}]
    for pid in range(1, 11):
        ev.append({"timestamp": 4_000 + pid * 100, "type": "ITEM_PURCHASED", "participantId": pid, "itemId": 1055})
        for level in range(2, 19):
            ev.append({"timestamp": (level - 1) * 90_000 + pid * 10, "type": "LEVEL_UP", "participantId": pid, "level": level})
    ev.append({"timestamp": 100_000, "type": "WARD_PLACED", "creatorId": 1, "wardType": "UNDEFINED"})
    for t, ward in ((420_000, "YELLOW_TRINKET"), (540_000, "YELLOW_TRINKET"), (720_000, "CONTROL_WARD"), (1_000_000, "YELLOW_TRINKET"), (1_300_000, "YELLOW_TRINKET")):
        ev.append({"timestamp": t, "type": "WARD_PLACED", "creatorId": 1, "wardType": ward})
    for t in (200_000, 330_000):
        ev.append({"timestamp": t, "type": "WARD_PLACED", "creatorId": 2, "wardType": "YELLOW_TRINKET"})

    def kill(t, killer, victim, x, y, assists=()):
        ev.append(
            {
                "timestamp": t, "type": "CHAMPION_KILL", "killerId": killer, "victimId": victim,
                "assistingParticipantIds": list(assists), "position": {"x": x, "y": y},
                "bounty": 300, "shutdownBounty": 0, "killStreakLength": 0, "victimDamageReceived": [], "victimDamageDealt": [],
            }
        )

    def monster(t, killer, team, kind, x, y, sub=None, assists=()):
        event = {"timestamp": t, "type": "ELITE_MONSTER_KILL", "killerId": killer, "killerTeamId": team, "monsterType": kind,
                 "position": {"x": x, "y": y}, "assistingParticipantIds": list(assists), "bounty": 0}
        if sub:
            event["monsterSubType"] = sub
        ev.append(event)

    kill(310_000, 7, 1, 2200, 13300, assists=[6])  # gank del jungla rojo: adelantado y sin ward
    for t in (380_000, 385_000, 392_000):
        monster(t, 7, 200, "HORDE", 5007, 10471)
    kill(390_000, 2, 9, 11000, 1300, assists=[4])  # gank del jungla azul en bot
    kill(480_000, 1, 6, 1300, 11000)  # solo kill de pid 1
    monster(540_000, 2, 100, "DRAGON", 9866, 4414, sub="FIRE_DRAGON", assists=[4, 5])
    kill(690_000, 6, 1, 1200, 9000)  # 1 contra 1
    ev.append({"timestamp": 1_000_000, "type": "BUILDING_KILL", "killerId": 6, "teamId": 100, "buildingType": "TOWER_BUILDING",
               "laneType": "TOP_LANE", "towerType": "OUTER_TURRET", "position": {"x": 981, "y": 10441}, "assistingParticipantIds": [], "bounty": 0})
    ev.append({"timestamp": 1_110_000, "type": "ITEM_PURCHASED", "participantId": 1, "itemId": 3071})
    ev.append({"timestamp": 1_320_000, "type": "ITEM_PURCHASED", "participantId": 1, "itemId": 2055})
    kill(1_440_000, 8, 1, 10979, 6799, assists=[9])  # aislado en la jungla roja con oro sin gastar
    ev.append({"timestamp": 1_460_000, "type": "ITEM_PURCHASED", "participantId": 1, "itemId": 3047})  # comprando muerto
    kill(1_545_000, 10, 1, 7400, 7400)
    monster(1_570_000, 7, 200, "BARON_NASHOR", 5007, 10471, assists=[8, 10])
    ev.append({"timestamp": TL_DURATION_MIN * 60_000, "type": "GAME_END", "winningTeam": 100, "gameId": 1})
    return ev


def make_timeline(match: dict) -> dict:
    """Payload de ``/lol/match/v5/matches/{id}/timeline`` coherente con ``make_match``."""
    events = _tl_events()
    frames = []
    for minute in range(TL_DURATION_MIN + 1):
        t0, t1 = minute * 60_000, (minute + 1) * 60_000
        frames.append(
            {
                "timestamp": t0,
                "participantFrames": {str(pid): _tl_frame(pid, minute) for pid in range(1, 11)},
                "events": [e for e in events if t0 <= e["timestamp"] < t1 or (minute == TL_DURATION_MIN and e["timestamp"] >= t0)],
            }
        )
    participants = match["info"]["participants"]
    return {
        "metadata": {"dataVersion": "2", "matchId": match["metadata"]["matchId"], "participants": [p["puuid"] for p in participants]},
        "info": {
            "endOfGameResult": "GameComplete",
            "frameInterval": 60_000,
            "gameId": match["info"]["gameId"],
            "participants": [{"participantId": p["participantId"], "puuid": p["puuid"]} for p in participants],
            "frames": frames,
        },
    }
