"""Tema de la página entera (web/js/theme.js): se guarda en la app y /api/meta lo devuelve."""

from fastapi.testclient import TestClient

from app.main import create_app


def _client(svc):
    app = create_app(svc.settings, run_startup=False)
    app.state.svc = svc
    return TestClient(app)


def test_theme_is_standard_by_default_and_is_saved(svc):
    with _client(svc) as client:
        assert client.get("/api/meta").json()["ui"] == {"theme": "standard"}
        assert client.post("/api/ui/theme", json={"theme": "hextech"}).json() == {"theme": "hextech"}
        assert client.get("/api/meta").json()["ui"] == {"theme": "hextech"}
        assert client.post("/api/ui/theme", json={"theme": "rosa"}).status_code == 400  # desconocido: no se guarda
        assert client.get("/api/meta").json()["ui"] == {"theme": "hextech"}
