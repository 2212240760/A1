import importlib

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def get_engine(url: str):
    connect_args = None
    if url.startswith("sqlite:"):
        connect_args = {"check_same_thread": False}

    return create_engine(url, future=True, connect_args=connect_args or {})


def get_session_factory(engine):
    return sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )


def create_all(engine) -> None:
    importlib.import_module("app.models")
    Base.metadata.create_all(engine)
