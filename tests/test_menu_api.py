"""Local menu HTTP boundary and complete public form workflow."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
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


def test_default_process_has_no_personal_menu_routes() -> None:
    with TestClient(create_app()) as c:
        assert not c.get("/api/capabilities").json()["menus"]
        for path in ("plans", "goals", "defaults", "revisions/unknown"):
            assert c.get(f"/api/menu/{path}").status_code == 404


def test_goals_creation_entries_confirmation_reload_and_cancel(client: TestClient) -> None:
    from test_catalog import card_data

    goals = {"calories": "2000", "protein": "100", "fat": "70", "carbs": "250"}
    assert client.put("/api/menu/goals", headers=HEADERS, json=goals).json() == goals
    assert client.get("/api/menu/defaults").json()["start_date"]
    recipe = client.post(
        "/api/recipes", headers=HEADERS, json=card_data().model_dump(mode="json")
    ).json()
    new = client.post(
        "/api/menu/plans", headers=HEADERS, json={"start_date": "2026-10-12", "targets": goals}
    )
    assert new.status_code == 201
    menu = new.json()
    path = f"/api/menu/revisions/{menu['id']}"
    assert (
        client.post(
            f"{path}/confirm", headers=HEADERS, json={"expected_number": menu["number"]}
        ).status_code
        == 422
    )
    assert client.get(path).json()["number"] == menu["number"]
    positions = [
        {"day": day, "slot": slot, "version_id": recipe["version_id"]}
        for day in range(7)
        for slot in ("breakfast", "second", "dinner")
    ]
    menu = client.put(
        f"{path}/entries",
        headers=HEADERS,
        json={"expected_number": menu["number"], "positions": positions},
    ).json()
    assert not menu["problems"]
    assert (
        client.put(
            f"{path}/entries", headers=HEADERS, json={"expected_number": 1, "positions": positions}
        ).status_code
        == 409
    )
    saved = client.post(
        f"{path}/confirm", headers=HEADERS, json={"expected_number": menu["number"]}
    ).json()
    assert saved["state"] == "confirmed"
    assert client.get(path).json() == saved
    draft = client.post(f"/api/menu/plans/{menu['plan_id']}/draft", headers=HEADERS).json()
    assert draft["entries"] == saved["entries"]
    assert (
        client.post(
            f"/api/menu/revisions/{draft['id']}/cancel",
            headers=HEADERS,
            json={"expected_number": draft["number"]},
        ).json()["state"]
        == "cancelled"
    )
    assert client.get("/api/menu/plans").json()[0]["confirmed_id"] == menu["id"]
    assert client.get(path).headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "extra",
    [
        {"owner_id": "foreign"},
        {"days": 28.0},
        {"start_date": "9999-12-31"},
        {"mode": "arbitrary"},
        {"repeat_limit": True},
        {"start_date": 0},
        {"mode": "limited", "repeat_limit": 10**20},
    ],
)
def test_invalid_request_has_no_partial_plan(client: TestClient, extra: dict[str, object]) -> None:
    response = client.post(
        "/api/menu/plans",
        headers=HEADERS,
        json={"start_date": "2026-10-12", "targets": client.get("/api/menu/goals").json(), **extra},
    )
    assert response.status_code == 422
    assert client.get("/api/menu/plans").json() == []


def test_oversized_limit_on_configuration_keeps_draft_unchanged(client: TestClient) -> None:
    menu = client.post(
        "/api/menu/plans",
        headers=HEADERS,
        json={"start_date": "2026-10-12", "targets": client.get("/api/menu/goals").json()},
    ).json()
    path = f"/api/menu/revisions/{menu['id']}"
    response = client.put(
        f"{path}/settings",
        headers=HEADERS,
        json={
            "expected_number": menu["number"],
            "mode": "limited",
            "repeat_limit": 10**20,
            "targets": menu["targets"],
        },
    )
    assert response.status_code == 422
    assert client.get(path).json() == menu


def test_cross_site_and_missing_application_header_rejected(client: TestClient) -> None:
    payload = {"start_date": "2026-10-12", "targets": client.get("/api/menu/goals").json()}
    assert client.post("/api/menu/plans", json=payload).status_code == 403
    assert (
        client.post(
            "/api/menu/plans", headers=HEADERS | {"Origin": "https://evil.example"}, json=payload
        ).status_code
        == 403
    )
