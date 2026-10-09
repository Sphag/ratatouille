"""Bot launcher handlers use private owner and web_app without contacting Telegram."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, User

from ratatouille.bot import launcher
from ratatouille.settings import TelegramSettings, telegram_settings


def test_launcher_owner_button_and_non_owner_refusal() -> None:
    settings = TelegramSettings("test-token", "https://app.example.invalid", 101)
    handler = launcher(settings).message.handlers[0].callback
    for identifier in [101, 202]:
        message = Message(
            message_id=1,
            date=datetime.fromtimestamp(1, UTC),
            chat=Chat(id=identifier, type="private"),
            from_user=User(id=identifier, is_bot=False, first_name="Тест"),
        )
        # Frozen pydantic Message: mock the bound answer method on its type.
        from unittest.mock import patch

        with patch.object(Message, "answer", new=AsyncMock()) as answer:
            asyncio.run(handler(message))
            assert answer.await_count == 1
            args = answer.call_args
            if identifier == 101:
                assert (
                    args.kwargs["reply_markup"].inline_keyboard[0][0].web_app.url
                    == settings.app_url
                )
            else:
                assert "владельцу" in args.args[0]
    assert "test-token" not in repr(settings)


def test_production_config_requires_credentials_and_https(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("BOT_TOKEN", "RATATOUILLE_APP_URL", "RATATOUILLE_OWNER_TELEGRAM_ID"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValueError):
        telegram_settings()
    monkeypatch.setenv("BOT_TOKEN", "not-a-real-token")
    monkeypatch.setenv("RATATOUILLE_OWNER_TELEGRAM_ID", "101")
    for url in (
        "http://app.example.invalid",
        "https://" + "user:pass@" + "app.example.invalid",
        "https://app.example.invalid?q=x",
        "https://app.example.invalid/app",
    ):
        monkeypatch.setenv("RATATOUILLE_APP_URL", url)
        with pytest.raises(ValueError):
            telegram_settings()
    monkeypatch.setenv("RATATOUILLE_APP_URL", "https://app.example.invalid")
    assert telegram_settings().owner_id == 101
