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
    UniqueConstraint,
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
    # The PRIMARY statement this traces back to (e.g. "presser:ARS:gw3"). Signals
    # sharing one are echoes of a single source: eight posts relaying one manager
    # quote are one piece of evidence, not eight. Nullable so ensure_columns can
    # add it to a live table, and because an original take has no upstream.
    origin_ref: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)


class ExpertTrackRecord(Base):
    """A concrete prediction a source made, scored once the outcome is known.

    Accuracy shown on the experts page comes from HERE and nowhere else. Seeding
    an `historical_accuracy` figure would attach an invented performance claim to
    a named, real person; a source with no scored sample shows "chưa đủ dữ liệu"
    instead of a number it did not earn.
    """
    __tablename__ = "expert_track_record"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("expert_sources.id"), index=True)
    domain: Mapped[str] = mapped_column(String(24), index=True)  # see EXPERTISE_DOMAINS
    gameweek: Mapped[int] = mapped_column(Integer, index=True)
    player_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    claim: Mapped[str] = mapped_column(Text)
    # None = not yet resolved; the UI must not count pending as either way
    correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


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


# ------------------------------------- player goal odds (Tier-2, theo trận) ----
class PlayerGoalOdds(Base):
    """Kèo "ghi bàn bất kỳ lúc nào" cho MỘT cầu thủ trong MỘT trận.

    Ghi bởi ingestion nên code lúc xử lý request không bao giờ gọi API (bảo vệ
    quota tháng), giống `market_odds`. Khác `market_odds` ở chỗ khoá duy nhất là
    CẶP (fixture_id, player_id) chứ không phải fixture_id.

    Lưu cả số nhà cái có đủ hai cửa Yes/No (`n_devigged`): chỉ giá đã loại biên mới
    được đưa vào xP, phần còn lại chỉ để hiển thị tham khảo. Xem
    app/engine/goal_market.py.
    """

    __tablename__ = "player_goal_odds"
    id: Mapped[int] = mapped_column(primary_key=True)
    fixture_id: Mapped[int] = mapped_column(Integer, index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    gameweek: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)

    # Xác suất đồng thuận (trung vị giữa các nhà cái ĐÃ loại biên trong nội bộ từng
    # nhà cái). KHÔNG chuẩn hoá giữa các cầu thủ — nhiều người cùng ghi bàn được.
    prob: Mapped[float] = mapped_column(Float, default=0.0)
    # Xác suất trung vị của TẤT CẢ giá, kể cả chưa loại biên — chỉ để hiển thị.
    prob_raw: Mapped[float | None] = mapped_column(Float, nullable=True)
    n_bookmakers: Mapped[int] = mapped_column(Integer, default=0)
    n_devigged: Mapped[int] = mapped_column(Integer, default=0)
    best_price_yes: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_price_no: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Điều kiện áp dụng: unknown | appears | any (xem engine/goal_market.py).
    # "unknown" là mặc định và nghĩa là CHỈ THAM KHẢO, không vào xP.
    condition: Mapped[str] = mapped_column(String(16), default="unknown")
    source_name: Mapped[str] = mapped_column(String(48), default="odds_api")
    bookmaker_names: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Thời điểm nhà cái cập nhật giá (khác thời điểm ta lấy về).
    market_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("fixture_id", "player_id", name="uq_goal_odds_fixture_player"),
    )


