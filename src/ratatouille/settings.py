"""Production configuration without loading credentials into logs or public files."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class TelegramSettings:
    token: str = field(repr=False)
    app_url: str
    owner_id: int


def telegram_settings() -> TelegramSettings:
    token = os.getenv("BOT_TOKEN", "")
    token_file = os.getenv("BOT_TOKEN_FILE", "")
    if token_file:
        if token:
            raise ValueError("Задайте только BOT_TOKEN либо BOT_TOKEN_FILE.")
        try:
            path = Path(token_file)
            if path.stat().st_size > 1024:
                raise ValueError("Некорректный размер файла BOT_TOKEN_FILE.")
            token = path.read_text().strip()
        except OSError:
            raise ValueError("Не удалось прочитать BOT_TOKEN_FILE.") from None
    url = os.getenv("RATATOUILLE_APP_URL", "")
    identifier = os.getenv("RATATOUILLE_OWNER_TELEGRAM_ID", "")
    parsed = urlsplit(url)
    if (
        not token
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Задайте BOT_TOKEN и RATATOUILLE_APP_URL с HTTPS "
            "в корне сайта без учётных данных, запроса и фрагмента."
        )
    if not identifier.isdecimal() or not 0 < int(identifier) < 2**52:
        raise ValueError("Задайте положительный RATATOUILLE_OWNER_TELEGRAM_ID.")
    return TelegramSettings(token, url, int(identifier))
