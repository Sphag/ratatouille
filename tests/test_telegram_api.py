"""Authenticated Mini App routes keep all business objects owner scoped."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from test_catalog import card_data
from test_telegram_auth import NOW, TOKEN, signed

from ratatouille.server import telegram_app
from ratatouille.storage import Store

ORIGIN = "https://app.example.invalid"


def headers(identifier: int = 101) -> dict[str, str]:
    return {
        "Authorization": "tma " + signed(identifier),
        "Origin": ORIGIN,
        "X-Ratatouille-Request": "1",
    }


@pytest.fixture
def client(store: Store, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr("ratatouille.telegram_auth.time.time", lambda: NOW)
    with TestClient(
        telegram_app(store, TOKEN, ORIGIN, frozenset({101, 202})), base_url=ORIGIN
    ) as c:
        yield c


def test_invalid_missing_and_unapproved_access_never_exposes_library(client: TestClient) -> None:
    assert client.get("/api/capabilities").json() == {
        "recipes": True,
        "menus": True,
        "telegram": True,
    }
    for h in [
        {},
        {"Authorization": "tma " + signed(101, date=NOW - 3601)},
        headers(303),
        headers() | {"Origin": "https://evil.example"},
    ]:
        assert client.get("/api/recipes", headers=h).status_code in (401, 403)
    assert (
        client.post(
            "/api/recipes",
            headers={"Authorization": "tma " + signed(101)},
            json=card_data().model_dump(mode="json"),
        ).status_code
        == 403
    )


def test_complete_owner_flow_and_foreign_objects_are_hidden(client: TestClient) -> None:
    a = headers()
    b = headers(202)
    recipe = client.post(
        "/api/recipes", headers=a, json=card_data(eligible=True).model_dump(mode="json")
    ).json()
    assert client.get("/api/recipes", headers=b).json() == []
    assert client.get("/api/recipes?owner_id=101", headers=b).json() == []
    goals = {"calories": "2000", "protein": "100", "fat": "70", "carbs": "250"}
    assert client.put("/api/menu/goals", headers=a, json=goals).json() == goals
    assert client.get("/api/menu/goals", headers=b).json()["calories"] == "0"
    m = client.post(
        "/api/menu/plans", headers=a, json={"start_date": "2026-10-12", "targets": goals}
    ).json()
    path = f"/api/menu/revisions/{m['id']}"
    p = client.post(path + "/proposal", headers=a, json={"expected_number": 1}).json()
    menu = client.put(
        path + "/proposal",
        headers=a,
        json={"expected_number": 1, "positions": p["positions"], "digest": p["digest"]},
    ).json()
    assert (
        client.post(path + "/confirm", headers=a, json={"expected_number": menu["number"]}).json()[
            "state"
        ]
        == "confirmed"
    )
    shopping = client.get(path + "/fulfilment", headers=a).json()["shopping"][0]
    check = {
        "week": shopping["week"],
        "ingredient_id": shopping["ingredient_id"],
        "unit": shopping["unit"],
        "checked": True,
        "expected_checked": False,
    }
    assert client.put(path + "/shopping-check", headers=a, json=check).status_code == 200
    for method, url, body in [
        ("GET", path, None),
        ("GET", path + "/fulfilment", None),
        ("POST", path + "/proposal", {"expected_number": 3}),
        ("PUT", path + "/shopping-check", check),
        (
            "POST",
            path + "/replacement",
            {
                "expected_number": 3,
                "day": 0,
                "slot": "breakfast",
                "version_id": recipe["version_id"],
            },
        ),
        ("POST", f"/api/menu/plans/{m['plan_id']}/draft", None),
        (
            "PATCH",
            f"/api/recipes/{recipe['recipe_id']}/flags",
            {
                "expected_version_id": recipe["version_id"],
                "expected_eligible": True,
                "expected_archived": False,
                "eligible": False,
                "archived": True,
            },
        ),
    ]:
        response = client.request(method, url, headers=b, json=body)
        assert response.status_code == 404, (url, response.text)
    assert client.get("/api/menu/plans", headers=b).json() == []
    assert client.get(path, headers=a).headers["cache-control"] == "no-store"
    assert (
        client.post(
            "/api/recipes",
            headers=a,
            json=card_data().model_dump(mode="json") | {"owner_id": "foreign"},
        ).status_code
        == 422
    )


@pytest.mark.parametrize("url", ["https://APP.EXAMPLE.INVALID", "https://app.example.invalid:443/"])
def test_browser_canonical_origin_can_write(
    store: Store, monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setattr("ratatouille.telegram_auth.time.time", lambda: NOW)
    with TestClient(telegram_app(store, TOKEN, url, frozenset({101})), base_url=ORIGIN) as c:
        response = c.put(
            "/api/menu/goals",
            headers=headers(),
            json={"calories": "2000", "protein": "100", "fat": "70", "carbs": "250"},
        )
        assert response.status_code == 200
