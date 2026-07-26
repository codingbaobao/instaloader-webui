import sqlite3
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker


def build_engine(database_path: Path) -> Engine:
    """Create a SQLite engine configured for concurrent, safe local access."""
    resolved_path = database_path.resolve()
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{resolved_path.as_posix()}")

    @event.listens_for(engine, "connect")
    def configure_sqlite(
        dbapi_connection: sqlite3.Connection, _connection_record: object
    ) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
        finally:
            cursor.close()

    return engine


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build sessions that retain values after a repository commits work."""
    return sessionmaker(bind=engine, expire_on_commit=False)
