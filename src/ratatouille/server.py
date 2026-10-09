"""Explicit production factory; migration and credentials are never loaded at import."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException

from ratatouille.app import create_app
from ratatouille.catalog import Catalog, load_starters
from ratatouille.database import database_path, make_engine
from ratatouille.menu_api import install_menu_api
from ratatouille.menus import Menus
from ratatouille.recipe_api import install_recipe_api
from ratatouille.settings import telegram_settings
from ratatouille.storage import Store
from ratatouille.telegram_auth import TelegramAccess


def telegram_app(store: Store, token: str, app_url: str, allowed_ids: frozenset[int]) -> FastAPI:
    parsed = urlsplit(app_url)
    hostname = parsed.hostname or ""
    host = f"[{hostname}]" if ":" in hostname else hostname
    port = parsed.port
    suffix = f":{port}" if port and port != {"https": 443, "http": 80}.get(parsed.scheme) else ""
    origin = f"{parsed.scheme}://{host}{suffix}"
    access = TelegramAccess(store, token, origin, allowed_ids)
    application = create_app(recipes_enabled=True, menus_enabled=True, telegram_enabled=True)
    install_recipe_api(application, Catalog(store), access.owner, load_starters(), access.guard)
    install_menu_api(application, Menus(store), access.owner, access.guard)

    @application.get("/api/ready")
    def ready() -> dict[str, str]:
        try:
            with store.engine.connect() as connection:
                if (
                    connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
                    != "0004"
                ):
                    raise ValueError
                if connection.exec_driver_sql("PRAGMA quick_check").scalar() != "ok":
                    raise ValueError
            if not application.state.frontend_ready:
                raise ValueError
        except Exception:
            raise HTTPException(503, "Сервис ещё не готов.") from None
        return {"status": "ready"}

    application.state.frontend_ready = (
        Path(os.getenv("RATATOUILLE_FRONTEND_DIR", "frontend/dist")) / "index.html"
    ).is_file()
    return application


def production_app() -> FastAPI:
    settings = telegram_settings()
    path = database_path()
    if not path.is_file():
        raise ValueError(
            "Сначала явно создайте и мигрируйте БД командой python -m ratatouille.database."
        )
    engine = make_engine(path)
    application = telegram_app(
        Store(engine), settings.token, settings.app_url, frozenset({settings.owner_id})
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            engine.dispose()

    application.router.lifespan_context = lifespan
    return application
