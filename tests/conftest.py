"""Each storage test uses a migrated temporary database, never a user's data."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from ratatouille.database import make_engine, migrate
from ratatouille.storage import Store


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    path = tmp_path / "test.sqlite3"
    migrate(path)
    engine = make_engine(path)
    try:
        yield Store(engine)
    finally:
        engine.dispose()
