"""HTTP validation, ownership and explicit loopback-only access to recipe forms."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_catalog import card_data

from ratatouille.app import create_app
from ratatouille.catalog import Catalog, load_starters
from ratatouille.local_app import local_app
from ratatouille.storage import Store

HEADERS = {"X-Ratatouille-Request": "1"}


@pytest.fixture
def client(store: Store) -> Iterator[TestClient]:
    owner = store.create_user()
    application = local_app(store, owner, load_starters().model_copy(update={"approved": True}))
    application.state.owner = owner
    with TestClient(
        application, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)
    ) as c:
        yield c


def test_public_app_has_no_personal_data_routes() -> None:
    with TestClient(create_app()) as client:
        for path in ("/api/recipes", "/api/ingredients", "/api/starter-library"):
            assert client.get(path).status_code == 404


def test_create_edit_admit_archive_restore_and_exact_numbers(client: TestClient) -> None:
    payload = card_data().model_dump(mode="json")
    payload["ingredients"][0]["quantity"] = "0.00000000000000000001"
    created = client.post("/api/recipes", headers=HEADERS, json=payload)
    assert created.status_code == 201
    card = created.json()
    assert card["ingredients"][0]["quantity"] == "0.00000000000000000001"
    assert not card["eligible"]
    payload.update(
        name="Новое имя",
        expected_version_id=card["version_id"],
        expected_eligible=card["eligible"],
        expected_archived=card["archived"],
        eligible=True,
    )
    updated = client.put(f"/api/recipes/{card['recipe_id']}", headers=HEADERS, json=payload)
    assert updated.status_code == 200
    new_card = updated.json()
    assert new_card["eligible"]
    assert (
        client.put(f"/api/recipes/{card['recipe_id']}", headers=HEADERS, json=payload).status_code
        == 409
    )
    for archived in (True, False):
        response = client.patch(
            f"/api/recipes/{card['recipe_id']}/flags",
            headers=HEADERS,
            json=dict(
                expected_version_id=new_card["version_id"],
                expected_eligible=True,
                expected_archived=not archived,
                eligible=True,
                archived=archived,
            ),
        )
        assert response.status_code == 200
        assert len(client.get(f"/api/recipes?archived={str(archived).lower()}").json()) == 1
    assert client.get("/api/recipes").headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", " "),
        ("yield_portions", True),
        ("yield_portions", 0),
        ("eligible", "true"),
        ("owner_id", "stranger"),
    ],
)
def test_invalid_top_level_fields_do_not_write(
    client: TestClient, field: str, value: object
) -> None:
    payload = card_data().model_dump(mode="json") | {field: value}
    response = client.post("/api/recipes", headers=HEADERS, json=payload)
    assert response.status_code == 422
    assert client.get("/api/recipes").json() == []


@pytest.mark.parametrize("value", [1.5, True, "NaN", "Infinity", "-1", "1e99999999", ""])
def test_decimal_fields_reject_coercion_and_nonfinite_values(
    client: TestClient, value: object
) -> None:
    payload = card_data().model_dump(mode="json")
    payload["nutrition"]["calories"] = value
    assert client.post("/api/recipes", headers=HEADERS, json=payload).status_code == 422


def test_foreign_recipe_identifier_cannot_be_modified(client: TestClient, store: Store) -> None:
    other = store.create_user()
    card = Catalog(store).save(other, card_data())
    payload = card_data().model_dump(mode="json") | {"expected_version_id": card.version_id}
    response = client.put(f"/api/recipes/{card.recipe_id}", headers=HEADERS, json=payload)
    assert response.status_code == 404
    assert client.get("/api/recipes").json() == []


@pytest.mark.parametrize(
    "base_url,peer,origin,headers",
    [
        ("http://evil.example", "127.0.0.1", None, HEADERS),
        ("http://127.0.0.1:8000", "192.0.2.1", None, HEADERS),
        ("http://127.0.0.1:8000", "127.0.0.1", "https://evil.example", HEADERS),
        ("http://127.0.0.1:8000", "127.0.0.1", None, {}),
    ],
)
def test_nonlocal_and_cross_site_writes_are_denied(
    store: Store, base_url: str, peer: str, origin: str | None, headers: dict[str, str]
) -> None:
    app = local_app(store, store.create_user())
    with TestClient(app, base_url=base_url, client=(peer, 50000)) as c:
        sent = headers | ({"Origin": origin} if origin else {})
        assert (
            c.post(
                "/api/recipes", headers=sent, json=card_data().model_dump(mode="json")
            ).status_code
            == 403
        )


def test_import_requires_confirmation_and_current_digest_then_is_repeatable(
    client: TestClient,
) -> None:
    preview = client.get("/api/starter-library").json()
    assert not preview["imported"]
    for body, status in [
        ({"digest": preview["digest"], "confirmed": False}, 422),
        ({"digest": "stale", "confirmed": True}, 409),
        ({"digest": preview["digest"], "confirmed": True}, 200),
    ]:
        assert (
            client.post("/api/starter-library/import", headers=HEADERS, json=body).status_code
            == status
        )
    assert len(client.get("/api/recipes").json()) == 18
    again = client.post(
        "/api/starter-library/import",
        headers=HEADERS,
        json={"digest": preview["digest"], "confirmed": True},
    )
    assert again.json() == {"imported": 0}


def test_small_nutrition_values_round_trip_without_scientific_notation(client: TestClient) -> None:
    payload = card_data().model_dump(mode="json")
    payload["nutrition"]["calories"] = "0.00000001"
    response = client.post("/api/recipes", headers=HEADERS, json=payload)
    assert response.status_code == 201
    assert response.json()["nutrition"]["calories"] == "0.00000001"
    assert client.get("/api/recipes").json()[0]["nutrition"]["calories"] == "0.00000001"


def test_existing_t05_precision_is_preserved_when_listing_cards(
    client: TestClient, store: Store
) -> None:
    from decimal import Decimal

    from ratatouille.domain import IngredientInput, Nutrition, RecipeInput, Unit

    assert isinstance(client.app, FastAPI)
    owner = client.app.state.owner
    ingredient = store.create_ingredient(owner, "Точный продукт")
    value = Decimal("0.12345678901234567890123456789")
    store.create_recipe(
        owner,
        RecipeInput(
            "Старый рецепт",
            "",
            1,
            Nutrition(calories=value),
            (IngredientInput(ingredient, value, Unit.GRAM),),
        ),
    )
    response = client.get("/api/recipes")
    assert response.status_code == 200
    assert response.json()[0]["nutrition"]["calories"] == str(value)
    assert response.json()[0]["ingredients"][0]["quantity"] == str(value)
