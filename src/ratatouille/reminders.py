"""Explicit Moscow schedules and durable, conservative reminder delivery."""

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ratatouille.catalog import Contract
from ratatouille.domain import ConflictError, ReminderKind, Schedule
from ratatouille.error_reporting import ErrorReporter
from ratatouille.models import Delivery, Plan, Reminder, User
from ratatouille.storage import Store

MOSCOW = ZoneInfo("Europe/Moscow")
logger = logging.getLogger("ratatouille.reminders")


class ScheduleItem(Contract):
    kind: ReminderKind = Field(strict=False)
    weekday: int = Field(ge=0, le=6)
    at: str
    enabled: bool = False

    @field_validator("at")
    @classmethod
    def minute(cls, value: str) -> str:
        if len(value) != 5 or value[2] != ":":
            raise ValueError("Введите время ЧЧ:ММ.")
        parsed = time.fromisoformat(value)
        if parsed.second or parsed.microsecond or parsed.tzinfo:
            raise ValueError("Введите местное время ЧЧ:ММ.")
        return value

    def schedule(self) -> Schedule:
        return Schedule(self.kind, self.weekday, time.fromisoformat(self.at), self.enabled)


class ScheduleView(Contract):
    items: list[ScheduleItem]
    digest: str
    timezone: str = "Europe/Moscow"


class SaveSchedule(Contract):
    items: list[ScheduleItem] = Field(max_length=14)
    expected_digest: str

    @model_validator(mode="after")
    def unique_days(self) -> "SaveSchedule":
        keys = [(s.kind, s.weekday) for s in self.items]
        if len(keys) != len(set(keys)):
            raise ValueError("Один вид напоминания можно задать один раз в день недели.")
        return self


class Schedules:
    def __init__(self, store: Store) -> None:
        self.store = store

    def _view(self, rows: list[Reminder]) -> ScheduleView:
        items = [
            ScheduleItem(
                kind=r.kind, weekday=r.weekday, at=r.at.strftime("%H:%M"), enabled=r.enabled
            )
            for r in rows
        ]
        content = "\n".join(item.model_dump_json() for item in items)
        return ScheduleView(items=items, digest=hashlib.sha256(content.encode()).hexdigest())

    def get(self, owner: str) -> ScheduleView:
        with self.store._session() as session:
            self.store._user(session, owner)
            return self._view(
                list(
                    session.scalars(
                        select(Reminder)
                        .where(Reminder.owner_id == owner)
                        .order_by(Reminder.kind, Reminder.weekday)
                    )
                )
            )

    def save(self, owner: str, data: SaveSchedule) -> ScheduleView:
        with self.store._session(write=True) as session:
            self.store._user(session, owner)
            rows = list(
                session.scalars(
                    select(Reminder)
                    .where(Reminder.owner_id == owner)
                    .order_by(Reminder.kind, Reminder.weekday)
                )
            )
            if self._view(rows).digest != data.expected_digest:
                raise ConflictError("Расписание изменилось. Перечитайте настройки.")
            session.execute(delete(Reminder).where(Reminder.owner_id == owner))
            for item in data.items:
                s = item.schedule()
                session.add(
                    Reminder(
                        owner_id=owner,
                        kind=s.kind,
                        weekday=s.weekday,
                        at=s.at,
                        enabled=s.enabled,
                        timezone=s.timezone,
                    )
                )
            # Never send queued events from an obsolete schedule.
            for row in session.scalars(
                select(Delivery).where(Delivery.owner_id == owner, Delivery.state == "pending")
            ):
                row.state = "cancelled"
            session.flush()
            return self._view(
                list(
                    session.scalars(
                        select(Reminder)
                        .where(Reminder.owner_id == owner)
                        .order_by(Reminder.kind, Reminder.weekday)
                    )
                )
            )


@dataclass(frozen=True)
class PlannedEvent:
    key: str
    owner: str
    plan_id: str
    revision_id: str
    kind: ReminderKind
    when: datetime
    text: str


