"""Fake clock/sender verify restart, claims, Moscow dates and failure outcomes."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from test_menus import fill, new_menu
from test_storage import setup_recipe

from ratatouille.domain import ConflictError, ReminderKind
from ratatouille.error_reporting import ErrorReporter, SafeLogs
from ratatouille.models import Delivery, User
from ratatouille.reminders import (
    MOSCOW,
    PermanentDelivery,
    PlannedEvent,
    ReminderWorker,
    RetryDelivery,
    SaveSchedule,
    ScheduleItem,
    Schedules,
    planned_events,
)
from ratatouille.storage import Store


def prepared(store: Store, *, confirmed: bool = True) -> tuple[str, datetime]:
    menus, owner, menu = new_menu(store, days=28)
    with store._session(write=True) as session:
        user = session.get(User, owner)
        assert user is not None
        user.telegram_id = 101
    recipe = setup_recipe(store, owner)
    menu = fill(menus, owner, menu, [recipe.version_id])
    if confirmed:
        menus.confirm(owner, menu.id, menu.number)
    when = datetime.combine(menu.start_date, datetime.min.time(), MOSCOW).replace(hour=18)
    service = Schedules(store)
    service.save(
        owner,
        SaveSchedule(
            expected_digest=service.get(owner).digest,
            items=[
                ScheduleItem(
                    kind=ReminderKind.COOKING, weekday=when.weekday(), at="18:00", enabled=True
                ),
                ScheduleItem(
                    kind=ReminderKind.THAWING,
                    weekday=(when.weekday() + 1) % 7,
                    at="07:00",
                    enabled=False,
                ),
            ],
        ),
    )
    return owner, when


def test_schedule_off_until_saved_validation_conflict_and_restart(store: Store) -> None:
    owner = store.create_user()
    service = Schedules(store)
    original = service.get(owner)
    assert original.items == []
    assert planned_events(store, owner) == []
    request = SaveSchedule(
        expected_digest=original.digest,
        items=[ScheduleItem(kind=ReminderKind.COOKING, weekday=0, at="18:00")],
    )
    result = service.save(owner, request)
    assert not result.items[0].enabled and result.timezone == "Europe/Moscow"
    assert Schedules(Store(store.engine)).get(owner) == result
    assert service.get(store.create_user()).items == []
    with pytest.raises(ConflictError):
        service.save(owner, request)
    for at in ["25:00", "1:30", "12:30:00", "12:30+03:00", ""]:
        with pytest.raises(ValidationError):
            ScheduleItem(kind=ReminderKind.COOKING, weekday=0, at=at)
    with pytest.raises(ValidationError):
        SaveSchedule(expected_digest=result.digest, items=result.items * 2)


def test_events_only_confirmed_and_enabled_moscow_and_restart_dedup(store: Store) -> None:
    owner, when = prepared(store)
    events = planned_events(store, owner)
    assert len(events) == 4 and all(e.kind == "cooking" for e in events)
    assert events[0].when == when
    sender = AsyncMock()
    worker = ReminderWorker(store, 101, sender)
    asyncio.run(worker.tick(when - timedelta(seconds=1)))
    assert sender.await_count == 0
    asyncio.run(worker.tick(when.astimezone(UTC)))
    asyncio.run(ReminderWorker(Store(store.engine), 101, sender).tick(when + timedelta(seconds=15)))
    assert sender.await_count == 1
    assert sender.call_args.args[0] == 101 and "Неделя 1" in sender.call_args.args[1]
    asyncio.run(worker.tick(when + timedelta(days=7)))
    assert sender.await_count == 2


def test_draft_and_long_outage_never_send(store: Store) -> None:
    owner, when = prepared(store, confirmed=False)
    sender = AsyncMock()
    assert planned_events(store, owner) == []
    asyncio.run(ReminderWorker(store, 101, sender).tick(when))
    assert sender.await_count == 0


@pytest.mark.parametrize(
    "failure,status",
    [
        (PermanentDelivery(), "failed"),
        (TimeoutError(), "uncertain"),
        (RetryDelivery(30), "pending"),
    ],
)
def test_failure_policy_and_retry_only_definite_rate_limit(
    store: Store, failure: Exception, status: str
) -> None:
    owner, when = prepared(store)
    sender = AsyncMock(side_effect=[failure, None])
    worker = ReminderWorker(store, 101, sender)
    asyncio.run(worker.tick(when))
    with store._session() as session:
        row = session.scalar(select(Delivery).where(Delivery.owner_id == owner))
        assert row is not None and row.state == status and row.attempts == 1
    asyncio.run(ReminderWorker(store, 101, sender).tick(when + timedelta(seconds=30)))
    assert sender.await_count == (2 if status == "pending" else 1)


def test_claim_before_send_survives_crash_and_disabling_cancels_pending(store: Store) -> None:
    owner, when = prepared(store)
    sender = AsyncMock()
    worker = ReminderWorker(store, 101, sender)
    identifier = worker.due(when)[0]
    with store._session(write=True) as session:
        row = session.get(Delivery, identifier)
        assert row is not None
        row.state = "sending"
    asyncio.run(ReminderWorker(store, 101, sender).tick(when + timedelta(seconds=121)))
    assert sender.await_count == 0
    with store._session() as session:
        row = session.get(Delivery, identifier)
        assert row is not None and row.state == "uncertain"
    worker.due(when + timedelta(days=7))
    service = Schedules(store)
    service.save(owner, SaveSchedule(expected_digest=service.get(owner).digest, items=[]))
    asyncio.run(worker.tick(when + timedelta(days=7, seconds=1)))
    assert sender.await_count == 0


def test_errors_are_sanitized_and_owner_notices_throttled_across_restart(store: Store) -> None:
    owner = store.create_user(telegram_id=101)
    sender = AsyncMock(side_effect=TimeoutError())
    now = datetime(2026, 10, 12, 18, tzinfo=MOSCOW)
    asyncio.run(ErrorReporter(store, owner, 101, sender).report(now))
    asyncio.run(ErrorReporter(Store(store.engine), owner, 101, sender).report(now))
    assert sender.await_count == 1
    record = logging.LogRecord("test", logging.ERROR, "", 0, "network %s", ("test-secret",), None)
    assert SafeLogs("test-secret").filter(record)
    assert "test-secret" not in record.getMessage()


def test_disabling_between_snapshot_and_enqueue_does_not_send(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, when = prepared(store)
    events = planned_events(store, owner)
    service = Schedules(store)

    def stale_events(current: Store, identifier: str) -> list[PlannedEvent]:
        service.save(owner, SaveSchedule(expected_digest=service.get(owner).digest, items=[]))
        return events

    monkeypatch.setattr("ratatouille.reminders.planned_events", stale_events)
    sender = AsyncMock()
    asyncio.run(ReminderWorker(store, 101, sender).tick(when))
    assert sender.await_count == 0


def test_disabling_during_rate_limited_send_cancels_retry(store: Store) -> None:
    owner, when = prepared(store)
    service = Schedules(store)

    async def send(recipient: int, message: str) -> None:
        service.save(owner, SaveSchedule(expected_digest=service.get(owner).digest, items=[]))
        raise RetryDelivery(1)

    sender = AsyncMock(side_effect=send)
    worker = ReminderWorker(store, 101, sender)
    asyncio.run(worker.tick(when))
    asyncio.run(worker.tick(when + timedelta(seconds=2)))
    assert sender.await_count == 1


def test_rescheduling_unsent_event_revives_cancelled_but_never_sent(store: Store) -> None:
    owner, when = prepared(store)
    sender = AsyncMock(side_effect=[RetryDelivery(120), None])
    worker = ReminderWorker(store, 101, sender)
    asyncio.run(worker.tick(when))
    service = Schedules(store)
    service.save(
        owner,
        SaveSchedule(
            expected_digest=service.get(owner).digest,
            items=[
                ScheduleItem(
                    kind=ReminderKind.COOKING, weekday=when.weekday(), at="18:01", enabled=True
                )
            ],
        ),
    )
    asyncio.run(worker.tick(when + timedelta(minutes=1)))
    assert sender.await_count == 2
    service.save(
        owner,
        SaveSchedule(
            expected_digest=service.get(owner).digest,
            items=[
                ScheduleItem(
                    kind=ReminderKind.COOKING, weekday=when.weekday(), at="18:02", enabled=True
                )
            ],
        ),
    )
    asyncio.run(worker.tick(when + timedelta(minutes=2)))
    assert sender.await_count == 2
