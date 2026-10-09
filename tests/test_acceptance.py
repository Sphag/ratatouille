"""One complete authenticated product journey across services and restart."""

import asyncio
from datetime import date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from icalendar import Calendar
from test_telegram_auth import NOW, TOKEN, signed

from ratatouille.database import make_engine
from ratatouille.reminders import MOSCOW, ReminderWorker
from ratatouille.server import telegram_app
from ratatouille.storage import Store


def test_plan_replace_confirm_check_remind_export_and_restart(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ratatouille.telegram_auth.time.time", lambda: NOW)
    origin = "https://app.example.invalid"
    a = {"Authorization": "tma " + signed(101), "Origin": origin, "X-Ratatouille-Request": "1"}
    b = a | {"Authorization": "tma " + signed(202)}
    goals = {"calories": "2000", "protein": "100", "fat": "70", "carbs": "250"}
    with TestClient(
        telegram_app(store, TOKEN, origin, frozenset({101, 202})), base_url=origin
    ) as client:

        def call(method: str, path: str, body: object | None = None, *, status: int = 200) -> Any:
            response = client.request(method, "/api/" + path, headers=a, json=body)
            assert response.status_code == status, response.text
            assert response.headers["cache-control"] == "no-store"
            return response.json()

        starter = call("GET", "starter-library")
        assert call(
            "POST", "starter-library/import", {"confirmed": True, "digest": starter["digest"]}
        ) == {"imported": 18}
        cards = call("GET", "recipes")
        assert len(cards) == 18 and not any(c["eligible"] for c in cards)
        assert call("PUT", "menu/goals", goals) == goals
        menu = call(
            "POST",
            "menu/plans",
            {"start_date": "2026-10-12", "days": 28, "mode": "ab", "targets": goals},
            status=201,
        )
        path = "menu/revisions/" + menu["id"]
        assert "detail" in call("POST", path + "/proposal", {"expected_number": 1}, status=422)
        assert call("GET", path)["entries"] == []
        for card in cards:
            call(
                "PATCH",
                "recipes/" + card["recipe_id"] + "/flags",
                {
                    "expected_version_id": card["version_id"],
                    "expected_eligible": False,
                    "expected_archived": False,
                    "eligible": True,
                    "archived": False,
                },
            )
        proposal = call("POST", path + "/proposal", {"expected_number": 1})
        menu = call(
            "PUT",
            path + "/proposal",
            {
                "expected_number": 1,
                "positions": proposal["positions"],
                "digest": proposal["digest"],
            },
        )
        assert menu["state"] == "draft" and len(menu["entries"]) >= 84
        # Four A days and three B days, without changing portion sizes.
        for day in [2, 4, 6]:
            assert {(e["slot"], e["version_id"]) for e in menu["entries"] if e["day"] == day} == {
                (e["slot"], e["version_id"]) for e in menu["entries"] if e["day"] == 0
            }
        options = call(
            "POST",
            path + "/replacement-options",
            {"expected_number": menu["number"], "day": 0, "slot": "breakfast"},
        )
        assert options
        selected = next(
            e["version_id"]
            for e in options[0]["entries"]
            if e["day"] == 0 and e["slot"] == "breakfast"
        )
        choice = {
            "expected_number": menu["number"],
            "day": 0,
            "slot": "breakfast",
            "version_id": selected,
            "scenario": "rebalance",
            "automatic": True,
        }
        preview = call("POST", path + "/replacement", choice)
        assert call("GET", path) == menu
        assert preview["changes"] and preview["menu"]["shopping"]
        menu = call("PUT", path + "/replacement", choice | {"digest": preview["digest"]})
        assert menu["state"] == "draft"
        call("PUT", path + "/replacement", choice | {"digest": preview["digest"]}, status=409)
        menu = call("POST", path + "/confirm", {"expected_number": menu["number"]})
        assert menu["state"] == "confirmed"
        output = call("GET", path + "/fulfilment")
        shopping = output["shopping"][0]
        output = call(
            "PUT",
            path + "/shopping-check",
            {k: shopping[k] for k in ("week", "ingredient_id", "unit")}
            | {"expected_checked": False, "checked": True},
        )
        cooking = output["cooking"][0]
        output = call(
            "PUT",
            path + "/cooking-check",
            {k: cooking[k] for k in ("week", "version_id")}
            | {"expected_checked": False, "checked": True},
        )
        assert output["shopping"][0]["checked"] and output["cooking"][0]["checked"]
        draft = call("POST", "menu/plans/" + menu["plan_id"] + "/draft", status=201)
        call(
            "POST",
            "menu/revisions/" + draft["id"] + "/cancel",
            {"expected_number": draft["number"]},
        )
        assert call("GET", path + "/fulfilment") == output
        sender = AsyncMock()
        clock = datetime.combine(date(2026, 10, 12), datetime.min.time(), MOSCOW).replace(hour=18)
        asyncio.run(ReminderWorker(store, 101, sender).tick(clock))
        assert sender.await_count == 0
        settings = call("GET", "menu/schedule")
        settings = call(
            "PUT",
            "menu/schedule",
            {
                "expected_digest": settings["digest"],
                "items": [
                    {"kind": "cooking", "weekday": 0, "at": "18:00", "enabled": True},
                    {"kind": "thawing", "weekday": 6, "at": "12:00", "enabled": True},
                ],
            },
        )
        asyncio.run(ReminderWorker(store, 101, sender).tick(clock))
        assert sender.await_count == 1
        calendar_path = "/api/menu/plans/" + menu["plan_id"] + "/calendar.ics"
        raw = client.get(calendar_path, headers=a).content
        assert len(Calendar.from_ical(raw).walk("VEVENT")) == 8
        assert client.get(calendar_path, headers=a).content == raw
        assert client.get(calendar_path, headers=b).status_code == 404
        assert client.get("/api/menu/schedule", headers=b).json()["items"] == []
        assert client.get("/api/recipes", headers=b).json() == []
        assert (
            client.put("/api/menu/goals", headers=a, json=goals | {"calories": "-1"}).status_code
            == 422
        )
        assert (
            client.post(
                "/api/menu/plans", headers=a, json={"start_date": "bad", "targets": goals}
            ).status_code
            == 422
        )
    engine = make_engine(Path(str(store.engine.url.database)))
    try:
        restarted = Store(engine)
        with TestClient(
            telegram_app(restarted, TOKEN, origin, frozenset({101, 202})), base_url=origin
        ) as client:
            assert client.get("/api/menu/goals", headers=a).json() == goals
            assert client.get("/api/menu/schedule", headers=a).json() == settings
            assert client.get("/api/" + path + "/fulfilment", headers=a).json() == output
            assert client.get(calendar_path, headers=a).content == raw
            asyncio.run(ReminderWorker(restarted, 101, sender).tick(clock))
            assert sender.await_count == 1
    finally:
        engine.dispose()
