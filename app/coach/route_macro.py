"""Macro que solo se ve con los recorridos reconstruidos: roams, contraganks, el mid con su jungla y el support que deja
solo a su ADC (reglas de ``macro/macro_por_rol.txt``: mid 3-8, support 5-7, jungla 10 y 13, general 26).

No son errores sueltos sino tendencias: hasta un Aspirante deja sin responder 4 de cada 5 jugadas del jungla rival. Se
comparan con lo que hace cada elo (``elo_profile``). Medido con ~17.000 partidas de jungla (Platino → Aspirante):
responder a lo que hace el jungla rival (kill o asistencia allí en 15 s) 17 % → 20 %, estar cerca 22 % → 26 %; en top,
roams con kill o asistencia 58 % (Diamante) → 66 % (Aspirante) y CS perdido por roamear 1,2 → 0,2. No separan elos (no
se usan): ganks fallidos largos y campamentos robados antes del 10.

Funciona igual con tus partidas (el análisis guardado) y con las de los OTPs (su dossier): ``from_analysis`` y
``from_dossier`` lo traducen al mismo formato.
"""

from __future__ import annotations

from app.coach import clocks as clk
from app.coach import geometry as geo
from app.coach import macro_habits
from app.coach.analyzer import interpolate

EARLY_MS = 14 * 60_000
STEP_MS = 5_000
ROAM_FROM_LANE = 2_500  # «fuera de tu línea»: a más de esto de ella (ni en base ni muerto)
ROAM_MIN_SAMPLES = 4  # 20 s seguidos fuera
NEAR = 3_000  # «cerca» de una jugada
RESPONSE_S = 15  # respuesta: kill o asistencia allí en los 15 s siguientes
SUPPORT_FAR = 4_000
# Visión (wards puestos: posición del que lo pone en ese momento, sale de su recorrido reconstruido).
WARD_TYPES = ("YELLOW_TRINKET", "SIGHT_WARD", "CONTROL_WARD", "BLUE_TRINKET")
OBJECTIVES = ("dragon", "elder", "grubs", "herald", "baron")
OBJ_WARD_NEAR = 3_500  # ward «del objetivo»: a menos de esto del foso…
OBJ_WARD_BEFORE_S = (120, 5)  # …puesto entre 2 min y 5 s antes de que lo maten
WARD_SPLIT_MIN = 3  # wards antes del 14 para repartirlos por zonas
ENEMY_JG_EARLY_S = 600


def _place(p, team: int | None) -> str:
    """Zona de un punto para roams y wards: otra línea, río, tu jungla, la jungla rival (o base)."""
    z = geo.zone(p)
    if z.startswith("base"):
        return "base"
    if geo.is_lane(z):
        return "lane"
    if z == "river":
        return "river"
    return "own_jg" if team and geo.is_jungle_of(z, team) else "enemy_jg"


def _dead(dead: list[tuple[int, int]], t: int) -> bool:
    return any(s <= t < e for s, e in dead)


def _involved(k, pid) -> bool:
    return k[1] == pid or pid in (k[5] or [])


def features(
    anchors: list[tuple[int, int, int, int]],
    dead: list[tuple[int, int]],
    kills: list,
    *,
    me: int,
    role: str | None,
    ally_jungler: int | None = None,
    enemy_jungler: int | None = None,
    adc: int | None = None,
    cs: list | None = None,
    opp_cs: list | None = None,
    team: int | None = None,
    wards: list | None = None,
    objectives: list | None = None,
    buildings: list | None = None,
    backs: list | None = None,
    extra: dict | None = None,
) -> dict:
    """Métricas de una partida. ``anchors`` en ms; ``kills``: [segundo, matador, víctima, x, y, asistentes];
    ``wards``: los tuyos, [segundo, tipo]; ``objectives``: [segundo, tipo, equipo, x, y]; ``buildings``: [segundo,
    equipo dueño, tipo, línea, x, y]; ``backs``: tus vueltas a base. Con ellos, también los hábitos de
    ``macro_habits``; ``extra``, lo demás que estos usan (``champ``, ``clocks``, ``opp``, ``opp_frames`` y
    ``gold_diff``, ver ``macro_habits.Game``)."""
    if len(anchors) < 5 or not role:
        return {}
    times = [a[0] for a in anchors]

    def pos(t: int):
        return None if _dead(dead, t) else interpolate(anchors, t, times)

    out: dict = {}
    lane = geo.ROLE_LANE.get(role)
    if lane:
        out.update(_roams(pos, lane, kills, me, cs, opp_cs, team))
    if role == "JUNGLE" and enemy_jungler:
        out.update(_counter_jungle(pos, kills, me, enemy_jungler))
    if role == "MID" and ally_jungler:
        out.update(_mid_with_jungler(pos, kills, ally_jungler))
    out.update(_vision(pos, team, wards, objectives))
    if role == "JUNGLE" and team:
        out.update(early_route(anchors, kills, me, team))
    if role == "SUPPORT" and adc:
        out["rm_adc_alone_deaths"] = _adc_alone_deaths(pos, kills, adc)
    game = macro_habits.Game(
        me=me, team=team, role=role, kills=kills, pos=pos, dead=dead, objectives=objectives, buildings=buildings, cs=cs,
        backs=backs, wards=wards, minutes=times[-1] / 60_000, **(extra or {}),
    )
    out.update(macro_habits.habits(game))
    return out


