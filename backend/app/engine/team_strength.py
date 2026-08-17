"""BƯỚC 2 + BƯỚC 3 — từ prior đội bóng ra λ của TỪNG TRẬN.

BƯỚC 2 — mô hình log tuyến tính. Số bàn kỳ vọng của một đội trong một trận là
**tích** của nhiều hệ số, nên chỗ đúng để cộng chúng là thang log:

    log λ(A tấn công B, A ở sân nhà) =
          log(nền của giải)
        + sức tấn công của A
        + độ hở hàng thủ của B
        + lợi thế sân nhà
        + sức mạnh đội hình (A khoẻ − B khoẻ)
        + chênh lệch ngày nghỉ
        + mật độ thi đấu
        + điều chỉnh chuyển nhượng
        + điều chỉnh huấn luyện viên / hệ thống
        + điều chỉnh đội mới lên hạng

λ_against của A **không** được viết bằng một công thức thứ hai. Nó là chính hàm
trên gọi ngược lại: `λ_against(A) = λ(B tấn công A, B ở sân khách)`. Bản trước có
hai nhánh song song cho hai chiều, và hai nhánh song song là hai chỗ để lệch nhau
— đúng một lần lệch dấu lợi thế sân nhà là đủ hỏng cả bảng độ khó.

BƯỚC 3 — hiệu chuẩn theo thị trường. λ từ kèo nhà cái đã được khớp bằng
Dixon–Coles trên 1X2 + tài/xỉu + kèo châu Á ở `providers/probability.py` (chạy lúc
đồng bộ, không tốn quota lúc tải trang). Ở đây chúng được ghép với λ cấu trúc bằng
**trung bình hình học**:

    λ_cuối = λ_thị_trường^w · λ_cấu_trúc^(1−w)

Hình học chứ không phải số học, vì cả mô hình lẫn thị trường đều sống trong thang
log — trộn số học ở đây là trộn ở một thang khác với thang đã dựng ra hai số đó.
`w` phụ thuộc **độ trưởng thành và thanh khoản** của thị trường: bao nhiêu nhà cái
đã ra giá, và trận còn cách bao xa.

Ngoài blend từng trận còn một bước nữa: những trận **có** giá được dùng để đo độ
lệch hệ thống của mô hình cấu trúc so với thị trường, rồi hệ số đó áp cho **tất
cả** các trận — kể cả trận chưa có giá. Đó mới là "calibration": thông tin thị
trường được lan sang phần lịch mà thị trường chưa chạm tới.

Quy ước hướng chỉ số giống `prior_strength.py`: `attack` cao = ghi nhiều;
`defence` cao = thủng ít. Trung bình giải = 1.0.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import timedelta

from app.engine.prior_strength import (  # noqa: F401  (re-export cho code cũ)
    NO_HISTORY_RATIO,
    PROMOTED_ATTACK,
    PROMOTED_DEFENCE,
    Observation,
    TeamPrior,
    build_priors,
    clamp_index,
)

# Nền của giải: số bàn một đội ghi trong một trận. Được khớp lại từ các trận đã đá
# khi có đủ mẫu; con số dưới đây chỉ là điểm khởi đầu.
LEAGUE_AVG_GOALS = 1.42
MIN_FIXTURES_FOR_BASELINE = 40

# Lợi thế sân nhà dưới dạng MỘT hệ số: nhân cho đội nhà, chia cho đội khách. Bản
# trước dùng hai hằng số rời (1.12 và 0.90) nên tích của chúng không bằng 1 và
# tổng số bàn của giải bị lệch nhẹ theo tỷ lệ nhà/khách của lịch.
HOME_ADV = 1.12          # giữ lại để code cũ import không gãy
AWAY_ADJ = 0.90
DEFAULT_HOME_FACTOR = math.sqrt(HOME_ADV / AWAY_ADJ)   # ≈ 1.1155

SHRINK_K = 6.0           # số trận để dữ liệu trong mùa lấn át prior
MIN_LAMBDA = 0.15
MAX_LAMBDA = 4.5

# Cỡ mẫu (phút thi đấu của cả đội) mà tại đó chỉ số suy ra từ tổng cả mùa được
# tin ở mức đầy đủ. Một vòng đấu không được phép làm một đội trông như ứng viên
# vô địch.
HISTORY_SHRINK_MINUTES = 8000

# --- hệ số của các số hạng BƯỚC 2 không khớp được từ dữ liệu trong DB ---------
# Cả hai đều CỐ Ý nhỏ và bị chặn hai đầu. Chúng tôi không có mẫu để khớp chúng ở
# đây (trước vòng 1 thì không có trận nào; trong mùa thì 380 trận vẫn quá ít để
# tách hiệu ứng ngày nghỉ khỏi chất lượng đội), nên chúng được đặt ở mức mà kể cả
# khi sai hoàn toàn cũng không đảo được thứ tự độ khó. Đặt về 0 để tắt hẳn.
REST_COEF_PER_DAY = 0.010        # mỗi ngày nghỉ nhiều hơn đối thủ
MAX_REST_DIFF_DAYS = 4.0
CONGESTION_COEF_PER_MATCH = 0.020
CONGESTION_WINDOW_DAYS = 14
CONGESTION_FREE_MATCHES = 2      # số trận trong 14 ngày coi là bình thường
# Đội hình: log(mức sẵn sàng) được nhân hệ số này. 1.0 = mất 10% giá trị đội hình
# thì mất 10% sản lượng — giả định tuyến tính, cố ý không phóng đại.
LINEUP_ELASTICITY = 1.0

# --- BƯỚC 3 -------------------------------------------------------------------
# Số trận có giá tối thiểu trước khi cho phép hiệu chuẩn toàn cục. Dưới mức này
# thì "độ lệch hệ thống" chỉ là nhiễu của một vài trận.
MIN_FIXTURES_FOR_CALIBRATION = 6
# Trần của hệ số hiệu chuẩn. Lệch quá mức này nghĩa là mô hình và thị trường đang
# mâu thuẫn nặng, và ép khớp sẽ giấu mâu thuẫn đó đi thay vì phơi nó ra.
CALIBRATION_CLAMP = 0.18         # tối đa ±18% trên thang log
# Trận càng xa thì giá càng mỏng và càng dễ đổi. Trong ngần này ngày = trọng số
# đầy đủ; xa hơn thì giảm tuyến tính xuống sàn.
MARKET_MATURE_DAYS = 10.0
MARKET_HORIZON_DAYS = 45.0
MARKET_MIN_MATURITY = 0.45


# ------------------------------------------------------- tương thích ngược ---
def _defence_proxy(players: list) -> dict[int, float]:
    """xGC/90 của thủ môn đá nhiều nhất mỗi đội. Xem `prior_strength._gk_xgc90`."""
    from app.engine.prior_strength import _gk_xgc90
    from app.services.season_state import new_signing_players

    return _gk_xgc90(players, new_signing_players())


def _manager_factor(team) -> float:
    """Hệ số còn lại của dữ liệu mùa trước khi CLB đã đổi huấn luyện viên.

    Dùng lại `prior_weight_new_manager` — đúng hệ số mà mô hình xMins đã dùng cho
    từng cầu thủ của các CLB đó. Một sự thật ("đội này đổi HLV nên dữ liệu cũ mô
    tả kém hơn") phải mang cùng một con số ở mọi nơi.

    Danh sách CLB đổi HLV do người vận hành khai (FPL API không công bố), nên danh
    sách rỗng nghĩa là CHƯA AI KHAI — khi đó không chiết khấu ai cả.
    """
    from app.config import settings
    from app.services.season_state import new_manager_clubs

    short = (getattr(team, "short_name", "") or "").upper()
    if short and short in new_manager_clubs():
        return settings.prior_weight_new_manager
    return 1.0


# ------------------------------------------------------------- bộ nạp DB -----
def load_market_map(db) -> dict[tuple[int, int], tuple[float, float]]:
    """(home_team_id, away_team_id) -> (lam_home, lam_away) từ kèo đã lưu.

    Đọc từ DB chứ không gọi API kèo, để mỗi lần tải trang không tốn quota.
    """
    from sqlalchemy import select

    from app.models import MarketOdds

    rows = db.scalars(select(MarketOdds)).all()
    return {(r.team_h, r.team_a): (r.lam_home, r.lam_away) for r in rows}


def load_market_support(db) -> dict[tuple[int, int], int]:
    """(home, away) -> số nhà cái đã ra giá cho trận đó.

    Đồng thuận của 20 nhà cái và giá lẻ của 2 nhà cái không phải cùng một loại
    bằng chứng.
    """
    from sqlalchemy import select

    from app.models import MarketOdds

    rows = db.scalars(select(MarketOdds)).all()
    return {(r.team_h, r.team_a): int(r.n_bookmakers or 0) for r in rows}


def load_market_maturity(db) -> dict[tuple[int, int], float]:
    """(home, away) -> độ trưởng thành của thị trường, 0..1.

    Giá của một trận đá tuần này và giá của một trận đá sau sáu tuần không đáng
    tin như nhau: trận xa có ít nhà cái treo bảng hơn, biên lợi nhuận rộng hơn,
    và bất kỳ tin đội hình nào cũng sẽ làm giá chạy trước khi bóng lăn. Đặc tả gọi
    đây là "market maturity/liquidity" và nó vào thẳng trọng số blend ở BƯỚC 3.

    Không biết ngày thi đấu thì trả về đầy đủ 1.0 — hạ trọng số vì THIẾU thông tin
    sẽ âm thầm làm yếu đi đúng những trận vốn có giá tốt.
    """
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.models import Fixture, MarketOdds

    kickoffs = {
        f.id: f.kickoff_time
        for f in db.scalars(select(Fixture)).all()
        if f.kickoff_time is not None
    }
    now = datetime.now(timezone.utc)
    out: dict[tuple[int, int], float] = {}
    for r in db.scalars(select(MarketOdds)).all():
        ko = kickoffs.get(r.fixture_id)
        if ko is None:
            out[(r.team_h, r.team_a)] = 1.0
            continue
        if ko.tzinfo is None:
            ko = ko.replace(tzinfo=timezone.utc)
        days = (ko - now).total_seconds() / 86400.0
        out[(r.team_h, r.team_a)] = _maturity_from_days(days)
    return out


def _maturity_from_days(days: float) -> float:
    if days <= MARKET_MATURE_DAYS:
        return 1.0
    if days >= MARKET_HORIZON_DAYS:
        return MARKET_MIN_MATURITY
    span = MARKET_HORIZON_DAYS - MARKET_MATURE_DAYS
    frac = (days - MARKET_MATURE_DAYS) / span
    return 1.0 - frac * (1.0 - MARKET_MIN_MATURITY)


def load_promoted_map(db) -> dict[int, tuple[float, float]]:
    """{team_id: (attack_index, defence_index)} từ Championship mùa trước."""
    from sqlalchemy import select

    from app.config import settings
    from app.models import ChampionshipStats

    if not settings.championship_enabled:
        return {}
    rows = db.scalars(select(ChampionshipStats)).all()
    return {r.team_id: (r.attack_index, r.defence_index) for r in rows}


def load_match_xg(db) -> list[Observation]:
    """xG theo TỪNG TRẬN, nguyên liệu duy nhất cho phép hiệu chỉnh đối thủ.

    Cộng `expected_goals` của các cầu thủ trong cùng một trận thành xG của đội
    trong trận đó. Bảng `player_gameweek_stats` chỉ có dữ liệu khi đã bật đồng bộ
    chi tiết (`sync_players_detail`), nên danh sách rỗng là chuyện bình thường —
    BƯỚC 1 tự rơi về tổng cả mùa và khai rõ là chưa hiệu chỉnh đối thủ.
    """
    from sqlalchemy import select

    from app.models import Fixture, PlayerGameweekStat

    fixtures = {
        f.id: f for f in db.scalars(select(Fixture).where(Fixture.finished.is_(True))).all()
    }
    if not fixtures:
        return []

    per_fixture_team: dict[tuple[int, int], float] = {}
    for s in db.scalars(select(PlayerGameweekStat)).all():
        f = fixtures.get(s.fixture_id)
        if f is None or s.was_home is None:
            continue
        tid = f.team_h if s.was_home else f.team_a
        key = (s.fixture_id, tid)
        per_fixture_team[key] = per_fixture_team.get(key, 0.0) + (s.expected_goals or 0.0)

    out: list[Observation] = []
    for (fid, tid), xg in per_fixture_team.items():
        f = fixtures[fid]
        opp = f.team_a if tid == f.team_h else f.team_h
        out.append(Observation(attacker=tid, defender=opp, value=xg, attacker_home=tid == f.team_h))
    return out


def market_observations(
    market: dict[tuple[int, int], tuple[float, float]],
) -> list[Observation]:
    """λ nhà cái dưới dạng quan sát, để BƯỚC 1 có thể hiệu chỉnh đối thủ."""
    out: list[Observation] = []
    for (home, away), (lam_h, lam_a) in market.items():
        out.append(Observation(home, away, lam_h, True))
        out.append(Observation(away, home, lam_a, False))
    return out


# ------------------------------------------------------------ kiểu dữ liệu ---
@dataclass
class TeamRates:
    team_id: int
    # đã chuẩn hoá (trung bình giải = 1.0)
    attack_home: float
    attack_away: float
    defence_home: float
    defence_away: float
    emp_xg_per_game: float      # tấn công thực nghiệm
    emp_xga_per_game: float     # phòng ngự thực nghiệm (bàn thua)
    matches: int
    # --- phần mới, phục vụ giải thích từng số hạng ---
    prior_attack: float = 1.0
    prior_defence: float = 1.0
    empirical_weight: float = 0.0     # trọng số của dữ liệu trong mùa
    availability: float = 1.0         # sức mạnh đội hình, 1.0 = đủ quân
    evidence_weight: float = 0.0      # tổng trọng số nguồn đã vào prior (BƯỚC 1)
    no_pl_history: bool = False
    promotion_cap_applied: bool = False


@dataclass
class LambdaTerms:
    """Phân rã `log λ` thành đúng các số hạng của BƯỚC 2. Tổng phải khớp λ."""

    baseline: float = 0.0
    attack: float = 0.0
    opponent_defence: float = 0.0
    home: float = 0.0
    lineup: float = 0.0
    rest: float = 0.0
    congestion: float = 0.0
    squad: float = 0.0
    manager: float = 0.0
    promotion: float = 0.0
    # BƯỚC 3
    structural: float = 0.0        # tổng các số hạng trên, SAU hiệu chuẩn toàn cục
    calibration: float = 0.0       # hiệu chuẩn toàn cục theo thị trường
    market: float | None = None    # log λ thị trường, None nếu trận không có giá
    market_weight: float = 0.0
    lam: float = 0.0
    notes: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "baseline": round(self.baseline, 4),
            "attack": round(self.attack, 4),
            "opponent_defence": round(self.opponent_defence, 4),
            "home": round(self.home, 4),
            "lineup": round(self.lineup, 4),
            "rest": round(self.rest, 4),
            "congestion": round(self.congestion, 4),
            "squad": round(self.squad, 4),
            "manager": round(self.manager, 4),
            "promotion": round(self.promotion, 4),
            "calibration": round(self.calibration, 4),
            "structural_lambda": round(math.exp(self.structural), 4),
            "market_lambda": round(math.exp(self.market), 4) if self.market is not None else None,
            "market_weight": round(self.market_weight, 3),
            "lambda": round(self.lam, 4),
            "notes": self.notes,
        }


# ------------------------------------------------------------------ engine ---
class TeamStrength:
    """Prior đội bóng + lịch thi đấu -> λ của từng trận."""

    def __init__(
        self,
        teams: list,
        players: list,
        finished_fixtures: list,
        market: dict[tuple[int, int], tuple[float, float]] | None = None,
        market_weight: float = 0.7,
        promoted: dict[int, tuple[float, float]] | None = None,
        promoted_damping: float = 0.35,
        market_support: dict[tuple[int, int], int] | None = None,
        full_support_books: int = 8,
        *,
        market_maturity: dict[tuple[int, int], float] | None = None,
        match_xg: list[Observation] | None = None,
        schedule: list | None = None,
    ) -> None:
        """`market` map (home_team_id, away_team_id) -> (lam_home, lam_away).

        `market_support` là số nhà cái sau mỗi trận và `market_maturity` là mức độ
        "đã chín" của thị trường trận đó (0..1); cả hai chỉ hạ trọng số blend, chưa
        bao giờ nâng. Bỏ trống cả hai = giữ nguyên hành vi cũ (trọng số đầy đủ ở
        mọi trận).

        `match_xg` là xG theo từng trận (xem `load_match_xg`) — có nó thì BƯỚC 1
        hiệu chỉnh được theo đối thủ. `schedule` là TOÀN BỘ lịch (kể cả trận chưa
        đá) để tính ngày nghỉ và mật độ thi đấu; thiếu nó thì hai số hạng đó bằng 0
        và được ghi rõ lý do trong phần giải thích.
        """
        self._rates: dict[int, TeamRates] = {}
        self._priors: dict[int, TeamPrior] = {}
        self._market = market or {}
        self._market_weight = market_weight
        self._market_support = market_support or {}
        self._market_maturity = market_maturity or {}
        self._full_support_books = max(1, full_support_books)
        self._promoted = promoted or {}
        self._promoted_damping = promoted_damping
        self._baseline = LEAGUE_AVG_GOALS
        self._home_factor = DEFAULT_HOME_FACTOR
        self._calibration = 0.0                # log-scale, 0 = không hiệu chuẩn
        self._calibration_n = 0
        self._fixture_ctx: dict[int, dict[int, tuple[float, int]]] = {}
        self._baseline_source = "mặc định (chưa đủ trận đã đá để khớp)"
        self._home_source = "mặc định (chưa đủ trận đã đá để khớp)"

        self._fit_league_constants(finished_fixtures)
        self._build(teams, players, finished_fixtures, match_xg)
        self._build_schedule_context(schedule or finished_fixtures)
        self._calibrate_to_market()

    # ------------------------------------------------------------- nền giải --
    def _fit_league_constants(self, finished_fixtures: list) -> None:
        """Nền của giải và lợi thế sân nhà — khớp từ trận đã đá khi đủ mẫu.

        Hai con số này là hằng số của GIẢI ĐẤU, không phải của đội, nên chúng phải
        đến từ kết quả thật ngay khi có đủ chứ không nằm mãi ở giá trị ghi cứng.
        """
        scored = [
            f for f in finished_fixtures
            if getattr(f, "team_h_score", None) is not None
            and getattr(f, "team_a_score", None) is not None
        ]
        if len(scored) < MIN_FIXTURES_FOR_BASELINE:
            return
        gh = sum(f.team_h_score for f in scored)
        ga = sum(f.team_a_score for f in scored)
        n = len(scored)
        self._baseline = max(0.6, min(2.4, (gh + ga) / (2 * n)))
        self._baseline_source = f"khớp từ {n} trận đã đá"
        if ga > 0 and gh > 0:
            self._home_factor = max(1.0, min(1.35, math.sqrt((gh / n) / (ga / n))))
            self._home_source = f"khớp từ {n} trận đã đá"

    # ---------------------------------------------------------------- build --
    def _build(self, teams, players, finished_fixtures, match_xg) -> None:
        if not teams:
            return
        from app.config import settings
        from app.services.season_state import new_manager_clubs, new_signing_players

        matches: dict[int, int] = {t.id: 0 for t in teams}
        goals_for: dict[int, int] = {t.id: 0 for t in teams}
        goals_against: dict[int, int] = {t.id: 0 for t in teams}
        for f in finished_fixtures:
            if getattr(f, "team_h_score", None) is None or getattr(f, "team_a_score", None) is None:
                continue
            matches[f.team_h] = matches.get(f.team_h, 0) + 1
            matches[f.team_a] = matches.get(f.team_a, 0) + 1
            goals_for[f.team_h] = goals_for.get(f.team_h, 0) + f.team_h_score
            goals_for[f.team_a] = goals_for.get(f.team_a, 0) + f.team_a_score
            goals_against[f.team_h] = goals_against.get(f.team_h, 0) + f.team_a_score
            goals_against[f.team_a] = goals_against.get(f.team_a, 0) + f.team_h_score

        signings = new_signing_players()
        self._priors = build_priors(
            teams,
            players,
            match_xg=match_xg,
            market_obs=market_observations(self._market) if self._market else None,
            matches_played=matches,
            new_manager_shorts=new_manager_clubs(),
            new_signing_ids=signings,
            championship=self._promoted,
            championship_damping=self._promoted_damping,
            manager_continuity_weight=settings.prior_weight_new_manager,
            history_shrink_minutes=HISTORY_SHRINK_MINUTES,
            match_shrink=SHRINK_K,
        )

        # xG cả mùa + số phút, dùng cho phần thực nghiệm và cỡ mẫu
        team_xg: dict[int, float] = {t.id: 0.0 for t in teams}
        team_minutes: dict[int, int] = {t.id: 0 for t in teams}
        signing_value: dict[int, float] = {t.id: 0.0 for t in teams}
        squad_value: dict[int, float] = {t.id: 0.0 for t in teams}
        for p in players:
            tid = getattr(p, "team_id", None)
            if tid not in team_xg:
                continue
            team_xg[tid] += getattr(p, "expected_goals", 0.0) or 0.0
            team_minutes[tid] += getattr(p, "minutes", 0) or 0
            price = (getattr(p, "now_cost", 0) or 0) / 10.0
            squad_value[tid] += price
            if str(getattr(p, "id", "")) in signings:
                signing_value[tid] += price

        availability = self._availability(players, [t.id for t in teams])

        # tách lệch sân nhà/khách của TỪNG ĐỘI ra khỏi lợi thế sân nhà của cả giải
        venue_split = self._venue_split(teams)

        for t in teams:
            prior = self._priors.get(t.id)
            p_att = prior.attack if prior else 1.0
            p_def = prior.defence if prior else 1.0

            mp = max(matches.get(t.id, 0), 0)
            emp_xg = (team_xg.get(t.id, 0.0) / mp) if mp else self._baseline
            emp_gf = (goals_for.get(t.id, 0) / mp) if mp else self._baseline
            emp_ga = (goals_against.get(t.id, 0) / mp) if mp else self._baseline
            emp_attack = 0.5 * emp_xg + 0.5 * emp_gf if mp else self._baseline

            # Số hạng "manager" và "squad" của BƯỚC 2: chúng KHÔNG dịch λ trực
            # tiếp, mà rút ngắn thời gian dữ liệu trong mùa cần để lấn át prior.
            # Với CLB đổi HLV hay thay máu đội hình, mô tả cũ hết hạn sớm hơn.
            k_eff = SHRINK_K
            mgr_f = _manager_factor(t)
            k_eff *= mgr_f
            churn = (
                signing_value.get(t.id, 0.0) / squad_value[t.id]
                if squad_value.get(t.id, 0.0) > 0
                else 0.0
            )
            squad_f = max(0.5, 1.0 - churn)
            k_eff *= squad_f
            k_eff = max(1.0, k_eff)
            w_emp = mp / (mp + k_eff) if mp else 0.0

            a_home, a_away, d_home, d_away = (
                p_att * venue_split[t.id][0],
                p_att / venue_split[t.id][0],
                p_def * venue_split[t.id][1],
                p_def / venue_split[t.id][1],
            )
            self._rates[t.id] = TeamRates(
                team_id=t.id,
                attack_home=clamp_index(a_home),
                attack_away=clamp_index(a_away),
                defence_home=clamp_index(d_home),
                defence_away=clamp_index(d_away),
                emp_xg_per_game=emp_attack,
                emp_xga_per_game=emp_ga,
                matches=mp,
                prior_attack=p_att,
                prior_defence=p_def,
                empirical_weight=round(w_emp, 4),
                availability=availability.get(t.id, 1.0),
                evidence_weight=prior.evidence_weight if prior else 0.0,
                no_pl_history=prior.no_pl_history if prior else False,
            )

        # Hai hệ số dùng lại ở phần giải thích, lưu để không tính lại hai nơi.
        self._manager_factor_by_team = {t.id: _manager_factor(t) for t in teams}
        self._squad_factor_by_team = {
            t.id: max(
                0.5,
                1.0
                - (
                    signing_value.get(t.id, 0.0) / squad_value[t.id]
                    if squad_value.get(t.id, 0.0) > 0
                    else 0.0
                ),
            )
            for t in teams
        }

    def _venue_split(self, teams: list) -> dict[int, tuple[float, float]]:
        """Lệch sân nhà/khách RIÊNG của từng đội, trung bình hình học = 1.

        Lợi thế sân nhà chung của giải đã là một số hạng riêng ở BƯỚC 2. Cái còn
        lại ở đây là phần riêng của đội — có đội thật sự mạnh hơn hẳn ở nhà — và nó
        chỉ tồn tại khi FPL đã phát strength ratings. Trước vòng 1 thì bằng 1.0.
        """
        out: dict[int, tuple[float, float]] = {}
        for t in teams:
            ah = getattr(t, "strength_attack_home", 0) or 0
            aa = getattr(t, "strength_attack_away", 0) or 0
            dh = getattr(t, "strength_defence_home", 0) or 0
            da = getattr(t, "strength_defence_away", 0) or 0
            a_split = math.sqrt(ah / aa) if ah and aa else 1.0
            d_split = math.sqrt(dh / da) if dh and da else 1.0
            out[t.id] = (
                max(0.85, min(1.18, a_split)),
                max(0.85, min(1.18, d_split)),
            )
        return out

    def _availability(self, players: list, team_ids: list[int]) -> dict[int, float]:
        """Sức mạnh đội hình = phần giá trị đội hình thật sự sẵn sàng ra sân.

        Đây là số hạng "lineup strength" của BƯỚC 2, và là thứ DUY NHẤT trong nhóm
        đó mà FPL API cấp đủ dữ liệu để tính thật: `status` + `chance_of_playing`
        là thông báo chính thức của CLB, cập nhật trước mỗi hạn chuyển nhượng.

        Đội hình dự kiến được xấp xỉ bằng 11 cầu thủ đắt nhất — API không công bố
        đội hình, và giá là ước lượng công khai tốt nhất về ai sẽ đá.
        """
        by_team: dict[int, list] = {t: [] for t in team_ids}
        for p in players:
            tid = getattr(p, "team_id", None)
            if tid in by_team:
                by_team[tid].append(p)

        raw: dict[int, float] = {}
        for tid, squad in by_team.items():
            xi = sorted(
                squad, key=lambda p: (getattr(p, "now_cost", 0) or 0), reverse=True
            )[:11]
            total = sum((getattr(p, "now_cost", 0) or 0) / 10.0 for p in xi)
            if total <= 0:
                continue
            live = 0.0
            for p in xi:
                price = (getattr(p, "now_cost", 0) or 0) / 10.0
                chance = getattr(p, "chance_of_playing_next_round", None)
                status = (getattr(p, "status", "a") or "a").lower()
                if chance is not None:
                    p_ok = max(0.0, min(1.0, chance / 100.0))
                elif status == "a":
                    p_ok = 1.0
                elif status == "d":
                    p_ok = 0.5
                else:
                    p_ok = 0.0
                live += price * p_ok
            raw[tid] = live / total

        if not raw:
            return {t: 1.0 for t in team_ids}
        # Chuẩn hoá theo trung bình giải: một tuần mà cả giải cùng có nhiều ca chấn
        # thương không được làm tổng số bàn của giải tụt xuống.
        mean = sum(raw.values()) / len(raw)
        if mean <= 0:
            return {t: 1.0 for t in team_ids}
        return {t: raw.get(t, mean) / mean for t in team_ids}

    # ------------------------------------------------- ngày nghỉ & mật độ ----
    def _build_schedule_context(self, schedule: list) -> None:
        """{fixture_id: {team_id: (ngày nghỉ, số trận trong 14 ngày trước)}}."""
        rows = [
            f for f in (schedule or [])
            if getattr(f, "kickoff_time", None) is not None
        ]
        if not rows:
            return
        by_team: dict[int, list] = {}
        for f in rows:
            by_team.setdefault(f.team_h, []).append(f)
            by_team.setdefault(f.team_a, []).append(f)
        for tid, fx in by_team.items():
            fx.sort(key=lambda f: f.kickoff_time)
            for i, f in enumerate(fx):
                prev = fx[i - 1] if i else None
                if prev is None:
                    rest = 7.0     # đầu mùa: coi như nghỉ đủ, không thưởng không phạt
                else:
                    rest = (f.kickoff_time - prev.kickoff_time).total_seconds() / 86400.0
                window_start = f.kickoff_time - timedelta(days=CONGESTION_WINDOW_DAYS)
                congestion = sum(
                    1 for g in fx[:i] if g.kickoff_time >= window_start
                )
                self._fixture_ctx.setdefault(f.id, {})[tid] = (
                    max(1.0, min(21.0, rest)),
                    congestion,
                )

    # ------------------------------------------------------------- BƯỚC 3 ---
    def _calibrate_to_market(self) -> None:
        """Độ lệch hệ thống của mô hình cấu trúc so với thị trường, trên thang log.

        Đo trên những trận CÓ giá, rồi áp cho **mọi** trận. Đây là chỗ thông tin
        thị trường lan sang phần lịch chưa ai ra giá: nếu mô hình nội bộ cho toàn
        giải nhiều bàn hơn thị trường 8%, thì nó cũng đang cho các trận GW12 nhiều
        hơn 8%, dù GW12 chưa có bảng kèo nào.

        Chỉ chạy khi đủ `MIN_FIXTURES_FOR_CALIBRATION` trận, và bị chặn ở ±18%.
        Lệch hơn thế thì mô hình và thị trường đang bất đồng về bản chất, và ép
        khớp sẽ giấu bất đồng đó đi thay vì phơi nó ra.
        """
        diffs: list[float] = []
        for (home, away), (mk_h, mk_a) in self._market.items():
            if home not in self._rates or away not in self._rates:
                continue
            s_h = self._structural_log(home, away, True, None).structural
            s_a = self._structural_log(away, home, False, None).structural
            if mk_h > 0:
                diffs.append(math.log(mk_h) - s_h)
            if mk_a > 0:
                diffs.append(math.log(mk_a) - s_a)
        self._calibration_n = len(diffs) // 2
        if self._calibration_n < MIN_FIXTURES_FOR_CALIBRATION:
            self._calibration = 0.0
            return
        mean = sum(diffs) / len(diffs)
        self._calibration = max(-CALIBRATION_CLAMP, min(CALIBRATION_CLAMP, mean))

    def _weight_for(self, key: tuple[int, int]) -> float:
        """Trọng số thị trường cho MỘT trận: thanh khoản × độ trưởng thành.

        Không có `n_bookmakers` (dữ liệu cũ ghi trước khi có cột này) thì giữ
        nguyên trọng số đầy đủ — hạ trọng số vì THIẾU THÔNG TIN sẽ âm thầm làm yếu
        đi những trận vốn có giá tốt.
        """
        w = self._market_weight
        n = self._market_support.get(key)
        if n:
            w *= min(1.0, n / self._full_support_books)
        maturity = self._market_maturity.get(key)
        if maturity is not None:
            w *= max(0.0, min(1.0, maturity))
        return max(0.0, min(1.0, w))

    # ------------------------------------------------------------- BƯỚC 2 ---
    def _structural_log(
        self, attacker: int, defender: int, attacker_home: bool, fixture_id: int | None
    ) -> LambdaTerms:
        """`log λ` của một CHIỀU tấn công, phân rã đúng theo BƯỚC 2.

        Cả λ_for lẫn λ_against đều đi qua đây; chiều còn lại chỉ là đổi vai hai
        đội và lật cờ sân nhà. Một công thức, không phải hai.
        """
        a = self._rates.get(attacker)
        d = self._rates.get(defender)
        t = LambdaTerms(baseline=math.log(self._baseline))
        if not a or not d:
            t.structural = t.baseline
            t.lam = self._baseline
            t.notes["missing"] = "thiếu chỉ số của một trong hai đội -> chỉ dùng nền giải"
            return t

        att_idx = a.attack_home if attacker_home else a.attack_away
        def_idx = d.defence_away if attacker_home else d.defence_home

        # sức mạnh tấn công: prior (BƯỚC 1) trộn với dữ liệu trong mùa
        w = a.empirical_weight
        emp_att_ratio = (
            a.emp_xg_per_game / self._baseline if self._baseline > 0 else 1.0
        )
        t.attack = (1 - w) * math.log(max(att_idx, 1e-6)) + w * math.log(
            max(emp_att_ratio, 1e-6)
        )

        emp_def_ratio = (
            self._baseline / max(d.emp_xga_per_game, 1e-6) if self._baseline > 0 else 1.0
        )
        wd = d.empirical_weight
        t.opponent_defence = -(
            (1 - wd) * math.log(max(def_idx, 1e-6))
            + wd * math.log(max(emp_def_ratio, 1e-6))
        )

        t.home = math.log(self._home_factor) * (1.0 if attacker_home else -1.0)

        t.lineup = LINEUP_ELASTICITY * (
            math.log(max(a.availability, 1e-3)) - math.log(max(d.availability, 1e-3))
        )

        ctx = self._fixture_ctx.get(fixture_id) if fixture_id is not None else None
        if ctx and attacker in ctx and defender in ctx:
            rest_a, cong_a = ctx[attacker]
            rest_d, cong_d = ctx[defender]
            diff = max(-MAX_REST_DIFF_DAYS, min(MAX_REST_DIFF_DAYS, rest_a - rest_d))
            t.rest = REST_COEF_PER_DAY * diff
            extra_a = max(0, cong_a - CONGESTION_FREE_MATCHES)
            extra_d = max(0, cong_d - CONGESTION_FREE_MATCHES)
            t.congestion = -CONGESTION_COEF_PER_MATCH * (extra_a - extra_d)
            if t.congestion == 0:
                # Nói rõ vì sao bằng 0, vì phần lớn thời gian nó sẽ bằng 0: FPL API
                # **chỉ có lịch Ngoại hạng**. Cúp châu Âu, cúp Liên đoàn và FA Cup
                # — đúng những giải tạo ra mật độ thi đấu thật — không nằm ở đâu
                # trong dữ liệu này. Số hạng chỉ kích hoạt được ở các vòng đá giữa
                # tuần của chính Ngoại hạng.
                t.notes["congestion"] = (
                    "0 — chỉ đếm được lịch Ngoại hạng; FPL API không công bố lịch cúp"
                )
        else:
            t.notes["rest"] = (
                "0 — không có giờ thi đấu cho trận này nên không tính được ngày nghỉ"
                if fixture_id is None
                else "0 — trận chưa có giờ thi đấu chính thức"
            )

        # --- squad / manager: tác động qua tốc độ dữ liệu trong mùa lấn át prior --
        mgr_f = getattr(self, "_manager_factor_by_team", {}).get(attacker, 1.0)
        squad_f = getattr(self, "_squad_factor_by_team", {}).get(attacker, 1.0)
        if mgr_f < 1.0 or squad_f < 1.0:
            base_w = a.matches / (a.matches + SHRINK_K) if a.matches else 0.0
            plain = (1 - base_w) * math.log(max(att_idx, 1e-6)) + base_w * math.log(
                max(emp_att_ratio, 1e-6)
            )
            delta = t.attack - plain
            # chia phần chênh theo mức đóng góp của hai nguyên nhân
            tot = (1 - mgr_f) + (1 - squad_f)
            if tot > 1e-9:
                t.manager = delta * (1 - mgr_f) / tot
                t.squad = delta * (1 - squad_f) / tot
            # `attack` giữ nguyên tổng; hai số hạng này chỉ ghi nhãn phần bên trong
            t.attack -= t.manager + t.squad
        if mgr_f >= 1.0:
            t.notes["manager"] = "0 — CLB không đổi HLV (theo danh sách người vận hành khai)"
        if squad_f >= 1.0:
            t.notes["squad"] = "0 — không có tân binh nội giải nào được khai cho CLB này"

        raw = (
            t.baseline + t.attack + t.opponent_defence + t.home
            + t.lineup + t.rest + t.congestion + t.squad + t.manager
        )

        # --- điều chỉnh đội mới lên hạng: trần bằng mức trung bình giải ----------
        # Một CLB chưa có phút Ngoại hạng nào không được phép được chấm trên mức
        # trung bình giải, bất kể các nguồn khác nói gì. Trần này tự biến mất ngay
        # khi đội đó tích đủ phút thật.
        if a.no_pl_history:
            ceiling = math.log(self._baseline) + math.log(self._home_factor) * (
                1.0 if attacker_home else -1.0
            ) + t.opponent_defence
            if raw > ceiling:
                t.promotion = ceiling - raw
                raw = ceiling
                self._rates[attacker].promotion_cap_applied = True
        else:
            t.notes["promotion"] = "0 — CLB có lịch sử Ngoại hạng"

        t.calibration = self._calibration
        t.structural = raw + t.calibration
        t.lam = min(max(math.exp(t.structural), MIN_LAMBDA), MAX_LAMBDA)
        return t

    def terms(
        self, team_id: int, opp_id: int, is_home: bool, fixture_id: int | None = None
    ) -> tuple[LambdaTerms, LambdaTerms]:
        """(số hạng của λ_for, số hạng của λ_against) — đã gồm cả BƯỚC 3."""
        t_for = self._structural_log(team_id, opp_id, is_home, fixture_id)
        t_against = self._structural_log(opp_id, team_id, not is_home, fixture_id)

        key = (team_id, opp_id) if is_home else (opp_id, team_id)
        mk = self._market.get(key)
        if mk:
            mk_for, mk_against = (mk[0], mk[1]) if is_home else (mk[1], mk[0])
            w = self._weight_for(key)
            for t, mkt in ((t_for, mk_for), (t_against, mk_against)):
                if mkt <= 0:
                    continue
                t.market = math.log(mkt)
                t.market_weight = w
                # BƯỚC 3: trung bình HÌNH HỌC, tức trung bình cộng trên thang log
                blended = w * t.market + (1 - w) * t.structural
                t.lam = min(max(math.exp(blended), MIN_LAMBDA), MAX_LAMBDA)
        else:
            for t in (t_for, t_against):
                t.notes["market"] = "trận chưa có kèo — hoàn toàn dùng mô hình cấu trúc"
        return t_for, t_against

    # ------------------------------------------------------------ giao diện --
    def has_market(self, team_id: int, opp_id: int, is_home: bool) -> bool:
        key = (team_id, opp_id) if is_home else (opp_id, team_id)
        return key in self._market

    def expected_goals(
        self, team_id: int, opp_id: int, is_home: bool, fixture_id: int | None = None
    ) -> tuple[float, float]:
        """(lambda_for, lambda_against) của `team_id` trong trận này."""
        if team_id not in self._rates or opp_id not in self._rates:
            return self._baseline, self._baseline
        t_for, t_against = self.terms(team_id, opp_id, is_home, fixture_id)
        return t_for.lam, t_against.lam

    def explain(
        self, team_id: int, opp_id: int, is_home: bool, fixture_id: int | None = None
    ) -> dict:
        """Phân rã đầy đủ cả hai chiều — dùng cho trang phương pháp và kiểm tra."""
        t_for, t_against = self.terms(team_id, opp_id, is_home, fixture_id)
        return {
            "for": t_for.as_dict(),
            "against": t_against.as_dict(),
            "baseline_source": self._baseline_source,
            "home_factor": round(self._home_factor, 4),
            "home_factor_source": self._home_source,
            "calibration": {
                "log_shift": round(self._calibration, 4),
                "multiplier": round(math.exp(self._calibration), 4),
                "fixtures_used": self._calibration_n,
                "applied": self._calibration != 0.0,
            },
        }

    def clean_sheet_prob(
        self, team_id: int, opp_id: int, is_home: bool, fixture_id: int | None = None
    ) -> float:
        _, lam_against = self.expected_goals(team_id, opp_id, is_home, fixture_id)
        return math.exp(-lam_against)   # Poisson P(0 bàn thua)

    def season_avg_gf(self, team_id: int) -> float:
        t = self._rates.get(team_id)
        return t.emp_xg_per_game if t else self._baseline

    def prior(self, team_id: int) -> TeamPrior | None:
        return self._priors.get(team_id)

    @property
    def baseline(self) -> float:
        return self._baseline

    @property
    def calibration_multiplier(self) -> float:
        return math.exp(self._calibration)
