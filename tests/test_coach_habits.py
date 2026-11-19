"""«Aprende esto» (material de coaches) y hábitos entre partidas: tilt, sesiones, pool y ventajas que se escapan."""

from __future__ import annotations

import re
from pathlib import Path

from app.coach import insights, learn
from tests.test_coach_audit import run

HOUR = 3_600_000
DOCS = Path(__file__).resolve().parents[1] / "docs" / "coaches.md"


def test_learn_videos_are_only_the_verified_ones():
    verified = set(re.findall(r"https://www\.youtube\.com/[^\s)]+", DOCS.read_text(encoding="utf-8")))
    for key, video in learn.VIDEOS.items():
        assert video["url"] in verified, f"{key}: URL no verificada en docs/coaches.md"
        assert video["lang"] in ("ES", "EN")
    for name, t in learn.TOPICS.items():
        assert t["videos"] and all(v in learn.VIDEOS for v in t["videos"]), name
        assert t["drill"] and t["purpose"]


def test_every_weakness_and_mistake_maps_to_a_topic_or_none_on_purpose():
    for rule in insights.RULES:
        if rule["metric"] != "r_late":
            assert learn.for_metric(rule["metric"]), rule["metric"]
    no_video = {"r_late", "no_boots", "no_antiheal", "no_resist"}  # de build: el consejo ya basta
    for mt in insights.MISTAKE_TYPES:
        assert (learn.for_mistake(mt["id"]) is None) == (mt["id"] in no_video), mt["id"]
    cat = learn.catalog()
    assert set(cat["topics"]) == set(learn.TOPICS)
    assert all(topic in learn.TOPICS for _tag, topic in cat["by_tag"])


def _game(i: int, win: bool, start: int, *, role="JUNGLE", champ=64, metrics=None) -> dict:
    return {"match_id": f"EUW1_{i}", "queue": 420, "game_start": start, "duration": 1800, "champ": champ, "role": role, "win": win,
            "has_timeline": True, "metrics": metrics or {}}


def _sessions(results_by_session: list[list[bool]]) -> list[dict]:
    games, i, t = [], 0, 1_700_000_000_000
    for session in results_by_session:
        for win in session:
            games.append(_game(i, win, t))
            i += 1
            t += 40 * 60_000  # 30 min de partida + 10 de pausa
        t += 5 * HOUR
    return games


def test_tilt_is_flagged_only_with_a_clear_drop():
    # Tras 2 derrotas seguidas pierde siempre (10 partidas) y en general gana el 43 %.
    tilted = _sessions([[True, True, True, False, False, False, False]] * 5)
    report = insights.tilt_report(tilted)
    assert report["after_losses"]["games"] >= insights.TILT_MIN_GAMES
    assert report["tilted"] is True
    assert "para al menos 15 minutos" in report["message"]
    assert report["learn"]["topic"] == "tilt"
    # Pocas partidas tras 2 derrotas: no se concluye nada (ni para bien ni para mal).
    few = _sessions([[False, False, True]] * 2 + [[True, True]] * 5)
    report = insights.tilt_report(few)
    assert report["tilted"] is False and report["learn"] is None
    assert "pocas partidas" in report["message"]


def test_sessions_split_by_one_hour_gaps_and_count_long_ones():
    games = _sessions([[True] * 6, [False, True]])
    report = insights.tilt_report(games)
    assert report["sessions"] == 2 and report["long_sessions"] == 1
    assert [b["games"] for b in report["by_game"]] == [2, 2, 1, 3]  # 1.ª, 2.ª, 3.ª y «4.ª o más»


def test_pool_report_suggests_a_core_when_too_wide():
    t = 1_700_000_000_000
    champs = [64, 64, 64, 64, 64, 234, 234, 234, 62, 62, 120, 9]
    games = [_game(i, i % 2 == 0, t + i * HOUR, champ=c) for i, c in enumerate(champs)]
    [row] = insights.pool_report(games)
    assert row["wide"] is True and row["champions"] == 5
    assert [c["champ"] for c in row["core"]] == [64, 234, 62]
    narrow = [_game(i, True, t + i * HOUR, champ=64 if i % 2 else 234) for i in range(12)]
    [row] = insights.pool_report(narrow)
    assert row["wide"] is False
    assert insights.pool_report(narrow[:5]) == []  # muestra insuficiente


def test_leads_report_counts_lost_games_with_a_big_lead():
    t = 1_700_000_000_000
    games = [
        _game(1, False, t, metrics={"max_lead": 3500, "max_lead_min": 18.0, "max_deficit": -200}),
        _game(2, False, t + HOUR, metrics={"max_lead": 900, "max_lead_min": 5.0, "max_deficit": -4000}),
        _game(3, True, t + 2 * HOUR, metrics={"max_lead": 500, "max_lead_min": 3.0, "max_deficit": -2500}),
        _game(4, False, t + 3 * HOUR, metrics={"max_lead": 2600, "max_lead_min": 21.0, "max_deficit": 0, "ally_afk_min": 5}),  # con AFK: no cuenta
    ]
    report = insights.leads_report(games)
    assert (report["losses"], report["wasted"], report["comebacks"]) == (2, 1, 1)
    assert report["examples"] == [{"match_id": "EUW1_1", "champ": 64, "lead": 3500, "t": 1080}]


def test_analysis_stores_the_biggest_team_gold_lead():
    a = run()
    m = a["metrics"]
    diffs = a["series"]["team_gold_diff"]
    assert m["max_lead"] == max(diffs) and m["max_deficit"] == min(diffs)
    assert m["max_lead_min"] == a["series"]["minute"][diffs.index(max(diffs))]


def test_review_picks_the_worst_moments_of_different_kinds():
    moments = [
        {"t": 100.0, "kind": "death", "severity": "low", "tags": ["solo"]},
        {"t": 200.0, "kind": "death", "severity": "high", "tags": ["gank"]},
        {"t": 300.0, "kind": "death", "severity": "high", "tags": ["isolated"]},
        {"t": 400.0, "kind": "objective", "severity": "medium", "tags": ["objective", "far"]},
        {"t": 450.0, "kind": "trade", "severity": "good", "tags": ["objective", "trade"]},
        {"t": 500.0, "kind": "death", "severity": "medium", "tags": ["fight_won"]},  # pelea ganada: no es un error
        {"t": 600.0, "kind": "afk", "severity": "low", "tags": ["afk"]},
    ]
    assert [m["t"] for m in insights.review_moments(moments)] == [200.0, 300.0, 400.0]
    assert insights.review_moments([moments[0]]) == [moments[0]]
    assert insights.review_moments([moments[4], moments[6]]) == []
