"""Engine and session factory for the configured PostgreSQL database."""

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from fiscal_astra.config.settings import Settings


def create_database_engine(settings: Settings) -> Engine:
    """Create a PostgreSQL engine without assuming a local dialect driver.

    ``ASTRA_DATABASE_URL`` already includes the driver. Callers should avoid
    rendering credentials in logs.
    """
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        future=True,
        echo=False,
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a session factory with sane transaction boundaries."""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    """Provide a transactional scope, including rollback on errors.

    Audit entries must remain when later operations fail. Callers that append
    audit events should commit them before an operation likely to fail, or
    use a nested transaction. This helper intentionally rolls back the current
    scope on an exception so partial domain writes do not remain.
    """
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
