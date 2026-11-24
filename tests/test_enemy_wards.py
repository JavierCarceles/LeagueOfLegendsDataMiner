"""Wards rivales estimados (``_ward_map``): el que te ve antes de morir con la lente sin usar y las pinks rivales en tu jungla."""

from app.coach import analyzer as an
from app.coach import insights


class _G:
    """Lo mínimo de ``_Game`` para el mapa de wards: posiciones fijas por jugador (o por tramos)."""

    def __init__(self, positions, ward_events, kills=(), purchases=None, role="JUNGLE", duration_ms=1_800_000):
        self.players = {1: {"team": 100, "champ": 64}, 6: {"team": 200, "champ": 99}, 7: {"team": 200, "champ": 412}}
        self.me = {"pid": 1}
        self.team = 100
        self.role = role
        self.has_timeline = True
        self.duration_ms = duration_ms
        self.ward_events = ward_events
        self.ward_kill_events = list(kills)
        self.purchases = purchases or {}
        self._pos = positions

    def level_at(self, pid, t):
        return 1

    def pos_at(self, pid, t):
        p = self._pos.get(pid)
        return p(t) if callable(p) else p


OWN_JG = (3500, 7500)  # jungla azul (equipo 100), lado de arriba


def test_la_pink_nueva_quita_la_anterior_y_el_ward_lo_quita_quien_esta_cerca():
    g = _G({1: OWN_JG, 6: OWN_JG}, {1: [], 6: [(60_000, "CONTROL_WARD"), (300_000, "CONTROL_WARD")], 7: []},
           kills=[(400_000, 1, "CONTROL_WARD")])
    w1, w2 = an._ward_map(g)
    assert w1["end"] == 300_000 and w1["killer"] is None  # la quitó su segunda pink
    assert w2["end"] == 400_000 and w2["killer"] == 1


def test_un_ward_rival_te_ve_antes_de_morir_y_llevabas_la_lente():
    g = _G({1: (9800, 4400), 6: (9900, 4500)}, {1: [], 6: [(100_000, "YELLOW_TRINKET")], 7: []},
           purchases={1: [(0, 3340), (60_000, 3364)]})
    seen = an._seen_by_ward(g, 150_000)
    assert seen and seen["pid"] == 6
    assert an._trinket(g, 1, 150_000) == "lens" and an._trinket(g, 1, 30_000) == "ward"
    assert an._seen_by_ward(g, 400_000) is None  # el trinket amarillo ya había caducado


def test_pink_rival_en_tu_jungla_pasando_a_su_lado_o_sin_pasar():
    far = (12000, 12000)
    passing = lambda t: OWN_JG if 200_000 <= t < 210_000 else far  # noqa: E731  (pasa una vez, corriendo)
    g = _G({1: passing, 6: OWN_JG}, {1: [], 6: [(100_000, "CONTROL_WARD")], 7: []})
    moments = []
    out = an._enemy_pinks(g, moments)
    assert out == {"enemy_pinks_own_jungle": 1, "enemy_pinks_long": 1, "enemy_pinks_ignored": 1}
    assert moments[0]["tags"] == ["vision", "enemy_pink", "passed"]
    assert insights.mistake_type("vision", set(moments[0]["tags"]), "MID")["id"] == "enemy_pink_ignored"

    g = _G({1: far, 6: OWN_JG}, {1: [], 6: [(100_000, "CONTROL_WARD")], 7: []}, role="MID")
    moments = []
    out = an._enemy_pinks(g, moments)
    assert out["enemy_pinks_ignored"] == 0 and moments == []  # en mid, sin pasar cerca: no es cosa suya
    g.role = "SUPPORT"
    moments = []
    an._enemy_pinks(g, moments)
    assert moments[0]["severity"] == "low"


def test_pink_rival_que_quitais_pronto_no_es_nada():
    g = _G({1: OWN_JG, 6: OWN_JG}, {1: [], 6: [(100_000, "CONTROL_WARD")], 7: []}, kills=[(150_000, 1, "CONTROL_WARD")])
    moments = []
    assert an._enemy_pinks(g, moments)["enemy_pinks_long"] == 0 and moments == []


def test_los_errores_nuevos_por_elo_solo_cuentan_las_partidas_que_los_pueden_tener():
    from app.coach import elo_profile as ep

    agg = ep._Agg()
    for i in range(40):  # analizadas antes: no pueden tener el error
        agg.add([], f"p{i}", {})
    for i in range(30):  # con el análisis nuevo: 1 de cada 3 lo tiene
        agg.add([("enemy_pink_ignored", "low")] if i % 3 == 0 else [], f"q{i}", {}, ("enemy_pink_ignored", "death_lens_unused"))
    types = agg.public()["types"]
    assert types["enemy_pink_ignored"]["pct"] == round(100 / 3, 1)
