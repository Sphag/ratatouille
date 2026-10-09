"""Strict bounded initData verification; never consume initDataUnsafe or client owner IDs."""

import hashlib
import hmac
import json
import re
import time
from urllib.parse import parse_qsl, urlsplit

from fastapi import HTTPException, Request

from ratatouille.storage import Store

MAX_INIT_DATA = 16384
MAX_AGE = 3600
FUTURE_TOLERANCE = 30


def telegram_identity(raw: str, token: str, *, now: int | None = None) -> int:
    try:
        if not raw or len(raw) > MAX_INIT_DATA:
            raise ValueError
        pairs = parse_qsl(raw, strict_parsing=True, keep_blank_values=True, max_num_fields=64)
        if len({k for k, _ in pairs}) != len(pairs):
            raise ValueError
        data = dict(pairs)
        provided = data.pop("hash")
        if not re.fullmatch("[0-9a-f]{64}", provided):
            raise ValueError
        message = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        secret = hmac.digest(b"WebAppData", token.encode(), "sha256")
        expected = hmac.new(secret, message.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(provided, expected):
            raise ValueError
        auth_date = data["auth_date"]
        if not re.fullmatch(r"\d{1,12}", auth_date):
            raise ValueError
        current = int(time.time()) if now is None else now
        if not -FUTURE_TOLERANCE <= current - int(auth_date) <= MAX_AGE:
            raise ValueError
        user = json.loads(data["user"])
        identifier = user["id"]
        if type(identifier) is not int or not 0 < identifier < 2**52:
            raise ValueError
        return identifier
    except (ValueError, KeyError, TypeError, OverflowError) as error:
        raise HTTPException(
            401,
            "Откройте приложение заново через Telegram: "
            "данные входа отсутствуют, изменены или устарели.",
        ) from error


class TelegramAccess:
    def __init__(self, store: Store, token: str, origin: str, allowed_ids: frozenset[int]) -> None:
        self.store = store
        self.token = token
        self.origin = origin
        self.allowed_ids = allowed_ids

    def guard(self, request: Request) -> None:
        if (
            request.url.hostname != urlsplit(self.origin).hostname
            or request.headers.get("origin", self.origin) != self.origin
        ):
            raise HTTPException(403, "Недопустимый адрес приложения.")
        if (
            request.method not in {"GET", "HEAD"}
            and request.headers.get("x-ratatouille-request") != "1"
        ):
            raise HTTPException(403, "Некорректный запрос приложения.")

    def owner(self, request: Request) -> str:
        scheme, _, raw = request.headers.get("authorization", "").partition(" ")
        if scheme != "tma":
            raise HTTPException(401, "Откройте приложение через Telegram.")
        identifier = telegram_identity(raw, self.token)
        if identifier not in self.allowed_ids:
            raise HTTPException(403, "Доступ к приложению пока открыт только владельцу.")
        return self.store.create_user(telegram_id=identifier)
