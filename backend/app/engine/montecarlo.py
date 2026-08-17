"""Monte Carlo simulation of gameweek points (spec §8).

Simulates at the *team-fixture* level so that correlation is preserved:
  * a team's clean sheet is shared by its GK + defenders (same conceded draw);
  * players' goals are drawn from the SAME team-goals total, so a high-scoring
    match lifts team-mates together (no false independence assumption).

Returns per-player point distributions -> summary percentiles & tail probs.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from app.scoring import RULES


@dataclass
class MCPlayer:
    player_id: int
    element_type: int
    p_start: float
    p_sub: float
    p_60_plus: float
    share_goal: float       # player's share of team goals
    share_assist: float     # player's share of team assists (approx)
    saves90: float
    dc_hit_prob: float      # P(defensive-contribution threshold) if started
    yellow90: float
    bonus_base: float       # expected bonus at full involvement


# --------------------------------------------------------- xoay tua DGW -----
# Tương quan giữa hai lần "đá chính" của CÙNG một cầu thủ trong CÙNG một vòng đôi.
# Âm, vì huấn luyện viên xoay tua: đá trận 1 thì dễ được nghỉ trận 2, và ngược lại.
#
# Bản trước rút hai trận hoàn toàn độc lập (đo được tương quan +0.0003). Điều đó
# **không sai ở kỳ vọng** — xP là tổng của hai kỳ vọng, và độc lập hay không thì
# tổng đó không đổi — nhưng nó sai ở **phương sai**: độc lập cho
# Var = 2p(1−p), còn tương quan âm cho Var = 2p(1−p)(1+ρ) < đó. Nghĩa là mô hình
# cũ thổi phồng cả trần lẫn sàn của cầu thủ đá vòng đôi, tức thổi phồng đúng hai
# con số mà quyết định Bench Boost và Triple Captain dựa vào.
#
# Giá trị này **không khớp được từ dữ liệu đang có** (DB chưa có vòng đôi nào đã
# đá). Nó là hằng số cấu hình, đặt ở mức vừa phải; 0 đưa về hành vi cũ.
ROTATION_RHO = -0.25

# A single player may never be credited with more than this share of the pool,
# however few team-mates are on the pitch alongside him.
MAX_INDIVIDUAL_SHARE = 0.95
# Ceiling on how far an absent player's share may inflate those who did play.
MAX_SHARE_TRANSFER = 3.0


def _effective_shares(
    players: list[MCPlayer], played: dict[int, np.ndarray], attr: str, n: int
) -> tuple[dict[int, np.ndarray], float]:
    """Per-simulation shares, with an absent player's share passed to the rest.

    The old code simply zeroed a missing player's goals, so his share of the
    team's output evaporated: a team-mate's expected points were byte-identical
    whether the first-choice striker started 95% or 5% of the time. Understudies
    were therefore under-rated by construction. Here the shares of whoever
    actually took the pitch are scaled back up to the full squad total, which
    hands the absentee's share to his replacements in proportion.
    """
    full = sum(getattr(p, attr) for p in players)
    if full <= 1e-9:
        return {p.player_id: np.zeros(n) for p in players}, 0.0

    present = np.zeros(n)
    for p in players:
        present += getattr(p, attr) * played[p.player_id]
    scale = np.where(present > 1e-9, full / np.maximum(present, 1e-9), 0.0)
    scale = np.minimum(scale, MAX_SHARE_TRANSFER)

    eff = {
        p.player_id: np.minimum(
            getattr(p, attr) * scale * played[p.player_id], MAX_INDIVIDUAL_SHARE
        )
        for p in players
    }
    return eff, full


def _allocate(
    totals: np.ndarray,
    eff: dict[int, np.ndarray],
    order: list[int],
    rng: np.random.Generator,
    cap: dict[int, np.ndarray] | None = None,
) -> dict[int, np.ndarray]:
    """Split `totals` among players by drawing conditional binomials in turn.

    This is a multinomial sampled sequentially, which numpy can vectorise while
    `rng.multinomial` cannot (both the count and the probability vector vary per
    simulation). Drawing each player independently — as the old code did — let
    the parts exceed the whole: 20.9% of simulated matches allocated more goals
    than the team had scored, once handing out 14 goals in a 4-goal match. The
    mean was right; the tail was not, and the tail is exactly where P(haul) and
    the ceiling are read off.

    The pool starts at 1.0 rather than at the sum of shares, so goals nobody in
    the modelled squad is credited with (own goals, fringe players excluded from
    the simulation) stay unattributed instead of being forced onto the last
    player in the list.
    """
    remaining = totals.astype(np.int64).copy()
    pool = np.ones(len(remaining))
    out: dict[int, np.ndarray] = {}
    for pid in order:
        share = eff[pid]
        prob = np.divide(share, pool, out=np.zeros_like(share), where=pool > 1e-9)
        np.clip(prob, 0.0, 1.0, out=prob)
        available = remaining if cap is None else np.minimum(remaining, cap[pid])
        drawn = rng.binomial(np.maximum(available, 0), prob)
        out[pid] = drawn
        remaining -= drawn
        pool = np.maximum(pool - share, 0.0)
    return out


def rotation_start_prob(
    p_prev: float, p_now: float, rho: float
) -> tuple[float, float]:
    """(P đá chính | đã đá trận trước, P đá chính | đã nghỉ trận trước).

    Dựng phân phối hai điểm giữ **nguyên vẹn biên duyên**: dù xoay tua mạnh đến
    đâu, xác suất đá chính trận này cộng lại vẫn đúng bằng `p_now`, nên kỳ vọng —
    tức xP — không đổi một chút nào. Chỉ phụ thuộc giữa hai trận là đổi.

        Cov = ρ·√(p_prev(1−p_prev)·p_now(1−p_now))
        P(đá | đã đá)    = p_now + Cov/p_prev
        P(đá | đã nghỉ)  = p_now − Cov/(1−p_prev)

    Kiểm tra: p_prev·P(đá|đã đá) + (1−p_prev)·P(đá|đã nghỉ) = p_now, đúng với mọi ρ.

    **Giới hạn khả thi là tính năng, không phải khiếm khuyết.** Cả hai xác suất
    phải nằm trong [0,1], nên |Cov| ≤ min(p_prev·p_now, (1−p_prev)(1−p_now)). Với
    một trụ cột chắc suất (p = 0.95) điều đó giới hạn ρ ở −0.05: **không còn chỗ
    nào để xoay**. Với một cầu thủ luân phiên (p = 0.6) nó cho phép tới −0.67. Đúng
    thực tế, và có được miễn phí từ ràng buộc toán chứ không cần thêm tham số nào.
    """
    p_prev = min(max(p_prev, 0.0), 1.0)
    p_now = min(max(p_now, 0.0), 1.0)
    if p_prev <= 0.0 or p_prev >= 1.0 or p_now <= 0.0 or p_now >= 1.0 or rho == 0.0:
        return p_now, p_now
    sd = math.sqrt(p_prev * (1 - p_prev) * p_now * (1 - p_now))
    cov = rho * sd
    cov = max(cov, -min(p_prev * p_now, (1 - p_prev) * (1 - p_now)))
    cov = min(cov, min(p_prev * (1 - p_now), (1 - p_prev) * p_now))
    return (
        min(max(p_now + cov / p_prev, 0.0), 1.0),
        min(max(p_now - cov / (1 - p_prev), 0.0), 1.0),
    )


def simulate_fixture(
    players: list[MCPlayer],
    lam_for: float,
    lam_conceded: float,
    n: int,
    rng: np.random.Generator,
    collect: dict[int, dict[str, float]] | None = None,
    *,
    prior_started: dict[int, np.ndarray] | None = None,
    prior_p_start: dict[int, float] | None = None,
    rotation_rho: float = ROTATION_RHO,
    out_started: dict[int, np.ndarray] | None = None,
) -> dict[int, np.ndarray]:
    """Return {player_id: array[n] of points} for one fixture.

    `collect` là chế độ chẩn đoán: truyền một dict vào thì mỗi cầu thủ được ghi
    trung bình TỪNG thành phần điểm, dùng để so với phân rã giải tích ở
    `engine/xpoints.py`. Hai đường tính cùng một đại lượng nên lệch ở thành phần
    nào là lỗi ở đúng thành phần đó — không có bước này thì chỉ thấy tổng lệch mà
    không biết vì sao.

    `prior_started` là mặt nạ "đã đá chính ở trận TRƯỚC của cùng vòng đấu này",
    theo từng lần mô phỏng. Có nó thì xác suất đá chính trận này được điều chỉnh
    theo từng lần mô phỏng để tạo ra xoay tua (xem `rotation_start_prob`); biên
    duyên giữ nguyên nên xP không đổi, chỉ phương sai đổi. `out_started` nhận lại
    mặt nạ của trận này để chuỗi được nối sang trận kế tiếp.
    """
    team_goals = rng.poisson(max(lam_for, 0.01), n)
    team_conceded = rng.poisson(max(lam_conceded, 0.01), n)
    clean_sheet = team_conceded == 0
    conceded_penalty = -(team_conceded // 2)

    # ---- who is on the pitch, drawn once and reused by every stage ----
    started_by: dict[int, np.ndarray] = {}
    subbed_by: dict[int, np.ndarray] = {}
    played_by: dict[int, np.ndarray] = {}
    reached60_by: dict[int, np.ndarray] = {}
    for p in players:
        r = rng.random(n)

        prev = prior_started.get(p.player_id) if prior_started else None
        if prev is None:
            p_start_arr = p.p_start
        else:
            p_prev = (prior_p_start or {}).get(p.player_id, p.p_start)
            hi, lo = rotation_start_prob(p_prev, p.p_start, rotation_rho)
            p_start_arr = np.where(prev, hi, lo)

        st = r < p_start_arr
        sb = (r >= p_start_arr) & (r < np.minimum(p_start_arr + p.p_sub, 1.0))
        started_by[p.player_id] = st
        subbed_by[p.player_id] = sb
        played_by[p.player_id] = st | sb
        # Mốc 60 phút rút từ CÙNG biến ngẫu nhiên `r`. Vì p_60_plus <= p_start, tập
        # "đá đủ 60" nằm gọn trong tập "đá chính" — đúng quan hệ thực tế, và không
        # cần thêm một lần rút độc lập (rút riêng sẽ sinh ra người đá đủ 60 phút mà
        # không đá chính).
        #
        # Khi có xoay tua, ngưỡng 60 phút phải dịch THEO TỶ LỆ với xác suất đá chính
        # đã điều chỉnh: giữ nguyên tỷ lệ "đá chính thì trụ được 60 phút", chứ không
        # để một cầu thủ được xoay vào lại có xác suất trụ 60 phút y như cũ.
        ratio = min(p.p_60_plus, p.p_start) / p.p_start if p.p_start > 0 else 0.0
        reached60_by[p.player_id] = r < (p_start_arr * ratio)

    if out_started is not None:
        out_started.update(started_by)

    # ---- share out the team's goals, then its assists ----
    order = [p.player_id for p in players]
    goal_eff, _ = _effective_shares(players, played_by, "share_goal", n)
    goals_by = _allocate(team_goals, goal_eff, order, rng)

    # A player cannot assist his own goal, so each is limited to the goals his
    # team-mates scored. The old draw was independent of the goal draw entirely,
    # which credited the same player with both on 26% of simulated matches.
    assist_eff, _ = _effective_shares(players, played_by, "share_assist", n)
    assist_cap = {
        pid: np.maximum(team_goals - goals_by[pid], 0) for pid in order
    }
    assists_by = _allocate(team_goals, assist_eff, order, rng, cap=assist_cap)

    out: dict[int, np.ndarray] = {}
    for p in players:
        started = started_by[p.player_id]
        subbed = subbed_by[p.player_id]
        played = played_by[p.player_id]
        got60 = reached60_by[p.player_id]
        pts = np.zeros(n, dtype=float)

        comp: dict[str, float] = {}

        # appearance — 2 điểm cần ĐỦ 60 PHÚT, không phải chỉ cần đá chính. Bản
        # trước trao 2 điểm cho mọi người đá chính, nên người bị thay ra phút 55
        # vẫn được 2 thay vì 1; đo được là đội lên ~0.03 điểm mỗi cầu thủ mỗi vòng.
        appearance = (np.where(got60, RULES.points_play_60_plus, 0.0)
                      + np.where(started & ~got60, RULES.points_play_under_60, 0.0)
                      + np.where(subbed, RULES.points_play_under_60, 0.0))
        pts += appearance
        comp["appearance"] = float(appearance.mean())

        g = goals_by[p.player_id]
        goal_pts = g * RULES.goal_points.get(p.element_type, 4)
        pts += goal_pts
        comp["goals"] = float(np.mean(goal_pts))

        a = assists_by[p.player_id]
        assist_pts = a * RULES.assist_points
        pts += assist_pts
        comp["assists"] = float(np.mean(assist_pts))

        # clean sheet (needs a start ~ 60'+)
        cs_pts = RULES.clean_sheet_points.get(p.element_type, 0)
        comp["clean_sheet"] = 0.0
        if cs_pts:
            cs_arr = np.where(got60 & clean_sheet, cs_pts, 0.0)
            pts += cs_arr
            comp["clean_sheet"] = float(cs_arr.mean())

        # conceded penalty (GK/DEF, needs 60'+)
        comp["conceded"] = 0.0
        if p.element_type in RULES.conceded_penalty_positions:
            conc = np.where(got60, conceded_penalty, 0)
            pts += conc
            comp["conceded"] = float(np.mean(conc))

        # saves (GK). The 0.3 factor is for a keeper who came ON, so it must not
        # apply to one who never left the bench: `np.where(started, 1, 0.3)` gave
        # a permanent reserve 0.064 points a match from saves he could not have
        # made — small, but it quietly flattered every cheap bench goalkeeper.
        comp["saves"] = 0.0
        if p.element_type == 1 and p.saves90 > 0:
            rate = np.where(started, 1.0, np.where(subbed, 0.3, 0.0))
            saves = rng.poisson(p.saves90 * rate, n)
            save_arr = np.where(played, np.floor(saves / RULES.saves_per_point), 0.0)
            pts += save_arr
            comp["saves"] = float(save_arr.mean())

        # defensive contribution
        comp["defcon"] = 0.0
        if p.element_type in RULES.defcon_positions and p.dc_hit_prob > 0:
            hit = rng.random(n) < p.dc_hit_prob
            dc_arr = np.where(started & hit, RULES.defcon_points, 0.0)
            pts += dc_arr
            comp["defcon"] = float(dc_arr.mean())

        # bonus — 1..3 điểm khi có mặt trong top 3 BPS của trận.
        #
        # Tần suất được khoá vào KỲ VỌNG GIẢI TÍCH (`bonus_base`, do
        # engine/bonus.allocate chia trong nội bộ trận). Khi mask nổ, số điểm rút
        # trong {1,2,3} nên trung bình là 2 — vậy để trung bình mô phỏng khớp kỳ
        # vọng, xác suất mask phải là `bonus_base / 2`.
        #
        # Bản trước dùng `min(1.0, 0.4 + bonus_base)` với `bonus_base` bị cắt ở
        # 0.6: mọi cầu thủ có kỳ vọng bonus từ 0.6 trở lên đều bão hoà ở xác suất
        # 1.0, tức là *luôn* được bonus. Với mô hình bonus mới (cầu thủ đầu bảng
        # ~1.8 điểm) thì phần lớn nhóm đầu rơi vào chỗ bão hoà đó.
        returned = (g + a) > 0
        bonus = np.where(
            returned,
            rng.integers(1, 4, n),  # 1..3 when involved
            np.where(started & clean_sheet & (p.element_type <= 2), 1, 0),
        )
        # Tần suất phải khớp KỲ VỌNG GIẢI TÍCH, mà kỳ vọng đó phụ thuộc giá trị
        # thực sự được rút — không phải một hằng số. Bản trước lấy `bonus_base / 2`
        # vì cho rằng rút trong {1,2,3} nên trung bình là 2; nhưng mảng `bonus` ở
        # trên chỉ là 1..3 KHI cầu thủ có bàn/kiến tạo, còn lại là 1 (hậu vệ giữ
        # sạch lưới) hoặc 0. Trung bình thật vì thế nhỏ hơn 2, và mọi vị trí bị hụt
        # 0.09–0.13 điểm bonus mỗi vòng. Chia đúng trung bình đo được thì khớp.
        target = min(max(0.0, p.bonus_base), float(RULES.max_bonus))
        drawn_mean = float(np.mean(bonus))
        if drawn_mean <= 1e-9:
            bonus_arr = np.zeros(n)
        elif drawn_mean >= target:
            # thừa: hạ TẦN SUẤT xuống cho khớp kỳ vọng
            bonus_arr = np.where(rng.random(n) < target / drawn_mean, bonus, 0)
        else:
            # Thiếu: hạ tần suất không giúp được gì, phải NÂNG GIÁ TRỊ. Trước đây
            # chỗ này chỉ `min(1.0, ...)` nên cầu thủ có kỳ vọng bonus cao (tiền đạo
            # đầu bảng, ~1.6 điểm) bị chặn ở đúng trung bình của mảng rút (~1.0) —
            # đo được là tiền đạo chỉ đạt 83% mức giải tích, dù ba thành phần khác
            # đã khớp. Với xác suất q, thay giá trị rút bằng mức trần 3 điểm; chọn q
            # để trung bình chạm đúng `target`.
            headroom = float(RULES.max_bonus) - drawn_mean
            q = 0.0 if headroom <= 1e-9 else (target - drawn_mean) / headroom
            boost = rng.random(n) < min(1.0, max(0.0, q))
            bonus_arr = np.where(boost, float(RULES.max_bonus), bonus)
        pts += bonus_arr
        comp["bonus"] = float(np.mean(bonus_arr))

        # yellow cards
        comp["cards"] = 0.0
        if p.yellow90 > 0:
            yc = rng.random(n) < (p.yellow90 * np.where(played, 1.0, 0.0))
            card_arr = np.where(yc, RULES.yellow_card_points, 0)
            pts += card_arr
            comp["cards"] = float(np.mean(card_arr))

        out[p.player_id] = pts
        if collect is not None:
            comp["total"] = float(pts.mean())
            collect[p.player_id] = comp
    return out


def summarise(points: np.ndarray) -> dict:
    return {
        "mc_mean": float(np.mean(points)),
        "mc_median": float(np.median(points)),
        # P10 là "sàn xấu": mức mà 1 trong 10 vòng sẽ tệ hơn. Cần cho quyết
        # định giữ/bán, vì P25 chưa chạm phần đuôi mà người chơi thật sự sợ.
        "mc_p10": float(np.percentile(points, 10)),
        "mc_p25": float(np.percentile(points, 25)),
        "mc_p75": float(np.percentile(points, 75)),
        "mc_p90": float(np.percentile(points, 90)),
        "mc_ceiling": float(np.percentile(points, 95)),
        "p_blank": float(np.mean(points <= 2)),
        "p_returns": float(np.mean(points >= 5)),
        "p_haul": float(np.mean(points >= 10)),
        # A captain doubles the score, so >=15 is the "won me the gameweek"
        # tail that separates ceiling picks from merely high-EV ones.
        "p_15": float(np.mean(points >= 15)),
        "variance": float(np.var(points)),
    }
