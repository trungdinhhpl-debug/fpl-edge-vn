"""Model output tables: expected minutes, per-GW projections, model versions.

Every projection is traceable to a model version, a data cutoff time and a
gameweek (spec §20: "Mỗi projection phải liên kết với model version, data
cutoff time, gameweek, source snapshot, confidence interval").
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


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
