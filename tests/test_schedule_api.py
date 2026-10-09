"""Schedule writes are authenticated, optimistic and private."""

import pytest
from fastapi.testclient import TestClient
from test_telegram_auth import NOW, TOKEN, signed

from ratatouille.server import telegram_app
from ratatouille.storage import Store


def test_schedule_owner_and_conflict_contract(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ratatouille.telegram_auth.time.time", lambda: NOW)
    origin = "https://app.example.invalid"
    with TestClient(
        telegram_app(store, TOKEN, origin, frozenset({101, 202})), base_url=origin
    ) as client:
        a = {"Authorization": "tma " + signed(101), "Origin": origin, "X-Ratatouille-Request": "1"}
        b = a | {"Authorization": "tma " + signed(202)}
        initial = client.get("/api/menu/schedule", headers=a).json()
        assert initial["items"] == []
        data = {
            "expected_digest": initial["digest"],
            "items": [{"kind": "cooking", "weekday": 0, "at": "18:00", "enabled": True}],
        }
        assert client.put("/api/menu/schedule", headers=a, json=data).status_code == 200
        assert client.put("/api/menu/schedule", headers=a, json=data).status_code == 409
        assert client.get("/api/menu/schedule", headers=b).json()["items"] == []
        assert (
            client.put("/api/menu/schedule", headers=a, json=data | {"owner_id": "202"}).status_code
            == 422
        )
        assert client.get("/api/menu/schedule").status_code == 401
