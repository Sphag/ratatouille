"""Signed raw initData, bounded freshness and independent library interoperability."""

import hashlib
import hmac
import json
from urllib.parse import urlencode

import pytest
from aiogram.utils.web_app import check_webapp_signature
from fastapi import HTTPException

from ratatouille.telegram_auth import telegram_identity

TOKEN = "12345:test-only-token"
NOW = 1_800_000_000


def signed(identifier: object = 101, *, date: int = NOW, **extra: str) -> str:
    values = {
        "user": json.dumps(
            {"id": identifier, "first_name": "Тестовый пользователь"},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "auth_date": str(date),
        "query_id": "test-query",
    } | extra
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    signature = hmac.new(
        secret, "\n".join(f"{k}={values[k]}" for k in sorted(values)).encode(), hashlib.sha256
    ).hexdigest()
    return urlencode(values | {"hash": signature})


def test_valid_signed_data_matches_aiogram_and_uses_only_signed_identity() -> None:
    raw = signed(signature="third-party-signature", start_param="test-start")
    assert check_webapp_signature(TOKEN, raw)
    assert telegram_identity(raw, TOKEN, now=NOW) == 101
    assert telegram_identity(signed(2**51), TOKEN, now=NOW) == 2**51


@pytest.mark.parametrize(
    "raw", ["", "user=x", "hash=bad", "auth_date=0&auth_date=1&hash=bad", "x" * 16385]
)
def test_malformed_raw_is_unauthorized(raw: str) -> None:
    with pytest.raises(HTTPException) as error:
        telegram_identity(raw, TOKEN, now=NOW)
    assert error.value.status_code == 401


@pytest.mark.parametrize("date", [NOW - 3601, NOW + 31])
def test_stale_and_future_sessions_rejected(date: int) -> None:
    with pytest.raises(HTTPException):
        telegram_identity(signed(date=date), TOKEN, now=NOW)


@pytest.mark.parametrize("identifier", [True, 0, -1, "101", 2**52, None])
def test_invalid_signed_user_identifier_rejected(identifier: object) -> None:
    with pytest.raises(HTTPException):
        telegram_identity(signed(identifier), TOKEN, now=NOW)


def test_hash_wrong_bot_token_and_duplicate_fields_rejected() -> None:
    raw = signed()
    for invalid, token in [
        (raw.replace("test-query", "changed"), TOKEN),
        (raw, "other-token"),
        (raw + "&auth_date=" + str(NOW), TOKEN),
        (raw + "&user=" + json.dumps({"id": 202}), TOKEN),
    ]:
        with pytest.raises(HTTPException):
            telegram_identity(invalid, token, now=NOW)
    with pytest.raises(HTTPException):
        telegram_identity(signed(user="null"), TOKEN, now=NOW)
