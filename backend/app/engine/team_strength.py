"""Team attacking / defensive strength -> expected goals per fixture.

Combines two signals with Bayesian shrinkage (spec §7):
  1. FPL strength ratings (strength_attack/defence_home/away), venue-aware.
  2. Empirical team xG / xGA per game, derived from players' season expected
     stats and finished fixtures.

The blend weight favours empirical data as more matches are played:
    w = matches / (matches + K)
so early-season noise is regularised toward the ratings-based prior.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# league-average goals scored by one team in one game
LEAGUE_AVG_GOALS = 1.42
HOME_ADV = 1.12          # multiply home team's attack
AWAY_ADJ = 0.90          # multiply away team's attack
SHRINK_K = 6.0           # matches before empirical dominates
MIN_LAMBDA = 0.15
MAX_LAMBDA = 4.5

# Newly-promoted sides have no Premier League history to learn from. Rather than
# dividing by a near-zero sample (which produced absurd ratings — an elite
# defence index for a promoted club), fall back to a documented prior: newly
# promoted teams both score fewer and concede more than the league average.
# These are deliberately round numbers — they are a placeholder that is replaced
# the moment better evidence exists, in this order:
#   1. bookmaker odds for the fixture (market view, already integrated)
#   2. FPL's own strength ratings once published for the season
#   3. real match results as the season progresses (empirical shrinkage)
PROMOTED_ATTACK = 0.80    # 1.0 = league average attack
PROMOTED_DEFENCE = 0.80   # 1.0 = league average defence (lower = concedes more)

# "No PL history" must be judged RELATIVE to the rest of the league, never by an
# absolute minute count: FPL resets season stats when a new season starts, so a
# fixed threshold would flag *every* club as newly promoted after gameweek 1.
NO_HISTORY_RATIO = 0.35      # squad minutes below 35% of the league median
# Sample size (squad minutes) at which derived indices are trusted over the
# league average — one round of fixtures must not make a team look elite.
HISTORY_SHRINK_MINUTES = 8000
# Championship results stop influencing a club once it has real PL matches.
CHAMP_FADE_MATCHES = 5


# Số phút tối thiểu để một thủ môn được dùng làm mốc hàng thủ. Dưới mức này thì
# xGC/90 quá nhiễu (một trận thua đậm là đủ bóp méo).
MIN_GK_MINUTES_FOR_PROXY = 900


def _defence_proxy(players: list) -> dict[int, float]:
    """xGC trên 90 phút của thủ môn đá nhiều nhất mỗi đội.

    Vì sao phải là thủ môn, và vì sao phải theo /90 — bản trước lấy
    `max(expected_goals_conceded)` trong toàn đội và sai theo hai cách:

    1. **`expected_goals_conceded` là số của MÙA TRƯỚC, còn `team_id` là CLB HIỆN
       TẠI.** FPL API không cho biết số đó tích luỹ ở đâu. Kết quả đo được: hàng thủ
       Man City bị chấm bằng Elliot Anderson — 53.6 xGC tích luỹ ở Nottingham
       Forest. City thành "kém trung bình giải" (0.887), và Crystal Palace được cho
       2.01 bàn kỳ vọng khi tiếp City. 4/20 đội dính lỗi này.
    2. **Tổng cả mùa phụ thuộc số phút.** 10/20 đội có mốc là cầu thủ ngoài sân;
       xGC của họ chỉ tính những phút có mặt, nên đội nào hay xoay trung vệ lại
       trông như phòng ngự tốt hơn.

    Thủ môn giải quyết cả hai: ít bị xoay vòng nhất, và xGC/90 của họ **chính là**
    mức bị uy hiếp của đội trong thời gian họ trên sân — một tỷ lệ, không phụ thuộc
    số phút. Loại tân binh đã khai để không lặp lại lỗi (1).

    Lưu ý bất đối xứng, và đây là lý do phía TẤN CÔNG cố ý KHÔNG sửa theo cách này:
    xG **đi theo cầu thủ** (tiền đạo chuyển CLB thì bàn thắng của anh ta thành sản
    lượng kỳ vọng của CLB mới, nên cộng vào là đúng), còn xGC là thuộc tính của
    ĐỘI BÓNG và **không đi theo ai cả**.

    Đội không có thủ môn đủ điều kiện (đội mới lên hạng) trả về 0.0 — nhánh
    `promoted` phía dưới xử lý riêng.
    """
    from app.services.season_state import new_signing_players

    signings = new_signing_players()
    best: dict[int, object] = {}
    for p in players:
        # `getattr` chứ không truy cập thẳng: hàm này còn được gọi với các đối tượng
        # cầu thủ rút gọn (test, và các nhánh chỉ cần vài trường), thiếu một thuộc
        # tính không được phép làm sập cả mô hình sức mạnh đội.
        if getattr(p, "element_type", None) != 1:
            continue
        if str(getattr(p, "id", "")) in signings:
            continue
        mins = getattr(p, "minutes", 0) or 0
        if mins < MIN_GK_MINUTES_FOR_PROXY:
            continue
        cur = best.get(p.team_id)
        if cur is None or mins > (getattr(cur, "minutes", 0) or 0):
            best[p.team_id] = p

    out: dict[int, float] = {}
    for tid, gk in best.items():
        mins = getattr(gk, "minutes", 0) or 0
        xgc = getattr(gk, "expected_goals_conceded", 0.0) or 0.0
        out[tid] = xgc / (mins / 90.0) if mins else 0.0
    return out


def _manager_factor(team) -> float:
    """Chiết khấu xếp hạng mùa trước khi CLB đã đổi huấn luyện viên.

    Dùng lại `prior_weight_new_manager` — đúng hệ số mà mô hình xMins đã dùng cho
    từng cầu thủ của các CLB đó. Một sự thật ("đội này đổi HLV nên dữ liệu cũ mô tả
    kém hơn") thì phải mang cùng một con số ở mọi nơi, chứ không đặt riêng mỗi chỗ.

    Danh sách CLB đổi HLV do người vận hành khai (FPL API không công bố), nên danh
    sách rỗng nghĩa là CHƯA AI KHAI — khi đó không chiết khấu ai cả, và đó là hành
    vi đúng: không được tự bịa ra thay đổi.
    """
    from app.config import settings
    from app.services.season_state import new_manager_clubs

    short = (getattr(team, "short_name", "") or "").upper()
    if short and short in new_manager_clubs():
        return settings.prior_weight_new_manager
    return 1.0


def _clamp(v: float, lo: float = 0.55, hi: float = 1.7) -> float:
    return max(lo, min(hi, v))


def load_market_map(db) -> dict[tuple[int, int], tuple[float, float]]:
    """(home_team_id, away_team_id) -> (lam_home, lam_away) from stored odds.

    Read from the DB, never from the odds API, so page loads cost no quota.
    """
    from sqlalchemy import select

    from app.models import MarketOdds

    rows = db.scalars(select(MarketOdds)).all()
    return {(r.team_h, r.team_a): (r.lam_home, r.lam_away) for r in rows}


def load_market_support(db) -> dict[tuple[int, int], int]:
    """(home, away) -> số nhà cái đã ra giá cho trận đó.

    Tách khỏi `load_market_map` để không đổi hình dạng tuple mà mọi nơi đang dùng.
    Dùng để hạ trọng số thị trường khi trận chỉ có vài nhà cái ra giá: đồng thuận
    của 20 nhà cái và giá lẻ của 2 nhà cái không phải cùng một loại bằng chứng.
    """
    from sqlalchemy import select

    from app.models import MarketOdds

    rows = db.scalars(select(MarketOdds)).all()
    return {(r.team_h, r.team_a): int(r.n_bookmakers or 0) for r in rows}


def load_promoted_map(db) -> dict[int, tuple[float, float]]:
    """{team_id: (attack_index, defence_index)} từ Championship mùa trước.

    Rỗng nếu tắt tính năng hoặc chưa đồng bộ — khi đó đội mới lên hạng dùng
    mức nền phẳng.
    """
    from sqlalchemy import select

    from app.config import settings
    from app.models import ChampionshipStats

    if not settings.championship_enabled:
        return {}
    rows = db.scalars(select(ChampionshipStats)).all()
    return {r.team_id: (r.attack_index, r.defence_index) for r in rows}


@dataclass
class TeamRates:
    team_id: int
    # normalized (league mean = 1.0)
    attack_home: float
    attack_away: float
    defence_home: float
    defence_away: float
    emp_xg_per_game: float      # empirical attack
    emp_xga_per_game: float     # empirical defence (goals conceded)
    matches: int


class TeamStrength:
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
    ) -> None:
        """`market` maps (home_team_id, away_team_id) -> (lam_home, lam_away)
        from bookmaker prices. Where present it is blended over the internal
        model (spec §3: licensed market data outranks a model estimate).

        `market_support` gives the number of books behind each fixture; the blend
        weight is scaled down when that number is small (see `_weight_for`).
        Omitting it keeps the old behaviour — full weight everywhere."""
        self._rates: dict[int, TeamRates] = {}
        self._market = market or {}
        self._market_weight = market_weight
        self._market_support = market_support or {}
        self._full_support_books = max(1, full_support_books)
        # {team_id: (attack_index, defence_index)} tương đối trong Championship
        self._promoted = promoted or {}
        self._promoted_damping = promoted_damping
        self._build(teams, players, finished_fixtures)

    def has_market(self, team_id: int, opp_id: int, is_home: bool) -> bool:
        key = (team_id, opp_id) if is_home else (opp_id, team_id)
        return key in self._market

    def _weight_for(self, key: tuple[int, int]) -> float:
        """Trọng số thị trường cho MỘT trận, hạ xuống khi ít nhà cái ra giá.

        Đồng thuận 20 nhà cái và giá lẻ của 2 nhà cái vào cùng công thức với cùng
        trọng số 0.7 là coi hai loại bằng chứng như nhau. Dốc tuyến tính tới
        `full_support_books` rồi giữ nguyên: đủ nhà cái thì không thay đổi gì so
        với trước, thiếu thì tự động nghiêng về mô hình nội bộ.

        Không có `n_bookmakers` (dữ liệu cũ ghi trước khi có cột này) thì giữ
        nguyên trọng số đầy đủ — hạ trọng số vì THIẾU THÔNG TIN sẽ âm thầm làm
        yếu đi những trận vốn có giá tốt.
        """
        n = self._market_support.get(key)
        if not n:
            return self._market_weight
        return self._market_weight * min(1.0, n / self._full_support_books)

    # ---------------------------------------------------------------- build ---
    def _build(self, teams, players, finished_fixtures) -> None:
        if not teams:
            return
        # league means for normalisation
        def _mean(attr: str) -> float:
            vals = [getattr(t, attr) or 0 for t in teams]
            vals = [v for v in vals if v]
            return sum(vals) / len(vals) if vals else 1.0

        m_att_h = _mean("strength_attack_home")
        m_att_a = _mean("strength_attack_away")
        m_def_h = _mean("strength_defence_home")
        m_def_a = _mean("strength_defence_away")

        # matches played per team (from finished fixtures)
        matches: dict[int, int] = {t.id: 0 for t in teams}
        goals_for: dict[int, int] = {t.id: 0 for t in teams}
        goals_against: dict[int, int] = {t.id: 0 for t in teams}
        for f in finished_fixtures:
            if f.team_h_score is None or f.team_a_score is None:
                continue
            matches[f.team_h] = matches.get(f.team_h, 0) + 1
            matches[f.team_a] = matches.get(f.team_a, 0) + 1
            goals_for[f.team_h] = goals_for.get(f.team_h, 0) + f.team_h_score
            goals_for[f.team_a] = goals_for.get(f.team_a, 0) + f.team_a_score
            goals_against[f.team_h] = goals_against.get(f.team_h, 0) + f.team_a_score
            goals_against[f.team_a] = goals_against.get(f.team_a, 0) + f.team_h_score

        # empirical team xG from players' season expected_goals
        team_xg: dict[int, float] = {t.id: 0.0 for t in teams}
        for p in players:
            team_xg[p.team_id] = team_xg.get(p.team_id, 0.0) + (p.expected_goals or 0.0)

        # Proxy hàng thủ = xGC/90 của THỦ MÔN đá nhiều nhất — xem `_defence_proxy`.
        team_xga_proxy = _defence_proxy(players)
        # tổng số phút Ngoại hạng của cả đội — dùng để phát hiện đội mới lên hạng
        team_minutes: dict[int, int] = {t.id: 0 for t in teams}
        for p in players:
            team_minutes[p.team_id] = team_minutes.get(p.team_id, 0) + (p.minutes or 0)

        # Mốc so sánh = đội có nhiều phút nhất. Dùng max thay vì trung vị vì
        # trung vị bị kéo về 0 khi nhiều đội cùng chưa có dữ liệu.
        reference_minutes = max(team_minutes.values()) if team_minutes else 0

        _xg_vals = [v for v in team_xg.values() if v > 0]
        _xga_vals = [v for v in team_xga_proxy.values() if v > 0]
        mean_team_xg = sum(_xg_vals) / len(_xg_vals) if _xg_vals else 1.0
        mean_team_xga = sum(_xga_vals) / len(_xga_vals) if _xga_vals else 1.0

        for t in teams:
            mp = max(matches.get(t.id, 0), 0)
            emp_xg = (team_xg.get(t.id, 0.0) / mp) if mp else LEAGUE_AVG_GOALS
            # blend actual goals + xG for empirical attack; goals-against for defence
            emp_gf = (goals_for.get(t.id, 0) / mp) if mp else LEAGUE_AVG_GOALS
            emp_ga = (goals_against.get(t.id, 0) / mp) if mp else LEAGUE_AVG_GOALS
            emp_attack = 0.5 * emp_xg + 0.5 * emp_gf if mp else LEAGUE_AVG_GOALS

            # fallback indices from last-season player xG/xGA when FPL ratings
            # aren't set yet (pre-season). Attack: higher = stronger. Defence:
            # higher = concedes fewer = stronger (matches how expected_goals uses it).
            _xga = team_xga_proxy.get(t.id, 0.0)
            own_minutes = team_minutes.get(t.id, 0)
            promoted = (
                reference_minutes > 0
                and own_minutes < NO_HISTORY_RATIO * reference_minutes
            )
            if promoted:
                # không đủ dữ liệu Ngoại hạng -> dùng mức nền, KHÔNG chia cho mẫu ~0
                derived_att, derived_def = PROMOTED_ATTACK, PROMOTED_DEFENCE
                # nếu có dữ liệu Championship: chỉ dùng để XẾP HẠNG ba đội mới lên
                # hạng so với nhau quanh mức nền, không quy đổi bàn thắng trực tiếp.
                champ = self._promoted.get(t.id)
                # dữ liệu Championship nhạt dần rồi tắt hẳn khi đã có trận thật
                champ_w = max(0.0, 1.0 - mp / CHAMP_FADE_MATCHES)
                if champ and champ_w > 0:
                    d = self._promoted_damping * champ_w
                    a_idx = max(champ[0], 0.2) ** d
                    d_idx = max(champ[1], 0.2) ** d
                    # trần 1.0: dữ liệu Championship không bao giờ đủ để chấm một
                    # đội mới lên hạng ngang/hơn mức trung bình Ngoại hạng
                    derived_att = min(PROMOTED_ATTACK * a_idx, 1.0)
                    derived_def = min(PROMOTED_DEFENCE * d_idx, 1.0)
            else:
                raw_att = (
                    _clamp(team_xg.get(t.id, 0.0) / mean_team_xg) if mean_team_xg else 1.0
                )
                raw_def = (
                    _clamp(mean_team_xga / max(_xga, 0.35 * mean_team_xga))
                    if _xga and mean_team_xga
                    else 1.0
                )
                # Cỡ mẫu nhỏ -> kéo về trung bình giải, tránh kết luận một đội
                # mạnh/yếu chỉ từ một trận.
                w_hist = own_minutes / (own_minutes + HISTORY_SHRINK_MINUTES)

                # ...nhưng cỡ mẫu KHÔNG phải điều duy nhất đáng hỏi. `own_minutes`
                # trước vòng 1 là số phút của MÙA TRƯỚC, nên nguyên trạng nó cho
                # trọng số ~0.79 vào một mô tả của đội bóng CŨ. Mùa 2026/27 có 12/20
                # CLB đổi huấn luyện viên — hàng thủ và cách pressing đổi theo, nên
                # xếp hạng mùa trước mô tả đội hiện tại kém hẳn đi.
                #
                # Không đưa về 0 (dữ liệu mùa trước vẫn có tín hiệu thật) mà chiết
                # khấu đúng bằng hệ số đã dùng cho từng cầu thủ ở xMins — cùng một
                # sự thật thì phải cùng một con số, không đặt riêng mỗi nơi một kiểu.
                w_hist *= _manager_factor(t)
                derived_att = w_hist * raw_att + (1 - w_hist) * 1.0
                derived_def = w_hist * raw_def + (1 - w_hist) * 1.0
            has_fpl = bool(t.strength_attack_home)

            self._rates[t.id] = TeamRates(
                team_id=t.id,
                attack_home=(t.strength_attack_home / m_att_h) if has_fpl else derived_att,
                attack_away=(t.strength_attack_away / m_att_a) if has_fpl else derived_att,
                defence_home=(t.strength_defence_home / m_def_h) if has_fpl else derived_def,
                defence_away=(t.strength_defence_away / m_def_a) if has_fpl else derived_def,
                emp_xg_per_game=emp_attack,
                emp_xga_per_game=emp_ga,
                matches=mp,
            )

    # ------------------------------------------------------------ expected ----
    def expected_goals(self, team_id: int, opp_id: int, is_home: bool) -> tuple[float, float]:
        """Return (lambda_for, lambda_against) for `team_id` in this fixture."""
        t = self._rates.get(team_id)
        o = self._rates.get(opp_id)
        if not t or not o:
            return LEAGUE_AVG_GOALS, LEAGUE_AVG_GOALS

        if is_home:
            att = t.attack_home
            opp_def = o.defence_away
            opp_att = o.attack_away
            own_def = t.defence_home
            venue_for, venue_against = HOME_ADV, AWAY_ADJ
        else:
            att = t.attack_away
            opp_def = o.defence_home
            opp_att = o.attack_home
            own_def = t.defence_away
            venue_for, venue_against = AWAY_ADJ, HOME_ADV

        # ratings-based prior (higher opp defence rating => fewer goals)
        prior_for = LEAGUE_AVG_GOALS * venue_for * att / max(opp_def, 0.3)
        prior_against = LEAGUE_AVG_GOALS * venue_against * opp_att / max(own_def, 0.3)

        # shrink toward empirical as matches accumulate
        w = t.matches / (t.matches + SHRINK_K)
        lam_for = w * t.emp_xg_per_game * (venue_for) + (1 - w) * prior_for
        lam_against = w * t.emp_xga_per_game * (venue_against) + (1 - w) * prior_against

        # bookmaker consensus for this exact fixture, if we have it
        key = (team_id, opp_id) if is_home else (opp_id, team_id)
        mk = self._market.get(key)
        if mk:
            mk_for, mk_against = (mk[0], mk[1]) if is_home else (mk[1], mk[0])
            w = self._weight_for(key)
            lam_for = w * mk_for + (1 - w) * lam_for
            lam_against = w * mk_against + (1 - w) * lam_against

        return (
            min(max(lam_for, MIN_LAMBDA), MAX_LAMBDA),
            min(max(lam_against, MIN_LAMBDA), MAX_LAMBDA),
        )

    def clean_sheet_prob(self, team_id: int, opp_id: int, is_home: bool) -> float:
        _, lam_against = self.expected_goals(team_id, opp_id, is_home)
        return math.exp(-lam_against)  # Poisson P(0 conceded)

    def season_avg_gf(self, team_id: int) -> float:
        t = self._rates.get(team_id)
        return t.emp_xg_per_game if t else LEAGUE_AVG_GOALS
