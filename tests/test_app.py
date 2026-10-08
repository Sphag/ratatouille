"""Web process contracts before the meal-planning features are implemented."""

from pathlib import Path

from fastapi.testclient import TestClient

from ratatouille.app import create_app


def test_health_does_not_need_a_frontend_build_or_bot_token(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "missing")) as client:
        response = client.get("/api/health")
        assert client.get("/api/capabilities").json() == {"recipes": False}
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_missing_frontend_returns_unavailable_instead_of_a_server_error(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path)) as client:
        response = client.get("/")
    assert response.status_code == 503
    assert "tmp" not in response.text


def test_frontend_build_and_assets_are_served(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<h1>Ratatouille</h1>", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('ready');", encoding="utf-8")
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/").text == "<h1>Ratatouille</h1>"
        assert client.get("/assets/app.js").text == "console.log('ready');"
        assert client.get("/api/health").json() == {"status": "ok"}


def test_unknown_api_paths_and_files_outside_assets_are_not_served(tmp_path: Path) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "private.txt").write_text("private fixture", encoding="utf-8")
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/api/menu").status_code == 404
        assert client.get("/assets/%2e%2e/private.txt").status_code == 404
        assert client.get("/private.txt").status_code == 404
