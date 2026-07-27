"""Experts, news/injuries, users, optimization runs, source logging."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


# ---------------------------------------------------------------- experts ----
class ExpertSource(Base):
    __tablename__ = "expert_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    source_type: Mapped[str] = mapped_column(String(32))  # site | analyst | community | journalist
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    reliability: Mapped[float] = mapped_column(Float, default=0.5)       # 0..1
    historical_accuracy: Mapped[float] = mapped_column(Float, default=0.5)
    expertise: Mapped[str | None] = mapped_column(String(128), nullable=True)
    independence: Mapped[float] = mapped_column(Float, default=1.0)      # echo-chamber discount
    verified_track_record: Mapped[bool] = mapped_column(Boolean, default=False)
    last_updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExpertSignal(Base):
    __tablename__ = "expert_signals"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("expert_sources.id"), index=True)
    player_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    gameweek: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    # start|injury|setpiece|penalty|captain|buy|sell|hold|avoid|differential|freehit
    signal_type: Mapped[str] = mapped_column(String(24))
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # computed signal score = reliability × recency × specificity × accuracy × independence
    signal_score: Mapped[float] = mapped_column(Float, default=0.0)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=True)  # clearly label model/demo data


# ------------------------------------------------------------ news/injury ----
class InjuryReport(Base):
    __tablename__ = "injury_reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(2), default="d")
    chance_of_playing: Mapped[int | None] = mapped_column(Integer, nullable=True)
    impact: Mapped[str] = mapped_column(String(12), default="Medium")  # Critical/High/Medium/Low
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    news: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_name: Mapped[str] = mapped_column(String(64), default="FPL")
    source_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SetPieceRole(Base):
    __tablename__ = "set_piece_roles"
    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(Integer, index=True)
    penalties_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    corners_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    freekicks_order: Mapped[int | None] = mapped_column(Integer, nullable=True)


# -------------------------------------------------------- source fetch log ----
class SourceFetchLog(Base):
    __tablename__ = "source_fetch_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_name: Mapped[str] = mapped_column(String(64), index=True)
    source_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_type: Mapped[str] = mapped_column(String(32), default="official")
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok | error | stale
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    rows: Mapped[int] = mapped_column(Integer, default=0)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# --------------------------------------------------- market odds (Tier-2) -----
class MarketOdds(Base):
    """Bookmaker-implied expected goals per fixture (spec §3 Tier-2).

    Written by ingestion so request-time code never calls the odds API
    (protects the monthly quota). `is_market=False` would mean a model estimate.
    """
    __tablename__ = "market_odds"
    id: Mapped[int] = mapped_column(primary_key=True)
    fixture_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    gameweek: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    team_h: Mapped[int] = mapped_column(Integer)
    team_a: Mapped[int] = mapped_column(Integer)
    lam_home: Mapped[float] = mapped_column(Float)
    lam_away: Mapped[float] = mapped_column(Float)
    p_home: Mapped[float] = mapped_column(Float, default=0.0)
    p_draw: Mapped[float] = mapped_column(Float, default=0.0)
    p_away: Mapped[float] = mapped_column(Float, default=0.0)
    total_goals: Mapped[float] = mapped_column(Float, default=0.0)
    n_bookmakers: Mapped[int] = mapped_column(Integer, default=0)
    source_name: Mapped[str] = mapped_column(String(32), default="odds_api")
    is_market: Mapped[bool] = mapped_column(Boolean, default=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ------------------------------------- Championship (đội mới lên hạng) --------
class ChampionshipStats(Base):
    """Thành tích Championship mùa trước của các đội vừa lên hạng (tuỳ chọn).

    Chỉ dùng để xếp hạng ba đội mới lên hạng so với nhau — xem
    app/providers/championship.py. Tắt bằng CHAMPIONSHIP_ENABLED=false.
    """
    __tablename__ = "championship_stats"
    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)  # FPL team id
    source_team_name: Mapped[str] = mapped_column(String(64))
    season: Mapped[str] = mapped_column(String(16))
    played: Mapped[int] = mapped_column(Integer, default=0)
    goals_for: Mapped[int] = mapped_column(Integer, default=0)
    goals_against: Mapped[int] = mapped_column(Integer, default=0)
    attack_index: Mapped[float] = mapped_column(Float, default=1.0)   # so với TB Championship
    defence_index: Mapped[float] = mapped_column(Float, default=1.0)
    source_name: Mapped[str] = mapped_column(String(64), default="football-data.co.uk")
    source_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# --------------------------------------------------------------- users --------
class UserProfile(Base):
    __tablename__ = "user_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    fpl_team_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    player_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    team_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    overall_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bank: Mapped[int] = mapped_column(Integer, default=0)          # tenths
    team_value: Mapped[int] = mapped_column(Integer, default=1000)  # tenths
    free_transfers: Mapped[int] = mapped_column(Integer, default=1)
    last_synced: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ------------------------------------------------------ optimization runs -----
class OptimizationRun(Base):
    __tablename__ = "optimization_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(24))  # next_gw | long_term | free_hit | wildcard
    fpl_team_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_gw: Mapped[int] = mapped_column(Integer)
    horizon: Mapped[int] = mapped_column(Integer, default=1)
    params_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