def planned_events(
    store: Store, owner: str, *, session: Session | None = None
) -> list[PlannedEvent]:
    if session is None:
        with store._session() as current:
            return planned_events(store, owner, session=current)
    store._user(session, owner)
    schedules = tuple(
        Schedule(r.kind, r.weekday, r.at, r.enabled, r.timezone)
        for r in session.scalars(select(Reminder).where(Reminder.owner_id == owner))
    )
    plans = list(
        session.scalars(
            select(Plan).where(Plan.owner_id == owner, Plan.current_revision_id.is_not(None))
        )
    )
    events: list[PlannedEvent] = []
    for plan in plans:
        assert plan.current_revision_id is not None
        revision = store._revision(session, owner, plan.current_revision_id)
        for day in range(plan.days):
            calendar_day = plan.start_date + timedelta(days=day)
            for schedule in schedules:
                if not schedule.enabled or schedule.weekday != calendar_day.weekday():
                    continue
                names = sorted({e.recipe.name for e in revision.entries if e.day // 7 == day // 7})
                title = (
                    "Пора готовить"
                    if schedule.kind == ReminderKind.COOKING
                    else "Проверьте разморозку"
                )
                message = title + ". Неделя " + str(day // 7 + 1) + ":\n" + "\n".join(names)
                if schedule.kind == ReminderKind.THAWING:
                    message += (
                        "\nВыберите продукты для разморозки вручную: "
                        "автоматический подбор по хранению не выполняется."
                    )
                key = f"{owner}:{plan.id}:{schedule.kind.value}:{calendar_day.isoformat()}"
                events.append(
                    PlannedEvent(
                        key,
                        owner,
                        plan.id,
                        revision.id,
                        schedule.kind,
                        datetime.combine(calendar_day, schedule.at, MOSCOW),
                        message[:3900],
                    )
                )
    return sorted(events, key=lambda event: (event.when, event.key))


class RetryDelivery(Exception):
    def __init__(self, seconds: int) -> None:
        self.seconds = max(1, seconds)


class PermanentDelivery(Exception):
    pass


class ReminderWorker:
    """Claim before send; uncertain sends are never automatically repeated."""

    def __init__(
        self, store: Store, recipient: int, send: Callable[[int, str], Awaitable[None]]
    ) -> None:
        self.store, self.recipient, self.send = store, recipient, send
        self.reporter: ErrorReporter | None = None

    def due(self, now: datetime) -> list[str]:
        if now.tzinfo is None:
            raise ValueError("Clock must be timezone-aware")
        stamp = int(now.timestamp())
        with self.store._session() as session:
            user = session.scalar(select(User).where(User.telegram_id == self.recipient))
            owner = user.id if user else None
        if owner is None:
            return []
        events = [
            e
            for e in planned_events(self.store, owner)
            if 0 <= stamp - int(e.when.timestamp()) <= 900
        ]
        with self.store._session(write=True) as session:
            for event in events:
                plan = session.get(Plan, event.plan_id)
                active = session.scalar(
                    select(Reminder).where(
                        Reminder.owner_id == owner,
                        Reminder.kind == event.kind,
                        Reminder.weekday == event.when.weekday(),
                        Reminder.at == event.when.time().replace(tzinfo=None),
                        Reminder.enabled.is_(True),
                    )
                )
                if active is None or plan is None or plan.current_revision_id != event.revision_id:
                    continue
                existing = session.scalar(select(Delivery).where(Delivery.event_key == event.key))
                if existing is not None and existing.state == "cancelled":
                    existing.revision_id = event.revision_id
                    existing.due_at = int(event.when.timestamp())
                    existing.next_at = stamp
                    existing.state = "pending"
                    existing.attempts = 0
                    existing.text = event.text
                elif existing is None:
                    session.add(
                        Delivery(
                            owner_id=owner,
                            event_key=event.key,
                            plan_id=event.plan_id,
                            revision_id=event.revision_id,
                            due_at=int(event.when.timestamp()),
                            next_at=stamp,
                            state="pending",
                            attempts=0,
                            text=event.text,
                        )
                    )
            # A process lost after claim may already have sent. Keep conservative state.
            for row in session.scalars(
                select(Delivery).where(
                    Delivery.owner_id == owner,
                    Delivery.state == "sending",
                    Delivery.next_at < stamp - 120,
                )
            ):
                row.state = "uncertain"
            session.flush()
            return list(
                session.scalars(
                    select(Delivery.id)
                    .where(
                        Delivery.owner_id == owner,
                        Delivery.state == "pending",
                        Delivery.next_at <= stamp,
                    )
                    .order_by(Delivery.due_at)
                )
            )

    async def tick(self, now: datetime) -> None:
        stamp = int(now.timestamp())
        for identifier in self.due(now):
            with self.store._session(write=True) as session:
                row = session.get(Delivery, identifier)
                if row is None or row.state != "pending" or row.next_at > stamp:
                    continue
                plan = session.get(Plan, row.plan_id)
                local = datetime.fromtimestamp(row.due_at, MOSCOW)
                kind = ReminderKind(row.event_key.rsplit(":", 2)[1])
                active = session.scalar(
                    select(Reminder).where(
                        Reminder.owner_id == row.owner_id,
                        Reminder.kind == kind,
                        Reminder.weekday == local.weekday(),
                        Reminder.at == local.time().replace(tzinfo=None),
                        Reminder.enabled.is_(True),
                    )
                )
                if (
                    stamp - row.due_at > 900
                    or plan is None
                    or plan.current_revision_id != row.revision_id
                    or active is None
                ):
                    row.state = "cancelled"
                    continue
                row.state, row.next_at = "sending", stamp
                row.attempts += 1
                message = row.text
            state, delay = "sent", 0
            try:
                await self.send(self.recipient, message)
            except RetryDelivery as error:
                state, delay = "pending", error.seconds
            except PermanentDelivery:
                state = "failed"
            except Exception:
                # Timeout/connection failure may follow successful Telegram delivery.
                state = "uncertain"
            with self.store._session(write=True) as session:
                row = session.get(Delivery, identifier)
                assert row is not None
                row.state = "failed" if state == "pending" and row.attempts >= 3 else state
                row.next_at = stamp + delay
            if state != "sent":
                logger.error("Reminder delivery status=%s", state)
                if self.reporter is not None:
                    await self.reporter.report(now)

    async def loop(self) -> None:
        while True:
            try:
                await self.tick(datetime.now(MOSCOW))
            except Exception as error:
                logger.error("Reminder worker failure class=%s", type(error).__name__)
                if self.reporter is not None:
                    try:
                        await self.reporter.report(datetime.now(MOSCOW))
                    except Exception:
                        logger.error("Error reporting storage unavailable")
            await asyncio.sleep(15)
