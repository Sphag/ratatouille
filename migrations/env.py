"""Alembic uses the same explicit SQLite connection policy as the application."""

from typing import Literal

from alembic import context
from sqlalchemy import Connection

from ratatouille.database import database_path, make_engine
from ratatouille.models import Base, ExactDecimal

config = context.config


def render_item(kind: str, item: object, autogen_context: object) -> str | Literal[False]:
    if kind == "type" and isinstance(item, ExactDecimal):
        return "sa.Text()"
    return False


def run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata, render_item=render_item)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(url="sqlite://", target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    supplied = config.attributes.get("connection")
    if isinstance(supplied, Connection):
        run(supplied)
    else:
        path = database_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        engine = make_engine(path)
        try:
            with engine.begin() as connection:
                run(connection)
        finally:
            engine.dispose()