def _roams(pos, lane: str, kills: list, me: int, cs: list | None, opp_cs: list | None, team: int | None) -> dict:
    """Roams antes del 14 (20 s o más fuera de tu línea): cuántos, adónde, cómo acaban y el CS que te cuestan."""
    points, _ = geo.LANES[lane]
    episodes = _roam_episodes(_away_from_lane(pos, points))
    success, died, cost = _roam_outcomes(episodes, kills, me, cs, opp_cs)
    out: dict = {"rm_roams": len(episodes)}
    # Adónde vas (el punto más lejos de tu línea): el support de Aspirante sale el doble que el de Platino, pero
    # sobre todo a otras líneas (0,45 → 1,0) y al río (0,7 → 1,2); el mid de Aspirante va más a otras líneas
    # (0,6 → 1,2) y menos al río (0,55 → 0,35).
    dest = {"lane": 0, "river": 0, "own_jg": 0, "enemy_jg": 0}
    for s0, e0 in episodes:
        d = _roam_destination(pos, points, s0, e0, team)
        if d in dest:
            dest[d] += 1
    out.update({f"rm_roam_{k}": v for k, v in dest.items()})
    if episodes:
        out["rm_roam_success_pct"] = round(100 * success / len(episodes), 1)
        out["rm_roam_died"] = died
        out["rm_roam_cs_cost"] = cost
    return out


def _away_from_lane(pos, points) -> list[bool]:
    """Cada 5 s del 1:30 al 14: ¿estabas fuera de tu línea (lejos de ella, ni en base ni muerto)?"""
    away = []
    for t in range(90_000, EARLY_MS, STEP_MS):
        p = pos(t)
        away.append(bool(p is not None and geo.dist_polyline(p, points) > ROAM_FROM_LANE and not geo.zone(p).startswith("base")))
    return away


def _roam_episodes(away: list[bool]) -> list[tuple[int, int]]:
    """(inicio, fin) en ms de cada salida de la línea de al menos ``ROAM_MIN_SAMPLES`` muestras."""
    episodes, i = [], 0
    while i < len(away):
        if away[i]:
            j = _episode_end(away, i)
            if j - i + 1 >= ROAM_MIN_SAMPLES:
                episodes.append((90_000 + i * STEP_MS, 90_000 + j * STEP_MS))
            i = j + 1
        else:
            i += 1
    return episodes


def _episode_end(away: list[bool], i: int) -> int:
    """Última muestra fuera de la línea de la salida que empieza en ``i`` (un hueco de una sola muestra no la corta)."""
    j = i
    while j + 1 < len(away) and (away[j + 1] or (j + 2 < len(away) and away[j + 2])):
        j += 1
    return j


