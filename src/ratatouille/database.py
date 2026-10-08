"""Explicit SQLite configuration and migration entrypoint, with no import-time I/O."""

import os
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, Connection, Engine, create_engine, event
from sqlalchemy.pool import ConnectionPoolEntry


def database_path() -> Path:
    return Path(os.getenv("RATATOUILLE_DB_PATH", "data/ratatouille.sqlite3"))


def make_engine(path: Path) -> Engine:
    engine = create_engine(
        URL.create("sqlite+pysqlite", database=str(path)),
        connect_args={"timeout": 10, "check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def configure(connection: sqlite3.Connection, record: ConnectionPoolEntry) -> None:
        # SQLAlchemy owns BEGIN, including the write lock before any read in a mutation.
        connection.isolation_level = None
        connection.execute("PRAGMA foreign_keys=ON")
        # REPLACE must invoke DELETE guards before replacing immutable snapshots.
        connection.execute("PRAGMA recursive_triggers=ON")

    @event.listens_for(engine, "begin")
    def begin(connection: Connection) -> None:
        statement = (
            "BEGIN IMMEDIATE" if connection.get_execution_options().get("sqlite_write") else "BEGIN"
        )
        connection.exec_driver_sql(statement)

    return engine


def migrate(path: Path | None = None) -> None:
    target = path if path is not None else database_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    engine = make_engine(target)
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    finally:
        engine.dispose()


if __name__ == "__main__":
    migrate()
