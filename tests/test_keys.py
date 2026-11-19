"""app/riot/keys.py sobre un .env temporal (nunca el real)."""

from __future__ import annotations

import json
import os
import threading
import time

import pytest

from app.riot.keys import KeyStore
from tests.factories import TEST_KEY


@pytest.fixture(autouse=True)
def _no_env_key(monkeypatch):
    # KeyStore.set() exporta la key a os.environ: monkeypatch lo deja como estaba.
    monkeypatch.delenv("RIOT_API_KEY", raising=False)


def test_missing_env_file(tmp_path):
    keys = KeyStore(tmp_path / ".env")
    assert keys.get() == ""
    assert keys.status == "missing"
    assert keys.public()["configured"] is False


def test_reads_key_from_env_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"OTHER=1\nRIOT_API_KEY={TEST_KEY}\n", encoding="utf-8")
    keys = KeyStore(env)
    assert keys.get() == TEST_KEY
    assert keys.status == "unknown"
    public = keys.public()
    assert public["configured"] is True
    assert public["masked"].endswith(TEST_KEY[-4:])
    assert TEST_KEY not in str(public)


def test_quoted_key_is_unquoted(tmp_path):
    env = tmp_path / ".env"
    env.write_text(f'RIOT_API_KEY="{TEST_KEY}"\n', encoding="utf-8")
    assert KeyStore(env).get() == TEST_KEY


def test_set_rejects_malformed_keys(tmp_path):
    env = tmp_path / ".env"
    keys = KeyStore(env)
    for bad in ("", "abc", "RGAPI-123", TEST_KEY + "0"):
        with pytest.raises(ValueError):
            keys.set(bad)
    assert not env.exists()


def test_set_writes_the_given_env_file(tmp_path):
    env = tmp_path / ".env"
    keys = KeyStore(env)
    version = keys.version
    keys.set(f"  {TEST_KEY} ")
    assert f"RIOT_API_KEY={TEST_KEY}" in env.read_text(encoding="utf-8")
    assert keys.get() == TEST_KEY
    assert keys.status == "unknown"
    assert keys.version == version + 1
    assert KeyStore(env).get() == TEST_KEY


def test_mark_invalid_then_valid(tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"RIOT_API_KEY={TEST_KEY}\n", encoding="utf-8")
    keys = KeyStore(env)
    keys.mark_invalid("403 Forbidden")
    assert keys.status == "invalid" and keys.detail == "403 Forbidden"
    keys.mark_valid()
    assert keys.status == "valid" and keys.detail == ""


def test_wait_for_change_returns_false_when_stopped(tmp_path):
    keys = KeyStore(tmp_path / ".env")
    stop = threading.Event()
    stop.set()
    assert keys.wait_for_change(keys.version, stop) is False


def test_wait_for_change_picks_up_a_new_key_in_the_file(tmp_path):
    env = tmp_path / ".env"
    keys = KeyStore(env)
    version = keys.version
    stop = threading.Event()

    def later():
        time.sleep(0.1)
        env.write_text(f"RIOT_API_KEY={TEST_KEY}\n", encoding="utf-8")

    writer = threading.Thread(target=later)
    writer.start()
    try:
        assert keys.wait_for_change(version, stop, poll=0.02) is True
    finally:
        stop.set()
        writer.join()
    assert keys.get() == TEST_KEY


# ------------------------------------------------- edad de la key entre reinicios
OTHER_KEY = "RGAPI-11111111-1111-1111-1111-111111111111"


def test_key_age_survives_an_app_restart(tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"RIOT_API_KEY={TEST_KEY}\n", encoding="utf-8")
    state = tmp_path / "data" / "riot_key_state.json"
    KeyStore(env, state_path=state)
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert TEST_KEY not in state.read_text(encoding="utf-8")  # solo una huella, nunca la key
    saved["first_seen"] = time.time() - 20 * 3600  # la app se abrió por primera vez hace 20 h
    state.write_text(json.dumps(saved), encoding="utf-8")
    age = KeyStore(env, state_path=state).public()["age_hours"]
    assert 19.9 < age < 20.1  # antes volvía a 0 al reiniciar («quedan ~24 h» falso)


def test_a_new_key_starts_counting_again(tmp_path, monkeypatch):
    monkeypatch.delenv("RIOT_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text(f"RIOT_API_KEY={TEST_KEY}\n", encoding="utf-8")
    state = tmp_path / "riot_key_state.json"
    KeyStore(env, state_path=state)
    state.write_text(json.dumps({**json.loads(state.read_text(encoding="utf-8")), "first_seen": time.time() - 30 * 3600}), encoding="utf-8")
    keys = KeyStore(env, state_path=state)
    keys.set(OTHER_KEY)
    assert keys.public()["age_hours"] < 0.01


def test_an_unseen_key_from_the_env_file_counts_from_the_file_date(tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"RIOT_API_KEY={TEST_KEY}\n", encoding="utf-8")
    three_hours_ago = time.time() - 3 * 3600
    os.utime(env, (three_hours_ago, three_hours_ago))
    age = KeyStore(env, state_path=tmp_path / "riot_key_state.json").public()["age_hours"]
    assert 2.9 < age < 3.1


def test_a_corrupt_state_file_is_ignored(tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"RIOT_API_KEY={TEST_KEY}\n", encoding="utf-8")
    state = tmp_path / "riot_key_state.json"
    state.write_text("{no es json", encoding="utf-8")
    keys = KeyStore(env, state_path=state)
    assert keys.get() == TEST_KEY and keys.public()["age_hours"] is not None


# ------------------------------------------------- comprobación al arrancar
def test_startup_checks_an_unvalidated_key_once(tmp_path, monkeypatch):
    from tests.factories import build_services

    svc = build_services(tmp_path, monkeypatch, key=TEST_KEY)
    calls = []

    def fake_check():
        calls.append(1)
        svc.keys.mark_valid()
        return {"valid": True, "status": 200, "reason": "ok"}

    monkeypatch.setattr(svc.riot, "check_key", fake_check)
    svc.check_key_on_startup()
    assert calls == [1] and svc.keys.status == "valid"
    svc.check_key_on_startup()  # ya validada: no vuelve a gastar una petición
    assert calls == [1]


def test_startup_rechecks_a_fresh_key_that_riot_still_rejects(tmp_path, monkeypatch):
    from tests.factories import build_services

    svc = build_services(tmp_path, monkeypatch, key=TEST_KEY)
    monkeypatch.setattr(svc.riot, "check_key", lambda: {"valid": False, "status": 403, "reason": "403 Forbidden"})
    rechecks = []
    monkeypatch.setattr(svc, "schedule_key_recheck", lambda: rechecks.append(1))
    svc.check_key_on_startup()
    assert rechecks == [1]


def test_startup_without_key_makes_no_request(tmp_path, monkeypatch):
    from tests.factories import build_services

    svc = build_services(tmp_path, monkeypatch, key=None)
    monkeypatch.setattr(svc.riot, "check_key", lambda: pytest.fail("no debería llamar a Riot sin key"))
    svc.check_key_on_startup()
