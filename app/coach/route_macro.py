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

from app.coach import geometry as geo
from app.coach.analyzer import interpolate

EARLY_MS = 14 * 60_000
STEP_MS = 5_000
ROAM_FROM_LANE = 2_500  # «fuera de tu línea»: a más de esto de ella (ni en base ni muerto)
ROAM_MIN_SAMPLES = 4  # 20 s seguidos fuera
NEAR = 3_000  # «cerca» de una jugada
RESPONSE_S = 15  # respuesta: kill o asistencia allí en los 15 s siguientes
SUPPORT_FAR = 4_000


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
) -> dict:
    """Métricas de una partida. ``anchors`` en ms; ``kills``: [segundo, matador, víctima, x, y, asistentes]."""
    if len(anchors) < 5 or not role:
        return {}
    times = [a[0] for a in anchors]

    def pos(t: int):
        return None if _dead(dead, t) else interpolate(anchors, t, times)

    out: dict = {}
    lane = geo.ROLE_LANE.get(role)
    if lane:
        points, _half = geo.LANES[lane]
        away = []
        for t in range(90_000, EARLY_MS, STEP_MS):
            p = pos(t)
            away.append(bool(p is not None and geo.dist_polyline(p, points) > ROAM_FROM_LANE and not geo.zone(p).startswith("base")))
        episodes, i = [], 0
        while i < len(away):
            if away[i]:
                j = i
                while j + 1 < len(away) and (away[j + 1] or (j + 2 < len(away) and away[j + 2])):
                    j += 1
                if j - i + 1 >= ROAM_MIN_SAMPLES:
                    episodes.append((90_000 + i * STEP_MS, 90_000 + j * STEP_MS))
                i = j + 1
            else:
                i += 1
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
        out["rm_roams"] = len(episodes)
        if episodes:
            out["rm_roam_success_pct"] = round(100 * success / len(episodes), 1)
            out["rm_roam_died"] = died
            out["rm_roam_cs_cost"] = cost
    if role == "JUNGLE" and enemy_jungler:
        events = [k for k in kills if k[0] * 1000 < EARLY_MS and _involved(k, enemy_jungler)]
        if events:
            near = answered = 0
            for k in events:
                t = int(k[0] * 1000)
                p = pos(t)
                if p is not None and geo.dist(p, (k[3], k[4])) <= NEAR:
                    near += 1
                if any(t - 2_000 <= k2[0] * 1000 <= t + RESPONSE_S * 1000 and _involved(k2, me) and geo.dist((k2[3], k2[4]), (k[3], k[4])) <= NEAR for k2 in kills):
                    answered += 1
            out["rm_jg_events"] = len(events)
            out["rm_counter_near_pct"] = round(100 * near / len(events), 1)
            out["rm_counter_pct"] = round(100 * answered / len(events), 1)
    if role == "MID" and ally_jungler:
        fights = [k for k in kills if k[0] * 1000 < EARLY_MS and (_involved(k, ally_jungler) or k[2] == ally_jungler) and not geo.is_lane(geo.zone((k[3], k[4])))]
        if fights:
            near = sum(1 for k in fights if (p := pos(int(k[0] * 1000))) is not None and geo.dist(p, (k[3], k[4])) <= NEAR)
            out["rm_mid_with_jg_pct"] = round(100 * near / len(fights), 1)
    if role == "SUPPORT" and adc:
        deaths = [k for k in kills if k[0] * 1000 < EARLY_MS and k[2] == adc]
        out["rm_adc_alone_deaths"] = sum(
            1 for k in deaths if (p := pos(int(k[0] * 1000))) is not None and geo.dist(p, (k[3], k[4])) > SUPPORT_FAR
        )
    return out


def from_analysis(a: dict) -> dict:
    """Desde un análisis guardado (tus partidas): su repetición (recorridos y kills) y sus series de CS."""
    replay = a.get("replay") or {}
    me = a.get("me")
    track = (replay.get("tracks") or {}).get(str(me)) or {}
    anchors = [(int(x[0] * 1000), x[1], x[2], x[3]) for x in track.get("a") or []]
    dead = [(int(d[0] * 1000), int(d[1] * 1000)) for d in track.get("dead") or []]
    team = a.get("team")
    adc = next((p.get("pid") for p in a.get("players") or [] if p.get("team") == team and p.get("role") == "ADC"), None)
    series = a.get("series") or {}
    return features(
        anchors, dead, replay.get("kills") or [], me=me, role=a.get("role"), ally_jungler=a.get("ally_jungler"),
        enemy_jungler=a.get("enemy_jungler"), adc=adc, cs=series.get("cs"), opp_cs=series.get("opp_cs"),
    )


def from_dossier(d: dict) -> dict:
    """Desde el dossier de un OTP (``data/otps``): su recorrido, las kills del mapa y su CS y el de su rival."""
    a = d.get("analysis") or {}
    track = d.get("track") or {}
    anchors = [(int(x[0] * 1000), x[1], x[2], x[3]) for x in track.get("a") or []]
    dead = [(int(x[0] * 1000), int(x[1] * 1000)) for x in track.get("dead") or []]
    team = a.get("team")
    adc = next((p.get("pid") for p in a.get("players") or [] if p.get("team") == team and p.get("role") == "ADC"), None)
    cs = [f.get("cs") for f in d.get("frames") or []]
    opp = [f.get("cs") for f in d.get("opponent_frames") or []]
    return features(
        anchors, dead, (d.get("map_events") or {}).get("kills") or [], me=a.get("me"), role=d.get("role") or a.get("role"),
        ally_jungler=a.get("ally_jungler"), enemy_jungler=a.get("enemy_jungler"), adc=adc, cs=cs, opp_cs=opp,
    )


KEYS = ("rm_roams", "rm_roam_success_pct", "rm_roam_cs_cost", "rm_counter_near_pct", "rm_counter_pct", "rm_mid_with_jg_pct", "rm_adc_alone_deaths")
