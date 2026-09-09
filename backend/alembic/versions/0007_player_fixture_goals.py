"""player_goal_odds + player_fixture_goals: ghi bàn theo từng trận

Hai bảng cho phần nâng cấp xP(1):

* `player_goal_odds` — kèo "ghi bàn bất kỳ lúc nào" theo (trận, cầu thủ), do
  ingestion ghi nên code lúc xử lý request không gọi API.
* `player_fixture_goals` — số bàn kỳ vọng của từng cầu thủ trong từng trận, trước
  và sau điều chỉnh, kèm vai trò chiến thuật và điểm yếu đối thủ theo trục vị trí.
  Bị xoá & ghi lại mỗi lần chạy engine, cùng nhịp `player_projections`.

Revision ID: 0007_player_fixture_goals
Revises: 0006_player_season_history
Create Date: 2026-09-09
"""
import sqlalchemy as sa
from alembic import op

revision = "0007_player_fixture_goals"
down_revision = "0006_player_season_history"
branch_labels = None
depends_on = None

ODDS = "player_goal_odds"
GOALS = "player_fixture_goals"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if ODDS not in tables:
        op.create_table(
            ODDS,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("fixture_id", sa.Integer(), index=True),
            sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), index=True),
            sa.Column("gameweek", sa.Integer(), nullable=True, index=True),
            sa.Column("prob", sa.Float(), server_default="0"),
            sa.Column("prob_raw", sa.Float(), nullable=True),
            sa.Column("n_bookmakers", sa.Integer(), server_default="0"),
            sa.Column("n_devigged", sa.Integer(), server_default="0"),
            sa.Column("best_price_yes", sa.Float(), nullable=True),
            sa.Column("best_price_no", sa.Float(), nullable=True),
            sa.Column("condition", sa.String(16), server_default="unknown"),
            sa.Column("source_name", sa.String(48), server_default="odds_api"),
            sa.Column("bookmaker_names", sa.String(256), nullable=True),
            sa.Column("market_updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
            sa.UniqueConstraint(
                "fixture_id", "player_id", name="uq_goal_odds_fixture_player"
            ),
        )

    if GOALS not in tables:
        op.create_table(
            GOALS,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), index=True),
            sa.Column("fixture_id", sa.Integer(), index=True),
            sa.Column("gameweek", sa.Integer(), index=True),
            sa.Column("element_type", sa.Integer(), server_default="0"),
            sa.Column("opponent_team", sa.Integer(), nullable=True),
            sa.Column("was_home", sa.Boolean(), nullable=True),
            # vai trò chiến thuật: NULL = chưa có nguồn, không suy từ nhãn vị trí
            sa.Column("tactical_role", sa.String(8), nullable=True),
            sa.Column("role_source", sa.String(64), nullable=True),
            sa.Column("role_confidence", sa.Float(), nullable=True),
            sa.Column("p_start", sa.Float(), server_default="0"),
            sa.Column("p_sub", sa.Float(), server_default="0"),
            sa.Column("minutes_if_start", sa.Float(), nullable=True),
            sa.Column("minutes_if_sub", sa.Float(), nullable=True),
            sa.Column("xmins", sa.Float(), server_default="0"),
            sa.Column("exp_goals_model", sa.Float(), server_default="0"),
            sa.Column("exp_goals_open", sa.Float(), server_default="0"),
            sa.Column("exp_goals_penalty", sa.Float(), server_default="0"),
            sa.Column("exp_goals_set_piece", sa.Float(), nullable=True),
            sa.Column("exp_goals_adjusted", sa.Float(), server_default="0"),
            sa.Column("exp_goals_final", sa.Float(), server_default="0"),
            sa.Column("market_prob", sa.Float(), nullable=True),
            sa.Column("market_exp_goals", sa.Float(), nullable=True),
            sa.Column("market_r90", sa.Float(), nullable=True),
            sa.Column("market_condition", sa.String(16), nullable=True),
            sa.Column("market_usable", sa.Boolean(), nullable=True),
            sa.Column("market_weight", sa.Float(), nullable=True),
            sa.Column("market_source", sa.String(48), nullable=True),
            sa.Column("market_n_bookmakers", sa.Integer(), nullable=True),
            sa.Column("market_updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("market_note", sa.Text(), nullable=True),
            sa.Column("weakness_axis", sa.String(32), nullable=True),
            sa.Column("axis_factor", sa.Float(), nullable=True),
            sa.Column("position_factor", sa.Float(), server_default="1"),
            sa.Column("finishing_share", sa.Float(), nullable=True),
            sa.Column("weakness_sample_90s", sa.Float(), nullable=True),
            sa.Column("weakness_reliability", sa.String(24), nullable=True),
            sa.Column("weakness_note", sa.Text(), nullable=True),
            sa.Column("goal_points", sa.Integer(), nullable=True),
            sa.Column("xp_before", sa.Float(), server_default="0"),
            sa.Column("xp_after", sa.Float(), server_default="0"),
            sa.Column("xp_delta", sa.Float(), server_default="0"),
            sa.Column("model_version", sa.String(32), server_default=""),
            sa.Column(
                "data_cutoff", sa.DateTime(timezone=True), server_default=sa.func.now()
            ),
            sa.UniqueConstraint(
                "player_id", "fixture_id", name="uq_player_fixture_goals"
            ),
        )


def downgrade() -> None:
    op.drop_table(GOALS)
    op.drop_table(ODDS)
