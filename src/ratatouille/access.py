"""Explicit request-bound owner resolution for local and Telegram profiles."""

from collections.abc import Callable

from fastapi import Request

type OwnerResolver = Callable[[Request], str]
type Guard = Callable[[Request], None]


def owner_dependency(owner: str | OwnerResolver) -> OwnerResolver:
    def resolve(request: Request) -> str:
        return owner if isinstance(owner, str) else owner(request)

    return resolve
