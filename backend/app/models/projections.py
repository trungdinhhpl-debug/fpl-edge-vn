"""Model output tables: expected minutes, per-GW projections, model versions.

Every projection is traceable to a model version, a data cutoff time and a
gameweek (spec §20: "Mỗi projection phải liên kết với model version, data
cutoff time, gameweek, source snapshot, confidence interval").
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ProjectionSnapshot(Base):
    """Dự báo ĐÓNG BĂNG tại deadline, cùng kết quả thật đổ vào sau.

    Vì sao phải có bảng riêng: `player_projections` bị **xoá và ghi lại** mỗi lần
    chạy engine (`engine/projections.py`), nên dự báo đưa ra trước deadline không
    còn tồn tại sau đó. Không có bảng này thì mọi chỉ số chất lượng mô hình (MAE,
    RMSE, Spearman, Brier, calibration) **vĩnh viễn không đo được** — không phải
    "chưa đo", mà là không bao giờ đo được, vì cái cần so đã bị ghi đè.

    Đây cũng là chỗ chống data leakage: sau khi deadline qua, bản ghi bị **khoá**
    (`is_locked`), nên một lần chạy engine muộn hơn — lúc đã biết đội hình ra sân,
    biết ai chấn thương — không thể lặng lẽ sửa lại "dự báo" cho đẹp điểm.

    Baseline được chụp ở CÙNG thời điểm. So mô hình hôm nay với baseline lấy dữ
    liệu tuần sau là so gian lận, nên `baseline_form` (chỉ số `form` của chính FPL)
    phải được đóng băng cùng lúc chứ không đọc lại khi tính điểm.
    """

    __tablename__ = "projection_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    # tên mùa: snapshot phải sống qua giao mùa, mà gameweek thì lặp lại 1..38
    season: Mapped[str] = mapped_column(String(16), index=True)
    gameweek: Mapped[int] = mapped_column(Integer, index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)

    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    deadline_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # True khi deadline đã qua: từ đó bản ghi không được sửa nữa
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    model_version: Mapped[str] = mapped_column(String(32))

    # ---- dự báo của mô hình ----
    xp: Mapped[float] = mapped_column(Float, default=0.0)
    p_start: Mapped[float] = mapped_column(Float, default=0.0)
    p_haul: Mapped[float] = mapped_column(Float, default=0.0)      # P(≥10 điểm)
    xmins: Mapped[float] = mapped_column(Float, default=0.0)

    # ---- baseline, chụp cùng thời điểm ----
    # `form` của chính FPL (điểm trung bình mấy trận gần nhất) — dự báo điểm sẵn có
    baseline_form: Mapped[float | None] = mapped_column(Float, nullable=True)
    # xP tính với sức mạnh đội LẤY HOÀN TOÀN TỪ KÈO (market_weight = 1.0)
    baseline_market_xp: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ---- kết quả thật, đổ vào sau khi vòng đấu kết thúc ----
    actual_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_started: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    outcome_filled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "season", "gameweek", "player_id", name="uq_snapshot_season_gw_player"
        ),
    )


class ModelVersion(Base):
    __tablename__ = "model_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # xp | xmins | montecarlo
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ExpectedMinutes(Base):
    __tablename__ = "expected_minutes"
    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    gameweek: Mapped[int] = mapped_column(Integer, index=True)
    xmins: Mapped[float] = mapped_column(Float, default=0.0)
    p_start: Mapped[float] = mapped_column(Float, default=0.0)
    p_sub: Mapped[float] = mapped_column(Float, default=0.0)
    p_no_play: Mapped[float] = mapped_column(Float, default=0.0)
    p_60_plus: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[str] = mapped_column(String(8), default="Medium")  # Low/Medium/High
    ci_low: Mapped[float] = mapped_column(Float, default=0.0)
    ci_high: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    model_version: Mapped[str] = mapped_column(String(32))
    data_cutoff: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("player_id", "gameweek", name="uq_xmins_player_gw"),
    )


class PlayerProjection(Base):
    __tablename__ = "player_projections"
    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    gameweek: Mapped[int] = mapped_column(Integer, index=True)

    # expected points decomposition (spec §7 transparency)
    xp: Mapped[float] = mapped_column(Float, default=0.0)
    xp_appearance: Mapped[float] = mapped_column(Float, default=0.0)
    xp_goals: Mapped[float] = mapped_column(Float, default=0.0)
    xp_assists: Mapped[float] = mapped_column(Float, default=0.0)
    xp_clean_sheet: Mapped[float] = mapped_column(Float, default=0.0)
    xp_saves: Mapped[float] = mapped_column(Float, default=0.0)
    xp_bonus: Mapped[float] = mapped_column(Float, default=0.0)
    xp_defcon: Mapped[float] = mapped_column(Float, default=0.0)
    xp_negative: Mapped[float] = mapped_column(Float, default=0.0)

    xmins: Mapped[float] = mapped_column(Float, default=0.0)
    p_start: Mapped[float] = mapped_column(Float, default=0.0)
    clean_sheet_prob: Mapped[float] = mapped_column(Float, default=0.0)
    goal_prob: Mapped[float] = mapped_column(Float, default=0.0)
    assist_prob: Mapped[float] = mapped_column(Float, default=0.0)

    # Monte Carlo distribution summary
    mc_mean: Mapped[float] = mapped_column(Float, default=0.0)
    mc_median: Mapped[float] = mapped_column(Float, default=0.0)
    mc_p25: Mapped[float] = mapped_column(Float, default=0.0)
    mc_p75: Mapped[float] = mapped_column(Float, default=0.0)
    mc_p90: Mapped[float] = mapped_column(Float, default=0.0)
    mc_ceiling: Mapped[float] = mapped_column(Float, default=0.0)  # p95
    p_blank: Mapped[float] = mapped_column(Float, default=0.0)     # <=2 pts
    p_returns: Mapped[float] = mapped_column(Float, default=0.0)   # >=5 pts (haul-ish)
    p_haul: Mapped[float] = mapped_column(Float, default=0.0)      # >=10 pts
    # Nullable so db.ensure_columns() can add it to a live table — it only adds
    # NULL-able columns, and a NOT NULL add would fail on the existing rows.
    p_15: Mapped[float | None] = mapped_column(Float, nullable=True, default=0.0)
    variance: Mapped[float] = mapped_column(Float, default=0.0)

    # difficulty context
    fixture_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    opponent_team: Mapped[int | None] = mapped_column(Integer, nullable=True)
    was_home: Mapped[bool | None] = mapped_column(Integer, nullable=True)
    n_fixtures: Mapped[int] = mapped_column(Integer, default=1)  # 0 blank, 2 double

    # risk / confidence
    confidence: Mapped[float] = mapped_column(Float, default=0.5)  # 0..1
    minutes_risk: Mapped[str] = mapped_column(String(12), default="Medium")
    performance_risk: Mapped[str] = mapped_column(String(12), default="Medium")
    overall_risk: Mapped[str] = mapped_column(String(12), default="Medium")

    model_version: Mapped[str] = mapped_column(String(32))
    data_cutoff: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("player_id", "gameweek", name="uq_proj_player_gw"),
    )
