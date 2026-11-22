"""Repeticiones (.rofl) de este ordenador: búsqueda de la carpeta, recordarla por sistema y leer su cabecera."""

from __future__ import annotations

import json
import struct

from app import replays_local as rl
from tests.test_crawler import keyed_svc  # noqa: F401  (fixture reutilizada)

META = {"gameLength": 1_834_000, "gameVersion": "16.22.123.4567", "statsJson": json.dumps([{"SKIN": "Sylas", "WIN": "Win"}] * 10)}


def rofl_v1(path, meta=META, payload=b"\x00" * 64):
    body = json.dumps(meta).encode()
    head = b"RIOT\x00\x00" + b"\x11" * 256
    meta_off = len(head) + struct.calcsize("<HIIIIIII")
    header = struct.pack("<HIIIIIII", 288, meta_off + len(body) + len(payload), meta_off, len(body), 0, 0, 0, 0)
    path.write_bytes(head + header + body + payload)
    return path


def rofl_tail(path, meta=META):
    path.write_bytes(b"RIOT2\x00" + b"\x22" * 2000 + json.dumps(meta).encode() + struct.pack("<I", len(json.dumps(meta))))
    return path


def test_match_id_from_file_name(tmp_path):
    assert rl.match_id_of(tmp_path / "EUW1-7412345678.rofl") == "EUW1_7412345678"
    assert rl.match_id_of(tmp_path / "kr-123.rofl") == "KR_123"
    assert rl.match_id_of(tmp_path / "mi repeticion.rofl") is None


def test_header_is_read_in_both_formats(tmp_path):
    for writer in (rofl_v1, rofl_tail):
        head = rl.parse_rofl(writer(tmp_path / f"EUW1-1-{writer.__name__}.rofl"))
        assert head["game_length"] == 1_834_000 and head["game_version"].startswith("16.22") and len(head["players"]) == 10
    (tmp_path / "x.rofl").write_bytes(b"no es una repeticion")
    assert rl.parse_rofl(tmp_path / "x.rofl") is None


def test_full_scan_finds_the_replays_folder_and_skips_system_dirs(tmp_path):
    (tmp_path / "Windows" / "Replays").mkdir(parents=True)
    rofl_v1(tmp_path / "Windows" / "Replays" / "EUW1-1.rofl")  # carpeta de sistema: no se mira
    (tmp_path / "Juegos" / "otros").mkdir(parents=True)
    rofl_v1(tmp_path / "Juegos" / "otros" / "EUW1-2.rofl")
    (tmp_path / "D" / "LoL" / "Replays").mkdir(parents=True)
    rofl_v1(tmp_path / "D" / "LoL" / "Replays" / "EUW1-3.rofl")
    assert rl.full_scan([tmp_path]) == tmp_path / "D" / "LoL" / "Replays"  # gana la que se llama Replays
    assert rl.full_scan([tmp_path / "Juegos"]) == tmp_path / "Juegos" / "otros"
    assert rl.full_scan([tmp_path / "vacio"]) is None


def _patch(monkeypatch, *, client=None, defaults=(), roots=()):
    monkeypatch.setattr(rl, "client_dir", lambda: client)
    monkeypatch.setattr(rl, "default_dirs", lambda: list(defaults))
    monkeypatch.setattr(rl, "scan_roots", lambda: list(roots))


def test_folder_is_found_once_and_remembered_per_os(keyed_svc, tmp_path, monkeypatch):
    replays = tmp_path / "disco" / "Partidas" / "Replays"
    replays.mkdir(parents=True)
    rofl_v1(replays / "EUW1-7412345678.rofl")
    _patch(monkeypatch, roots=[tmp_path / "disco"])
    local = rl.LocalReplays(keyed_svc)
    assert local.locate() == replays and local.state["found_by"] == "scan"
    saved = json.loads(keyed_svc.db.meta_get(f"replays_dir:{rl.os_key()}"))
    assert saved["path"] == str(replays) and saved["found_by"] == "scan"
    # Siguiente arranque: no vuelve a buscar por el disco.
    monkeypatch.setattr(rl, "full_scan", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no debería buscar")))
    assert rl.LocalReplays(keyed_svc).locate() == replays


def test_client_folder_wins_and_updates_the_saved_one(keyed_svc, tmp_path, monkeypatch):
    old, new = tmp_path / "vieja", tmp_path / "nueva"
    old.mkdir()
    new.mkdir()
    keyed_svc.db.meta_set(f"replays_dir:{rl.os_key()}", json.dumps({"path": str(old), "found_by": "default"}))
    _patch(monkeypatch, client=new)
    assert rl.LocalReplays(keyed_svc).locate() == new
    assert json.loads(keyed_svc.db.meta_get(f"replays_dir:{rl.os_key()}"))["found_by"] == "client"


def test_empty_default_folder_is_used_when_there_are_no_replays_anywhere(keyed_svc, tmp_path, monkeypatch):
    default = tmp_path / "Documents" / "League of Legends" / "Replays"
    default.mkdir(parents=True)
    _patch(monkeypatch, defaults=[default], roots=[tmp_path / "nada"])
    local = rl.LocalReplays(keyed_svc)
    assert local.locate() == default
    saved = json.loads(keyed_svc.db.meta_get(f"replays_dir:{rl.os_key()}"))
    assert saved["path"] == str(default) and saved["not_found_at"]


def test_index_adds_updates_and_removes(keyed_svc, tmp_path):
    folder = tmp_path / "Replays"
    folder.mkdir()
    rofl_v1(folder / "EUW1-1.rofl")
    rofl_tail(folder / "EUW1-2.rofl")
    local = rl.LocalReplays(keyed_svc)
    assert local.index(folder) == 2
    assert len(local.players("EUW1_1")) == 10
    (folder / "EUW1-1.rofl").unlink()
    rofl_v1(folder / "EUW1-3.rofl")
    assert local.index(folder) == 2
    assert {r["match_id"] for r in local.summary()["recent"]} == {"EUW1_2", "EUW1_3"}


def test_api_reports_and_accepts_a_manual_folder(keyed_svc, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    folder = tmp_path / "MisRepes"
    folder.mkdir()
    rofl_v1(folder / "EUW1-9.rofl")
    _patch(monkeypatch)
    monkeypatch.setattr(rl.LocalReplays, "start", lambda self: None)  # sin hilo en la prueba
    app = create_app(keyed_svc.settings, run_startup=False)
    app.state.svc = keyed_svc
    with TestClient(app) as client:
        assert client.post("/api/replay/local/rescan", json={"path": str(tmp_path / "no-existe")}).status_code == 400
        assert client.post("/api/replay/local/rescan", json={"path": str(folder)}).status_code == 200
        keyed_svc.local_replays.index(keyed_svc.local_replays.locate())
        data = client.get("/api/replay/local").json()
    assert data["path"] == str(folder) and data["found_by"] == "manual" and data["files"] == 1
    assert data["recent"][0]["match_id"] == "EUW1_9"
