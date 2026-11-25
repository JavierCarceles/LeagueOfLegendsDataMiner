"""Recorridos que cuadran entre los 10 jugadores (app/coach/analyzer.py): peleas sin muerte deducidas del daño a campeones
de cada minuto, wards rotos y la misma reconstrucción desde los 10 puntos de vista."""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.coach import analyzer as A
from app.coach import geometry as geo
from app.ingest import otp_details
from tests.factories import make_match, make_timeline

JUNGLE = (3800, 8100)  # jungla azul
TOP_LANE = (1500, 11000)
RIVER = (9000, 6000)


def frame(dmg: int = 0, cs: int = 0, hp: int = 1000, top: int = 1000, speed: int = 345) -> dict:
    return {"damageStats": {"totalDamageDoneToChampions": dmg}, "minionsKilled": cs, "jungleMinionsKilled": 0,
            "championStats": {"health": hp, "healthMax": top, "movementSpeed": speed}}


def game(frames: dict[int, list[dict]], champs: dict[int, int] | None = None) -> SimpleNamespace:
    """Partida de 2 minutos con los 10 jugadores (1-5 azul, 6-10 rojo); los que no se dan, quietos y sin pegar."""
    pf = {pid: frames.get(pid) or [frame(), frame(), frame()] for pid in range(1, 11)}
    players = {pid: {"team": 100 if pid <= 5 else 200, "champ": (champs or {}).get(pid, 1)} for pid in range(1, 11)}
    return SimpleNamespace(has_timeline=True, ts=[0, 60_000, 120_000], pf=pf, players=players,
                           ward_kill_events=[], ward_events={}, is_dead=lambda pid, t: False)


def still(pos) -> list[tuple]:
    return [(0, *pos, 0), (60_000, *pos, 0), (120_000, *pos, 0)]


def anchors_far(override: dict[int, list]) -> dict[int, list]:
    """Cada uno quieto en su base salvo los que se dan (lejos de todo: nadie pelea con ellos)."""
    out = {pid: still((600, 600) if pid <= 5 else (14200, 14200)) for pid in range(1, 11)}
    out.update(override)
    return out


def test_un_gank_sin_muerte_lleva_al_jungla_a_la_linea_y_el_laner_que_farmea_se_queda():
    """El jungla azul (2) pega 400 a campeones en el segundo minuto sin kill ni asistencia; el top rojo (6) farmea en su
    línea y también pegó: el jungla fue a la línea (el laner no se mueve)."""
    g = game({2: [frame(), frame(), frame(dmg=400)], 6: [frame(), frame(cs=0), frame(dmg=150, cs=6)]})
    out = A.infer_fights(g, anchors_far({2: still(JUNGLE), 6: still(TOP_LANE)}))
    assert 6 not in out  # farmeaba: no se le saca de su línea
    [(t, x, y, kind)] = out[2]
    assert kind == A.ANCHOR_FIGHT and 60_000 < t < 120_000
    assert abs(geo.dist((x, y), TOP_LANE) - A.FIGHT_GAP) <= 2  # a distancia de pegarle


def test_no_se_inventa_una_pelea_si_no_da_tiempo_andando_ni_con_quien_pega_de_lejos():
    far_apart = anchors_far({2: still((500, 500)), 7: still((14000, 14000))})
    hit = {2: [frame(), frame(), frame(dmg=400, speed=300)], 7: [frame(), frame(), frame(dmg=300, cs=6)]}
    assert A.infer_fights(game(hit), far_apart) == {}  # el 7 farmea en su sitio: 18.600 unidades en 30 s, imposible
    near = anchors_far({2: still(JUNGLE), 6: still(TOP_LANE)})
    xerath = game({2: [frame(), frame(), frame(dmg=400)], 6: [frame(), frame(), frame(dmg=150)]}, champs={2: 101})
    assert A.infer_fights(xerath, near) == {}  # pega de lejos: su daño no dice dónde estaba


def test_sin_senal_del_rival_no_hay_pelea_y_quien_ya_pasa_cerca_no_se_mueve():
    near = anchors_far({2: still(JUNGLE), 6: still(TOP_LANE)})
    # El rival ni pegó ni perdió vida: no hay con quién emparejarlo.
    assert A.infer_fights(game({2: [frame(), frame(), frame(dmg=400)]}), near) == {}
    # Ya estaban a distancia de pegarse: su daño está explicado.
    close = anchors_far({2: still((1600, 10500)), 6: still(TOP_LANE)})
    assert A.infer_fights(game({2: [frame(), frame(), frame(dmg=400)], 6: [frame(), frame(), frame(dmg=200)]}), close) == {}


