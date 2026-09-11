"""Local demo storage. Secrets are kept by the OS credential vault, never SQLite."""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


def data_path() -> Path:
    path = Path(os.environ.get("POCKER_AGENT_DATA_DIR", str(Path(os.getenv("LOCALAPPDATA", Path.home())) / "PockerAgent")))
    path.mkdir(parents=True, exist_ok=True)
    return path / "pocker.db"


@contextmanager
def connect(path: Path):
    connection = sqlite3.connect(path, timeout=10)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        with connection:
            yield connection
    finally:
        connection.close()
