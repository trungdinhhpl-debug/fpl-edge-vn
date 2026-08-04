"""Database session management (SQLAlchemy 2.x)."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _make_engine():
    connect_args = {}
    if settings.is_sqlite:
        # allow use across FastAPI's threadpool
        connect_args = {"check_same_thread": False}
    return create_engine(
        settings.db_url,
        echo=False,
        future=True,
        pool_pre_ping=not settings.is_sqlite,
        connect_args=connect_args,
    )


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: yields a session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for scripts / jobs."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def ensure_columns() -> list[str]:
    """Thêm các cột mới vào bảng đã tồn tại (chỉ THÊM, không sửa/xoá).

    `create_all` chỉ tạo bảng mới, không cập nhật bảng cũ — nên khi model có thêm
    cột (vd luật mùa giải trong `seasons`), cơ sở dữ liệu đang chạy sẽ báo
    "no such column". Bước này chạy được trên cả SQLite lẫn Postgres và có tính
    idempotent, nên deploy không cần thao tác thủ công.

    Chỉ thêm cột cho phép NULL: cột NOT NULL không thể thêm vào bảng đã có dữ liệu.
    """
    from sqlalchemy import inspect, text
    from sqlalchemy.schema import CreateColumn

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    added: list[str] = []

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        present = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present:
                continue
            if not column.nullable and column.server_default is None:
                continue
            ddl = CreateColumn(column).compile(dialect=engine.dialect)
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {ddl}"))
            added.append(f"{table.name}.{column.name}")
    return added


def init_db() -> None:
    """Create tables if they don't exist (dev convenience; prod uses Alembic)."""
    # import models so they register on Base.metadata
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    ensure_columns()
