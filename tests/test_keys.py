"""app/riot/keys.py sobre un .env temporal (nunca el real)."""

from __future__ import annotations

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
