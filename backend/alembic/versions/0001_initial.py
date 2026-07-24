"""initial baseline schema

Creates every table from the ORM metadata. This baseline keeps the migration
in lock-step with app.models; generate incremental migrations afterwards with
`alembic revision --autogenerate -m "..."`.

Revision ID: 0001_initial
Revises:
Create Date: 2025-08-01
"""
from alembic import op

from app.db import Base
import app.models  # noqa: F401

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind())
