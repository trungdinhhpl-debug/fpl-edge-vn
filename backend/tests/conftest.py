"""Pytest fixtures — isolated SQLite DB seeded with offline demo data."""
import os
import tempfile

# Point at a throwaway DB BEFORE any app module imports (engine binds at import).
_TMP_DB = os.path.join(tempfile.gettempdir(), "fpl_edge_test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB}"
os.environ["AUTO_SYNC_ON_STARTUP"] = "false"
os.environ["MONTECARLO_ITERATIONS"] = "1500"
os.environ["PROJECTION_HORIZON"] = "6"

import pytest  # noqa: E402

from app.db import SessionLocal, init_db, session_scope  # noqa: E402
from app.seed_demo import seed_demo  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _seed():
    if os.path.exists(_TMP_DB):
        os.remove(_TMP_DB)
    init_db()
    with session_scope() as db:
        seed_demo(db)
    yield
    if os.path.exists(_TMP_DB):
        try:
            os.remove(_TMP_DB)
        except OSError:
            pass


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