# ------------------------------- bản ghi ghi bàn theo cầu thủ & theo trận ------
class PlayerFixtureGoals(Base):
    """Số bàn kỳ vọng của MỘT cầu thủ trong MỘT trận, trước và sau điều chỉnh.

    Đây là bảng mà phần nâng cấp xP(1) đứng trên: mọi thứ ở cấp **từng trận**, nên
    vòng đôi là hai dòng rồi cộng lại, còn vòng trắng là không có dòng nào (xP = 0).
    Bị **xoá và ghi lại** cho mỗi vòng trong tầm dự báo mỗi lần chạy engine, cùng
    nhịp với `player_projections` — đó là lý do làm mới nhiều lần không bao giờ cộng
    dồn điều chỉnh.

    Trường nào chưa có nguồn thì để **NULL**, không điền số bịa: `tactical_role`
    null nghĩa là chưa biết vai trò, và khi đó `position_factor` đúng bằng 1.0.
    """

    __tablename__ = "player_fixture_goals"
    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    fixture_id: Mapped[int] = mapped_column(Integer, index=True)
    gameweek: Mapped[int] = mapped_column(Integer, index=True)
    # Vị trí FPL để tính điểm (1 GK, 2 DEF, 3 MID, 4 FWD) — nguồn của điểm/bàn.
    element_type: Mapped[int] = mapped_column(Integer, default=0)
    opponent_team: Mapped[int | None] = mapped_column(Integer, nullable=True)
    was_home: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # ---- vai trò chiến thuật (NULL = chưa có nguồn, KHÔNG suy từ GK/DEF/MID/FWD) --
    tactical_role: Mapped[str | None] = mapped_column(String(8), nullable=True)
    role_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ---- phút thi đấu theo kịch bản ----
    p_start: Mapped[float] = mapped_column(Float, default=0.0)
    p_sub: Mapped[float] = mapped_column(Float, default=0.0)
    minutes_if_start: Mapped[float | None] = mapped_column(Float, nullable=True)
    minutes_if_sub: Mapped[float | None] = mapped_column(Float, nullable=True)
    xmins: Mapped[float] = mapped_column(Float, default=0.0)

    # ---- số bàn kỳ vọng, tách bóng sống / bóng chết / penalty ----
    exp_goals_model: Mapped[float] = mapped_column(Float, default=0.0)
    exp_goals_open: Mapped[float] = mapped_column(Float, default=0.0)
    exp_goals_penalty: Mapped[float] = mapped_column(Float, default=0.0)
    # Bóng chết (không tính 11m) chưa có nguồn tách riêng trong dữ liệu FPL ->
    # NULL, không phải 0: "không đo được" khác "đo được và bằng 0".
    exp_goals_set_piece: Mapped[float | None] = mapped_column(Float, nullable=True)
    exp_goals_adjusted: Mapped[float] = mapped_column(Float, default=0.0)
    exp_goals_final: Mapped[float] = mapped_column(Float, default=0.0)

    # ---- thị trường ----
    market_prob: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_exp_goals: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_r90: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_condition: Mapped[str | None] = mapped_column(String(16), nullable=True)
    market_usable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    market_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_source: Mapped[str | None] = mapped_column(String(48), nullable=True)
    market_n_bookmakers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    market_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    market_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---- điểm yếu đối thủ theo trục vị trí ----
    weakness_axis: Mapped[str | None] = mapped_column(String(32), nullable=True)
    axis_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    position_factor: Mapped[float] = mapped_column(Float, default=1.0)
    finishing_share: Mapped[float | None] = mapped_column(Float, nullable=True)
    weakness_sample_90s: Mapped[float | None] = mapped_column(Float, nullable=True)
    weakness_reliability: Mapped[str | None] = mapped_column(String(24), nullable=True)
    weakness_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---- ảnh hưởng lên xP của CHÍNH trận này ----
    goal_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    xp_before: Mapped[float] = mapped_column(Float, default=0.0)
    xp_after: Mapped[float] = mapped_column(Float, default=0.0)
    xp_delta: Mapped[float] = mapped_column(Float, default=0.0)

    model_version: Mapped[str] = mapped_column(String(32), default="")
    data_cutoff: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "player_id", "fixture_id", name="uq_player_fixture_goals"
        ),
    )


# ----------------------------------------- vai trò chiến thuật của cầu thủ ----
class PlayerRole(Base):
    """Vai trò chiến thuật (LW/RW/ST/AM/CM/DM/LB/RB/LWB/RWB/CB/GK) của một cầu thủ.

    FPL API KHÔNG có dữ liệu này, và nó **không được suy ra từ nhãn GK/DEF/MID/FWD**
    — xem `app/engine/roles.py`. Bảng này giữ vai trò lấy từ nguồn ngoài đã khai báo
    (hiện là API công khai của Premier League, `app/providers/pl_positions.py`).

    `role` cho phép NULL **có chủ ý**: nguồn có những nhãn không quy được về một vai
    trò (`"Left/Right Winger"` — đá cả hai biên) hoặc chỉ thô bằng FPL
    (`"Midfielder"`). Những hàng đó vẫn được ghi lại kèm `raw_label` và `reason`, để
    giao diện nói được "nguồn có nói, nhưng nói không đủ rõ" thay vì im lặng — hai
    tình huống rất khác nhau với người đọc.
    """

    __tablename__ = "player_roles"
    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id"), unique=True, index=True
    )
    role: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # Nhãn nguyên văn của nguồn, giữ lại để đối chiếu khi nguồn đổi cách viết.
    raw_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Tên và CLB theo nguồn — dùng để kiểm tra khớp tên có đúng người không.
    source_name: Mapped[str] = mapped_column(String(64), default="")
    source_url: Mapped[str | None] = mapped_column(String(256), nullable=True)
    external_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    external_club: Mapped[str | None] = mapped_column(String(64), nullable=True)
    season_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
