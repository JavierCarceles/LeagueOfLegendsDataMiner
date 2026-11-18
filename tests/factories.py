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


def write_ddragon_cache(cache_dir: Path, version: str = DD_VERSION) -> Path:
    """Escribe ``<cache_dir>/<version>/champion.json`` (lo que lee ``DDragon.load(allow_network=False)``)."""
    target = Path(cache_dir) / version / "champion.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(champion_json(version), ensure_ascii=False), encoding="utf-8")
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
        gemini_keys=[],
        gemini_models=[],
        opgg_enabled=False,
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

    def __init__(self, *, versions=("16.22",), tier_lists=None, counters=None, synergies=None) -> None:
        self._versions = list(versions)
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

    def match_ids(self, platform, puuid, *, queue=None, ranked_only=False, start_time=None, start=0, count=20, **kw):
        self._record("match_ids", platform, puuid, queue, ranked_only, start_time, count)
        return list(self.ladder.get(puuid, []))[start : start + count]

    def match(self, match_id, **kw):
        self._record("match", match_id)
        payload = self.payloads.get(match_id)
        return copy.deepcopy(payload) if payload is not None else None
