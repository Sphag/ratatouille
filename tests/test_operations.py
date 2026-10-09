"""Release readiness and consistent backup protect existing personal data."""

from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ratatouille.backup import backup
from ratatouille.database import make_engine
from ratatouille.domain import Nutrition
from ratatouille.server import telegram_app
from ratatouille.settings import telegram_settings
from ratatouille.storage import Store


def test_online_backup_restores_goals_and_does_not_overwrite(store: Store, tmp_path: Path) -> None:
    owner = store.create_user(telegram_id=101)
    store.save_goals(owner, Nutrition(calories=Decimal("2000")))
    source = Path(str(store.engine.url.database))
    target = tmp_path / "backup.sqlite3"
    backup(source, target)
    store.save_goals(owner, Nutrition(calories=Decimal("2200")))
    engine = make_engine(target)
    try:
        assert Store(engine).get_goals(owner).calories == 2000
    finally:
        engine.dispose()
    assert store.get_goals(owner).calories == 2200
    with pytest.raises(ValueError):
        backup(source, target)
    with pytest.raises(ValueError):
        backup(source, source)
    with pytest.raises(ValueError):
        backup(tmp_path / "missing.sqlite3", tmp_path / "another.sqlite3")


def test_readiness_requires_schema_and_frontend_without_exposing_credentials(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATATOUILLE_FRONTEND_DIR", str(tmp_path))
    origin = "https://app.example.invalid"
    with TestClient(
        telegram_app(store, "test-token", origin, frozenset({101})), base_url=origin
    ) as client:
        assert client.get("/api/ready").status_code == 503
    (tmp_path / "index.html").write_text("<h1>Test</h1>")
    with TestClient(
        telegram_app(store, "test-token", origin, frozenset({101})), base_url=origin
    ) as client:
        assert client.get("/api/ready").json() == {"status": "ready"}
        with store.engine.begin() as connection:
            connection.exec_driver_sql("UPDATE alembic_version SET version_num='0000'")
        response = client.get("/api/ready")
        assert response.status_code == 503 and "test-token" not in response.text


def test_file_secret_is_exclusive_bounded_and_never_printed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.setenv("RATATOUILLE_APP_URL", "https://app.example.invalid")
    monkeypatch.setenv("RATATOUILLE_OWNER_TELEGRAM_ID", "101")
    path = tmp_path / "bot-token"
    path.write_text("test-only-token\n")
    monkeypatch.setenv("BOT_TOKEN_FILE", str(path))
    assert telegram_settings().token == "test-only-token"
    assert "test-only-token" not in repr(telegram_settings())
    monkeypatch.setenv("BOT_TOKEN", "another")
    with pytest.raises(ValueError):
        telegram_settings()
    monkeypatch.delenv("BOT_TOKEN")
    path.write_text("x" * 1025)
    with pytest.raises(ValueError):
        telegram_settings()
    path.unlink()
    with pytest.raises(ValueError):
        telegram_settings()
