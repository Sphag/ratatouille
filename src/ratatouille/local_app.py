"""Opt-in loopback-only recipe development server; public ASGI app stays closed."""

import argparse
import json
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from ratatouille.app import create_app
from ratatouille.catalog import Catalog, StarterLibrary, load_starters
from ratatouille.database import database_path, make_engine, migrate
from ratatouille.recipe_api import install_recipe_api
from ratatouille.storage import Store


def local_app(store: Store, owner: str, library: StarterLibrary | None = None) -> FastAPI:
    store.get_goals(owner)  # Validate the configured owner, never trust a client user_id.
    application = create_app(recipes_enabled=True)
    install_recipe_api(application, Catalog(store), owner, library or load_starters())
    return application


def initialize(path: Path) -> str:
    migrate(path)
    marker = path.with_suffix(".local-owner.json")
    engine = make_engine(path)
    try:
        store = Store(engine)
        if marker.exists():
            owner = str(json.loads(marker.read_text(encoding="utf-8"))["owner_id"])
            store.get_goals(owner)
        else:
            owner = store.create_user()
            marker.write_text(json.dumps({"owner_id": owner}) + "\n", encoding="utf-8")
        return owner
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Локальная библиотека рецептов Ratatouille")
    parser.add_argument("--init", action="store_true", help="Создать БД и локальный профиль")
    args = parser.parse_args()
    path = database_path()
    marker = path.with_suffix(".local-owner.json")
    if args.init:
        initialize(path)
        print("База и локальный профиль готовы. Запустите команду без --init.")
        return
    if not path.is_file() or not marker.is_file():
        parser.error("Сначала выполните эту команду с --init.")
    owner = str(json.loads(marker.read_text(encoding="utf-8"))["owner_id"])
    engine = make_engine(path)
    try:
        uvicorn.run(
            local_app(Store(engine), owner), host="127.0.0.1", port=8000, proxy_headers=False
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
