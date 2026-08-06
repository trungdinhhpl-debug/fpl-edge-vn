"""season_rules: phiên bản từng nhóm luật theo mùa

Cần một bảng riêng vì `seasons.rules_version` chỉ là một vân tay của cả
`game_config`: nó đổi khi FPL sửa bất cứ thứ gì và không nói nhóm luật nào đã
đổi. Riêng bộ trọng số BPS thì FPL không phát qua API, nên phiên bản của nó do
`app/bps_rules.py` khai và được ghi lại ở đây để biết mỗi dự báo chạy trên luật
nào (BPS 2026/27 khác 2025/26).

Revision ID: 0002_season_rules
Revises: 0001_initial
Create Date: 2026-08-05
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_season_rules"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

TABLE = "season_rules"


def upgrade() -> None:
    # 0001 tạo bảng từ ORM metadata, nên cơ sở dữ liệu dựng mới đã có bảng này.
    if TABLE in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("season_id", sa.Integer(), sa.ForeignKey("seasons.id"), index=True),
        sa.Column("scoring_rules_version", sa.String(32), nullable=True),
        sa.Column("bps_rules_version", sa.String(32), nullable=True),
        sa.Column("assist_rules_version", sa.String(32), nullable=True),
        sa.Column("chip_rules_version", sa.String(32), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_url", sa.String(255), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "season_id", "effective_from", name="uq_season_rules_effective"
        ),
    )


def downgrade() -> None:
    op.drop_table(TABLE)
