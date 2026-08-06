"""captain_picks: đóng băng lựa chọn đội trưởng trước deadline

Trang Đội trưởng tính bốn bảng xếp hạng tại chỗ mỗi lần mở, không lưu gì — nên
sau vòng đấu không còn biết hệ thống đã khuyên ai, và `Captain top pick hit rate`
không chấm được. Lưu cả bốn bảng để so được chiến lược nào thắng.

Revision ID: 0004_captain_picks
Revises: 0003_projection_snapshots
Create Date: 2026-08-06
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_captain_picks"
down_revision = "0003_projection_snapshots"
branch_labels = None
depends_on = None

TABLE = "captain_picks"


def upgrade() -> None:
    if TABLE in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("season", sa.String(16), index=True),
        sa.Column("gameweek", sa.Integer(), index=True),
        sa.Column("list_kind", sa.String(16), index=True),
        sa.Column("rank", sa.Integer()),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), index=True),
        sa.Column("captain_xp", sa.Float(), server_default="0"),
        sa.Column("ceiling", sa.Float(), server_default="0"),
        sa.Column("projected_eo", sa.Float(), nullable=True),
        sa.Column(
            "captured_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("deadline_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("model_version", sa.String(32)),
        sa.UniqueConstraint(
            "season", "gameweek", "list_kind", "rank", name="uq_captain_pick_slot"
        ),
    )


def downgrade() -> None:
    op.drop_table(TABLE)
