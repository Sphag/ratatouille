"""Recipe HTTP adapter, installed only in the explicit local development process."""

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from ratatouille.catalog import Catalog, Contract, Flags, RecipeCard, SaveRecipe, StarterLibrary
from ratatouille.domain import ConflictError, NotFoundError, ValidationError

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
LOCAL_ORIGINS = {
    f"http://{host}:{port}" for host in ("127.0.0.1", "localhost") for port in (8000, 5173)
}


def local_request(request: Request) -> None:
    if (
        request.client is None
        or request.client.host not in {"127.0.0.1", "::1"}
        or request.url.hostname not in LOCAL_HOSTS
        or (
            request.headers.get("origin") is not None
            and request.headers["origin"] not in LOCAL_ORIGINS
        )
    ):
        raise HTTPException(403, "Библиотека доступна только в локальном режиме.")
    if (
        request.method not in {"GET", "HEAD"}
        and request.headers.get("x-ratatouille-request") != "1"
    ):
        raise HTTPException(403, "Некорректный запрос приложения.")


class ImportRequest(Contract):
    digest: str
    confirmed: bool


class StarterPreview(Contract):
    library: StarterLibrary
    digest: str
    imported: bool


def install_recipe_api(
    application: FastAPI, catalog: Catalog, owner: str, library: StarterLibrary
) -> None:
    router = APIRouter(prefix="/api", dependencies=[Depends(local_request)])

    @application.middleware("http")
    async def private_responses(request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(ValidationError)
    @application.exception_handler(NotFoundError)
    @application.exception_handler(ConflictError)
    def domain_error(request: Request, error: Exception) -> JSONResponse:
        status = (
            404
            if isinstance(error, NotFoundError)
            else (409 if isinstance(error, ConflictError) else 422)
        )
        return JSONResponse(status_code=status, content={"detail": str(error)})

    @application.exception_handler(RequestValidationError)
    def bad_input(request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": "Проверьте название, КБЖУ, выход рецепта и количества ингредиентов."
            },
        )

    @router.get("/recipes")
    def recipes(archived: bool = False) -> list[RecipeCard]:
        return catalog.list_cards(owner, archived=archived)

    @router.get("/ingredients")
    def ingredients() -> list[dict[str, str]]:
        return catalog.ingredients(owner)

    @router.post("/recipes", status_code=201)
    def create(data: SaveRecipe) -> RecipeCard:
        return catalog.save(owner, data)

    @router.put("/recipes/{recipe_id}")
    def edit(recipe_id: str, data: SaveRecipe) -> RecipeCard:
        return catalog.save(owner, data, recipe_id)

    @router.patch("/recipes/{recipe_id}/flags")
    def flags(recipe_id: str, data: Flags) -> RecipeCard:
        return catalog.flags(owner, recipe_id, data)

    @router.get("/starter-library")
    def starters() -> StarterPreview:
        return StarterPreview(
            library=library, digest=library.digest, imported=catalog.imported(owner, library)
        )

    @router.post("/starter-library/import")
    def import_library(data: ImportRequest) -> dict[str, int]:
        if data.digest != library.digest:
            raise ConflictError("Стартовые карточки изменились. Обновите список перед импортом.")
        return {"imported": catalog.import_starters(owner, library, confirmed=data.confirmed)}

    application.include_router(router)
