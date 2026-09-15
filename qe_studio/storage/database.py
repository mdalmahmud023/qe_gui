from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3


@contextmanager
def connect(path: Path, *, create: bool = False, read_only: bool = False):
    """Open a short-lived connection; ordinary opens never create a database."""
    mode = "ro" if read_only else "rwc" if create else "rw"
    connection = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode={mode}", uri=True, timeout=5)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        if not read_only:
            connection.execute("PRAGMA synchronous = FULL")
        else:
            connection.execute("BEGIN")  # Consistent reads during another tab's migration/write.
        yield connection
        if not read_only:
            connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
