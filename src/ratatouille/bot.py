"""Private-chat Mini App launcher; menu business operations remain in the web interface."""

import asyncio
import logging
from contextlib import suppress
from datetime import datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import CommandStart
from aiogram.types import (
    ErrorEvent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)

from ratatouille.database import database_path, make_engine
from ratatouille.error_reporting import ErrorReporter, configure_logs
from ratatouille.reminders import MOSCOW, PermanentDelivery, ReminderWorker, RetryDelivery
from ratatouille.settings import TelegramSettings, telegram_settings
from ratatouille.storage import Store


def launcher(settings: TelegramSettings) -> Router:
    router = Router()

    @router.message(CommandStart(), F.chat.type == "private")
    async def start(message: Message) -> None:
        if message.from_user is None or message.from_user.id != settings.owner_id:
            await message.answer("Приложение пока доступно только владельцу.")
            return
        await message.answer(
            "Откройте Ratatouille: составьте меню, проверьте покупки и план готовки.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Открыть Ratatouille", web_app=WebAppInfo(url=settings.app_url)
                        )
                    ]
                ]
            ),
        )

    return router


async def run(settings: TelegramSettings) -> None:
    path = database_path()
    if not path.is_file():
        raise ValueError("Сначала мигрируйте базу данных.")
    engine = make_engine(path)
    store = Store(engine)
    owner = store.create_user(telegram_id=settings.owner_id)
    async with Bot(settings.token) as bot:

        async def send(recipient: int, text: str) -> None:
            try:
                await bot.send_message(recipient, text)
            except TelegramRetryAfter as error:
                raise RetryDelivery(error.retry_after) from None
            except (TelegramForbiddenError, TelegramBadRequest):
                raise PermanentDelivery from None

        reporter = ErrorReporter(store, owner, settings.owner_id, send)
        worker = ReminderWorker(store, settings.owner_id, send)
        worker.reporter = reporter
        # Per-owner menu avoids granting a launcher to unknown users.
        await bot.set_chat_menu_button(
            chat_id=settings.owner_id,
            menu_button=MenuButtonWebApp(
                text="Ratatouille", web_app=WebAppInfo(url=settings.app_url)
            ),
        )
        dispatcher = Dispatcher()
        dispatcher.include_router(launcher(settings))

        @dispatcher.errors()
        async def error(event: ErrorEvent) -> bool:
            logging.getLogger("ratatouille.bot").error(
                "Bot handler failure class=%s", type(event.exception).__name__
            )
            await reporter.report(datetime.now(MOSCOW))
            return True

        task = asyncio.create_task(worker.loop())
        try:
            await dispatcher.start_polling(bot)
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            engine.dispose()


def main() -> None:
    settings = telegram_settings()
    configure_logs(settings.token)
    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        pass
    except Exception as error:
        logging.getLogger("ratatouille.bot").error("Bot stopped class=%s", type(error).__name__)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
