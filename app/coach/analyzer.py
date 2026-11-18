"""Análisis de una partida propia a partir de match-v5 y su timeline.

No hace falta grabar la repetición: el timeline de Riot trae la posición de
los 10 jugadores al final de cada minuto y todos los eventos con su
coordenada (kills, objetivos, torres, compras, wards, subidas de nivel).
Con eso se reconstruye por dónde se movió cada uno y se detectan errores
concretos:

* **Muertes** clasificadas: gank (sobre todo del jungla rival), 1 contra 1,
  adelantado en la línea, sin ward en los 2 minutos previos, con oro sin
  gastar, justo antes de un objetivo rival, en la jungla rival o aislado.
* **Tiempo**: minutos con mucho oro sin gastar, minutos «vacíos» (sin
  farmear, pelear, wardear ni hacer objetivos) y tiempo muerto.
* **Pathing**: campamento inicial, ruta temprana, presencia en cada línea,
  ganks, invasiones y objetivos perdidos estando al otro lado del mapa.
* **Visión, objetivos y fase de líneas** (CS, oro y experiencia frente a tu
  rival a los 10 y 15 minutos).

La función principal es pura (sin BD ni red): ``analyze_game(match, timeline, pid=…)``.
Si aún no hay timeline se calcula lo que permite el resumen de la partida.
"""

from __future__ import annotations

import bisect
from collections import Counter

from app.coach import geometry as geo
from app.riot.routing import QUEUES, TEAM_POSITION_TO_ROLE

ANALYSIS_VERSION = 1

EARLY_MS = 14 * 60_000
IDLE_FROM_MS = 15 * 60_000
HOARD_GOLD = 1500
DEATH_GOLD = 1200
WARD_WINDOW_MS = 120_000
OBJECTIVE_WINDOW_MS = 90_000
NEAR_OBJECTIVE = 2600
FAR_FROM_OBJECTIVE = 5500
ISOLATED_RADIUS = 2200
SMITE = 11
CONTROL_WARD = 2055

# Tiempo base de reaparición por nivel (s) y factor por minuto de partida.
BRW = [10, 10, 12, 12, 14, 16, 20, 25, 28, 32.5, 35, 37.5, 40, 42.5, 45, 47.5, 50, 52.5]

MONSTERS = {"DRAGON": "dragon", "BARON_NASHOR": "baron", "RIFTHERALD": "herald", "HORDE": "grubs", "ATAKHAN": "atakhan"}
OBJ_LABEL = {
    "dragon": "el dragón",
    "elder": "el dragón ancestral",
    "baron": "el barón",
    "herald": "el heraldo",
    "grubs": "las larvas",
    "atakhan": "Atakhan",
}
ROLE_LABEL = {"TOP": "top", "JUNGLE": "jungla", "MID": "mid", "ADC": "ADC", "SUPPORT": "support"}


class NotInMatch(ValueError):
    """El jugador del perfil no aparece en la partida."""


def time_impact(minutes: float) -> float:
    """Aumento del tiempo de muerte según el minuto (0 % antes del 15, tope 50 %)."""
    if minutes < 15:
        return 0.0
    if minutes <= 30:
        return 0.00425 * (minutes - 15) * 2
    if minutes <= 45:
        return 0.1275 + 0.003 * (minutes - 30) * 2
    return min(0.5, 0.2175 + 0.0145 * (minutes - 45) * 2)


def respawn_seconds(level: int, minutes: float) -> float:
    return BRW[max(0, min(17, level - 1))] * (1 + time_impact(minutes))


def duration_seconds(info: dict) -> int:
    duration = int(info.get("gameDuration") or 0)
    if "gameEndTimestamp" not in info and duration > 10_000:  # partidas anteriores a 11.20 (ms)
        duration //= 1000
    return duration


def player_role(p: dict) -> str | None:
    """Rol interno (TOP, JUNGLE, MID, ADC, SUPPORT) según ``teamPosition``."""
    return TEAM_POSITION_TO_ROLE.get((p.get("teamPosition") or "").upper()) or TEAM_POSITION_TO_ROLE.get(
        (p.get("individualPosition") or "").upper()
    )


def _pos(obj: dict | None) -> tuple[int, int] | None:
    if not isinstance(obj, dict):
        return None
    p = obj.get("position")
    if not isinstance(p, dict) or "x" not in p or "y" not in p:
        return None
    return int(p["x"]), int(p["y"])


