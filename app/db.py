"""Database engine and session setup.

SQLite by default (zero setup); set DATABASE_URL to a PostgreSQL URL for the
Docker deployment. All quantities and money are stored as integers, so the
schema behaves identically on both engines.
"""
import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


engine = None
SessionLocal = sessionmaker(expire_on_commit=False)


def init(url: str | None = None):
    """(Re)bind the global engine. Tests call init('sqlite://') for an in-memory DB."""
    global engine
    url = url or os.environ.get("DATABASE_URL", "sqlite:///./data/app.db")
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool
        else:
            path = url.replace("sqlite:///", "")
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
    SessionLocal.configure(bind=engine)
    return engine


def create_all():
    from app import models  # noqa: F401  (register tables)
    Base.metadata.create_all(engine)


def drop_all():
    from app import models  # noqa: F401
    Base.metadata.drop_all(engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
