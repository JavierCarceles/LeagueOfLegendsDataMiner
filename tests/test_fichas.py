"""Fichas de campeón (macro/campeones): formato válido y una por cada campeón de Data Dragon."""

from __future__ import annotations

import pytest

from app.coach import fichas as F


def test_todas_las_fichas_tienen_el_formato():
    assert F.check() == {}


def test_cada_ficha_es_de_un_campeon_de_verdad():
    if not F.champions():
        pytest.skip("sin caché de Data Dragon")
    assert set(F.all_cards()) <= set(F.champions())  # las que faltan: python -m app.coach.fichas --faltan


def test_alcance_largo_de_las_fichas():
    assert F._reach("global") == float("inf") and F._reach("2.500-3.500 u") == 3500 and F._reach("1000 u") == 1000
    if F.load("Jinx"):
        assert 3 in F.far_reach().get(222, {})  # la R de Jinx llega a todo el mapa


def test_nombres_de_campeones_y_ids():
    if not F.champions():
        pytest.skip("sin caché de Data Dragon")
    assert F.resolve("Wukong") == "MonkeyKing" and F.resolve("Nunu & Willump") == "Nunu"
    assert F.resolve("Maestro Yi") == "MasterYi" and F.resolve("no existe") is None
    card = F.all_cards().get("LeeSin")
    if card:
        assert F.by_id(64)["clave"] == "LeeSin"


def test_el_validador_marca_errores():
    errors = F.validate({"clave": "LeeSin"})
    assert any("roles" in e for e in errors)
    good = F.load("LeeSin")
    if good:
        broken = {**good, "alcance": "lejos", "roles": [{"rol": "JUNGLA", "principal": True}]}
        errors = F.validate(broken, "LeeSin")
        assert any("alcance" in e for e in errors) and any("roles" in e for e in errors)
