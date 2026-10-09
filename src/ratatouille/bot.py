"""Private-chat Mini App launcher; menu business operations remain in the web interface."""

import asyncio

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)

from ratatouille.settings import TelegramSettings, telegram_settings


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
    async with Bot(settings.token) as bot:
        # Per-owner menu avoids granting a launcher to unknown users.
        await bot.set_chat_menu_button(
            chat_id=settings.owner_id,
            menu_button=MenuButtonWebApp(
                text="Ratatouille", web_app=WebAppInfo(url=settings.app_url)
            ),
        )
        dispatcher = Dispatcher()
        dispatcher.include_router(launcher(settings))
        await dispatcher.start_polling(bot)


def main() -> None:
    asyncio.run(run(telegram_settings()))


if __name__ == "__main__":
    main()
