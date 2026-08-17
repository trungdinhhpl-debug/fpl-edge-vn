"""BƯỚC 4, 5, 6 — từ λ ra độ dễ, rồi ra FDR.

Ba bước cuối của đặc tả, và cả ba đều xoay quanh một quyết định: **độ khó là một
thứ hạng, không phải một con số tuyệt đối.**

    BƯỚC 4  Attack Ease  = percentile của λ_for
            Defence Ease = percentile của 4·P(sạch lưới) − trừ điểm thủng lưới
            Role Ease    = percentile điểm kỳ vọng của cầu thủ THAM CHIẾU từng vai

    BƯỚC 5  Schedule Ease = trung bình có trọng số suy giảm theo thời gian của
                            Role Ease − phạt bất định

    BƯỚC 6  20% dễ nhất → FDR 1 … 20% khó nhất → FDR 5

Vì sao percentile chứ không phải một phép co tuyến tính. Bản trước ánh xạ
λ ∈ [0.6, 2.6] xuống thang 1–5 bằng một đoạn thẳng có hai đầu mút ghi cứng. Hai
hệ quả đo được: (1) trong một mùa nhiều bàn thì gần như cả giải trôi về phía
"dễ", vì hai đầu mút không đi theo; (2) khoảng giữa của phân bố — nơi có phần lớn
các trận — bị nén vào chưa tới một bậc, nên FDR 3 gom cả những trận thật sự khác
nhau. Percentile lấy chính phân bố của mùa này làm thước, nên "20% dễ nhất" luôn
đúng nghĩa 20% dễ nhất.

Vì sao Role Ease phải có, dù đã có Attack Ease và Defence Ease. Cùng một trận
không cùng độ khó cho mọi vị trí: một trận λ_for 1.9 / λ_against 1.6 là trận tốt
cho tiền đạo và trận tệ cho hậu vệ. Xếp một hạng duy nhất cho cả bốn vị trí — thứ
mà FDR chính thức của FPL làm — là trộn hai câu hỏi khác nhau vào một câu trả lời.
Ở đây mỗi vai trò có bảng xếp hạng riêng, dựng bằng cách **giữ nguyên cầu thủ và
chỉ đổi trận đấu**: một cầu thủ tham chiếu (trung vị của giải ở vai trò đó, đá
chính chắc suất) được đưa qua đúng mô hình xP đang dùng cho cầu thủ thật.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.engine.team_strength import TeamStrength
from app.engine.xpoints import _expected_floor_div, expected_points
from app.scoring import RULES

# Suy giảm theo thời gian ở BƯỚC 5. Nửa chu kỳ 4 vòng: vòng kế cận nặng gấp đôi
# vòng thứ năm. Lịch xa hơn vẫn được tính, chỉ là không được phép quyết định.
DECAY_HALF_LIFE_GWS = 4.0

# Trần của phạt bất định, tính bằng ĐIỂM PERCENTILE. 12 điểm là hơn nửa bậc FDR:
# đủ để một lịch "dễ nhưng chưa ai ra giá" không vượt mặt một lịch "dễ và đã có
# đồng thuận nhà cái", nhưng không đủ để lật ngược một khác biệt thật sự lớn.
UNCERTAINTY_MAX_PENALTY = 12.0
# Ba nguồn bất định, và tỷ trọng của chúng trong khoản phạt trên.
UNC_W_NO_MARKET = 0.50     # tỷ lệ trận trong cửa sổ chưa có kèo
UNC_W_THIN_PRIOR = 0.35    # prior dựng từ bao nhiêu trong 5 nguồn của BƯỚC 1
UNC_W_NO_KICKOFF = 0.15    # trận chưa có giờ thi đấu chính thức (dễ bị dời)

# Cầu thủ tham chiếu: lấy trung vị của giải trong nhóm đá đều.
#
# "Đá đều" phải xét TƯƠNG ĐỐI với phần còn lại của giải, không bao giờ bằng một
# mốc phút tuyệt đối — FPL reset thống kê mỗi mùa, nên 900 phút cố định nghĩa là
# suốt GW1–GW9 **không một ai** đủ điều kiện và cả bốn vai trò rơi về prior. Đây
# đúng là lỗi mà `team_strength` đã dính một lần với ngưỡng "đội mới lên hạng".
# Trần 900 phút vẫn giữ cho giai đoạn tiền mùa, khi FPL còn phát tổng mùa trước.
REFERENCE_MIN_MINUTES = 900
REFERENCE_MINUTES_RATIO = 0.6      # so với cầu thủ đá nhiều nhất giải
# ...nhưng ngưỡng tương đối cần một cái sàn, nếu không nó tự huỷ ở đúng đầu mùa:
# khi chưa ai đá phút nào, `0.6 × 0` cho ngưỡng 0 và **mọi** cầu thủ đều "đủ điều
# kiện" với 0 phút, dựng ra một cầu thủ tham chiếu toàn số 0. Chừng nào người đá
# nhiều nhất giải chưa qua 2 trận trọn vẹn thì chưa có cái gì để lấy trung vị.
REFERENCE_MIN_BUSIEST = 180
# Cầu thủ tham chiếu là người đá chính chắc suất — cố ý. Mục tiêu là chấm TRẬN
# ĐẤU, nên mọi thứ thuộc về cầu thủ phải được giữ cố định giữa các trận.
REFERENCE_XMINS = 85.0
REFERENCE_P_START = 0.95
REFERENCE_P_APPEAR = 0.97
REFERENCE_P_60 = 0.90

POSITIONS = (1, 2, 3, 4)
POSITION_NAMES = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


# ------------------------------------------------------------- percentile ---
def percentiles(values: list[float]) -> list[float]:
    """Percentile 0..100 theo thứ hạng, đồng hạng dùng hạng giữa.

    0 = thấp nhất trong nhóm, 100 = cao nhất. Đồng hạng phải dùng hạng giữa chứ
    không phải hạng đầu: trước vòng 1 có rất nhiều ô bằng nhau, và hạng đầu sẽ
    dồn tất cả bọn chúng xuống đáy bảng chỉ vì thứ tự trong danh sách.
    """
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [50.0]
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        mid = (i + j) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = mid
        i = j + 1
    return [100.0 * r / (n - 1) for r in ranks]


def fdr_from_percentile(pct: float | None) -> int:
    """BƯỚC 6 — percentile độ dễ (100 = dễ nhất) → FDR 1..5, mỗi bậc 20%."""
    if pct is None:
        return 3
    pct = max(0.0, min(100.0, pct))
    return max(1, min(5, 1 + int((100.0 - pct) * 5.0 / 100.0)))


def quintile_fdr(rank_from_easiest: int, n: int) -> int:
    """FDR theo thứ hạng, chia đúng năm nhóm bằng nhau.

    Dùng cho bảng xếp hạng ĐỘI (20 đội → đúng 4 đội mỗi bậc). Với các ô lẻ thì
    `fdr_from_percentile` hợp hơn, vì số ô mỗi vòng không chia hết cho 5.
    """
    if n <= 0:
        return 3
    return max(1, min(5, 1 + int(rank_from_easiest * 5 // n)))


def decay_weight(offset: int, half_life: float = DECAY_HALF_LIFE_GWS) -> float:
    """Trọng số của vòng thứ `offset` kể từ vòng đầu cửa sổ (offset 0 = 1.0)."""
    if half_life <= 0:
        return 1.0
    return 0.5 ** (offset / half_life)


# ------------------------------------------------------ cầu thủ tham chiếu ---
@dataclass
class ReferencePlayer:
    """Trung vị của giải ở một vai trò, đá chính chắc suất.

    Đây là "cầu thủ tham chiếu" của BƯỚC 4. Mọi thuộc tính thuộc về CẦU THỦ được
    giữ cố định; chỉ λ của trận thay đổi. Nhờ vậy chênh lệch giữa hai ô đúng bằng
    chênh lệch của hai trận đấu, không lẫn chênh lệch giữa hai đội hình.
    """

    element_type: int
    minutes_season: int
    xg_season: float
    xa_season: float
    saves_season: int
    dc_season: float
    yellow_season: int
    red_season: int
    bps_season: int
    cbi_season: float
    source: str = "trung vị giải"


# Mốc dự phòng khi trong DB chưa có ai đủ `REFERENCE_MIN_MINUTES` phút — trước
# vòng 1 của một mùa mà FPL đã reset thống kê. Suy ra từ chính các prior per-90 mà
# `engine/xpoints.py` dùng để co giãn mẫu nhỏ, quy về 1800 phút, nên không có con
# số nào ở đây được đặt riêng cho module này.
_FALLBACK_MINUTES = 1800


def _fallback_reference(pos: int) -> ReferencePlayer:
    from app.engine.xpoints import PRIOR_DC90, PRIOR_XA90, PRIOR_XG90

    mins90 = _FALLBACK_MINUTES / 90.0
    return ReferencePlayer(
        element_type=pos,
        minutes_season=_FALLBACK_MINUTES,
        xg_season=PRIOR_XG90.get(pos, 0.1) * mins90,
        xa_season=PRIOR_XA90.get(pos, 0.1) * mins90,
        saves_season=int(3.0 * mins90) if pos == 1 else 0,
        dc_season=PRIOR_DC90.get(pos, 4.0) * mins90,
        yellow_season=int(0.15 * mins90),
        red_season=0,
        bps_season=int(18.0 * mins90),
        cbi_season=(PRIOR_DC90.get(pos, 4.0) * 0.6) * mins90,
        source="prior theo vị trí (chưa có cầu thủ nào đá đủ đều)",
    )


def _median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0


def build_reference_players(players: list) -> dict[int, ReferencePlayer]:
    """Một cầu thủ tham chiếu cho mỗi vai trò, dựng từ trung vị của giải.

    Trung vị chứ không phải trung bình: phân bố sản lượng của cầu thủ lệch mạnh
    (một Haaland kéo trung bình tiền đạo lên khỏi mọi tiền đạo thật), và mốc cần
    là một cầu thủ ĐIỂN HÌNH chứ không phải một cầu thủ trung bình không tồn tại.
    """
    busiest = max((getattr(p, "minutes", 0) or 0) for p in players) if players else 0
    enough = busiest >= REFERENCE_MIN_BUSIEST
    threshold = min(REFERENCE_MIN_MINUTES, REFERENCE_MINUTES_RATIO * busiest)

    out: dict[int, ReferencePlayer] = {}
    for pos in POSITIONS:
        pool = [
            p for p in players
            if getattr(p, "element_type", None) == pos
            and (getattr(p, "minutes", 0) or 0) >= threshold
        ] if enough else []
        if not pool:
            out[pos] = _fallback_reference(pos)
            continue
        mins = _median([p.minutes for p in pool])
        scale = mins / 90.0 if mins else 1.0

        def per90(attr: str) -> float:
            vals = [
                (getattr(p, attr, 0.0) or 0.0) / ((p.minutes or 1) / 90.0)
                for p in pool
            ]
            return _median(vals)

        out[pos] = ReferencePlayer(
            element_type=pos,
            minutes_season=int(mins),
            xg_season=per90("expected_goals") * scale,
            xa_season=per90("expected_assists") * scale,
            saves_season=int(per90("saves") * scale) if pos == 1 else 0,
            dc_season=per90("defensive_contribution") * scale,
            yellow_season=int(round(per90("yellow_cards") * scale)),
            red_season=0,
            bps_season=int(per90("bps") * scale),
            cbi_season=per90("clearances_blocks_interceptions") * scale,
            source=f"trung vị {len(pool)} cầu thủ ≥{int(threshold)} phút",
        )
    return out


def reference_points(
    ref: ReferencePlayer,
    lam_for: float,
    lam_against: float,
    baseline: float,
    stats_season: str | None = None,
) -> float:
    """Điểm kỳ vọng của cầu thủ tham chiếu TRONG TRẬN NÀY.

    `team_avg_gf=baseline` là điểm mấu chốt: `expected_points` co giãn sản lượng
    tấn công theo `λ_for / team_avg_gf`, nên lấy mốc là nền của GIẢI (không phải
    trung bình của đội) khiến kết quả chỉ phản ánh trận đấu. Nếu lấy trung bình
    của đội thì một trận λ 1.8 sẽ ra "dễ" cho đội yếu và "khó" cho đội mạnh — tức
    là đo lại chính đội bóng, đúng thứ mà bước này cố ý loại ra.
    """
    bd = expected_points(
        element_type=ref.element_type,
        minutes_season=ref.minutes_season,
        xg_season=ref.xg_season,
        xa_season=ref.xa_season,
        saves_season=ref.saves_season,
        dc_season=ref.dc_season,
        yellow_season=ref.yellow_season,
        red_season=ref.red_season,
        bps_season=ref.bps_season,
        cbi_season=ref.cbi_season,
        stats_season=stats_season,
        penalties_order=None,
        xmins=REFERENCE_XMINS,
        p_start=REFERENCE_P_START,
        p_appear=REFERENCE_P_APPEAR,
        p_60_plus=REFERENCE_P_60,
        lam_team_goals=lam_for,
        lam_conceded=lam_against,
        team_avg_gf=baseline,
    )
    return bd.xp


def defence_value(lam_against: float) -> float:
    """BƯỚC 4 — `4·P(sạch lưới) − trừ điểm thủng lưới`, theo luật đang áp.

    Cả hai vế đọc từ `RULES` chứ không ghi cứng số 4: FPL đã từng đổi điểm sạch
    lưới, và một hằng số chép tay ở đây sẽ lặng lẽ sai vào đúng mùa họ đổi.

    Trừ điểm tính theo **mốc trọn** (−1 mỗi 2 bàn thua), không theo tỷ lệ: thủng
    một bàn không mất điểm nào, và `λ/2` sẽ phạt oan đúng những trận sát nút.
    """
    cs_points = RULES.clean_sheet_points.get(2, 4)
    cs_prob = math.exp(-max(lam_against, 0.0))
    deduction = _expected_floor_div(lam_against, 2) * RULES.points_per_two_conceded
    return cs_points * cs_prob + deduction


# --------------------------------------------------------------- xếp hạng ---
@dataclass
class FixtureRating:
    """Một ô lịch: một đội, một trận. Percentile được điền sau khi có cả bảng."""

    team_id: int
    opponent_id: int
    gameweek: int
    is_home: bool
    fixture_id: int | None
    proj_goals_for: float
    proj_goals_against: float
    clean_sheet_prob: float
    defence_value: float
    role_points: dict[int, float]
    has_market: bool
    market_weight: float
    has_kickoff: bool

    # điền ở `rank_fixtures`
    attack_ease: float | None = None
    defence_ease: float | None = None
    role_ease: dict[int, float] = field(default_factory=dict)
    attack_difficulty: int | None = None
    defence_difficulty: int | None = None
    role_fdr: dict[int, int] = field(default_factory=dict)


def rate_fixture(
    ts: TeamStrength,
    team_id: int,
    opp_id: int,
    gw: int,
    is_home: bool,
    *,
    references: dict[int, ReferencePlayer],
    fixture_id: int | None = None,
    has_kickoff: bool = True,
    stats_season: str | None = None,
) -> FixtureRating:
    """λ của một trận (BƯỚC 2+3) → các đại lượng thô của BƯỚC 4.

    Chưa có percentile ở đây: percentile chỉ có nghĩa khi đã biết cả phân bố, nên
    nó được điền ở `rank_fixtures` sau khi toàn bộ cửa sổ đã được chấm.
    """
    t_for, t_against = ts.terms(team_id, opp_id, is_home, fixture_id)
    lam_for, lam_against = t_for.lam, t_against.lam
    return FixtureRating(
        team_id=team_id,
        opponent_id=opp_id,
        gameweek=gw,
        is_home=is_home,
        fixture_id=fixture_id,
        proj_goals_for=round(lam_for, 3),
        proj_goals_against=round(lam_against, 3),
        clean_sheet_prob=round(math.exp(-lam_against), 4),
        defence_value=round(defence_value(lam_against), 4),
        role_points={
            pos: round(
                reference_points(
                    references[pos], lam_for, lam_against, ts.baseline, stats_season
                ),
                4,
            )
            for pos in POSITIONS
            if pos in references
        },
        has_market=ts.has_market(team_id, opp_id, is_home),
        market_weight=round(t_for.market_weight, 3),
        has_kickoff=has_kickoff,
    )


def rank_fixtures(ratings: list[FixtureRating]) -> None:
    """BƯỚC 4 — điền percentile và FDR cho từng ô, tại chỗ.

    Phân bố tham chiếu là **chính cửa sổ đang xem**. Đó là điều đúng cần làm cho
    một bảng lịch: câu hỏi người dùng đang hỏi là "trong 8 vòng tới, ô nào dễ", chứ
    không phải "ô này dễ so với lịch sử Ngoại hạng".
    """
    if not ratings:
        return
    att = percentiles([r.proj_goals_for for r in ratings])
    dfc = percentiles([r.defence_value for r in ratings])
    for r, a, d in zip(ratings, att, dfc):
        r.attack_ease = round(a, 2)
        r.defence_ease = round(d, 2)
        r.attack_difficulty = fdr_from_percentile(a)
        r.defence_difficulty = fdr_from_percentile(d)

    for pos in POSITIONS:
        subset = [r for r in ratings if pos in r.role_points]
        if not subset:
            continue
        pcts = percentiles([r.role_points[pos] for r in subset])
        for r, p in zip(subset, pcts):
            r.role_ease[pos] = round(p, 2)
            r.role_fdr[pos] = fdr_from_percentile(p)


# ---------------------------------------------------------------- BƯỚC 5 ----
@dataclass
class ScheduleEase:
    team_id: int
    role: int
    ease: float                 # sau khi trừ phạt, thang percentile 0..100
    raw_ease: float             # trước khi trừ phạt
    uncertainty_penalty: float
    fdr: int = 3
    n_fixtures: int = 0
    blanks: list[int] = field(default_factory=list)
    doubles: list[int] = field(default_factory=list)


def uncertainty_penalty(
    *,
    share_no_market: float,
    evidence_weight: float,
    share_no_kickoff: float,
) -> float:
    """Phạt bất định của BƯỚC 5, tính bằng điểm percentile.

    Ba nguồn, và cả ba đều là điều **đã biết là chưa biết**, không phải phỏng đoán:

    * `share_no_market` — phần lịch chưa nhà cái nào ra giá. λ của những trận đó
      hoàn toàn là mô hình nội bộ.
    * `evidence_weight` — BƯỚC 1 đã gộp được bao nhiêu trong 5 nguồn cho đội này
      (1.0 = đủ cả năm). Đội mới lên hạng và đội đổi HLV luôn thấp hơn.
    * `share_no_kickoff` — trận chưa có giờ chính thức, tức còn có thể bị dời.

    Khoản phạt này gần như đồng đều trước vòng 1, và đó là hành vi đúng: nó **không
    nên** đảo thứ tự các đội, nó chỉ nên hạ mức tin cậy của cả bảng. Nó chỉ cắn
    thật khi hai đội chênh nhau về lượng bằng chứng — ví dụ một đội có kèo cho 6/8
    vòng còn đội kia chỉ có 1/8.
    """
    thin_prior = max(0.0, min(1.0, 1.0 - evidence_weight))
    raw = (
        UNC_W_NO_MARKET * max(0.0, min(1.0, share_no_market))
        + UNC_W_THIN_PRIOR * thin_prior
        + UNC_W_NO_KICKOFF * max(0.0, min(1.0, share_no_kickoff))
    )
    return round(UNCERTAINTY_MAX_PENALTY * raw, 3)


def schedule_ease(
    ratings_by_team: dict[int, list[FixtureRating]],
    gws: list[int],
    role: int,
    *,
    evidence_weight: dict[int, float],
    half_life: float = DECAY_HALF_LIFE_GWS,
) -> list[ScheduleEase]:
    """BƯỚC 5 — gộp các ô thành MỘT điểm lịch cho mỗi đội, ở một vai trò.

    Gộp ở cấp **vòng đấu** chứ không phải cấp trận, và đó là chỗ duy nhất xử lý
    đúng được vòng trắng với vòng đôi: một vòng trắng là 0 điểm (không đá thì
    không có điểm, đó là sự thật chứ không phải dữ liệu thiếu), một vòng đôi là
    tổng của hai trận. Lấy trung bình theo TRẬN sẽ làm vòng trắng biến mất khỏi
    phép tính và làm vòng đôi trông y hệt vòng đơn — đúng hai lỗi khiến các bảng
    FDR thông thường vô dụng ở giai đoạn tái đấu.

    Percentile được lấy trên tổng điểm CẢ VÒNG của mọi (đội, vòng) trong cửa sổ,
    nên thang đo là chung cho cả bảng.
    """
    team_ids = list(ratings_by_team.keys())
    if not team_ids or not gws:
        return []

    # điểm cả vòng của từng (đội, vòng)
    gw_points: dict[tuple[int, int], float] = {}
    gw_count: dict[tuple[int, int], int] = {}
    for tid, rows in ratings_by_team.items():
        for gw in gws:
            cells = [r for r in rows if r.gameweek == gw]
            gw_points[(tid, gw)] = sum(r.role_points.get(role, 0.0) for r in cells)
            gw_count[(tid, gw)] = len(cells)

    keys = list(gw_points.keys())
    pcts = dict(zip(keys, percentiles([gw_points[k] for k in keys])))

    out: list[ScheduleEase] = []
    for tid in team_ids:
        num = den = 0.0
        for offset, gw in enumerate(gws):
            w = decay_weight(offset, half_life)
            num += w * pcts[(tid, gw)]
            den += w
        raw = num / den if den else 50.0

        rows = ratings_by_team[tid]
        n_fix = len(rows)
        share_no_market = (
            sum(1 for r in rows if not r.has_market) / n_fix if n_fix else 1.0
        )
        share_no_kickoff = (
            sum(1 for r in rows if not r.has_kickoff) / n_fix if n_fix else 1.0
        )
        pen = uncertainty_penalty(
            share_no_market=share_no_market,
            evidence_weight=evidence_weight.get(tid, 0.0),
            share_no_kickoff=share_no_kickoff,
        )
        out.append(
            ScheduleEase(
                team_id=tid,
                role=role,
                ease=round(max(0.0, raw - pen), 2),
                raw_ease=round(raw, 2),
                uncertainty_penalty=pen,
                n_fixtures=n_fix,
                blanks=[gw for gw in gws if gw_count[(tid, gw)] == 0],
                doubles=[gw for gw in gws if gw_count[(tid, gw)] >= 2],
            )
        )

    # ---------------------------------------------------------- BƯỚC 6 ------
    # Xếp hạng rồi chia đúng năm nhóm bằng nhau. Chia theo THỨ HẠNG chứ không theo
    # ngưỡng giá trị: 20 đội phải ra đúng 4 đội mỗi bậc, kể cả khi cả giải chụm
    # lại quanh nhau — mà trước vòng 1 thì đúng là như vậy.
    out.sort(key=lambda s: s.ease, reverse=True)
    n = len(out)
    for i, s in enumerate(out):
        s.fdr = quintile_fdr(i, n)
    return out
