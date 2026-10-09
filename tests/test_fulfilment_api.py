"""Checklist contracts, cache policy and local-only owner boundary."""

from fastapi.testclient import TestClient
from test_catalog import card_data
from test_proposal_api import client as client
from test_proposal_api import new
from test_recipe_api import HEADERS


def test_view_confirm_mark_reload_and_stale_conflict(client: TestClient) -> None:
    card = client.post(
        "/api/recipes", headers=HEADERS, json=card_data(eligible=True).model_dump(mode="json")
    ).json()
    menu = new(client)
    path = f"/api/menu/revisions/{menu['id']}"
    p = client.post(path + "/proposal", headers=HEADERS, json={"expected_number": 1}).json()
    client.put(
        path + "/proposal",
        headers=HEADERS,
        json={"expected_number": 1, "positions": p["positions"], "digest": p["digest"]},
    )
    view = client.get(path + "/fulfilment")
    assert view.status_code == 200 and view.headers["cache-control"] == "no-store"
    item = view.json()["shopping"][0]
    data = {
        "week": 0,
        "ingredient_id": item["ingredient_id"],
        "unit": item["unit"],
        "expected_checked": False,
        "checked": True,
    }
    assert client.put(path + "/shopping-check", headers=HEADERS, json=data).status_code == 409
    client.post(path + "/confirm", headers=HEADERS, json={"expected_number": 2})
    changed = client.put(path + "/shopping-check", headers=HEADERS, json=data)
    assert changed.status_code == 200 and changed.json()["shopping"][0]["checked"]
    assert client.put(path + "/shopping-check", headers=HEADERS, json=data).status_code == 409
    cooking = {
        "week": 0,
        "version_id": card["version_id"],
        "expected_checked": False,
        "checked": True,
    }
    assert client.put(path + "/cooking-check", headers=HEADERS, json=cooking).json()["cooking"][0][
        "checked"
    ]
    assert client.get(path + "/fulfilment").json()["shopping"][0]["checked"]
    assert (
        client.put(
            path + "/shopping-check", headers=HEADERS, json=data | {"checked": 1}
        ).status_code
        == 422
    )
    assert (
        client.put(
            path + "/cooking-check", headers=HEADERS, json=cooking | {"owner_id": "foreign"}
        ).status_code
        == 422
    )
    assert client.put(path + "/shopping-check", json=data).status_code == 403
