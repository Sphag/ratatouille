"""Sanitized logs and one persistent owner error notice per hour."""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from sqlalchemy import select

from ratatouille.models import ErrorNotice
from ratatouille.storage import Store


class SafeLogs(logging.Filter):
    def __init__(self, token: str) -> None:
        super().__init__()
        self.token = token

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = record.getMessage().replace(self.token, "[redacted]")
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def configure_logs(token: str) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(SafeLogs(token))
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=logging.WARNING, handlers=[handler], force=True)


class ErrorReporter:
    def __init__(
        self, store: Store, owner: str, recipient: int, send: Callable[[int, str], Awaitable[None]]
    ) -> None:
        self.store, self.owner, self.recipient, self.send = store, owner, recipient, send

    async def report(self, now: datetime) -> None:
        window = int(now.timestamp()) // 3600
        with self.store._session(write=True) as session:
            if session.scalar(
                select(ErrorNotice).where(
                    ErrorNotice.owner_id == self.owner, ErrorNotice.window == window
                )
            ):
                return
            # Claim before sending: ambiguous failure is never recursively retried.
            session.add(ErrorNotice(owner_id=self.owner, window=window))
        try:
            await self.send(
                self.recipient,
                (
                    "Ratatouille: обнаружен сбой. Проверьте состояние сервиса "
                    "и журнал доставки напоминаний. Настройки сохранены."
                ),
            )
        except Exception:
            logging.getLogger("ratatouille.errors").error("Owner error notice unavailable")
