"""HTTP replacement review/apply and owner/local boundary."""

from fastapi.testclient import TestClient
from test_catalog import card_data
from test_proposal_api import client as client
from test_proposal_api import new
from test_recipe_api import HEADERS

from ratatouille.app import create_app


def test_replacement_review_does_not_write_then_atomic_apply(client: TestClient) -> None:
    cards = [
        client.post(
            "/api/recipes", headers=HEADERS, json=card_data(eligible=True).model_dump(mode="json")
        ).json()
        for _ in range(2)
    ]
    menu = new(client)
    path = f"/api/menu/revisions/{menu['id']}"
    p = client.post(path + "/proposal", headers=HEADERS, json={"expected_number": 1}).json()
    m = client.put(
        path + "/proposal",
        headers=HEADERS,
        json={"expected_number": 1, "positions": p["positions"], "digest": p["digest"]},
    ).json()
    current = next(
        e["version_id"] for e in m["entries"] if e["day"] == 0 and e["slot"] == "breakfast"
    )
    other = next(c for c in cards if c["version_id"] != current)
    data = {
        "expected_number": m["number"],
        "day": 0,
        "slot": "breakfast",
        "version_id": other["version_id"],
        "scenario": "keep",
        "automatic": True,
    }
    options = client.post(
        path + "/replacement-options",
        headers=HEADERS,
        json={k: v for k, v in data.items() if k in ("expected_number", "day", "slot")},
    )
    assert options.status_code == 200 and len(options.json()) == 1
    preview = client.post(path + "/replacement", headers=HEADERS, json=data)
    assert preview.status_code == 200 and preview.headers["cache-control"] == "no-store"
    assert client.get(path).json() == m
    proposal = preview.json()
    assert len(proposal["changes"]) == 4 and proposal["menu"]["shopping"]
    applied = client.put(
        path + "/replacement", headers=HEADERS, json=data | {"digest": proposal["digest"]}
    )
    assert applied.status_code == 200 and applied.json()["state"] == "draft"
    assert client.get("/api/menu/plans").json()[0]["confirmed_id"] is None
    assert (
        client.put(
            path + "/replacement", headers=HEADERS, json=data | {"digest": proposal["digest"]}
        ).status_code
        == 409
    )


def test_replacement_boundary_and_invalid_contract(client: TestClient) -> None:
    menu = new(client)
    path = f"/api/menu/revisions/{menu['id']}/replacement"
    body = {"expected_number": 1, "day": 0, "slot": "dinner", "version_id": "missing"}
    assert client.post(path, json=body).status_code == 403
    assert (
        client.post(
            path, headers=HEADERS | {"Origin": "https://evil.example"}, json=body
        ).status_code
        == 403
    )
    for extra in ({"owner_id": "other"}, {"day": True}, {"scenario": "unknown"}):
        assert client.post(path, headers=HEADERS, json=body | extra).status_code == 422
    with TestClient(create_app()) as closed:
        assert closed.post(path, headers=HEADERS, json=body).status_code == 404
