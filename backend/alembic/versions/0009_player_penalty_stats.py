"""player_penalty_stats: số quả 11m thực tế từng cầu thủ đã đá mỗi mùa

FPL phát xG tổng (gồm 11m) mà không phát số quả 11m, nên engine chỉ trừ được một
tỷ lệ trung bình — người đá nhiều hơn mức đó bị thổi phồng xG bóng sống. Bảng này
giữ số quả thật (API công khai của Premier League). Có hàng = đã biết (kể cả 0);
không có hàng = chưa biết, engine quay về cách trừ theo tỷ lệ.

Revision ID: 0009_player_penalty_stats
Revises: 0008_player_roles
Create Date: 2026-10-20
"""
import sqlalchemy as sa
from alembic import op

revision = "0009_player_penalty_stats"
down_revision = "0008_player_roles"
branch_labels = None
depends_on = None

TABLE = "player_penalty_stats"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if TABLE in set(inspector.get_table_names()):
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "player_id", sa.Integer(), sa.ForeignKey("players.id"), index=True,
        ),
        sa.Column("season_name", sa.String(16), index=True),
        sa.Column("attempts", sa.Integer(), server_default="0"),
        sa.Column("goals", sa.Integer(), server_default="0"),
        sa.Column("source_name", sa.String(64), server_default=""),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "player_id", "season_name", name="uq_player_penalty_season"
        ),
    )


def downgrade() -> None:
    op.drop_table(TABLE)