def _mmss(ms: float) -> str:
    s = int(ms // 1000)
    return f"{s // 60}:{s % 60:02d}"


def _num(value, default=0):
    try:
        return type(default)(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def find_me(match: dict, *, pid: int | None = None, puuid: str | None = None, game_name: str | None = None, tag_line: str | None = None) -> dict:
    parts = (match.get("info") or {}).get("participants") or []
    if pid:
        found = next((p for p in parts if _num(p.get("participantId")) == pid), None)
        if found:
            return found
    if puuid:
        found = next((p for p in parts if p.get("puuid") == puuid), None)
        if found:
            return found
    if game_name:
        name, tag = game_name.casefold(), (tag_line or "").casefold()
        found = next(
            (p for p in parts if (p.get("riotIdGameName") or "").casefold() == name and (not tag or (p.get("riotIdTagline") or "").casefold() == tag)),
            None,
        )
        if found:
            return found
    raise NotInMatch("El jugador no aparece en esta partida")


class _Game:
    """Datos de la partida indexados para consultas rápidas."""

    def __init__(self, match: dict, timeline: dict | None, me: dict) -> None:
        info = match.get("info") or {}
        self.info = info
        self.duration = duration_seconds(info)
        self.duration_ms = self.duration * 1000
        self.minutes = max(self.duration, 60) / 60
        self.raw = {}
        self.players: dict[int, dict] = {}
        roles_seen: Counter = Counter()
        me_pid = None
        for index, p in enumerate(info.get("participants") or [], start=1):
            pid = _num(p.get("participantId"), index) or index
            role = player_role(p)
            team = _num(p.get("teamId"))
            if p is me:
                me_pid = pid
            self.raw[pid] = p
            self.players[pid] = {
                "pid": pid,
                "team": team,
                "champ": _num(p.get("championId")),
                "role": role,
                "name": p.get("riotIdGameName") or p.get("summonerName") or "",
                "tag": p.get("riotIdTagline") or "",
                "smite": SMITE in (_num(p.get("summoner1Id")), _num(p.get("summoner2Id"))),
            }
            if role:
                roles_seen[(team, role)] += 1
        for pl in self.players.values():  # rol duplicado en un equipo: no es fiable
            if pl["role"] and roles_seen[(pl["team"], pl["role"])] > 1:
                pl["role"] = None
        if me_pid is None:
            raise NotInMatch("El jugador no aparece en esta partida")
        self.me = self.players[me_pid]
        self.team = self.me["team"]
        self.role = self.me["role"]
        self.allies = [p for p in self.players if self.players[p]["team"] == self.team and p != self.me["pid"]]
        self.enemies = [p for p in self.players if self.players[p]["team"] != self.team]
        self.opponent = self._by_role(300 - self.team, self.role) if self.role else None
        self.enemy_jungler = self._jungler(300 - self.team)
        self.ally_jungler = self._jungler(self.team)

        tl_info = (timeline or {}).get("info") or {}
        frames = tl_info.get("frames") or []
        self.has_timeline = bool(frames)
        self.ts: list[int] = [_num(f.get("timestamp")) for f in frames]
        self.pf: dict[int, list[dict | None]] = {pid: [] for pid in self.players}
        for f in frames:
            pfs = f.get("participantFrames") or {}
            for pid in self.players:
                self.pf[pid].append(pfs.get(str(pid)))
        events = [e for f in frames for e in (f.get("events") or []) if isinstance(e, dict)]
        events.sort(key=lambda e: _num(e.get("timestamp")))
        self.events = events
        self._index_events()

    # ----------------------------------------------------------- jugadores
    def _by_role(self, team: int, role: str | None) -> int | None:
        return next((pid for pid, p in self.players.items() if p["team"] == team and p["role"] == role), None)

    def _jungler(self, team: int) -> int | None:
        pid = self._by_role(team, "JUNGLE")
        if pid:
            return pid
        return next((pid for pid, p in self.players.items() if p["team"] == team and p["smite"]), None)

    def natural_opponents(self) -> set[int]:
        """Rivales con los que es normal pelear en la fase de líneas."""
        if self.role in ("ADC", "SUPPORT"):
            return {pid for pid in (self._by_role(300 - self.team, "ADC"), self._by_role(300 - self.team, "SUPPORT")) if pid}
        return {self.opponent} if self.opponent else set()

    # ------------------------------------------------------------- eventos
    def _index_events(self) -> None:
        self.levels: dict[int, list[tuple[int, int]]] = {pid: [] for pid in self.players}
        self.kills: list[dict] = []
        self.objectives: list[dict] = []
        self.buildings: list[dict] = []
        self.wards: dict[int, list[int]] = {pid: [] for pid in self.players}
        self.control_wards_placed: Counter = Counter()
        self.wards_killed: Counter = Counter()
        self.purchases: dict[int, list[tuple[int, int]]] = {pid: [] for pid in self.players}
        for e in self.events:
            kind = e.get("type")
            t = _num(e.get("timestamp"))
            if kind == "LEVEL_UP":
                pid = _num(e.get("participantId"))
                if pid in self.levels:
                    self.levels[pid].append((t, _num(e.get("level"), 1)))
            elif kind == "CHAMPION_KILL":
                victim = _num(e.get("victimId"))
                if victim not in self.players:
                    continue
                self.kills.append(
                    {
                        "t": t,
                        "killer": _num(e.get("killerId")),
                        "victim": victim,
                        "assists": [_num(a) for a in e.get("assistingParticipantIds") or []],
                        "pos": _pos(e),
                    }
                )
            elif kind == "ELITE_MONSTER_KILL":
                monster = MONSTERS.get((e.get("monsterType") or "").upper())
                if not monster:
                    continue
                if monster == "dragon" and (e.get("monsterSubType") or "").upper() == "ELDER_DRAGON":
                    monster = "elder"
                killer = _num(e.get("killerId"))
                team = _num(e.get("killerTeamId")) or (self.players[killer]["team"] if killer in self.players else 0)
                if team not in (100, 200):
                    continue
                assists = [_num(a) for a in e.get("assistingParticipantIds") or []]
                last = self.objectives[-1] if self.objectives else None
                if monster == "grubs" and last and last["kind"] == "grubs" and last["team"] == team and t - last["t_end"] <= 120_000:
                    last["t_end"] = t
                    last["takers"].update([killer, *assists])
                    continue
                self.objectives.append(
                    {
                        "t": t,
                        "t_end": t,
                        "kind": monster,
                        "team": team,
                        "pos": _pos(e) or (geo.PITS["baron"] if monster in ("baron", "herald", "grubs") else geo.PITS["dragon"]),
                        "takers": {killer, *assists},
                        "sub": e.get("monsterSubType"),
                        "assists_known": "assistingParticipantIds" in e,
                    }
                )
            elif kind in ("BUILDING_KILL", "TURRET_PLATE_DESTROYED"):
                owner = _num(e.get("teamId"))
                self.buildings.append(
                    {
                        "t": t,
                        "owner": owner,
                        "kind": "plate" if kind == "TURRET_PLATE_DESTROYED" else ("tower" if e.get("buildingType") == "TOWER_BUILDING" else "inhibitor"),
                        "lane": (e.get("laneType") or "").replace("_LANE", "").lower(),
                        "takers": {_num(e.get("killerId")), *[_num(a) for a in e.get("assistingParticipantIds") or []]},
                        "pos": _pos(e),
                    }
                )
            elif kind == "WARD_PLACED":
                creator = _num(e.get("creatorId"))
                ward_type = (e.get("wardType") or "").upper()
                if creator in self.wards and ward_type not in ("UNDEFINED", "TEEMO_MUSHROOM", ""):
                    self.wards[creator].append(t)
                    if ward_type == "CONTROL_WARD":
                        self.control_wards_placed[creator] += 1
            elif kind == "WARD_KILL":
                killer = _num(e.get("killerId"))
                if killer in self.players:
                    self.wards_killed[killer] += 1
            elif kind == "ITEM_PURCHASED":
                pid = _num(e.get("participantId"))
                if pid in self.purchases:
                    self.purchases[pid].append((t, _num(e.get("itemId"))))
        self._deaths()
        self._tracks()

    def level_at(self, pid: int, t: int) -> int:
        level = 1
        for ts, lvl in self.levels.get(pid) or []:
            if ts > t:
                break
            level = lvl
        if level == 1 and not self.levels.get(pid):  # sin eventos LEVEL_UP: el del frame anterior
            i = self.frame_before(t)
            if i is not None:
                level = _num((self.pf[pid][i] or {}).get("level"), 1)
        return level

    def _deaths(self) -> None:
        estimates: dict[int, list[list]] = {pid: [] for pid in self.players}
        for k in self.kills:
            seconds = respawn_seconds(self.level_at(k["victim"], k["t"]), k["t"] / 60_000)
            estimates[k["victim"]].append([k["t"], seconds, k["pos"]])
        self.dead: dict[int, list[tuple[int, int, tuple | None]]] = {}
        for pid, items in estimates.items():
            total = _num(self.raw[pid].get("totalTimeSpentDead"))
            est = sum(i[1] for i in items)
            factor = max(0.5, min(1.8, total / est)) if total and est else 1.0
            intervals = []
            for t, seconds, pos in items:
                end = min(t + int(seconds * factor * 1000), self.duration_ms or t + int(seconds * 1000))
                intervals.append((t, max(end, t + 1000), pos))
            self.dead[pid] = intervals

    def is_dead(self, pid: int, t: int) -> bool:
        return any(start <= t < end for start, end, _ in self.dead.get(pid) or [])

    def dead_overlap(self, pid: int, t0: int, t1: int) -> int:
        return sum(max(0, min(end, t1) - max(start, t0)) for start, end, _ in self.dead.get(pid) or [])

    def _tracks(self) -> None:
        """Puntos de paso de cada jugador: frames + eventos con posición + tienda y reapariciones."""
        anchors: dict[int, list[tuple[int, int, int, int]]] = {pid: [] for pid in self.players}

        def add(pid, t, p, kind):
            if pid in anchors and p is not None:
                anchors[pid].append((t, int(p[0]), int(p[1]), kind))

        for pid in self.players:
            team = self.players[pid]["team"]
            for i, t in enumerate(self.ts):
                p = _pos(self.pf[pid][i])
                if p and not self.is_dead(pid, t):
                    add(pid, t, p, 0)
            for t, _item in self.purchases[pid]:
                if not self.is_dead(pid, t):
                    add(pid, t, geo.FOUNTAIN[team], 2)
            for start, end, pos in self.dead.get(pid) or []:
                add(pid, start, pos, 3)
                if end < self.duration_ms:
                    add(pid, end, geo.FOUNTAIN[team], 2)
        for k in self.kills:
            for pid in (k["killer"], *k["assists"]):
                if pid in self.players and not self.is_dead(pid, k["t"]):
                    add(pid, k["t"], k["pos"], 1)
        for o in self.objectives:
            if o["assists_known"]:
                for pid in o["takers"]:
                    if pid in self.players and not self.is_dead(pid, o["t"]):
                        add(pid, o["t"], o["pos"], 1)
        for b in self.buildings:
            for pid in b["takers"]:
                if pid in self.players and not self.is_dead(pid, b["t"]):
                    add(pid, b["t"], b["pos"], 1)
        self.anchors = {pid: sorted(a) for pid, a in anchors.items()}
        self._anchor_t = {pid: [a[0] for a in items] for pid, items in self.anchors.items()}

    def pos_at(self, pid: int, t: int) -> tuple[float, float] | None:
        """Posición estimada (interpolada) o ``None`` si estaba muerto o no se sabe."""
        if self.is_dead(pid, t):
            return None
        items = self.anchors.get(pid) or []
        if not items:
            return None
        times = self._anchor_t[pid]
        i = bisect.bisect_right(times, t)
        prev = items[i - 1] if i > 0 else None
        nxt = items[i] if i < len(items) else None
        if prev is not None and prev[3] == 3:  # justo antes de morir
            return prev[1], prev[2]
        if prev is None:
            return (nxt[1], nxt[2]) if nxt else None
        if nxt is None or nxt[0] == prev[0]:
            return prev[1], prev[2]
        f = (t - prev[0]) / (nxt[0] - prev[0])
        return prev[1] + (nxt[1] - prev[1]) * f, prev[2] + (nxt[2] - prev[2]) * f

    # -------------------------------------------------------------- frames
    def frame_before(self, t: int) -> int | None:
        i = bisect.bisect_right(self.ts, t) - 1
        return i if i >= 0 else None

    def frame_at_minute(self, minute: int) -> int | None:
        target = minute * 60_000
        for i, t in enumerate(self.ts):
            if abs(t - target) <= 30_000:
                return i
        return None

    def fval(self, pid: int | None, i: int | None, key: str):
        if pid is None or i is None or pid not in self.pf or i >= len(self.pf[pid]):
            return None
        frame = self.pf[pid][i] or {}
        if key == "cs":
            if "minionsKilled" not in frame and "jungleMinionsKilled" not in frame:
                return None
            return _num(frame.get("minionsKilled")) + _num(frame.get("jungleMinionsKilled"))
        if key == "dmg":
            return _num((frame.get("damageStats") or {}).get("totalDamageDoneToChampions"))
        value = frame.get(key)
        return _num(value) if value is not None else None

    def frame_pos(self, pid: int, i: int) -> tuple[int, int] | None:
        return _pos(self.pf[pid][i]) if pid in self.pf and i < len(self.pf[pid]) else None


# ====================================================================== análisis
def analyze_game(
    match: dict,
    timeline: dict | None,
    *,
    pid: int | None = None,
    puuid: str | None = None,
    game_name: str | None = None,
    tag_line: str | None = None,
) -> dict:
    me_raw = find_me(match, pid=pid, puuid=puuid, game_name=game_name, tag_line=tag_line)
    g = _Game(match, timeline, me_raw)
    me = g.me["pid"]
    info = g.info
    ch = me_raw.get("challenges") or {}
    team_obj = next((t.get("objectives") or {} for t in info.get("teams") or [] if _num(t.get("teamId")) == g.team), {})

    deaths = [_classify_death(g, k) for k in g.kills if k["victim"] == me]
    moments: list[dict] = [d["moment"] for d in deaths]

    kills = _num(me_raw.get("kills"))
    n_deaths = _num(me_raw.get("deaths"))
    assists = _num(me_raw.get("assists"))
    cs = _num(me_raw.get("totalMinionsKilled")) + _num(me_raw.get("neutralMinionsKilled"))
    team_kills = sum(_num(p.get("kills")) for p in info.get("participants") or [] if _num(p.get("teamId")) == g.team)
    kp = ch.get("killParticipation")
    kp = float(kp) * 100 if kp is not None else ((kills + assists) / team_kills * 100 if team_kills else None)
    dmg_share = ch.get("teamDamagePercentage")
    time_dead = _num(me_raw.get("totalTimeSpentDead"))

    def count(tag: str, early_only: bool = False) -> int:
        return sum(1 for d in deaths if tag in d["tags"] and (not early_only or d["early"]))

    metrics: dict[str, float | int | None] = {
        "win": int(bool(me_raw.get("win"))),
        "minutes": round(g.minutes, 2),
        "kills": kills,
        "deaths": n_deaths,
        "assists": assists,
        "kda": round((kills + assists) / max(1, n_deaths), 2),
        "kp": round(kp, 1) if kp is not None else None,
        "dmg_share": round(float(dmg_share) * 100, 1) if dmg_share is not None else None,
        "cs_min": round(cs / g.minutes, 2),
        "gold_min": round(_num(me_raw.get("goldEarned")) / g.minutes),
        "vision_min": round(_num(me_raw.get("visionScore")) / g.minutes, 2),
        "wards_min": round(_num(me_raw.get("wardsPlaced")) / g.minutes, 2),
        "control_wards": _num(me_raw.get("visionWardsBoughtInGame")),
        "wards_killed": _num(me_raw.get("wardsKilled")),
        "time_dead_pct": round(time_dead / max(1, g.duration) * 100, 1),
        "solo_kills": _num(ch.get("soloKills")) if "soloKills" in ch else None,
        "plates": _num(ch.get("turretPlatesTaken")) if "turretPlatesTaken" in ch else None,
    }

    # Objetivos (sin timeline, con lo que trae el resumen de la partida).
    team_total = sum(_num((team_obj.get(k) or {}).get("kills")) for k in ("dragon", "baron", "riftHerald"))
    mine_total = _num(ch.get("dragonTakedowns")) + _num(ch.get("baronTakedowns")) + _num(ch.get("riftHeraldTakedowns"))
    metrics["obj_participation"] = round(min(100.0, mine_total / team_total * 100), 1) if team_total and ch else None

    result = {
        "version": ANALYSIS_VERSION,
        "match_id": (match.get("metadata") or {}).get("matchId"),
        "queue": _num(info.get("queueId")),
        "queue_label": QUEUES.get(_num(info.get("queueId")), f"Cola {_num(info.get('queueId'))}"),
        "game_start": _num(info.get("gameStartTimestamp") or info.get("gameCreation")),
        "duration": g.duration,
        "patch": ".".join((info.get("gameVersion") or "").split(".")[:2]),
        "has_timeline": g.has_timeline,
        "me": me,
        "team": g.team,
        "role": g.role,
        "champ": g.me["champ"],
        "win": bool(me_raw.get("win")),
        "opponent": g.opponent,
        "enemy_jungler": g.enemy_jungler,
        "ally_jungler": g.ally_jungler,
        "players": [
            {k: p[k] for k in ("pid", "team", "champ", "role", "name", "tag")}
            | {
                "kills": _num(g.raw[pid].get("kills")),
                "deaths": _num(g.raw[pid].get("deaths")),
                "assists": _num(g.raw[pid].get("assists")),
                "cs": _num(g.raw[pid].get("totalMinionsKilled")) + _num(g.raw[pid].get("neutralMinionsKilled")),
                "gold": _num(g.raw[pid].get("goldEarned")),
                "level": _num(g.raw[pid].get("champLevel")),
                "items": [_num(g.raw[pid].get(f"item{i}")) for i in range(7)],
            }
            for pid, p in sorted(g.players.items())
        ],
        "metrics": metrics,
        "deaths": [{k: v for k, v in d.items() if k != "moment"} for d in deaths],
        "moments": moments,
    }

    if not g.has_timeline:
        for key in ("deaths_pre14", "deaths_solo", "deaths_ganked", "deaths_to_jungler_pre14", "deaths_no_vision_pre14"):
            metrics[key] = None
        return result

    metrics.update(
        {
            "deaths_pre14": sum(1 for d in deaths if d["early"]),
            "deaths_solo": count("solo"),
            "deaths_ganked": count("gank", True),
            "deaths_to_jungler_pre14": count("jungler", True),
            "deaths_no_vision_pre14": count("no_ward", True),
            "deaths_overextended": count("overextended", True),
            "deaths_enemy_jungle": count("enemy_jungle"),
            "deaths_unspent_gold": count("unspent_gold"),
            "deaths_before_objective": count("before_objective"),
            "deaths_isolated_late": count("isolated"),
            "wards_pre14": sum(1 for t in g.wards[me] if t < EARLY_MS),
            "control_wards_placed": g.control_wards_placed[me],
        }
    )

    _lane_phase(g, metrics, moments)
    _objectives(g, metrics, moments)
    _time_use(g, metrics, moments)
    if g.role == "JUNGLE":
        result["jungle"] = _jungle(g, metrics, moments)
    else:
        _roaming(g, metrics)

    moments.sort(key=lambda m: m["t"])
    result["series"] = _series(g)
    result["replay"] = _replay(g)
    return result


# ------------------------------------------------------------------ muertes
def _classify_death(g: _Game, k: dict) -> dict:
    me = g.me["pid"]
    t, pos = k["t"], k["pos"]
    early = t < EARLY_MS
    enemies = {pid for pid in (k["killer"], *k["assists"]) if pid in g.players and g.players[pid]["team"] != g.team}
    tags: list[str] = []
    zone = geo.zone(pos) if pos else None
    lane = geo.ROLE_LANE.get(g.role or "")

    if not enemies:
        tags.append("executed")
    if len(enemies) == 1:
        tags.append("solo")
    if g.enemy_jungler and g.enemy_jungler in enemies:
        tags.append("jungler")
    if early and g.role and g.role != "JUNGLE" and enemies - g.natural_opponents():
        tags.append("gank")
    if zone and geo.is_jungle_of(zone, 300 - g.team):
        tags.append("enemy_jungle")
    if early and lane and pos and zone == lane and geo.overextended(g.team, lane, pos):
        tags.append("overextended")
    if early and not any(t - WARD_WINDOW_MS <= w <= t for w in g.wards[me]):
        tags.append("no_ward")
    i = g.frame_before(t)
    gold = g.fval(me, i, "currentGold")
    if t >= 3 * 60_000 and gold is not None and gold >= DEATH_GOLD:
        tags.append("unspent_gold")
    lost = next((o for o in g.objectives if o["team"] != g.team and t < o["t"] <= t + OBJECTIVE_WINDOW_MS), None)
    if lost:
        tags.append("before_objective")
    if not early and pos:
        near = [a for a in g.allies if (ap := g.pos_at(a, t)) is not None and geo.dist(ap, pos) <= ISOLATED_RADIUS]
        if not near:
            tags.append("isolated")

    names = [_champ_ref(g, pid) for pid in sorted(enemies)]
    killer_name = _champ_ref(g, k["killer"]) if k["killer"] in g.players else "torre/súbditos"
    if "gank" in tags:
        title = f"Gank de {_champ_ref(g, g.enemy_jungler)} (jungla rival)" if "jungler" in tags else f"Gank ({', '.join(names)})"
    elif "enemy_jungle" in tags:
        title = "Muerte en la jungla rival"
    elif "solo" in tags:
        title = f"Muerte 1 contra 1 contra {killer_name}"
    elif "isolated" in tags:
        title = "Muerte aislado de tu equipo"
    elif "executed" in tags:
        title = "Ejecutado por torre o súbditos"
    else:
        title = f"Muerte en pelea ({len(enemies)} rivales)"

    details = []
    if zone:
        details.append(f"en {geo.zone_label(zone, g.team)}")
    if "overextended" in tags:
        details.append("estabas adelantado, más cerca de su torre que de la tuya")
    if "no_ward" in tags:
        details.append("sin poner ningún ward en los 2 minutos previos")
    if "unspent_gold" in tags:
        details.append(f"con {gold:,} de oro sin gastar".replace(",", "."))
    if lost:
        details.append(f"y el rival cogió {OBJ_LABEL[lost['kind']]} a continuación")
    if "isolated" in tags:
        details.append("sin ningún aliado cerca")

    score = 1.0
    score += 1.0 if ("gank" in tags and "no_ward" in tags) else 0
    score += 0.8 if "overextended" in tags else 0
    score += 1.0 if "before_objective" in tags else 0
    score += 0.5 if "unspent_gold" in tags else 0
    score += 0.6 if "enemy_jungle" in tags and "no_ward" in tags else 0
    severity = "high" if score >= 2.5 else "medium" if score >= 1.5 else "low"
    entry = {
        "t": round(t / 1000, 1),
        "x": pos[0] if pos else None,
        "y": pos[1] if pos else None,
        "zone": zone,
        "early": early,
        "tags": tags,
        "killer": k["killer"],
        "enemies": sorted(enemies),
        "severity": severity,
    }
    entry["moment"] = {
        "t": entry["t"],
        "kind": "death",
        "severity": severity,
        "title": title,
        "detail": (", ".join(details) + ".").capitalize() if details else "",
        "x": entry["x"],
        "y": entry["y"],
        "tags": tags,
    }
    return entry


def _champ_ref(g: _Game, pid: int | None) -> str:
    """Referencia legible a un campeón (el nombre lo pone el frontend: «{champ:ID}»)."""
    if not pid or pid not in g.players:
        return "?"
    return f"{{champ:{g.players[pid]['champ']}}}"


# --------------------------------------------------------- fase de líneas
def _lane_phase(g: _Game, metrics: dict, moments: list) -> None:
    me, opp = g.me["pid"], g.opponent
    for minute in (10, 15):
        i = g.frame_at_minute(minute)
        cs = g.fval(me, i, "cs")
        metrics[f"cs{minute}"] = cs
        if opp:
            ocs, gold, ogold = g.fval(opp, i, "cs"), g.fval(me, i, "totalGold"), g.fval(opp, i, "totalGold")
            xp, oxp = g.fval(me, i, "xp"), g.fval(opp, i, "xp")
            metrics[f"cs_diff{minute}"] = cs - ocs if cs is not None and ocs is not None else None
            metrics[f"gold_diff{minute}"] = gold - ogold if gold is not None and ogold is not None else None
            metrics[f"xp_diff{minute}"] = xp - oxp if xp is not None and oxp is not None else None
        else:
            metrics[f"cs_diff{minute}"] = metrics[f"gold_diff{minute}"] = metrics[f"xp_diff{minute}"] = None
    gd = metrics.get("gold_diff15")
    if g.role and g.role != "JUNGLE" and gd is not None and gd <= -1000:
        moments.append(
            {
                "t": 900.0,
                "kind": "lane",
                "severity": "medium" if gd > -2000 else "high",
                "title": f"Línea perdida: {gd:+,} de oro a los 15".replace(",", "."),
                "detail": f"Frente a {_champ_ref(g, opp)}: {metrics.get('cs_diff15') or 0:+} CS y {metrics.get('xp_diff15') or 0:+} de experiencia.",
                "x": None,
                "y": None,
                "tags": ["lane"],
            }
        )


# --------------------------------------------------------------- objetivos
def _objectives(g: _Game, metrics: dict, moments: list) -> None:
    me = g.me["pid"]
    taken = participated = lost_dead = lost_far = 0
    for o in g.objectives:
        here = me in o["takers"] if o["assists_known"] else False
        p = g.pos_at(me, o["t"])
        if not here and p is not None and geo.dist(p, o["pos"]) <= NEAR_OBJECTIVE:
            here = True
        if o["team"] == g.team:
            taken += 1
            participated += int(here)
            continue
        label = OBJ_LABEL[o["kind"]]
        if g.is_dead(me, o["t"]):
            lost_dead += 1
            moments.append(
                {
                    "t": round(o["t"] / 1000, 1),
                    "kind": "objective",
                    "severity": "high" if o["kind"] in ("baron", "elder", "dragon") else "medium",
                    "title": f"El rival cogió {label} mientras estabas muerto",
                    "detail": "Una muerte antes de un objetivo se paga doble: no puedes disputarlo.",
                    "x": o["pos"][0],
                    "y": o["pos"][1],
                    "tags": ["objective", "dead"],
                }
            )
        elif p is not None and geo.dist(p, o["pos"]) >= FAR_FROM_OBJECTIVE:
            lost_far += 1
            if g.role in ("JUNGLE", "SUPPORT", "MID"):
                moments.append(
                    {
                        "t": round(o["t"] / 1000, 1),
                        "kind": "objective",
                        "severity": "medium",
                        "title": f"El rival cogió {label} y estabas lejos",
                        "detail": f"Estabas en {geo.zone_label(geo.zone(p), g.team)}, a {int(geo.dist(p, o['pos'])):,} unidades.".replace(",", "."),
                        "x": int(p[0]),
                        "y": int(p[1]),
                        "tags": ["objective", "far"],
                    }
                )
    metrics["objectives_team"] = taken
    if taken:
        metrics["obj_participation"] = round(participated / taken * 100, 1)
    metrics["obj_lost_dead"] = lost_dead
    metrics["obj_lost_far"] = lost_far


# ---------------------------------------------------------- uso del tiempo
def _time_use(g: _Game, metrics: dict, moments: list) -> None:
    me = g.me["pid"]
    team = g.team
    # Oro acumulado sin gastar (vivo y fuera de la fuente).
    hoard: list[tuple[int, int]] = []
    for i, t in enumerate(g.ts):
        if t < 3 * 60_000 or g.is_dead(me, t):
            continue
        p = g.frame_pos(me, i)
        if p and geo.in_fountain(team, p):
            continue
        gold = g.fval(me, i, "currentGold")
        if gold is not None and gold >= HOARD_GOLD:
            hoard.append((i, gold))
    metrics["gold_hoard_min"] = len(hoard)
    for run in _runs([i for i, _ in hoard]):
        if len(run) >= 2 or max(gold for i, gold in hoard if i in run) >= 2500:
            peak = max(gold for i, gold in hoard if i in run)
            moments.append(
                {
                    "t": round(g.ts[run[0]] / 1000, 1),
                    "kind": "gold",
                    "severity": "medium" if len(run) >= 2 else "low",
                    "title": f"{len(run)} min con oro sin gastar (hasta {peak:,})".replace(",", "."),
                    "detail": f"Del minuto {round(g.ts[run[0]] / 60_000)} al {round(g.ts[run[-1]] / 60_000)}: ese oro no pelea. Vuelve a base cuando tengas una pieza o al caer una oleada.",
                    "x": None,
                    "y": None,
                    "tags": ["gold"],
                }
            )

    # Minutos vacíos desde el 15: sin farmear, pelear, wardear ni hacer objetivos.
    my_events = sorted(
        [k["t"] for k in g.kills if me == k["killer"] or me in k["assists"]]
        + [o["t"] for o in g.objectives if me in o["takers"]]
        + [b["t"] for b in g.buildings if me in b["takers"]]
        + list(g.wards[me])
    )
    eligible = 0
    idle: list[int] = []
    for i in range(1, len(g.ts)):
        t0, t1 = g.ts[i - 1], g.ts[i]
        if t0 < IDLE_FROM_MS or t1 - t0 < 40_000:
            continue
        if g.dead_overlap(me, t0, t1) > 15_000:
            continue
        eligible += 1
        cs0, cs1 = g.fval(me, i - 1, "cs"), g.fval(me, i, "cs")
        d0, d1 = g.fval(me, i - 1, "dmg"), g.fval(me, i, "dmg")
        busy = (cs0 is not None and cs1 is not None and cs1 - cs0 >= 3) or (d0 is not None and d1 is not None and d1 - d0 >= 400)
        if not busy:
            j = bisect.bisect_left(my_events, t0)
            busy = j < len(my_events) and my_events[j] <= t1
        if not busy:
            idle.append(i)
    metrics["idle_min"] = len(idle)
    metrics["idle_pct"] = round(len(idle) / eligible * 100, 1) if eligible else None
    for run in _runs(idle):
        if len(run) >= 2:
            p = g.frame_pos(me, run[0])
            moments.append(
                {
                    "t": round(g.ts[run[0] - 1] / 1000, 1),
                    "kind": "idle",
                    "severity": "medium" if len(run) >= 3 else "low",
                    "title": f"{len(run)} minutos sin farmear ni pelear",
                    "detail": f"Del minuto {round(g.ts[run[0] - 1] / 60_000)} al {round(g.ts[run[-1]] / 60_000)} no hiciste CS, daño, wards ni objetivos. Si no hay jugada, farmea una línea o la jungla.",
                    "x": p[0] if p else None,
                    "y": p[1] if p else None,
                    "tags": ["idle"],
                }
            )

    # Viajes a base (compras estando vivo, sin contar la compra inicial).
    trips: list[int] = []
    for t, _item in g.purchases[me]:
        if t < 90_000 or g.is_dead(me, t):
            continue
        if not trips or t - trips[-1] > 40_000:
            trips.append(t)
    metrics["recalls"] = len(trips)
    metrics["first_recall_min"] = round(trips[0] / 60_000, 1) if trips else None
    metrics["control_wards_bought"] = sum(1 for _t, item in g.purchases[me] if item == CONTROL_WARD)


def _runs(indices: list[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for i in indices:
        if runs and i == runs[-1][-1] + 1:
            runs[-1].append(i)
        else:
            runs.append([i])
    return runs


# ------------------------------------------------------------------ jungla
def _jungle(g: _Game, metrics: dict, moments: list) -> dict:
    me = g.me["pid"]
    team = g.team
    path = []
    for i in range(1, min(len(g.ts), 9)):
        p = g.frame_pos(me, i)
        if not p:
            continue
        camp = geo.nearest_camp(p, 1500)
        z = geo.zone(p)
        path.append(
            {
                "minute": round(g.ts[i] / 60_000),
                "x": p[0],
                "y": p[1],
                "zone": z,
                "camp": camp[1] if camp else None,
                "camp_team": camp[0] if camp else None,
            }
        )
    start = next((s for s in path if s["camp"] and s["camp_team"] == team), None)
    presence: Counter = Counter()
    invade = 0
    mirror_same = mirror_n = 0
    for i, t in enumerate(g.ts):
        if t < 2 * 60_000 or t > EARLY_MS:
            continue
        p = g.frame_pos(me, i)
        if not p or g.is_dead(me, t):
            continue
        z = geo.zone(p)
        if geo.is_lane(z):
            presence[z] += 1
        elif geo.is_jungle_of(z, 300 - team):
            invade += 1
        elif z == "river":
            presence["river"] += 1
        ej = g.enemy_jungler
        q = g.frame_pos(ej, i) if ej else None
        if q and not g.is_dead(ej, t):
            mirror_n += 1
            mirror_same += int(geo.half_of(p) == geo.half_of(q))

    takedowns = [k for k in g.kills if (me == k["killer"] or me in k["assists"]) and k["pos"]]
    ganks = [k for k in takedowns if k["t"] < 10 * 60_000 and geo.is_lane(geo.zone(k["pos"]))]
    gank_events = []
    for k in ganks:
        if not gank_events or k["t"] - gank_events[-1]["t"] > 30_000:
            gank_events.append(k)
    first_gank = next((k for k in takedowns if k["t"] < EARLY_MS and geo.is_lane(geo.zone(k["pos"]))), None)

    # Ganks del jungla rival mientras estabas en la otra mitad del mapa.
    ej = g.enemy_jungler
    enemy_ganks = far = 0
    for k in g.kills:
        if not ej or k["t"] >= EARLY_MS or not k["pos"] or g.players[k["victim"]]["team"] != team:
            continue
        if ej != k["killer"] and ej not in k["assists"]:
            continue
        if not geo.is_lane(geo.zone(k["pos"])):
            continue
        enemy_ganks += 1
        p = g.pos_at(me, k["t"])
        if p is not None and geo.half_of(p) != geo.half_of(k["pos"]):
            far += 1

    i10 = g.frame_at_minute(10)
    jcs10 = g.fval(me, i10, "jungleMinionsKilled")
    ejcs10 = g.fval(ej, i10, "jungleMinionsKilled") if ej else None
    metrics.update(
        {
            "ganks_pre10": len(gank_events),
            "first_gank_min": round(first_gank["t"] / 60_000, 1) if first_gank else None,
            "invade_min": invade,
            "jungle_cs10": jcs10,
            "jungle_cs_diff10": jcs10 - ejcs10 if jcs10 is not None and ejcs10 is not None else None,
            "enemy_ganks_far": far,
            "mirror_pct": round(mirror_same / mirror_n * 100) if mirror_n else None,
            "scuttles": _num((g.raw[me].get("challenges") or {}).get("scuttleCrabKills")) if "scuttleCrabKills" in (g.raw[me].get("challenges") or {}) else None,
            "enemy_jungle_cs": _num(g.raw[me].get("totalEnemyJungleMinionsKilled")),
        }
    )
    if not gank_events:
        moments.append(
            {
                "t": 600.0,
                "kind": "jungle",
                "severity": "medium",
                "title": "Ninguna kill en línea antes del minuto 10",
                "detail": "Tras la primera limpieza busca la línea con prioridad (la que tenga empuje y control de masas) y aparece por donde no te esperan.",
                "x": None,
                "y": None,
                "tags": ["jungle"],
            }
        )
    if far >= 2:
        moments.append(
            {
                "t": 840.0,
                "kind": "jungle",
                "severity": "medium",
                "title": f"El jungla rival ganqueó {far} veces en la otra mitad del mapa",
                "detail": "Si sabes dónde está el jungla rival, juega en espejo: contragank en su lado o invade/objetivo en el contrario.",
                "x": None,
                "y": None,
                "tags": ["jungle", "tracking"],
            }
        )
    return {
        "start_camp": start["camp"] if start else None,
        "start_side": geo.half_of((start["x"], start["y"])) if start else None,
        "path": path,
        "presence": dict(presence),
        "ganks": [{"t": round(k["t"] / 1000, 1), "x": k["pos"][0], "y": k["pos"][1], "zone": geo.zone(k["pos"])} for k in gank_events],
        "enemy_ganks": enemy_ganks,
    }


def _roaming(g: _Game, metrics: dict) -> None:
    me = g.me["pid"]
    lane = geo.ROLE_LANE.get(g.role or "")
    if not lane:
        return
    roam = 0
    for i, t in enumerate(g.ts):
        if t < 3 * 60_000 or t > EARLY_MS or g.is_dead(me, t):
            continue
        p = g.frame_pos(me, i)
        if not p:
            continue
        z = geo.zone(p)
        if z != lane and not z.startswith("base_"):
            roam += 1
    metrics["roam_min"] = roam
    metrics["roam_takedowns"] = sum(
        1
        for k in g.kills
        if (me == k["killer"] or me in k["assists"]) and k["t"] < EARLY_MS and k["pos"] and geo.zone(k["pos"]) not in (lane,)
    )


# -------------------------------------------------------- series y replay
def _series(g: _Game) -> dict:
    me, opp = g.me["pid"], g.opponent
    out: dict[str, list] = {k: [] for k in ("minute", "gold", "opp_gold", "cs", "opp_cs", "xp", "opp_xp", "current_gold", "level", "team_gold_diff")}
    for i, t in enumerate(g.ts):
        out["minute"].append(round(t / 60_000, 2))
        out["gold"].append(g.fval(me, i, "totalGold"))
        out["cs"].append(g.fval(me, i, "cs"))
        out["xp"].append(g.fval(me, i, "xp"))
        out["current_gold"].append(g.fval(me, i, "currentGold"))
        out["level"].append(g.fval(me, i, "level"))
        out["opp_gold"].append(g.fval(opp, i, "totalGold") if opp else None)
        out["opp_cs"].append(g.fval(opp, i, "cs") if opp else None)
        out["opp_xp"].append(g.fval(opp, i, "xp") if opp else None)
        diff = 0
        for pid, p in g.players.items():
            value = g.fval(pid, i, "totalGold") or 0
            diff += value if p["team"] == g.team else -value
        out["team_gold_diff"].append(diff)
    return out


def _replay(g: _Game) -> dict:
    """Datos compactos para la «repetición» 2D del frontend (tiempos en segundos)."""
    tracks = {}
    for pid in g.players:
        tracks[str(pid)] = {
            "a": [[round(t / 1000, 1), x, y, kind] for t, x, y, kind in g.anchors[pid]],
            "dead": [
                [round(s / 1000, 1), round(e / 1000, 1), p[0] if p else None, p[1] if p else None]
                for s, e, p in g.dead.get(pid) or []
            ],
        }
    return {
        "tracks": tracks,
        "kills": [[round(k["t"] / 1000, 1), k["killer"], k["victim"], *(k["pos"] or (None, None)), k["assists"]] for k in g.kills],
        "objectives": [[round(o["t"] / 1000, 1), o["kind"], o["team"], int(o["pos"][0]), int(o["pos"][1])] for o in g.objectives],
        "buildings": [
            [round(b["t"] / 1000, 1), b["owner"], b["kind"], b["lane"], *(b["pos"] or (None, None))] for b in g.buildings if b["kind"] != "plate"
        ],
    }
