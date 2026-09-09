"""player_roles: vai trò chiến thuật từ nguồn ngoài

FPL API chỉ có 4 nhãn vị trí, không có vai trò chiến thuật. Bảng này giữ vai trò
lấy từ nguồn đã khai báo (hiện là API công khai của Premier League). `role` cho phép
NULL có chủ ý: nguồn có những nhãn không quy được về một vai trò ("Left/Right
Winger") — vẫn ghi lại kèm raw_label để giao diện nói được vì sao.

Revision ID: 0008_player_roles
Revises: 0007_player_fixture_goals
Create Date: 2026-09-09
"""
import sqlalchemy as sa
from alembic import op

revision = "0008_player_roles"
down_revision = "0007_player_fixture_goals"
branch_labels = None
depends_on = None

TABLE = "player_roles"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if TABLE in set(inspector.get_table_names()):
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "player_id", sa.Integer(), sa.ForeignKey("players.id"),
            unique=True, index=True,
        ),
        sa.Column("role", sa.String(8), nullable=True),
        sa.Column("raw_label", sa.String(64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("source_name", sa.String(64), server_default=""),
        sa.Column("source_url", sa.String(256), nullable=True),
        sa.Column("external_name", sa.String(128), nullable=True),
        sa.Column("external_club", sa.String(64), nullable=True),
        sa.Column("season_label", sa.String(32), nullable=True),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table(TABLE)
