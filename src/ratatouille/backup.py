"""Consistent SQLite online backup; never overwrite a previous backup."""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from ratatouille.database import database_path


def backup(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise ValueError("База для резервной копии отсутствует.")
    if destination.exists() or source.resolve() == destination.resolve():
        raise ValueError("Выберите новый путь резервной копии.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".backup-", dir=destination.parent)
    os.close(descriptor)
    path = Path(temporary)
    try:
        with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as original:
            with sqlite3.connect(path) as copy:
                original.backup(copy)
                if copy.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise ValueError("Проверка резервной копии не прошла.")
                if copy.execute("PRAGMA foreign_key_check").fetchall():
                    raise ValueError("Нарушена целостность резервной копии.")
        # Atomic non-overwriting publication, including concurrent backup commands.
        os.link(path, destination)
    finally:
        path.unlink(missing_ok=True)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Укажите новый путь: python -m ratatouille.backup BACKUP.sqlite3")
    backup(database_path(), Path(sys.argv[1]))


if __name__ == "__main__":
    main()
