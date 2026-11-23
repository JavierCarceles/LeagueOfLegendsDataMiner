"""Rutas que rodean los muros de la Grieta (navgrid) para la repetición 2D."""

from __future__ import annotations

from app.coach import geometry as geo
from app.coach import navgrid as nav


def test_la_rejilla_encaja_con_el_mapa():
    assert nav.walkable(geo.FOUNTAIN[100]) and nav.walkable(geo.FOUNTAIN[200])
    assert nav.walkable(geo.PITS["dragon"]) and nav.walkable(geo.PITS["baron"])
    assert all(nav.walkable(p) for p in geo.CAMPS.values())
    assert not nav.walkable((-100, 14900))  # fuera del mapa


def test_rodea_muros_y_los_que_saltan_muros_atajan():
    blue_buff, enemy_krugs = geo.CAMPS[(100, "blue")], geo.CAMPS[(200, "krugs")]
    path = nav.route(blue_buff, enemy_krugs)
    assert len(path) > 2 and path[0] == blue_buff and path[-1] == enemy_krugs
    assert all(nav.clear(path[i], path[i + 1]) for i in range(len(path) - 1))  # ningún tramo cruza un muro
    assert nav.length(path) > geo.dist(blue_buff, enemy_krugs)
    kayn = nav.route(blue_buff, enemy_krugs, champ=141)
    assert nav.length(kayn) <= nav.length(path)
    # Por la línea de mid no hay muros: recta.
    assert nav.route(geo.FOUNTAIN[100], geo.FOUNTAIN[200]) == [geo.FOUNTAIN[100], geo.FOUNTAIN[200]]


def test_la_repeticion_anade_puntos_de_ruta_con_su_hora_y_respeta_los_saltos():
    blue_buff, enemy_krugs = geo.CAMPS[(100, "blue")], geo.CAMPS[(200, "krugs")]
    track = [[60.0, int(blue_buff[0]), int(blue_buff[1]), 0], [120.0, int(enemy_krugs[0]), int(enemy_krugs[1]), 0]]
    out = nav.route_track(track)
    assert len(out) > 2 and all(a[3] == nav.ANCHOR_ROUTE for a in out[1:-1])
    assert [a[0] for a in out] == sorted(a[0] for a in out) and 60 < out[1][0] < 120
    hold = [[60.0, int(blue_buff[0]), int(blue_buff[1]), 4], [120.0, int(enemy_krugs[0]), int(enemy_krugs[1]), 2]]
    assert nav.route_track(hold) == hold  # vuelta a base: se queda y salta, no anda
    replay = nav.route_replay({"tracks": {"1": {"a": track, "dead": []}}}, {"1": 141})
    assert replay["routed"] and len(replay["tracks"]["1"]["a"]) >= 2
