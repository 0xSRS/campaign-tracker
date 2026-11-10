from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from core.config import Settings, get_settings
from core.exceptions import CampaignTrackerError, DatabaseError
from database import models  # noqa: F401  (registers tables on Base.metadata)
from database.base import Base

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


class Database:
    """Owns the engine and hands out transactional sessions."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._engine: Engine = create_engine(URL.create("sqlite", database=str(self._path)))
        event.listen(self._engine, "connect", self._on_connect)
        self._factory = sessionmaker(self._engine, expire_on_commit=False)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> Database:
        settings = settings or get_settings()
        settings.ensure_directories()
        return cls(settings.database_path)

    @staticmethod
    def _on_connect(dbapi_conn, _record) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    @property
    def path(self) -> Path:
        return self._path

    def initialize(self) -> None:
        """Create missing tables and verify the schema version. Safe to call repeatedly."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            Base.metadata.create_all(self._engine)
            with self._engine.begin() as conn:
                version = conn.exec_driver_sql("PRAGMA user_version").scalar_one()
                if version == 0:
                    conn.exec_driver_sql(f"PRAGMA user_version = {SCHEMA_VERSION}")
                elif version != SCHEMA_VERSION:
                    raise DatabaseError(
                        f"Database schema version {version} does not match expected {SCHEMA_VERSION}."
                    )
        except CampaignTrackerError:
            raise
        except (SQLAlchemyError, OSError) as exc:
            logger.exception("Database initialization failed")
            raise DatabaseError(f"Could not initialize database: {exc.__class__.__name__}") from exc

    @contextmanager
    def session_scope(self) -> Iterator[Session]:
        """One transaction: commit on success, roll back on any error."""
        session = self._factory()
        try:
            yield session
            session.commit()
        except CampaignTrackerError:
            session.rollback()
            raise
        except SQLAlchemyError as exc:
            session.rollback()
            logger.exception("Database operation failed; rolled back")
            raise DatabaseError(f"Database operation failed: {exc.__class__.__name__}") from exc
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def close(self) -> None:
        self._engine.dispose()