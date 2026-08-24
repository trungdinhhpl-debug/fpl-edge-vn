"""fixtures.finished_provisional: biết một trận đã đá xong TRƯỚC khi FPL data-check

`Fixture.finished` của FPL chỉ bật khi cả vòng được data-check, nhiều ngày sau
khi bóng đã lăn (đo 2026-08-22: Arsenal 3-0 tối 21/08, bonus đã chốt 3/2/1, vẫn
`finished=false`). Trong khoảng chờ đó `stats_season()` tưởng mùa chưa bắt đầu
và quy đổi BPS thừa một lần, còn cả trang vẫn dán nhãn PRE-SEASON.

Revision ID: 0005_fixture_finished_provisional
Revises: 0004_captain_picks
Create Date: 2026-08-22
"""
import sqlalchemy as sa
from alembic import op

revision = "0005_fixture_finished_provisional"
down_revision = "0004_captain_picks"
branch_labels = None
depends_on = None

TABLE = "fixtures"
COLUMN = "finished_provisional"


def upgrade() -> None:
    # 0001 dựng bảng từ metadata ORM, nên cơ sở dữ liệu mới đã có sẵn cột này;
    # `db.ensure_columns()` cũng thêm nó khi ứng dụng khởi động. Bước này chỉ để
    # đường alembic không lệch với đường runtime.
    inspector = sa.inspect(op.get_bind())
    if TABLE not in inspector.get_table_names():
        return
    if COLUMN in {c["name"] for c in inspector.get_columns(TABLE)}:
        return
    # Nullable: NULL ở hàng cũ nghĩa là "chưa đồng bộ trường này", không phải
    # "chưa đá" — phía đọc OR với `finished` nên không diễn dịch nhầm.
    op.add_column(TABLE, sa.Column(COLUMN, sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column(TABLE, COLUMN)
