"""Tus partidas escritas en data/mis_partidas (organizadas por rol y campeón) para subirlas y revisarlas."""

from __future__ import annotations

import gzip
import json

from app.coach import store
from app.coach.analyzer import ANALYSIS_VERSION, analyze_game
from app.coach.files import MyGamesFiles
from tests.factories import make_match, make_timeline

ME = "me-puuid"


def _save(svc, match_id, *, pid=2, win=True, start=1_795_000_000_000, version=ANALYSIS_VERSION):
    match = make_match(match_id, puuids={pid: ME}, blue_win=win, game_start=start)
    timeline = make_timeline(match)
    analysis = analyze_game(match, timeline, puuid=ME)
    store.save_game(
        svc.db,
        {
            "match_id": match_id, "puuid": ME, "pid": pid, "queue": 420, "game_start": start, "duration": 1800,
            "champ": analysis["champ"], "role": analysis["role"], "team": 100, "win": int(win), "status": "ready",
            "match_raw": match, "timeline_raw": timeline, "analysis": analysis,
            "digest": {"m": analysis["metrics"]}, "version": version, "analyzed_at": 1000,
        },
    )
    return analysis


def read(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def test_games_are_written_by_role_and_champion(keyed_svc):
    _save(keyed_svc, "EUW1_100", pid=2, win=True)  # jungla azul (Lee Sin)
    _save(keyed_svc, "EUW1_101", pid=2, win=False, start=1_795_100_000_000)
    _save(keyed_svc, "EUW1_102", pid=1, win=True, start=1_795_200_000_000)  # top azul (Aatrox)
    files = MyGamesFiles(keyed_svc)
    assert files.sync() == 3
    base = keyed_svc.settings.data_dir / "mis_partidas"
    jungle = sorted(p.name for p in (base / "JUNGLE" / "Lee_Sin").iterdir())
    assert jungle == ["2026-11-18_EUW1_100_V.json.gz", "2026-11-19_EUW1_101_D.json.gz"]
    game = read(base / "JUNGLE" / "Lee_Sin" / jungle[0])
    assert game["role"] == "JUNGLE" and game["win"] and game["analysis"]["metrics"] and game["timeline"]["info"]["frames"]
    assert game["match"]["metadata"]["matchId"] == "EUW1_100" and game["queue_label"]
    summary = json.loads((base / "resumen.json").read_text(encoding="utf-8"))
    assert summary["games"] == 3 and summary["by_role"]["JUNGLE"]["games"] == 2 and summary["by_role"]["TOP"]["wins"] == 1
    assert summary["by_role"]["JUNGLE"]["metrics_mean"]["cs_min"] > 0
    assert summary["index"][0]["match_id"] == "EUW1_102" and summary["index"][0]["file"].startswith("TOP/")
    assert (base / "LEEME.md").exists()
    assert files.sync() == 0  # sin cambios no se reescribe nada


def test_reanalysed_game_is_rewritten_and_moved_if_its_role_changes(keyed_svc):
    _save(keyed_svc, "EUW1_200", pid=2)
    files = MyGamesFiles(keyed_svc)
    files.sync()
    base = keyed_svc.settings.data_dir / "mis_partidas"
    old = next(base.rglob("*EUW1_200*.json.gz"))
    analysis = read(old)["analysis"]
    analysis["role"] = None  # al reanalizarla, su teamPosition no era fiable
    store.update_analysis(keyed_svc.db, "EUW1_200", analysis, {"m": analysis["metrics"]}, ANALYSIS_VERSION + 1)
    assert files.sync() == 1
    (new,) = list(base.rglob("*EUW1_200*.json.gz"))
    assert new.parent.parent.name == "SIN_ROL" and not old.exists()
    assert read(new)["analysis_version"] == ANALYSIS_VERSION + 1


def test_folder_comes_back_if_deleted(keyed_svc):
    import shutil

    _save(keyed_svc, "EUW1_300")
    files = MyGamesFiles(keyed_svc)
    files.sync()
    shutil.rmtree(keyed_svc.settings.data_dir / "mis_partidas")
    assert files.sync() == 1 and list((keyed_svc.settings.data_dir / "mis_partidas").rglob("*EUW1_300*"))
