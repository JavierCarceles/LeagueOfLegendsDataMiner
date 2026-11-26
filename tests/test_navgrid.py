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


def test_el_mapa_del_alma_cambia_los_caminos(monkeypatch):
    # Un portal de prueba entre dos puntos lejanos: con ese mapa la ruta lo cruza y casi no cuenta en el tiempo.
    a, b = (3000.0, 9000.0), (11000.0, 6000.0)
    monkeypatch.setitem(nav.RIFT_CHANGES, "Prueba", {"portals": (((3400.0, 8800.0), (10600.0, 6200.0), True),)})
    for fn in (nav._free_for, nav._links_for, nav._cost_for, nav._route, nav._line_clear):
        fn.cache_clear()
    plain, portal = nav.route(a, b), nav.route(a, b, rift="Prueba")
    assert nav.length(portal) < nav.length(plain)
    assert any(nav.portal_hop(p, q, "Prueba") for p, q in zip(portal, portal[1:]))
    track = nav.route_track([[0, a[0], a[1], 0], [20_000, b[0], b[1], 0]], rift=("Prueba", 0))
    assert len(track) > 2 and all(x[0] <= y[0] for x, y in zip(track, track[1:]))
    anchors = [[0, a[0], a[1], 0], [20_000, b[0], b[1], 0]]
    assert nav.route_track(anchors, rift=("Prueba", 30_000)) == nav.route_track(anchors)  # antes del cambio, el mapa de siempre
    for fn in (nav._free_for, nav._links_for, nav._cost_for, nav._route, nav._line_clear):
        fn.cache_clear()


def test_muros_que_caen_y_rocas_que_cierran(monkeypatch):
    monkeypatch.setitem(nav.RIFT_CHANGES, "Abre", {"open": ((7400.0, 7400.0, 600.0),)})
    monkeypatch.setitem(nav.RIFT_CHANGES, "Cierra", {"close": ((7400.0, 7400.0, 600.0),)})
    nav._free_for.cache_clear()
    i = nav.cell((7400.0, 7400.0))
    k = i[1] * nav.N + i[0]
    assert nav._free_for("Abre")[k] is True and nav._free_for("Cierra")[k] is False
    assert nav._free_for(None) is nav._FREE
    nav._free_for.cache_clear()


def test_mapas_reales_del_alma():
    for fn in (nav._free_for, nav._links_for, nav._cost_for, nav._route, nav._line_clear):
        fn.cache_clear()
    # Montaña: la roca de la boca del foso del dragón cierra el paso.
    rock = nav.cell((10290, 4810))
    assert nav._FREE[rock[1] * nav.N + rock[0]] and not nav._free_for("Mountain")[rock[1] * nav.N + rock[0]]
    # Hextech: de la base azul se llega antes a sus lobos por el portal (solo de ida: de vuelta no se usa).
    out_, back = nav.route((2550, 3800), (3800, 6600), rift="Hextech"), nav.route((3800, 6600), (2550, 3800), rift="Hextech")
    assert any(nav.portal_hop(p, q, "Hextech") for p, q in zip(out_, out_[1:]))
    assert not any(nav.portal_hop(p, q, "Hextech") for p, q in zip(back, back[1:]))
    # Nube: hay corriente alrededor del azul azul, no en su centro.
    ring = nav._cost_for("Cloud")
    c = nav.cell((3821, 7901 + 1100))
    assert ring.get(c[1] * nav.N + c[0]) == 0.74 and nav.cell((3821, 7901))[1] * nav.N + nav.cell((3821, 7901))[0] not in ring
    for fn in (nav._free_for, nav._links_for, nav._cost_for, nav._route, nav._line_clear):
        fn.cache_clear()
