"""Convierte el JSON de match-v5 en registros limpios y listos para guardar.

Reglas de calidad (el código antiguo no aplicaba ninguna):

* Solo la Grieta del Invocador (``mapId == 11``) aporta filas por rol.
* Las **remakes** (< 5 min o rendición temprana) se marcan y no cuentan.
* El rol sale de ``teamPosition``; si un equipo tiene un rol duplicado o
  vacío, esos jugadores quedan sin rol (no se inventa el enfrentamiento).
* Las métricas de línea salen de ``challenges`` (CS a los 10 min, ventaja
  de oro/XP al final de la fase de líneas, placas, solo kills…), así que no
  hace falta pedir el timeline (que costaría otra llamada por partida).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.riot.routing import ROLE_INDEX, ROLES, SUMMONERS_RIFT, TEAM_POSITION_TO_ROLE, region_of

REMAKE_SECONDS = 300


@dataclass
class ParticipantRow:
    pid: int
    puuid: str
    game_name: str
    tag_line: str
    team: int
    role: str | None
    champ: int
    win: bool
    kills: int
    deaths: int
    assists: int
    level: int
    cs: int
    cs10: float | None
    gold: int
    dmg: int
    dmg_phys: int
    dmg_magic: int
    dmg_true: int
    taken: int
    mitigated: int
    cc: float
    vision: int
    wards: int
    wards_killed: int
    control_wards: int
    kp: float | None
    dmg_share: float | None
    solo_kills: int | None
    plates: int | None
    lane_adv: int | None
    early_lane_adv: int | None
    max_cs_adv: float | None
    first_blood: bool
    items: list[int] = field(default_factory=list)
    spells: list[int] = field(default_factory=list)
    runes: list[int] = field(default_factory=list)
    profile_icon: int | None = None
    summoner_level: int | None = None


@dataclass
class MatchRecord:
    match_id: str
    platform: str
    region: str
    queue: int
    map_id: int
    patch: str
    game_start: int  # epoch ms
    duration: int  # segundos
    winner: int | None
    is_remake: bool
    surrender: bool
    participants: list[ParticipantRow]
    bans: list[int]
    objectives: dict[str, int | None]
    team_totals: dict[str, int]

    @property
    def minutes(self) -> float:
        return max(self.duration, 60) / 60.0

    @property
    def is_rift(self) -> bool:
        return self.map_id == SUMMONERS_RIFT

    @property
    def full_roles(self) -> bool:
        roles = [(p.team, p.role) for p in self.participants if p.role]
        return len(roles) == 10 and len(set(roles)) == 10

    def lane_pairs(self):
        """(rol, jugador_azul, jugador_rojo) para cada línea con ambos rivales."""
        by_slot = {(p.team, p.role): p for p in self.participants if p.role}
        for role in ROLES:
            blue = by_slot.get((100, role))
            red = by_slot.get((200, role))
            if blue and red:
                yield role, blue, red

    def team_pairs(self):
        """Parejas de aliados ordenadas por rol: (equipo, a, b) con rol(a) < rol(b)."""
        for team in (100, 200):
            members = sorted((p for p in self.participants if p.team == team and p.role), key=lambda p: ROLE_INDEX[p.role])
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    yield team, members[i], members[j]


def parse_patch(game_version: str | None) -> str:
    parts = (game_version or "").split(".")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{int(parts[0])}.{int(parts[1])}"
    return "unknown"


def _num(value, default=0):
    if value is None:
        return default
    try:
        return type(default)(value) if default is not None else value
    except (TypeError, ValueError):
        return default


def _opt_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _opt_int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _duration(info: dict) -> int:
    """Duración en segundos: desde 11.20 (cuando existe gameEndTimestamp) Riot ya la da en segundos; antes, en ms."""
    duration = _num(info.get("gameDuration"), 0)
    if "gameEndTimestamp" not in info and duration > 10_000:
        duration //= 1000
    return duration


def _team_sums(raw_participants: list[dict]) -> tuple[dict[int, int], dict[int, int]]:
    """Kills y daño a campeones de cada equipo (para la participación en kills y la parte del daño)."""
    team_kills = {100: 0, 200: 0}
    team_dmg = {100: 0, 200: 0}
    for p in raw_participants:
        team = p.get("teamId")
        if team in team_kills:
            team_kills[team] += _num(p.get("kills"))
            team_dmg[team] += _num(p.get("totalDamageDealtToChampions"))
    return team_kills, team_dmg


def _slot_counts(raw_participants: list[dict]) -> dict[tuple[int, str], int]:
    """Cuántos jugadores hay en cada (equipo, rol): el rol solo es fiable si es único en su equipo."""
    slot_counts: dict[tuple[int, str], int] = {}
    for p in raw_participants:
        role = TEAM_POSITION_TO_ROLE.get((p.get("teamPosition") or "").upper())
        if role:
            key = (p.get("teamId"), role)
            slot_counts[key] = slot_counts.get(key, 0) + 1
    return slot_counts


def _surrenders(raw_participants: list[dict]) -> tuple[bool, bool]:
    """(rendición temprana, rendición) según lo que dicen los jugadores de la partida."""
    early = any(p.get("gameEndedInEarlySurrender") or p.get("teamEarlySurrendered") for p in raw_participants)
    return early, any(p.get("gameEndedInSurrender") for p in raw_participants)


def _cs10(ch: dict) -> float | None:
    """CS a los 10 min (súbditos de línea + campamentos) de ``challenges``; ``None`` si Riot no lo da."""
    if "laneMinionsFirst10Minutes" in ch or "jungleCsBefore10Minutes" in ch:
        return float(ch.get("laneMinionsFirst10Minutes") or 0) + float(ch.get("jungleCsBefore10Minutes") or 0)
    return None


def _rune_ids(p: dict) -> list[int]:
    """[runa clave, rama principal, rama secundaria] de un jugador (vacía si no hay runas)."""
    styles = (p.get("perks") or {}).get("styles") or []
    if not styles:
        return []
    selections = styles[0].get("selections") or []
    return [
        _num(selections[0].get("perk")) if selections else 0,
        _num(styles[0].get("style")),
        _num(styles[1].get("style")) if len(styles) > 1 else 0,
    ]


def _participant(index: int, p: dict, slot_counts: dict, team_kills: dict[int, int], team_dmg: dict[int, int]) -> ParticipantRow:
    """La fila de un jugador (``index``: su posición, desde 1, por si falta ``participantId``)."""
    team = _num(p.get("teamId"), 0)
    role = TEAM_POSITION_TO_ROLE.get((p.get("teamPosition") or "").upper())
    if role and slot_counts.get((team, role), 0) != 1:
        role = None
    ch = p.get("challenges") or {}
    cs10 = _cs10(ch)

    kills = _num(p.get("kills"))
    assists = _num(p.get("assists"))
    dmg = _num(p.get("totalDamageDealtToChampions"))
    kp = _opt_float(ch.get("killParticipation"))
    if kp is None and team_kills.get(team):
        kp = (kills + assists) / team_kills[team]
    dmg_share = _opt_float(ch.get("teamDamagePercentage"))
    if dmg_share is None and team_dmg.get(team):
        dmg_share = dmg / team_dmg[team]
    runes = _rune_ids(p)

    return ParticipantRow(
        pid=_num(p.get("participantId"), index) or index,
        puuid=p.get("puuid") or "",
        game_name=p.get("riotIdGameName") or p.get("summonerName") or "",
        tag_line=p.get("riotIdTagline") or "",
        team=team,
        role=role,
        champ=_num(p.get("championId")),
        win=bool(p.get("win")),
        kills=kills,
        deaths=_num(p.get("deaths")),
        assists=assists,
        level=_num(p.get("champLevel")),
        cs=_num(p.get("totalMinionsKilled")) + _num(p.get("neutralMinionsKilled")),
        cs10=cs10,
        gold=_num(p.get("goldEarned")),
        dmg=dmg,
        dmg_phys=_num(p.get("physicalDamageDealtToChampions")),
        dmg_magic=_num(p.get("magicDamageDealtToChampions")),
        dmg_true=_num(p.get("trueDamageDealtToChampions")),
        taken=_num(p.get("totalDamageTaken")),
        mitigated=_num(p.get("damageSelfMitigated")),
        cc=float(_num(p.get("timeCCingOthers"))),
        vision=_num(p.get("visionScore")),
        wards=_num(p.get("wardsPlaced")),
        wards_killed=_num(p.get("wardsKilled")),
        control_wards=_num(p.get("visionWardsBoughtInGame")),
        kp=kp,
        dmg_share=dmg_share,
        solo_kills=_opt_int(ch.get("soloKills")),
        plates=_opt_int(ch.get("turretPlatesTaken")),
        lane_adv=_opt_int(ch.get("laningPhaseGoldExpAdvantage")),
        early_lane_adv=_opt_int(ch.get("earlyLaningPhaseGoldExpAdvantage")),
        max_cs_adv=_opt_float(ch.get("maxCsAdvantageOnLaneOpponent")),
        first_blood=bool(p.get("firstBloodKill")),
        items=[_num(p.get(f"item{i}")) for i in range(7)],
        spells=[_num(p.get("summoner1Id")), _num(p.get("summoner2Id"))],
        runes=runes,
        profile_icon=_opt_int(p.get("profileIcon")),
        summoner_level=_opt_int(p.get("summonerLevel")),
    )


# Objetivo de ``teams[].objectives`` -> (clave de quién lo cogió primero, total por lado o ``None``).
OBJECTIVE_KEYS = {
    "champion": ("first_blood", "kills"),
    "tower": ("first_tower", "towers"),
    "dragon": ("first_dragon", "dragons"),
    "riftHerald": ("first_herald", "heralds"),
    "horde": ("first_grubs", "grubs"),
    "baron": ("first_baron", "barons"),
    "inhibitor": ("first_inhib", None),
}


def _add_bans(bans: list[int], team: dict) -> None:
    """Añade los campeones que baneó un equipo (sin repetir)."""
    for ban in team.get("bans") or []:
        champ = _num(ban.get("championId"), -1)
        if champ > 0 and champ not in bans:
            bans.append(champ)


def _add_objectives(objectives: dict[str, int | None], totals: dict[str, int], team_id, team: dict) -> None:
    """Los primeros objetivos que cogió un equipo y sus totales (kills, torres, dragones…) en su lado."""
    side = "blue" if team_id == 100 else "red"
    for obj_name, obj in (team.get("objectives") or {}).items():
        if obj_name not in OBJECTIVE_KEYS:
            continue
        first_key, total_key = OBJECTIVE_KEYS[obj_name]
        if obj.get("first"):
            objectives[first_key] = team_id
        if total_key:
            totals[f"{side}_{total_key}"] = _num(obj.get("kills"))


def _team_stats(
    teams: dict, participants: list[ParticipantRow], team_kills: dict[int, int]
) -> tuple[list[int], dict[str, int | None], dict[str, int]]:
    """Bans (sin repetir), qué equipo cogió cada primer objetivo y los totales por lado (kills, oro, torres…)."""
    bans: list[int] = []
    objectives: dict[str, int | None] = {
        "first_blood": None,
        "first_tower": None,
        "first_dragon": None,
        "first_herald": None,
        "first_grubs": None,
        "first_baron": None,
        "first_inhib": None,
    }
    totals = {
        f"{side}_{name}": 0
        for side in ("blue", "red")
        for name in ("kills", "gold", "towers", "dragons", "barons", "grubs", "heralds")
    }
    for team_id, team in teams.items():
        _add_bans(bans, team)
        _add_objectives(objectives, totals, team_id, team)
    for p in participants:
        side = "blue" if p.team == 100 else "red"
        totals[f"{side}_gold"] += p.gold
    if not any(totals[f"{s}_kills"] for s in ("blue", "red")):
        totals["blue_kills"] = team_kills[100]
        totals["red_kills"] = team_kills[200]
    return bans, objectives, totals


def parse_match(payload: dict) -> MatchRecord | None:
    """Devuelve ``None`` si el JSON no tiene la forma esperada."""
    if not isinstance(payload, dict):
        return None
    meta = payload.get("metadata") or {}
    info = payload.get("info") or {}
    match_id = meta.get("matchId") or ""
    raw_participants = info.get("participants") or []
    if not match_id or not raw_participants:
        return None

    platform = (info.get("platformId") or match_id.split("_", 1)[0]).lower()
    try:
        region = region_of(platform)
    except ValueError:
        return None

    duration = _duration(info)
    teams = {t.get("teamId"): t for t in info.get("teams") or []}
    winner = next((tid for tid, t in teams.items() if t.get("win")), None)
    team_kills, team_dmg = _team_sums(raw_participants)
    # Roles fiables: teamPosition único por equipo.
    slot_counts = _slot_counts(raw_participants)
    participants = [
        _participant(index, p, slot_counts, team_kills, team_dmg) for index, p in enumerate(raw_participants, start=1)
    ]
    early_surrender, surrender = _surrenders(raw_participants)
    bans, objectives, totals = _team_stats(teams, participants, team_kills)

    return MatchRecord(
        match_id=match_id,
        platform=platform,
        region=region,
        queue=_num(info.get("queueId")),
        map_id=_num(info.get("mapId")),
        patch=parse_patch(info.get("gameVersion")),
        game_start=_num(info.get("gameStartTimestamp") or info.get("gameCreation")),
        duration=duration,
        winner=winner,
        is_remake=duration < REMAKE_SECONDS or early_surrender,
        surrender=surrender,
        participants=participants,
        bans=bans,
        objectives=objectives,
        team_totals=totals,
    )


def duration_bucket(minutes: float) -> int:
    """0: <25 min · 1: 25-30 · 2: 30-35 · 3: ≥35."""
    if minutes < 25:
        return 0
    if minutes < 30:
        return 1
    if minutes < 35:
        return 2
    return 3