def _roam_outcomes(episodes: list[tuple[int, int]], kills: list, me: int, cs: list | None, opp_cs: list | None) -> tuple:
    """(roams con kill o asistencia tuya, roams en los que mueres, CS que pierdes frente a tu rival mientras tanto)."""
    success = died = cost = 0
    for s, e in episodes:
        ks = [k for k in kills if s - 5_000 <= k[0] * 1000 <= e + 15_000]
        if any(_involved(k, me) for k in ks):
            success += 1
        elif any(k[2] == me for k in ks):
            died += 1
        m0, m1 = int(s // 60_000), int(e // 60_000) + 1
        if cs and opp_cs and m1 < len(cs) and m1 < len(opp_cs) and None not in (cs[m0], cs[m1], opp_cs[m0], opp_cs[m1]):
            cost += (opp_cs[m1] - opp_cs[m0]) - (cs[m1] - cs[m0])
    return success, died, cost


def _roam_destination(pos, points, start: int, end: int, team: int | None) -> str | None:
    """Zona del punto del roam más lejos de tu línea (``None`` si no hay ninguna posición)."""
    positions = (p for p in map(pos, range(start, end + 1, STEP_MS)) if p is not None)
    far = max(((p, geo.dist_polyline(p, points)) for p in positions), key=lambda x: x[1], default=None)
    return None if far is None else _place(far[0], team)


def _counter_jungle(pos, kills: list, me: int, enemy_jungler: int) -> dict:
    """Jugadas del jungla rival antes del 14: cuántas, en cuántas estabas cerca y a cuántas respondiste (kill o asistencia)."""
    events = [k for k in kills if k[0] * 1000 < EARLY_MS and _involved(k, enemy_jungler)]
    if not events:
        return {}
    near = answered = 0
    for k in events:
        t = int(k[0] * 1000)
        p = pos(t)
        if p is not None and geo.dist(p, (k[3], k[4])) <= NEAR:
            near += 1
        if any(t - 2_000 <= k2[0] * 1000 <= t + RESPONSE_S * 1000 and _involved(k2, me) and geo.dist((k2[3], k2[4]), (k[3], k[4])) <= NEAR for k2 in kills):
            answered += 1
    return {
        "rm_jg_events": len(events),
        "rm_counter_near_pct": round(100 * near / len(events), 1),
        "rm_counter_pct": round(100 * answered / len(events), 1),
    }


def _mid_with_jungler(pos, kills: list, ally_jungler: int) -> dict:
    """% de las peleas de tu jungla antes del 14 fuera de las líneas en las que estabas cerca."""
    fights = [k for k in kills if k[0] * 1000 < EARLY_MS and (_involved(k, ally_jungler) or k[2] == ally_jungler) and not geo.is_lane(geo.zone((k[3], k[4])))]
    if not fights:
        return {}
    near = sum(1 for k in fights if (p := pos(int(k[0] * 1000))) is not None and geo.dist(p, (k[3], k[4])) <= NEAR)
    return {"rm_mid_with_jg_pct": round(100 * near / len(fights), 1)}


def _adc_alone_deaths(pos, kills: list, adc: int) -> int:
    """Muertes de tu ADC antes del 14 estando tú lejos (a más de ``SUPPORT_FAR``)."""
    deaths = [k for k in kills if k[0] * 1000 < EARLY_MS and k[2] == adc]
    return sum(
        1 for k in deaths if (p := pos(int(k[0] * 1000))) is not None and geo.dist(p, (k[3], k[4])) > SUPPORT_FAR
    )


def _vision(pos, team: int | None, wards: list | None, objectives: list | None) -> dict:
    """Visión útil (medida con ~35.000 partidas, Platino → Aspirante): support con ward junto al objetivo antes de que
    caiga 41 % → 53 %; pinks junto a dragón o barón, jungla 0,9 → 2,0 y support 2,2 → 3,4; wards en la jungla rival antes
    del 10 (seguir al jungla rival), jungla 0,26 → 0,40 y support 0,10 → 0,25; el support pone menos wards en su línea
    (56 % → 43 %) y más en total antes del 14 (8,7 → 12). En top, mid y ADC el ward del objetivo no cambia (~20 %)."""
    if wards is None or not team:
        return {}
    placed = _placed_wards(pos, team, wards)
    out: dict = {}
    early = [w for w in placed if w[0] * 1000 < EARLY_MS]
    out["rm_wards_pre14"] = sum(1 for w in wards if w[1] in WARD_TYPES and w[0] * 1000 < EARLY_MS)
    if len(early) >= WARD_SPLIT_MIN:
        for zone in ("lane", "river", "enemy_jg"):
            out[f"rm_ward_{zone}_pct"] = round(100 * sum(1 for w in early if w[3] == zone) / len(early), 1)
    out["rm_ward_enemy_jg10"] = sum(1 for w in placed if w[0] < ENEMY_JG_EARLY_S and w[3] == "enemy_jg")
    out["rm_pink_obj"] = sum(
        1 for w in placed if w[1] == "CONTROL_WARD" and min(geo.dist(w[2], geo.PITS["dragon"]), geo.dist(w[2], geo.PITS["baron"])) <= OBJ_WARD_NEAR
    )
    out.update(_objective_wards(placed, objectives))
    return out


def _placed_wards(pos, team: int, wards: list) -> list[tuple]:
    """(segundo, tipo, posición, zona) de cada ward que pusiste estando vivo (los de ``WARD_TYPES``)."""
    placed = []
    for w in wards:
        if w[1] in WARD_TYPES and (p := pos(int(w[0] * 1000))) is not None:
            placed.append((w[0], w[1], p, _place(p, team)))
    return placed


def _objective_wards(placed: list[tuple], objectives: list | None) -> dict:
    """% de objetivos (dragón, barón…) con un ward tuyo junto al foso puesto entre 2 min y 5 s antes de que caigan."""
    seen, n, covered = set(), 0, 0
    for o in objectives or []:
        if o[1] not in OBJECTIVES or (o[1], int(o[0] // 60)) in seen:
            continue
        seen.add((o[1], int(o[0] // 60)))
        n += 1
        pit = _objective_pit(o)
        if any(o[0] - OBJ_WARD_BEFORE_S[0] <= w[0] <= o[0] - OBJ_WARD_BEFORE_S[1] and geo.dist(w[2], pit) <= OBJ_WARD_NEAR for w in placed):
            covered += 1
    return {"rm_obj_ward_pct": round(100 * covered / n, 1)} if n else {}


ROUTE_WINDOW_MS = 240_000  # los primeros 4 min
ROUTE_CAMP_NEAR = 900
ANCHOR_CAMP = 6  # anclas de campamento de la reconstrucción (``analyzer.infer_camps``)
SMALL_CAMPS = ("raptors", "wolves", "gromp", "krugs")


def early_route(anchors: list, kills: list, me: int, team: int) -> dict:
    """Tus primeros 4 min de jungla (con los campamentos de la reconstrucción): full clear, 3-4 campamentos y gank, kill
    o asistencia antes de 3:00, algún campamento de la jungla rival y si empiezas por uno pequeño. En % (0 o 100 por
    partida) para comparar con cada elo: no son errores. Medido el 25-11-2026 con 22.739 partidas (Hierro-Oro →
    Diamante+): full clear 26 → 12 %, 3-4 campamentos y gank 27 → 45 %, kill antes de 3:00 15 → 26 %, empezar por uno
    pequeño 11 → 35 % (en elo alto se rompe la ruta para ganar un gank o un invade)."""
    camps: list[tuple[int, str]] = []
    for a in anchors:
        if a[0] >= ROUTE_WINDOW_MS:
            break
        if a[3] != ANCHOR_CAMP:
            continue
        camp = geo.nearest_camp((a[1], a[2]), ROUTE_CAMP_NEAR)
        if camp and (not camps or camps[-1] != camp):
            camps.append(camp)
    if len(camps) < 2:
        return {}
    own = sum(1 for c in camps if c[0] == team)
    first_kill = min((k[0] for k in kills if k[0] * 1000 < ROUTE_WINDOW_MS and _involved(k, me)), default=None)
    gank = first_kill is not None and first_kill < 210 and own <= 4
    return {
        "jr_full": 100 * (own >= 5),
        "jr_three_gank": 100 * gank,
        "jr_kill3": 100 * (first_kill is not None and first_kill < 180),
        "jr_invade": 100 * any(c[0] != team for c in camps),
        "jr_start_small": 100 * (camps[0][1] in SMALL_CAMPS),
    }


def _objective_pit(o: list) -> tuple:
    """Dónde cayó el objetivo: su posición si viene; si no, el foso del dragón (dragones y anciano) o el del barón."""
    if len(o) >= 5 and o[3] is not None:
        return (o[3], o[4])
    return geo.PITS["dragon" if o[1] in ("dragon", "elder") else "baron"]


def _adc_of(a: dict, team: int | None) -> int | None:
    """El ADC de tu equipo (su participante), para el support."""
    return next((p.get("pid") for p in a.get("players") or [] if p.get("team") == team and p.get("role") == "ADC"), None)


def _track(track: dict) -> tuple[list, list]:
    """(anclas en ms, muertes en ms) de un recorrido guardado."""
    anchors = [(int(x[0] * 1000), x[1], x[2], x[3]) for x in track.get("a") or []]
    dead = [(int(x[0] * 1000), int(x[1] * 1000)) for x in track.get("dead") or []]
    return anchors, dead


def _extra(a: dict, champ, patch, opp_frames: list) -> dict:
    """Lo que usan los hábitos de ``macro_habits`` además de lo de siempre."""
    return {
        "champ": champ,
        "clocks": clk.clocks_for_patch(patch) if a.get("clocks") else None,  # solo si los relojes cuadraron
        "opp": a.get("opponent"),
        "opp_frames": opp_frames,
        "gold_diff": (a.get("series") or {}).get("team_gold_diff"),
    }


def _frames_from_track(track: dict, xp: list) -> list:
    """(x, y, experiencia) de tu rival en cada minuto, con su recorrido reconstruido (tus partidas)."""
    anchors, dead = _track(track)
    if len(anchors) < 2:
        return [(None, None, v) for v in xp]
    times = [x[0] for x in anchors]
    out = []
    for m, v in enumerate(xp):
        p = None if _dead(dead, m * 60_000) else interpolate(anchors, m * 60_000, times)
        out.append((p[0], p[1], v) if p else (None, None, v))
    return out


def from_analysis(a: dict) -> dict:
    """Desde un análisis guardado (tus partidas): su repetición (recorridos y kills) y sus series de CS."""
    replay = a.get("replay") or {}
    me = a.get("me")
    tracks = replay.get("tracks") or {}
    anchors, dead = _track(tracks.get(str(me)) or {})
    team = a.get("team")
    series = a.get("series") or {}
    opp_frames = _frames_from_track(tracks.get(str(a.get("opponent"))) or {}, series.get("opp_xp") or [])
    return features(
        anchors, dead, replay.get("kills") or [], me=me, role=a.get("role"), ally_jungler=a.get("ally_jungler"),
        enemy_jungler=a.get("enemy_jungler"), adc=_adc_of(a, team), cs=series.get("cs"), opp_cs=series.get("opp_cs"),
        team=team, wards=a.get("my_wards"), objectives=replay.get("objectives"), buildings=replay.get("buildings"),
        backs=(a.get("build") or {}).get("backs"), extra=_extra(a, a.get("champ"), a.get("patch"), opp_frames),
    )


def from_dossier(d: dict) -> dict:
    """Desde el dossier de un OTP (``data/otps``): su recorrido, las kills del mapa y su CS y el de su rival."""
    a = d.get("analysis") or {}
    anchors, dead = _track(d.get("track") or {})
    team = a.get("team")
    map_events = d.get("map_events") or {}
    opp_frames = [(f.get("x"), f.get("y"), f.get("xp")) for f in d.get("opponent_frames") or []]
    return features(
        anchors, dead, map_events.get("kills") or [], me=a.get("me"), role=d.get("role") or a.get("role"),
        ally_jungler=a.get("ally_jungler"), enemy_jungler=a.get("enemy_jungler"), adc=_adc_of(a, team),
        cs=[f.get("cs") for f in d.get("frames") or []], opp_cs=[f.get("cs") for f in d.get("opponent_frames") or []],
        team=team, wards=(d.get("events") or {}).get("wards_placed"), objectives=map_events.get("objectives"),
        buildings=map_events.get("buildings"), backs=(a.get("build") or {}).get("backs"),
        extra=_extra(a, d.get("champ"), d.get("patch") or a.get("patch"), opp_frames),
    )


KEYS = (
    "jr_full", "jr_three_gank", "jr_kill3", "jr_invade", "jr_start_small",
    "rm_roams", "rm_roam_success_pct", "rm_roam_cs_cost", "rm_counter_near_pct", "rm_counter_pct", "rm_mid_with_jg_pct",
    "rm_adc_alone_deaths", "rm_roam_lane", "rm_roam_river", "rm_roam_own_jg", "rm_roam_enemy_jg", "rm_wards_pre14",
    "rm_ward_lane_pct", "rm_ward_river_pct", "rm_ward_enemy_jg_pct", "rm_ward_enemy_jg10", "rm_pink_obj", "rm_obj_ward_pct",
) + macro_habits.KEYS
