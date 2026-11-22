"""Fixtures comunes y "sandbox" de la suite.

Garantías para toda la sesión:

* **Sin red**: cualquier conexión (Riot, Data Dragon, OP.GG…) lanza ``NetworkDisabled``.
* **Sin RIOT_API_KEY del entorno**: ``KeyStore`` la usaría como respaldo.
* **Nunca el .env ni la BD reales**: abrir ``.env`` o ``data/lol.db`` del proyecto
  falla en el acto, y ``app.services.ENV_PATH`` apunta a un fichero temporal
  aunque un test se olvide de parchearlo.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.services
from app.data.db import Database
from app.riot.keys import KeyStore
from tests.factories import TEST_KEY, build_services, offline_ddragon

REAL_ENV = (ROOT / ".env").resolve()
REAL_DB = (ROOT / "data" / "lol.db").resolve()


class NetworkDisabled(RuntimeError):
    """Los tests no deben salir a la red."""


def _no_network(*args, **kwargs):
    raise NetworkDisabled(f"Acceso a red bloqueado en los tests: {args[1:3]!r}")


def _stat(path: Path):
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return st.st_mtime_ns, st.st_size


@pytest.fixture(scope="session", autouse=True)
def _sandbox(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setattr(socket.socket, "connect", _no_network)
    mp.setattr(socket.socket, "connect_ex", _no_network)
    mp.setattr(socket, "create_connection", _no_network)
    mp.setattr(socket, "getaddrinfo", _no_network)
    mp.delenv("RIOT_API_KEY", raising=False)
    mp.setattr(app.services, "ENV_PATH", tmp_path_factory.mktemp("env") / ".env")

    original_db_init = Database.__init__
    original_keys_init = KeyStore.__init__

    def guarded_db_init(self, path, *args, **kwargs):
        if Path(path).resolve() == REAL_DB:
            raise RuntimeError("Un test ha intentado abrir data/lol.db real")
        original_db_init(self, path, *args, **kwargs)

    def guarded_keys_init(self, env_path, *args, **kwargs):
        if Path(env_path).resolve() == REAL_ENV:
            raise RuntimeError("Un test ha intentado usar el .env real")
        original_keys_init(self, env_path, *args, **kwargs)

    mp.setattr(Database, "__init__", guarded_db_init)
    mp.setattr(KeyStore, "__init__", guarded_keys_init)

    env_before = _stat(REAL_ENV)
    yield
    mp.undo()
    assert _stat(REAL_ENV) == env_before, "Un test ha modificado el .env real del proyecto"


@pytest.fixture(scope="session")
def dd(tmp_path_factory):
    """Data Dragon sintético cargado sin red (solo lectura: se comparte)."""
    return offline_ddragon(tmp_path_factory.mktemp("ddragon"))


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "t.db")
    yield database
    database.close()


@pytest.fixture
def svc(tmp_path, monkeypatch):
    """``Services`` aislado y vacío (sin key de Riot)."""
    services = build_services(tmp_path, monkeypatch)
    yield services
    services.shutdown()
    services.db.close()


@pytest.fixture
def keyed_svc(tmp_path, monkeypatch):
    """``Services`` aislado con una API key de pruebas (recolector, seguimiento…); para los hilos del recolector al acabar."""
    services = build_services(tmp_path, monkeypatch, key=TEST_KEY)
    yield services
    services.shutdown()
    for worker in services.crawler.workers.values():
        worker.join(timeout=5)
    services.db.close()
