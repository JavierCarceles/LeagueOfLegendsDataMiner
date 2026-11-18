"""Comprueba que el sandbox de conftest.py funciona (sin red, sin ficheros reales)."""

from __future__ import annotations

import socket

import pytest
import requests

from app.data.db import Database
from app.riot.keys import KeyStore
from tests.conftest import REAL_DB, REAL_ENV, NetworkDisabled


def test_network_is_blocked():
    # 127.0.0.1: aunque el bloqueo fallara, nada saldría de la máquina.
    with pytest.raises(NetworkDisabled):
        requests.get("http://127.0.0.1:9/", timeout=1)
    with pytest.raises(NetworkDisabled):
        socket.create_connection(("127.0.0.1", 9), timeout=1)


def test_real_project_db_and_env_cannot_be_opened():
    with pytest.raises(RuntimeError, match="lol.db"):
        Database(REAL_DB)
    with pytest.raises(RuntimeError, match=".env"):
        KeyStore(REAL_ENV)
