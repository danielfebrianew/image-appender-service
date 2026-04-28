from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal = None


def init_db(db_url: str) -> None:
    global _engine, _SessionLocal
    _engine = create_engine(db_url, pool_pre_ping=True)
    _SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)


def get_engine():
    if _engine is None:
        raise RuntimeError("DB not initialised — call init_db first")
    return _engine


def get_session() -> Generator[Session, None, None]:
    if _SessionLocal is None:
        raise RuntimeError("DB not initialised — call init_db first")
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


def make_session() -> Session:
    if _SessionLocal is None:
        raise RuntimeError("DB not initialised — call init_db first")
    return _SessionLocal()
