"""ASGI entry point and the initial build-serving boundary."""

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def create_app(
    frontend_dir: Path | None = None, *, recipes_enabled: bool = False, menus_enabled: bool = False
) -> FastAPI:
    """Create the web process without bot credentials or a database connection."""
    directory = frontend_dir or Path(os.getenv("RATATOUILLE_FRONTEND_DIR", "frontend/dist"))
    application = FastAPI(title="Ratatouille", version="0.1.0")

    @application.get("/api/health")
    def health() -> dict[str, str]:
        """Report process liveness; this does not claim business-feature readiness."""
        return {"status": "ok"}

    @application.get("/api/capabilities")
    def capabilities() -> dict[str, bool]:
        return {"recipes": recipes_enabled, "menus": menus_enabled}

    @application.get("/", include_in_schema=False)
    def index() -> FileResponse:
        page = directory / "index.html"
        if not page.is_file():
            raise HTTPException(status_code=503, detail="Приложение пока недоступно.")
        return FileResponse(page)

    assets = directory / "assets"
    if assets.is_dir():
        application.mount("/assets", StaticFiles(directory=assets), name="assets")
    return application


app = create_app()
