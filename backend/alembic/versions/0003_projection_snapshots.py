"""projection_snapshots: đóng băng dự báo trước deadline để chấm được về sau

`player_projections` bị xoá và ghi lại mỗi lần chạy engine, nên dự báo đưa ra
trước deadline không còn tồn tại sau vòng đấu. Không có bảng này thì MAE, RMSE,
Spearman, Brier, calibration **vĩnh viễn** không đo được — không phải "chưa đo".

Cột `is_locked` là cơ chế chống data leakage: sau deadline bản ghi bị khoá, nên
một lần chạy muộn hơn (đã biết đội hình ra sân) không thể sửa lại dự báo.

Revision ID: 0003_projection_snapshots
Revises: 0002_season_rules
Create Date: 2026-08-06
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_projection_snapshots"
down_revision = "0002_season_rules"
branch_labels = None
depends_on = None

TABLE = "projection_snapshots"


def upgrade() -> None:
    # 0001 tạo bảng từ ORM metadata, nên cơ sở dữ liệu dựng mới đã có bảng này.
    if TABLE in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("season", sa.String(16), index=True),
        sa.Column("gameweek", sa.Integer(), index=True),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), index=True),
        sa.Column(
            "captured_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("deadline_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("model_version", sa.String(32)),
        sa.Column("xp", sa.Float(), server_default="0"),
        sa.Column("p_start", sa.Float(), server_default="0"),
        sa.Column("p_haul", sa.Float(), server_default="0"),
        sa.Column("xmins", sa.Float(), server_default="0"),
        sa.Column("baseline_form", sa.Float(), nullable=True),
        sa.Column("baseline_market_xp", sa.Float(), nullable=True),
        sa.Column("actual_points", sa.Integer(), nullable=True),
        sa.Column("actual_minutes", sa.Integer(), nullable=True),
        sa.Column("actual_started", sa.Boolean(), nullable=True),
        sa.Column("outcome_filled_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "season", "gameweek", "player_id", name="uq_snapshot_season_gw_player"
        ),
    )


def downgrade() -> None:
    op.drop_table(TABLE)
