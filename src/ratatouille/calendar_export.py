"""Manual RFC5545 export with stable event IDs and persisted export versions."""

import hashlib
from datetime import UTC, datetime

from icalendar import Calendar, Event, Timezone
from sqlalchemy import select

from ratatouille.domain import ConflictError, ReminderKind
from ratatouille.models import CalendarVersion, Plan
from ratatouille.reminders import MOSCOW, planned_events
from ratatouille.storage import Store


def export_calendar(store: Store, owner: str, plan_id: str) -> bytes:
    with store._session(write=True) as session:
        plan = store._owned(session, Plan, owner, plan_id)
        if plan.current_revision_id is None:
            raise ConflictError("Сначала подтвердите меню для экспорта календаря.")
        events = [e for e in planned_events(store, owner, session=session) if e.plan_id == plan_id]
        content = "\n".join(
            f"{e.key}:{e.revision_id}:{e.when.isoformat()}:{e.text}" for e in events
        )
        digest = hashlib.sha256(content.encode()).hexdigest()
        version = session.scalar(
            select(CalendarVersion).where(
                CalendarVersion.owner_id == owner, CalendarVersion.plan_id == plan_id
            )
        )
        if version is None:
            version = CalendarVersion(
                owner_id=owner,
                plan_id=plan_id,
                digest=digest,
                sequence=0,
                updated_at=int(datetime.now(UTC).timestamp()),
            )
            session.add(version)
        elif version.digest != digest:
            version.digest = digest
            version.sequence += 1
            version.updated_at = max(version.updated_at + 1, int(datetime.now(UTC).timestamp()))
        calendar = Calendar()
        calendar.add("prodid", "-//Ratatouille//Meal preparation//RU")
        calendar.add("version", "2.0")
        calendar.add("calscale", "GREGORIAN")
        calendar.add("x-wr-calname", "Ratatouille: готовка и разморозка")
        calendar.add("x-wr-timezone", "Europe/Moscow")
        if events:
            calendar.add_component(
                Timezone.from_tzinfo(
                    MOSCOW,
                    first_date=min(e.when.date() for e in events),
                    last_date=max(e.when.date() for e in events),
                )
            )
        for planned in events:
            event = Event()
            event.add("uid", hashlib.sha256(planned.key.encode()).hexdigest() + "@ratatouille")
            event.add("dtstamp", datetime.fromtimestamp(version.updated_at, UTC))
            event.add("last-modified", datetime.fromtimestamp(version.updated_at, UTC))
            event.add("sequence", version.sequence)
            event.add("dtstart", planned.when)
            event.add(
                "summary", "Готовка" if planned.kind == ReminderKind.COOKING else "Разморозка"
            )
            event.add("description", planned.text)
            event.add("status", "CONFIRMED")
            event.add("transp", "TRANSPARENT")
            calendar.add_component(event)
        return bytes(calendar.to_ical())
