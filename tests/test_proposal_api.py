"""HTTP preview/application contracts and explicit user confirmation."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from test_catalog import card_data
from test_recipe_api import HEADERS

from ratatouille.app import create_app
from ratatouille.local_app import local_app
from ratatouille.storage import Store


@pytest.fixture
def client(store: Store) -> Iterator[TestClient]:
    with TestClient(
        local_app(store, store.create_user()),
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 50000),
    ) as c:
        yield c


def new(client: TestClient, *, limited: bool = False) -> dict[str, object]:
    return dict(
        client.post(
            "/api/menu/plans",
            headers=HEADERS,
            json={
                "start_date": "2026-10-12",
                "mode": "limited" if limited else "ab",
                "targets": {"calories": "2000", "protein": "100", "fat": "70", "carbs": "250"},
            },
        ).json()
    )


def test_proposal_then_apply_then_explicit_confirmation(client: TestClient) -> None:
    recipe = client.post(
        "/api/recipes", headers=HEADERS, json=card_data(eligible=True).model_dump(mode="json")
    ).json()
    menu = new(client)
    path = f"/api/menu/revisions/{menu['id']}"
    preview = client.post(f"{path}/proposal", headers=HEADERS, json={"expected_number": 1})
    assert preview.status_code == 200
    assert preview.headers["cache-control"] == "no-store"
    proposal = preview.json()
    assert client.get(path).json() == menu
    assert {e["recipe_id"] for e in proposal["menu"]["entries"]} == {recipe["recipe_id"]}
    body = {"expected_number": 1, "positions": proposal["positions"], "digest": proposal["digest"]}
    applied = client.put(f"{path}/proposal", headers=HEADERS, json=body)
    assert applied.status_code == 200
    assert applied.json()["state"] == "draft"
    assert client.get("/api/menu/plans").json()[0]["confirmed_id"] is None
    assert client.put(f"{path}/proposal", headers=HEADERS, json=body).status_code == 409
    confirmed = client.post(f"{path}/confirm", headers=HEADERS, json={"expected_number": 2})
    assert confirmed.json()["state"] == "confirmed"
    assert (
        client.post(f"{path}/proposal", headers=HEADERS, json={"expected_number": 3}).status_code
        == 409
    )


def test_missing_admission_and_capacity_explain_conflict_without_writes(client: TestClient) -> None:
    menu = new(client, limited=True)
    path = f"/api/menu/revisions/{menu['id']}"
    response = client.post(f"{path}/proposal", headers=HEADERS, json={"expected_number": 1})
    assert response.status_code == 422 and "допуск" in response.json()["detail"]
    client.post(
        "/api/recipes", headers=HEADERS, json=card_data(eligible=True).model_dump(mode="json")
    )
    response = client.post(f"{path}/proposal", headers=HEADERS, json={"expected_number": 1})
    assert response.status_code == 422
    assert "Увеличьте лимит" in response.json()["detail"]
    assert "A/B" in response.json()["detail"]
    assert client.get(path).json() == menu


@pytest.mark.parametrize(
    "extra", [{"owner_id": "foreign"}, {"expected_number": True}, {"expected_number": 0}]
)
def test_unknown_fields_and_invalid_numbers_rejected(
    client: TestClient, extra: dict[str, object]
) -> None:
    menu = new(client)
    response = client.post(
        f"/api/menu/revisions/{menu['id']}/proposal",
        headers=HEADERS,
        json={"expected_number": 1, **extra},
    )
    assert response.status_code == 422


def test_proposal_respects_closed_default_app_and_local_write_boundary(client: TestClient) -> None:
    menu = new(client)
    path = f"/api/menu/revisions/{menu['id']}/proposal"
    for headers in ({}, HEADERS | {"Origin": "https://evil.example"}):
        assert client.post(path, headers=headers, json={"expected_number": 1}).status_code == 403
    with TestClient(create_app()) as closed:
        assert closed.post(path, headers=HEADERS, json={"expected_number": 1}).status_code == 404
