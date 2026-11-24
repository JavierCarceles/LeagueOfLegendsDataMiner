"""Parsers de las fuentes externas cuyas expresiones regulares se simplificaron: dan lo mismo que las anteriores.

Los resultados esperados son los que daban las regex de antes con estas mismas entradas.
"""

from __future__ import annotations

import pytest

from app.external.opgg import _mcp_tokens, iter_objects, parse_mcp_text
from app.external.patchnotes import _page_title

MCP_TOKENS = [
    (
        'Champion(266, "Aatrox")',
        [("id", "Champion"), ("p", "("), ("num", "266"), ("p", ","), ("str", '"Aatrox"'), ("p", ")")],
    ),
    (
        'Guide(tip="Juega \\"seguro\\"", rate=0.5123, n=-12, e=1.5E-3, ok=true)',
        [
            ("id", "Guide"), ("p", "("), ("id", "tip"), ("str", '"Juega \\"seguro\\""'), ("p", ","), ("id", "rate"),
            ("num", "0.5123"), ("p", ","), ("id", "n"), ("num", "-12"), ("p", ","), ("id", "e"), ("num", "1.5E-3"),
            ("p", ","), ("id", "ok"), ("id", "true"), ("p", ")"),
        ],
    ),
    # Los identificadores son ASCII (lo demás se salta); los dígitos, cualquiera que Python lea como número.
    (
        "Champion(naïve, ñandú, 12٣4, x)",
        [
            ("id", "Champion"), ("p", "("), ("id", "na"), ("id", "ve"), ("p", ","), ("id", "and"), ("p", ","),
            ("num", "12٣4"), ("p", ","), ("id", "x"), ("p", ")"),
        ],
    ),
    # Unas comillas sin cerrar no abren una cadena: se saltan.
    ('"sin cerrar Foo(1)', [("id", "sin"), ("id", "cerrar"), ("id", "Foo"), ("p", "("), ("num", "1"), ("p", ")")]),
    (
        'a"b"c -x --6 1.2.3 1e 1e+',
        [
            ("id", "a"), ("str", '"b"'), ("id", "c"), ("id", "x"), ("num", "-6"), ("num", "1.2"), ("num", "3"),
            ("num", "1"), ("id", "e"), ("num", "1"), ("id", "e"),
        ],
    ),
    ("  \n\t ", []),
]


@pytest.mark.parametrize(("text", "expected"), MCP_TOKENS)
def test_mcp_tokens_are_the_same_as_before(text, expected):
    assert _mcp_tokens(text) == expected


def test_parse_mcp_text_reads_the_compact_format():
    text = (
        "class LolListChampions: data\nclass Data: champions\nclass Champion: champion_id,name\n"
        'LolListChampions(Data([Champion(266,"Aatrox"),Champion(103,"Ahri")]))'
    )
    champions = [{"champion_id": 266, "name": "Aatrox"}, {"champion_id": 103, "name": "Ahri"}]
    assert parse_mcp_text(text) == {"data": {"champions": champions}}
    assert parse_mcp_text('Unknown(1, [2.5, -3e2], "a,b")') == {"_type": "Unknown", "args": [1, [2.5, -300.0], "a,b"]}
    assert parse_mcp_text("Pair(1)\nclass Pair: a, b") == {"_type": "Pair", "args": [1]}  # los campos no encajan
    guide = parse_mcp_text('Guide(tip="Juega \\"seguro\\"", e=1.5E-3, ok=true, ko=false, x=null, y=None)')
    args = ["tip", 'Juega "seguro"', "e", 0.0015, "ok", True, "ko", False, "x", None, "y", None]
    assert guide == {"_type": "Guide", "args": args}
    assert parse_mcp_text("Champion(12٣4)") == {"_type": "Champion", "args": [1234]}
    assert parse_mcp_text("Foo") == "Foo"
    assert parse_mcp_text("   ") is None
    assert parse_mcp_text('{"data": [1]}') == {"data": [1]}
    with pytest.raises(IndexError):
        parse_mcp_text("Champion([1, 2")  # respuesta cortada


def test_iter_objects_ignores_braces_inside_strings_and_skips_broken_objects():
    text = 'x{"key":"A","s":"}{\\"","n":{"b":[1,{"c":2}]}}y{"key":"B",}{"key":"C","t":"\\\\"}{"key":"D","u":"{'
    assert list(iter_objects(text, '{"key":"')) == [{"key": "A", "s": '}{"', "n": {"b": [1, {"c": 2}]}}, {"key": "C", "t": "\\"}]


@pytest.mark.parametrize(
    ("raw", "title"),
    [
        ("Notas de la versión 26.23 | League of Legends", "Notas de la versión 26.23"),
        ("\n  Patch 25.S1.1 notes | League of Legends\n", "Patch 25.S1.1 notes"),
        ("a | b | c", "a"),
        ("a|b\nc|d", "a|b\nc"),  # solo cuenta la «|» de la última línea
        ("a | b\nc", "a | b\nc"),
        ("a|b\n\n", "a|b"),
        ("x\xa0|\xa0y", "x"),
        ("sin barra", "sin barra"),
        ("", ""),
    ],
)
def test_patch_notes_title_is_cut_as_before(raw, title):
    assert _page_title(raw) == title
