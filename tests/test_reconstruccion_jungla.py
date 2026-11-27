"""Reconstrucción de los junglas con todo lo que da Riot: campamentos en tramos con kills o vueltas a base, escuttles y
cupos de la partida, la experiencia de cada campamento, daño de lejos y el mapa del alma del dragón (comparado con una
partida grabada el 26-11-2026)."""

from __future__ import annotations

from app.coach import analyzer as A
from app.coach import geometry as geo


class _G:
    """Lo mínimo de una partida para los ayudantes de la jungla."""

    ts = [60_000, 120_000]
    epic_kills: list = []

    def dead_overlap(self, pid, t0, t1):
        return 0

    def frame_pos(self, pid, i):
        return None


def test_repartos_de_campamentos_entre_trozos():
    assert sorted(A._compositions(2, 2)) == [(0, 2), (1, 1), (2, 0)]
    assert list(A._compositions(1, 1)) == [(1,)]
    assert sum(1 for _ in A._compositions(3, 3)) == 10


def test_un_minuto_con_kill_y_vuelta_a_base_se_parte_en_trozos():
    fountain = geo.FOUNTAIN[200]
    inside = [(30_000, 7000, 11000, 1), (50_000, int(fountain[0]), int(fountain[1]), A.ANCHOR_BASE)]
    gaps = A._camp_gaps(_G(), 7, (0, (8000, 9000)), inside, (60_000, (12000, 7000)))
    assert gaps[0] == (0, (8000, 9000), 30_000, (7000, 11000))
    assert gaps[1][3] is None and gaps[1][2] == 50_000 - A.RECALL_MS  # acaba donde canalizó la vuelta a base
    assert gaps[2][:2] == (50_000, (int(fountain[0]), int(fountain[1])))


def test_el_dano_de_lejos_no_coloca_a_nadie_donde_murio_la_victima():
    # Tu Hecarim: pegó a Orianna en el río, ella murió 19 s después en mid y él ya iba a los krugs.
    items = [(240_000, 7927, 8709, 0), (300_000, 7380, 11873, 0)]
    assert not A._near_fits(items, 288_600, (6364, 6431))
    assert A._near_fits(items, 288_600, (7200, 10500))


def test_escuttles_y_campamentos_rivales_segun_lo_que_hizo_en_la_partida():
    sites = A._camp_sites()
    assert any(A._is_scuttle(name) for name, _ in sites)
    budget = {"scuttles": 0, "enemy": 1}
    names = {name for name, _ in A._camp_candidates(sites, 200, budget)}
    assert not any(A._is_scuttle(n) for n in names) and "100_red" in names
    A._spend_budget(budget, 200, ["100_red"])
    assert "100_red" not in {name for name, _ in A._camp_candidates(sites, 200, budget)}
    assert A._alive("0_scuttle_bot", {}, A.SCUTTLE_SPAWN_MS) and not A._alive("0_scuttle_bot", {}, A.SCUTTLE_SPAWN_MS - 1)


def test_el_mapa_del_alma_sale_del_evento_de_riot():
    g = A._Game.__new__(A._Game)
    g.rift = None
    A._Game._on_soul(g, {"name": "Hextech", "teamId": 0}, 786_000)
    A._Game._on_soul(g, {"name": "Hextech", "teamId": 200}, 1_500_000)  # el del alma conseguida no cambia nada
    assert g.rift == ("Hextech", 786_000 + A.RIFT_DELAY_MS)  # el terreno nuevo entra unos segundos después


def test_la_experiencia_elige_que_campamentos_hizo():
    # Tu primer despeje grabado: con 12 de CS entre la 1:00 y las 2:00 hiciste rojo, krugs y rapaces (676 de XP), no
    # rojo, rapaces y lobos (lo más corto hasta donde estabas a las 2:00).
    red, krugs, raptors, wolves = (f"200_{n}" for n in ("red", "krugs", "raptors", "wolves"))
    sites = [(name, pos) for name, pos in A._camp_sites() if name in (red, krugs, raptors, wolves)]
    ends = ((7163, 11147), (10440, 8892))
    shortest = A._camp_route(sites, {}, ends, 120_000, 3)
    with_xp = A._camp_route(sites, {}, ends, 120_000, 3, (676, A.XP_FACTOR[1]))
    assert {n for n, _ in with_xp[1]} == {red, krugs, raptors}
    assert {n for n, _ in shortest[1]} != {red, krugs, raptors}


def test_los_campamentos_que_el_cs_da_por_hechos_se_adelantan_en_orden_sin_solaparse():
    # Tramo de 2 minutos con el frame del medio sin posición: el CS dice 3 campamentos hechos al 2:00.
    sites = [(f"100_{n}", geo.CAMPS[(100, n)]) for n in ("blue", "gromp", "wolves")]
    route = (A._route_length((3800, 7000), tuple(sites), (3800, 6500)), tuple(sites))
    out, killed = {}, {}
    A._place_camps(out, killed, (60_000, 180_000, 2), (3800, 7000), route, (9_000, 60_000), [120_000] * 3)
    times = [t for t, *_ in out[2]]
    assert times == sorted(times)
    assert all(b - a >= 9_000 for a, b in zip(times[0::2], times[1::2]))  # cada uno, su limpieza entera
    assert times[-1] <= 120_000  # el último, hecho a la hora del CS


def test_las_dos_primeras_escuttles_solo_son_de_quien_dice_riot():
    sites = A._camp_sites()
    nobody = {"scuttles": 3, "enemy": 0, "initial": 0, "buffs": 0}
    names = {n for n, _ in A._camp_candidates(sites, 100, nobody, killed={})}
    assert not any(A._is_scuttle(n) for n in names)
    later = {n for n, _ in A._camp_candidates(sites, 100, nobody, killed={"0_scuttle_top": 200_000})}
    assert "0_scuttle_top" in later and "0_scuttle_bot" not in later  # la de arriba ya reapareció: esa sí
    budget = {"scuttles": 2, "initial": 1}
    A._spend_budget(budget, 100, ["0_scuttle_bot"], frozenset({"0_scuttle_bot", "0_scuttle_top"}))
    assert budget == {"scuttles": 1, "initial": 0}


def test_las_primeras_escuttles_dan_menos_experiencia():
    assert A._camp_xp("0_scuttle_top", {}) == A.CAMP_XP["scuttle"] * A.INITIAL_SCUTTLE_XP
    assert A._camp_xp("0_scuttle_top", {"0_scuttle_top": 200_000}) == A.CAMP_XP["scuttle"]


def test_una_subida_de_nivel_junto_a_una_muerte_rival_no_es_de_un_campamento():
    g = _G()
    g.players = {7: {"team": 200}, 3: {"team": 100}}
    g.kills = [{"t": 300_000, "killer": 9, "assists": [], "victim": 3}]  # muere un rival cerca: experiencia para todos
    g.levels = {7: [(300_400, 5), (330_000, 6)]}
    g.fval = lambda pid, i, key: 0
    g.ts = [240_000, 360_000]
    assert A._camp_level_ups(g, 7, 240_000, 360_000) == [330_000]
