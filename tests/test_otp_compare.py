"""«Mi progreso»: tú frente a los OTPs de tu campeón (data/otps/<Campeón>/resumen.json)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.coach import otp_compare

AATROX = 266


def _otp_summary(base: Path):
    def block(games, players, **metrics):
        return {"games": games, "players": players, "wr": 52.0, "metrics": {k: {"mean": v, "median": v, "n": games} for k, v in metrics.items()},
                "first_legendary": [{"value": "Cuchilla negra", "key": 3071, "games": 60, "pct": 60.0, "wr": 55.0}],
                "skill_max": [{"value": "Q>E>W", "key": "Q>E>W", "games": 90, "pct": 90.0, "wr": 52.0}], "keystones": []}
    folder = base / "otps" / "Aatrox"
    folder.mkdir(parents=True)
    summary = {"roles": {"TOP": {"all": {}, "by_tier": {
        "EMERALD": block(120, 6, deaths=5.0, cs10=70, ganks_pre10=1.0),
        "CHALLENGER": block(200, 9, deaths=4.0, cs10=82, ganks_pre10=1.5),
        "DIAMOND": block(10, 1, deaths=4.5, cs10=75),  # pocas partidas: no se usa
    }}}}
    (folder / "resumen.json").write_text(json.dumps(summary), encoding="utf-8")


def _games(n=6, **metrics):
    return [{"champ": AATROX, "role": "TOP", "has_timeline": True, "queue": 400, "win": i % 2 == 0, "metrics": dict(metrics),
             "build": {"items": [3071], "skill_max": ["Q", "E", "W"]}} for i in range(n)]


def _svc(tmp_path):
    return SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path), dd=SimpleNamespace(name=lambda c: "Aatrox", item_catalog=None))


def test_compares_you_with_otps_of_your_target_tier_and_the_top(tmp_path):
    _otp_summary(tmp_path)
    (res,) = otp_compare.compare(_svc(tmp_path), _games(deaths=6.0, cs10=71, ganks_pre10=1.02), "EMERALD")
    assert res["ref"]["tier"] == "EMERALD" and res["ref_top"]["tier"] == "CHALLENGER" and res["ranked_share"] == 0.0
    rows = {r["metric"]: r for r in res["metrics"]}
    assert rows["deaths"]["status"] == "bad" and rows["deaths"]["ref_top"] == 4.0  # 6 frente a 5: muere más
    assert rows["cs10"]["status"] == "even" and rows["ganks_pre10"]["status"] == "even"
    assert res["metrics"][0]["metric"] == "deaths"  # lo peor primero
    assert res["build"]["otp_first_item"]["key"] == 3071 and res["build"]["my_skill_max"]["key"] == "Q>E>W"


def test_needs_enough_games_on_both_sides(tmp_path):
    _otp_summary(tmp_path)
    assert otp_compare.compare(_svc(tmp_path), _games(n=3, deaths=5.0), "EMERALD") == []  # pocas tuyas
    (res,) = otp_compare.compare(_svc(tmp_path), _games(deaths=5.0), "DIAMOND")  # Diamante con pocas: solo la de arriba
    assert res["ref"]["tier"] == "CHALLENGER" and res["ref_top"] is None
    assert otp_compare.compare(_svc(tmp_path / "nada"), _games(deaths=5.0), "EMERALD") == []


def test_games_with_an_afk_do_not_count(tmp_path):
    """Una partida con un AFK no entra en tus medias (como en el resto de «Mi progreso»): no hay falso «te separa»."""
    _otp_summary(tmp_path)
    games = _games(deaths=5.0, cs10=70)
    games[0]["metrics"] |= {"deaths": 15.0, "afk_min": 6.0, "riot_afk": 1}
    (res,) = otp_compare.compare(_svc(tmp_path), games, "EMERALD")
    rows = {r["metric"]: r for r in res["metrics"]}
    assert res["my_games"] == 5 and rows["deaths"]["mine"] == 5.0 and rows["deaths"]["status"] == "even"
