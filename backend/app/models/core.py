"""Core domain tables: seasons, gameweeks, teams, players, prices, fixtures, stats."""
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Season(Base):
    """Mùa giải + luật của mùa đó, lấy nguyên văn từ FPL game_config.

    Không ghi cứng tên mùa hay luật trong code: ingestion ghi vào đây, engine đọc
    ra qua app.scoring.load_rules().
    """
    __tablename__ = "seasons"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(16), unique=True)  # vd "2026/27"
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    scoring_source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # game_config nguyên văn (scoring + rules + settings)
    rules_json: Mapped[str | None] = mapped_column(String, nullable=True)
    # danh sách chip kèm khoảng gameweek (hai bộ cho hai nửa mùa)
    chips_json: Mapped[str | None] = mapped_column(String, nullable=True)
    # vân tay của luật; chỉ đổi khi FPL đổi luật
    rules_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # thời điểm luật thay đổi lần gần nhất (không phải mỗi lần đồng bộ)
    rules_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Gameweek(Base):
    __tablename__ = "gameweeks"
    id: Mapped[int] = mapped_column(primary_key=True)  # FPL event id (1..38)
    name: Mapped[str] = mapped_column(String(32))
    deadline_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    is_next: Mapped[bool] = mapped_column(Boolean, default=False)
    is_previous: Mapped[bool] = mapped_column(Boolean, default=False)
    finished: Mapped[bool] = mapped_column(Boolean, default=False)
    data_checked: Mapped[bool] = mapped_column(Boolean, default=False)
    average_entry_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    highest_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # blank / double detection is computed from fixtures, cached here
    fixture_count_by_team: Mapped[str | None] = mapped_column(String, nullable=True)


class Team(Base):
    __tablename__ = "teams"
    id: Mapped[int] = mapped_column(primary_key=True)  # FPL team id
    name: Mapped[str] = mapped_column(String(64))
    short_name: Mapped[str] = mapped_column(String(8))
    code: Mapped[int] = mapped_column(Integer)
    # FPL strength ratings (1000-1400 scale)
    strength: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strength_overall_home: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strength_overall_away: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strength_attack_home: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strength_attack_away: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strength_defence_home: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strength_defence_away: Mapped[int | None] = mapped_column(Integer, nullable=True)

    players: Mapped[list["Player"]] = relationship(back_populates="team")


class Player(Base):
    __tablename__ = "players"
    id: Mapped[int] = mapped_column(primary_key=True)  # FPL element id
    code: Mapped[int] = mapped_column(Integer, index=True)
    first_name: Mapped[str] = mapped_column(String(64))
    second_name: Mapped[str] = mapped_column(String(64))
    web_name: Mapped[str] = mapped_column(String(64), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    element_type: Mapped[int] = mapped_column(Integer, index=True)  # 1 GK..4 FWD

    now_cost: Mapped[int] = mapped_column(Integer)  # in tenths (55 = £5.5m)
    selected_by_percent: Mapped[float] = mapped_column(Float, default=0.0)
    transfers_in_event: Mapped[int] = mapped_column(Integer, default=0)
    transfers_out_event: Mapped[int] = mapped_column(Integer, default=0)

    # availability
    status: Mapped[str] = mapped_column(String(2), default="a")  # a/d/i/s/u/n
    chance_of_playing_next_round: Mapped[int | None] = mapped_column(Integer, nullable=True)
    news: Mapped[str | None] = mapped_column(String, nullable=True)
    news_added: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # season-to-date totals
    total_points: Mapped[int] = mapped_column(Integer, default=0)
    minutes: Mapped[int] = mapped_column(Integer, default=0)
    starts: Mapped[int] = mapped_column(Integer, default=0)
    goals_scored: Mapped[int] = mapped_column(Integer, default=0)
    assists: Mapped[int] = mapped_column(Integer, default=0)
    clean_sheets: Mapped[int] = mapped_column(Integer, default=0)
    goals_conceded: Mapped[int] = mapped_column(Integer, default=0)
    saves: Mapped[int] = mapped_column(Integer, default=0)
    yellow_cards: Mapped[int] = mapped_column(Integer, default=0)
    red_cards: Mapped[int] = mapped_column(Integer, default=0)
    bonus: Mapped[int] = mapped_column(Integer, default=0)
    bps: Mapped[int] = mapped_column(Integer, default=0)
    penalties_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    corners_and_indirect_freekicks_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    direct_freekicks_order: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # underlying (FPL-provided expected stats, season totals)
    expected_goals: Mapped[float] = mapped_column(Float, default=0.0)
    expected_assists: Mapped[float] = mapped_column(Float, default=0.0)
    expected_goal_involvements: Mapped[float] = mapped_column(Float, default=0.0)
    expected_goals_conceded: Mapped[float] = mapped_column(Float, default=0.0)
    # defensive contribution stat (2025/26)
    defensive_contribution: Mapped[float] = mapped_column(Float, default=0.0)
    recoveries: Mapped[int] = mapped_column(Integer, default=0)
    tackles: Mapped[int] = mapped_column(Integer, default=0)
    clearances_blocks_interceptions: Mapped[int] = mapped_column(Integer, default=0)

    form: Mapped[float] = mapped_column(Float, default=0.0)
    points_per_game: Mapped[float] = mapped_column(Float, default=0.0)
    ep_next: Mapped[float | None] = mapped_column(Float, nullable=True)  # FPL's own estimate
    photo_code: Mapped[str | None] = mapped_column(String(32), nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    team: Mapped["Team"] = relationship(back_populates="players")


class PlayerPrice(Base):
    """Price history snapshots (for value / price-change tracking)."""
    __tablename__ = "player_prices"
    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    now_cost: Mapped[int] = mapped_column(Integer)
    selected_by_percent: Mapped[float] = mapped_column(Float)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Fixture(Base):
    __tablename__ = "fixtures"
    id: Mapped[int] = mapped_column(primary_key=True)  # FPL fixture id
    event: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)  # gameweek
    kickoff_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    team_h: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    team_a: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    team_h_difficulty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    team_a_difficulty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    team_h_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    team_a_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    finished: Mapped[bool] = mapped_column(Boolean, default=False)
    started: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (UniqueConstraint("id", name="uq_fixture_id"),)


class PlayerGameweekStat(Base):
    """Per-gameweek actuals for a player (from element-summary history / live)."""
    __tablename__ = "player_gameweek_stats"
    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    gameweek: Mapped[int] = mapped_column(Integer, index=True)
    fixture_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    opponent_team: Mapped[int | None] = mapped_column(Integer, nullable=True)
    was_home: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    minutes: Mapped[int] = mapped_column(Integer, default=0)
    total_points: Mapped[int] = mapped_column(Integer, default=0)
    goals_scored: Mapped[int] = mapped_column(Integer, default=0)
    assists: Mapped[int] = mapped_column(Integer, default=0)
    clean_sheets: Mapped[int] = mapped_column(Integer, default=0)
    goals_conceded: Mapped[int] = mapped_column(Integer, default=0)
    saves: Mapped[int] = mapped_column(Integer, default=0)
    bonus: Mapped[int] = mapped_column(Integer, default=0)
    bps: Mapped[int] = mapped_column(Integer, default=0)
    expected_goals: Mapped[float] = mapped_column(Float, default=0.0)
    expected_assists: Mapped[float] = mapped_column(Float, default=0.0)
    starts: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("player_id", "fixture_id", name="uq_player_fixture"),
    )
