"""Calendar parsing, explicit Moscow time, stable identity and private exports."""

from datetime import UTC, timedelta

import pytest
from fastapi.testclient import TestClient
from icalendar import Calendar
from sqlalchemy import select
from test_reminders import prepared
from test_telegram_auth import NOW, TOKEN, signed

from ratatouille.calendar_export import export_calendar
from ratatouille.domain import ConflictError, NotFoundError, ReminderKind
from ratatouille.models import Plan
from ratatouille.reminders import SaveSchedule, ScheduleItem, Schedules
from ratatouille.server import telegram_app
from ratatouille.storage import Store


def plan_id(store: Store, owner: str) -> str:
    with store._session() as session:
        identifier = session.scalar(select(Plan.id).where(Plan.owner_id == owner))
        assert identifier is not None
        return identifier


def test_export_parses_moscow_events_only_and_repeated_export_identical(store: Store) -> None:
    owner, when = prepared(store)
    identifier = plan_id(store, owner)
    raw = export_calendar(store, owner, identifier)
    assert raw == export_calendar(Store(store.engine), owner, identifier)
    assert b"\r\n" in raw and all(len(line) <= 75 for line in raw.split(b"\r\n"))
    calendar = Calendar.from_ical(raw)
    assert len(calendar.walk("VTIMEZONE")) == 1
    events = calendar.walk("VEVENT")
    assert len(events) == 4
    assert events[0].decoded("dtstart").astimezone(UTC) == when.astimezone(UTC)
    assert str(events[0]["dtstart"].params["TZID"]) == "Europe/Moscow"
    assert len({str(e["uid"]) for e in events}) == 4
    assert all(str(e["summary"]) == "Готовка" for e in events)
    assert not calendar.walk("VALARM")


def test_time_change_keeps_uid_advances_sequence_and_disabled_events_removed(store: Store) -> None:
    owner, when = prepared(store)
    identifier = plan_id(store, owner)
    before = Calendar.from_ical(export_calendar(store, owner, identifier)).walk("VEVENT")
    service = Schedules(store)
    service.save(
        owner,
        SaveSchedule(
            expected_digest=service.get(owner).digest,
            items=[
                ScheduleItem(
                    kind=ReminderKind.COOKING, weekday=when.weekday(), at="19:00", enabled=True
                ),
                ScheduleItem(
                    kind=ReminderKind.THAWING,
                    weekday=(when.weekday() + 1) % 7,
                    at="07:00",
                    enabled=True,
                ),
            ],
        ),
    )
    raw = export_calendar(store, owner, identifier)
    events = Calendar.from_ical(raw).walk("VEVENT")
    cooking = [e for e in events if str(e["summary"]) == "Готовка"]
    assert [str(e["uid"]) for e in cooking] == [str(e["uid"]) for e in before]
    assert int(str(cooking[0]["sequence"])) == int(str(before[0]["sequence"])) + 1
    assert cooking[0].decoded("dtstart") == when + timedelta(hours=1)
    assert len(events) == 8
    service.save(owner, SaveSchedule(expected_digest=service.get(owner).digest, items=[]))
    assert not Calendar.from_ical(export_calendar(store, owner, identifier)).walk("VEVENT")


def test_drafts_foreign_plans_and_text_escape(store: Store) -> None:
    owner, when = prepared(store, confirmed=False)
    identifier = plan_id(store, owner)
    with pytest.raises(ConflictError):
        export_calendar(store, owner, identifier)
    with pytest.raises(NotFoundError):
        export_calendar(store, store.create_user(), identifier)


def test_calendar_http_is_authenticated_and_downloadable(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, _ = prepared(store)
    identifier = plan_id(store, owner)
    origin = "https://app.example.invalid"
    monkeypatch.setattr("ratatouille.telegram_auth.time.time", lambda: NOW)
    with TestClient(
        telegram_app(store, TOKEN, origin, frozenset({101, 202})), base_url=origin
    ) as client:
        path = f"/api/menu/plans/{identifier}/calendar.ics"
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "tma " + signed(202)}).status_code == 404
        response = client.get(path, headers={"Authorization": "tma " + signed(101)})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/calendar")
        assert response.headers["cache-control"] == "no-store"
        assert "attachment" in response.headers["content-disposition"]
        assert len(Calendar.from_ical(response.content).walk("VEVENT")) == 4


def test_confirmed_revision_updates_calendar_but_draft_and_cancel_do_not(store: Store) -> None:

    from test_menus import fill
    from test_storage import setup_recipe

    from ratatouille.domain import IngredientInput, RecipeInput
    from ratatouille.menus import Menus, view

    owner, _ = prepared(store)
    identifier = plan_id(store, owner)
    original = export_calendar(store, owner, identifier)
    menus = Menus(store)
    cancelled = store.create_draft(owner, plan_id=identifier)
    store.cancel(owner, cancelled.id, expected_number=cancelled.number)
    assert export_calendar(store, owner, identifier) == original
    draft = store.create_draft(owner, plan_id=identifier)
    first = setup_recipe(store, owner)
    name = "Блюдо, с; соусом\\\nBEGIN:VEVENT\r\nEND:VCALENDAR"
    recipe = store.edit_recipe(
        owner,
        first.recipe_id,
        RecipeInput(
            name,
            "",
            first.yield_portions,
            first.nutrition,
            tuple(IngredientInput(i.ingredient_id, i.quantity, i.unit) for i in first.ingredients),
        ),
    )
    draft_view = fill(menus, owner, view(draft), [recipe.version_id])
    assert export_calendar(store, owner, identifier) == original
    menus.confirm(owner, draft_view.id, draft_view.number)
    updated = Calendar.from_ical(export_calendar(store, owner, identifier)).walk("VEVENT")
    before = Calendar.from_ical(original).walk("VEVENT")
    assert len(updated) == 4
    assert [str(e["uid"]) for e in updated] == [str(e["uid"]) for e in before]
    assert int(str(updated[0]["sequence"])) == int(str(before[0]["sequence"])) + 1
    assert "BEGIN:VEVENT" in str(updated[0]["description"])
    assert "соусом" in str(updated[0]["description"])