def test_quien_rompe_un_ward_lejos_de_su_camino_fue_hasta_el():
    """El rojo 7 puso un ward amarillo en el río (0:30); el azul 2 lo rompe a la 1:30 y su recorrido no pasa por allí."""
    g = game({2: [frame(), frame(), frame()]})
    g.ward_events = {7: [(30_000, "YELLOW_TRINKET")]}
    g.ward_kill_events = [(90_000, 2, "YELLOW_TRINKET")]
    out = A.infer_ward_kills(g, anchors_far({2: still(JUNGLE), 7: still(RIVER)}))
    [(t, x, y, kind)] = out[2]
    assert (t, kind) == (90_000, A.ANCHOR_WARD) and abs(geo.dist((x, y), RIVER) - A.WARD_BREAK_GAP) <= 2
    # Si ya pasaba cerca de un ward rival vivo, no hace falta moverle.
    assert A.infer_ward_kills(g, anchors_far({2: still((8800, 6100)), 7: still(RIVER)})) == {}


def test_los_10_analisis_de_una_partida_comparten_la_reconstruccion():
    """Con ``shared``, los recorridos y el movimiento de los 10 se calculan una vez y son los mismos en los 10 análisis
    (antes solo el del analizado rodeaba muros)."""
    match = make_match("EUW1_9400000077", duration=1800)
    timeline = make_timeline(match)
    shared: dict = {}
    dossiers = [otp_details.build_dossier(match, timeline, p["puuid"], shared=shared) for p in match["info"]["participants"]]
    assert len({json.dumps(d["analysis"].get("movement"), sort_keys=True) for d in dossiers}) == 1
    for d in dossiers:  # el recorrido propio de cada uno es el de la reconstrucción compartida
        pid = d["analysis"]["me"]
        assert [tuple(a[:3]) for a in d["track"]["a"]] == [(round(t / 1000, 1), x, y) for t, x, y, _ in shared["anchors"][pid]]


def test_los_recorridos_tipicos_apartan_el_punto_medio_de_cada_tramo_como_suele_la_gente(monkeypatch):
    from app.coach import typical_paths as TP

    start, end = JUNGLE, (5000, 7000)
    blue = TP.key("JUNGLE", 90_000, start, end, 0.5)
    monkeypatch.setattr(TP, "table", lambda: {blue: (300.0, -200.0)})
    assert TP.offset("JUNGLE", 100, 90_000, start, end, 0.5) == (300.0, -200.0)
    # Un rojo se ve como si jugara de azul: su tramo en espejo usa la misma casilla y el desvío vuelve en espejo.
    red_start, red_end = geo.mirror(start), geo.mirror(end)
    assert TP.offset("JUNGLE", 200, 90_000, red_start, red_end, 0.5) == (-300.0, 200.0)
    assert TP.offset(None, 100, 90_000, start, end, 0.5) is None

    g = game({})
    g.players[2]["role"] = "JUNGLE"
    track = {2: [(60_000, *start, 0), (120_000, *end, 0)]}
    [(t, x, y, kind)] = A.infer_typical(g, track)[2]
    mid = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
    assert (t, kind) == (90_000, A.ANCHOR_TYPICAL) and (x, y) == (int(mid[0] + 300), int(mid[1] - 200))


def test_los_recorridos_tipicos_no_tocan_vueltas_a_base_muertes_ni_tramos_cortos(monkeypatch):
    from app.coach import typical_paths as TP

    monkeypatch.setattr(TP, "offset", lambda *a, **k: (300.0, -200.0))
    g = game({})
    g.players[2]["role"] = "JUNGLE"
    short = {2: [(60_000, *JUNGLE, 0), (70_000, 4000, 8000, 0)]}
    assert A.infer_typical(g, short) == {}
    for kind in (A.ANCHOR_BASE, A.ANCHOR_DEATH, A.ANCHOR_HOLD):
        assert A.infer_typical(g, {2: [(60_000, *JUNGLE, 0), (120_000, 5000, 7000, kind)]}) == {}
    monkeypatch.setattr(TP, "offset", lambda *a, **k: (9000.0, 0.0))  # 9.000 unidades en 12 s: imposible andando
    assert A.infer_typical(g, {2: [(60_000, *JUNGLE, 0), (84_000, 5000, 7000, 0)]}) == {}
