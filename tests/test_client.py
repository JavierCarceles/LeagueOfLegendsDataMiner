"""Validación de la API key: distinguir rechazo de Riot, key válida y fallo de red."""

from __future__ import annotations

import requests

import app.riot.client as client_mod
from app.riot.client import RiotClient
from app.riot.keys import KeyStore
from app.riot.ratelimit import RateLimiter
from tests.factories import TEST_KEY


class _Response:
    def __init__(self, status: int, body: dict | None = None) -> None:
        self.status_code = status
        self._body = body or {}
        self.headers = {}
        self.text = str(self._body)

    def json(self):
        return self._body


class _Session:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.response


def _client(tmp_path, session, monkeypatch) -> tuple[RiotClient, KeyStore]:
    monkeypatch.setattr(client_mod.RiotClient, "_sleep", staticmethod(lambda seconds, stop: None))
    # KeyStore.set() también exporta RIOT_API_KEY al entorno: así se restaura al acabar.
    monkeypatch.setenv("RIOT_API_KEY", "")
    keys = KeyStore(tmp_path / ".env")
    keys.set(TEST_KEY)
    client = RiotClient(keys, RateLimiter())
    client._local.session = session
    return client, keys


def test_check_key_accepted(tmp_path, monkeypatch):
    client, keys = _client(tmp_path, _Session(_Response(200, {"id": "EUW1"})), monkeypatch)
    result = client.check_key()
    assert result["valid"] is True
    assert keys.status == "valid"


def test_check_key_unknown_apikey_is_a_rejection_with_riot_message(tmp_path, monkeypatch):
    body = {"status": {"message": "Unknown apikey", "status_code": 401}}
    client, keys = _client(tmp_path, _Session(_Response(401, body)), monkeypatch)
    result = client.check_key()
    assert result["valid"] is False
    assert result["status"] == 401
    assert "Unknown apikey" in result["reason"]
    assert keys.status == "invalid"
    assert "Unknown apikey" in keys.detail


def test_check_key_forbidden_is_a_rejection(tmp_path, monkeypatch):
    body = {"status": {"message": "Forbidden", "status_code": 403}}
    client, keys = _client(tmp_path, _Session(_Response(403, body)), monkeypatch)
    result = client.check_key()
    assert result["valid"] is False
    assert result["status"] == 403
    assert keys.status == "invalid"


def test_network_error_is_not_reported_as_rejection(tmp_path, monkeypatch):
    client, keys = _client(tmp_path, _Session(error=requests.ConnectionError("sin red")), monkeypatch)
    result = client.check_key()
    assert result["valid"] is None
    assert keys.status == "unknown"
    assert client.validate_key() is False  # sin confirmar no se da por buena


def test_server_error_is_not_reported_as_rejection(tmp_path, monkeypatch):
    client, keys = _client(tmp_path, _Session(_Response(503)), monkeypatch)
    result = client.check_key()
    assert result["valid"] is None
    assert result["status"] == 503
    assert keys.status == "unknown"


def test_public_exposes_recheck_state(tmp_path, monkeypatch):
    monkeypatch.setenv("RIOT_API_KEY", "")
    keys = KeyStore(tmp_path / ".env")
    keys.set(TEST_KEY)
    data = keys.public()
    assert data["rechecking"] is False
    assert data["next_check_in"] is None
