"""player_season_history: nền lịch sử mà FPL xoá mất ở hạn vòng 1

FPL reset tổng cả-mùa ngay tại hạn vòng 1 (đo 2026-08-21), nên bảng `players` chỉ
còn số của mùa đang chơi và engine mất nền lịch sử đúng vào lúc cần nhất. Nguồn
duy nhất còn lại là `element-summary.history_past`, và vì mùa đã khép lại thì số
không đổi nữa nên chỉ phải nạp một lần cho mỗi cầu thủ.

Revision ID: 0006_player_season_history
Revises: 0005_fixture_finished_provisional
Create Date: 2026-08-24
"""
import sqlalchemy as sa
from alembic import op

revision = "0006_player_season_history"
down_revision = "0005_fixture_finished_provisional"
branch_labels = None
depends_on = None

TABLE = "player_season_history"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if TABLE not in tables:
        op.create_table(
            TABLE,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), index=True),
            sa.Column("season_name", sa.String(16), index=True),
            sa.Column("minutes", sa.Integer(), server_default="0"),
            sa.Column("starts", sa.Integer(), server_default="0"),
            sa.Column("total_points", sa.Integer(), server_default="0"),
            sa.Column("goals_scored", sa.Integer(), server_default="0"),
            sa.Column("assists", sa.Integer(), server_default="0"),
            sa.Column("clean_sheets", sa.Integer(), server_default="0"),
            sa.Column("goals_conceded", sa.Integer(), server_default="0"),
            sa.Column("saves", sa.Integer(), server_default="0"),
            sa.Column("yellow_cards", sa.Integer(), server_default="0"),
            sa.Column("red_cards", sa.Integer(), server_default="0"),
            sa.Column("bonus", sa.Integer(), server_default="0"),
            sa.Column("bps", sa.Integer(), server_default="0"),
            sa.Column("expected_goals", sa.Float(), server_default="0"),
            sa.Column("expected_assists", sa.Float(), server_default="0"),
            sa.Column("defensive_contribution", sa.Float(), server_default="0"),
            sa.Column("clearances_blocks_interceptions", sa.Float(), server_default="0"),
            sa.UniqueConstraint("player_id", "season_name", name="uq_player_season"),
        )

    if "players" in tables:
        cols = {c["name"] for c in inspector.get_columns("players")}
        if "history_fetched_at" not in cols:
            # Dấu "đã hỏi rồi" phải TÁCH khỏi "có dòng lịch sử": tân binh chưa từng
            # đá Ngoại hạng không có dòng nào, và lấy sự vắng mặt làm dấu thì mỗi
            # lần đồng bộ lại đi hỏi lại toàn bộ nhóm đó mãi mãi.
            op.add_column(
                "players",
                sa.Column("history_fetched_at", sa.DateTime(timezone=True), nullable=True),
            )


def downgrade() -> None:
    op.drop_column("players", "history_fetched_at")
    op.drop_table(TABLE)
