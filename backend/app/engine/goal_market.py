"""Từ kèo "ghi bàn bất kỳ lúc nào" sang **số bàn kỳ vọng** — phần toán học.

Đây là chỗ dễ sai nhất của cả tính năng, nên từng bước được tách ra và test riêng.

**1. Xác suất ghi ÍT NHẤT MỘT bàn ≠ số bàn kỳ vọng.** Haaland p=0.60 không phải
0.60 bàn: dưới Poisson thì `p = 1 − e^(−λ)` nên `λ = −ln(1 − p) = 0.92`. Lẫn hai
đại lượng này là sai 50% ở nhóm đầu.

**2. Loại biên (vig) phải làm trong nội bộ MỘT nhà cái.** Khi có cả cửa Yes và No::

    p = (1/oddsYes) / [(1/oddsYes) + (1/oddsNo)]

Chỉ có cửa Yes thì `1/oddsYes` là xác suất **CÓ BIÊN**, cao hơn thật — với biên
điển hình 6–10% của kèo ghi bàn, dùng thẳng nó sẽ thổi số bàn kỳ vọng lên tương
ứng. Nên trường hợp đó được đánh dấu `devigged=False` và **không được đưa vào xP**.

**3. KHÔNG chuẩn hoá xác suất giữa các cầu thủ.** Nhiều người cùng ghi bàn trong
một trận là chuyện bình thường, nên tổng các xác suất ghi bàn của một đội **không
phải** và không được ép về 1 (khác hẳn 1X2, nơi ba kết cục loại trừ nhau).

**4. Chuyển đổi phải đi qua TỪNG KỊCH BẢN phút thi đấu.** Kèo ghi bàn là một con
số duy nhất cho cả trận, nhưng một cầu thủ có thể đá chính (≈84 phút) hoặc vào sân
từ ghế (≈20 phút) — hai λ rất khác nhau. Ta giải `r90` (số bàn mỗi 90 phút **trên
sân**) sao cho mô hình tái tạo đúng giá thị trường::

    p_thị_trường = P(chính)·(1 − e^(−r90·phút_chính/90))
                 + P(ghế) ·(1 − e^(−r90·phút_ghế/90))        [điều kiện "any"]

rồi lấy kỳ vọng **trong từng kịch bản**::

    E[G] = P(chính)·r90·phút_chính/90 + P(ghế)·r90·phút_ghế/90

Làm thẳng `−ln(1−p)` trên xác suất TỔNG rồi nhân phút là sai hai lần: nó coi như
cầu thủ chắc chắn ra sân, rồi nhân thêm một yếu tố phút mà giá thị trường vốn đã
bao gồm.

**5. Điều kiện áp dụng của kèo quyết định phép chia.** Hầu hết nhà cái ghi "hoàn
tiền nếu cầu thủ không ra sân" — khi đó giá là xác suất **có điều kiện đã ra sân**,
và mẫu số phải là `P(chính) + P(ghế)`. Nếu kèo vẫn được tính khi cầu thủ không ra
sân thì giá là xác suất không điều kiện. **The Odds API không phát hành luật thanh
toán**, nên mặc định là `unknown`: hiển thị tham khảo, KHÔNG đưa vào xP. Người dùng
xác minh với nhà cái của mình rồi đặt `ODDS_PLAYER_GOALS_CONDITION`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# Điều kiện áp dụng của kèo ghi bàn.
COND_UNKNOWN = "unknown"    # không rõ -> chỉ tham khảo, không vào xP
COND_APPEARS = "appears"    # hoàn tiền nếu không ra sân -> xác suất CÓ ĐIỀU KIỆN
COND_ANY = "any"            # tính cả khi không ra sân -> xác suất KHÔNG điều kiện
CONDITIONS = (COND_UNKNOWN, COND_APPEARS, COND_ANY)

CONDITION_LABEL_VI = {
    COND_UNKNOWN: "Chưa xác minh điều kiện áp dụng (chỉ tham khảo)",
    COND_APPEARS: "Áp dụng khi cầu thủ ra sân (hoàn tiền nếu không ra sân)",
    COND_ANY: "Áp dụng bất kể cầu thủ có ra sân hay không",
}

# Trần của r90 khi dò nghiệm. 6 bàn mỗi 90 phút là vô lý ở mọi giải đấu, nên nó chỉ
# là biên trên của phép dò chứ không phải một ràng buộc mô hình.
MAX_R90 = 6.0


def devig_yes_no(price_yes: float | None, price_no: float | None) -> tuple[float | None, bool]:
    """(xác suất, đã loại biên) từ một cặp giá thập phân của CÙNG một nhà cái.

    Chỉ có cửa Yes thì trả về xác suất CÓ BIÊN kèm cờ `False` — người gọi phải tự
    quyết định, và ở dự án này nghĩa là không được đưa vào xP.
    """
    y = price_yes if (price_yes or 0) > 1 else None
    n = price_no if (price_no or 0) > 1 else None
    if y is None:
        return None, False
    if n is None:
        return 1.0 / y, False
    inv_y, inv_n = 1.0 / y, 1.0 / n
    s = inv_y + inv_n
    if s <= 0:
        return None, False
    return inv_y / s, True


def consensus_probability(probs: list[float]) -> float | None:
    """Đồng thuận giữa các nhà cái bằng **trung vị**, như phần kèo đội đang làm.

    Trung bình để một nhà cái treo giá lệch dịch đồng thuận 1/n; trung vị bỏ qua
    nó trừ khi nó nằm gần giữa. KHÔNG có chuẩn hoá giữa các cầu thủ ở đây.
    """
    vals = sorted(p for p in probs if p is not None and 0.0 < p < 1.0)
    if not vals:
        return None
    mid = len(vals) // 2
    if len(vals) % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0


def poisson_lambda_from_prob(p: float) -> float:
    """λ = −ln(1 − p). Chỉ đúng dưới giả định Poisson **và cùng điều kiện phút**.

    Dùng trực tiếp hàm này chỉ hợp lý khi cầu thủ coi như chắc chắn đá đủ trận; khi
    có kịch bản đá chính/dự bị thì phải đi qua `market_expected_goals`.
    """
    p = max(0.0, min(0.999999, p))
    return -math.log(1.0 - p)


@dataclass
class MarketGoals:
    """Kết quả chuyển đổi kèo -> số bàn kỳ vọng, kèm mọi thứ cần để giải thích."""

    expected_goals: float | None     # None = không chuyển đổi được
    r90: float | None                # số bàn mỗi 90 phút trên sân, đã khớp giá
    p_market: float
    p_used: float | None             # xác suất thực sự được khớp (sau điều kiện)
    condition: str
    usable_for_xp: bool
    reason: str


def market_expected_goals(
    *,
    p_market: float,
    p_start: float,
    p_sub: float,
    minutes_if_start: float,
    minutes_if_sub: float,
    condition: str = COND_UNKNOWN,
    devigged: bool = True,
    blocked_reason: str | None = None,
) -> MarketGoals:
    """Số bàn kỳ vọng suy từ xác suất ghi bàn của thị trường, theo từng kịch bản.

    Trả `usable_for_xp=False` (kèm lý do) thay vì một con số im lặng khi: điều kiện
    áp dụng chưa xác minh, giá chưa loại biên, hoặc mô hình phút không cho phép
    cầu thủ ra sân.
    """
    p_appear = max(0.0, min(1.0, p_start + p_sub))
    # Giá vô lý (0, 1, âm, hoặc lớn hơn 1) không phải "xác suất bằng 0" — nó là dữ
    # liệu hỏng. Chặn ở đây để một dòng lỗi không lặng lẽ kéo số bàn kỳ vọng về 0.
    if not (0.0 < p_market < 1.0):
        return MarketGoals(
            expected_goals=None, r90=None, p_market=p_market, p_used=None,
            condition=condition, usable_for_xp=False,
            reason=f"Giá thị trường không hợp lệ ({p_market}) — bỏ qua",
        )
    if blocked_reason:
        return MarketGoals(
            expected_goals=None, r90=None, p_market=p_market, p_used=None,
            condition=condition, usable_for_xp=False, reason=blocked_reason,
        )
    if not devigged:
        return MarketGoals(
            expected_goals=None, r90=None, p_market=p_market, p_used=None,
            condition=condition, usable_for_xp=False,
            reason=(
                "Chỉ có cửa Yes nên xác suất chưa loại biên nhà cái — hiển thị tham "
                "khảo, không đưa vào xP"
            ),
        )
    if p_appear <= 1e-6:
        return MarketGoals(
            expected_goals=None, r90=None, p_market=p_market, p_used=None,
            condition=condition, usable_for_xp=False,
            reason="Mô hình phút cho xác suất ra sân ≈ 0 — không chuyển đổi được",
        )

    # Điều kiện quyết định xác suất nào được đem khớp.
    if condition == COND_APPEARS:
        # Giá là P(ghi bàn | ra sân) -> quy về không điều kiện trước khi khớp.
        p_target = max(0.0, min(0.999999, p_market)) * p_appear
    else:
        p_target = max(0.0, min(0.999999, p_market))

    m_start = max(0.0, minutes_if_start)
    m_sub = max(0.0, minutes_if_sub)

    def p_of(r90: float) -> float:
        lam_s = r90 * m_start / 90.0
        lam_b = r90 * m_sub / 90.0
        return p_start * (1.0 - math.exp(-lam_s)) + p_sub * (1.0 - math.exp(-lam_b))

    # p_of tăng đơn điệu theo r90 và chặn trên bởi p_appear, nên chia đôi là đủ và
    # tất định (không có scipy trong repo, và không cần).
    p_max = p_of(MAX_R90)
    if p_target >= p_max:
        r90 = MAX_R90
    else:
        lo, hi = 0.0, MAX_R90
        for _ in range(80):
            mid = (lo + hi) / 2.0
            if p_of(mid) < p_target:
                lo = mid
            else:
                hi = mid
        r90 = (lo + hi) / 2.0

    # Kỳ vọng lấy TRONG TỪNG kịch bản rồi cộng theo xác suất kịch bản. Không nhân
    # thêm hệ số phút nào nữa: phút đã nằm trong chính phép khớp này.
    exp_goals = p_start * r90 * m_start / 90.0 + p_sub * r90 * m_sub / 90.0

    usable = condition in (COND_APPEARS, COND_ANY)
    if usable:
        reason = (
            f"Khớp {CONDITION_LABEL_VI[condition].lower()}: r90 = {r90:.3f} bàn/90' "
            f"trên sân, kịch bản chính {p_start * 100:.0f}% × {m_start:.0f}' và "
            f"ghế {p_sub * 100:.0f}% × {m_sub:.0f}' -> E[G] = {exp_goals:.3f}"
        )
    else:
        reason = (
            "Điều kiện áp dụng của kèo chưa xác minh (The Odds API không phát luật "
            "thanh toán) — chỉ hiển thị tham khảo, không đưa vào xP. Đặt "
            "ODDS_PLAYER_GOALS_CONDITION sau khi xác minh với nhà cái."
        )

    return MarketGoals(
        expected_goals=exp_goals, r90=r90, p_market=p_market, p_used=p_target,
        condition=condition, usable_for_xp=usable, reason=reason,
    )


def blend(model_goals: float, market_goals: float | None, weight: float) -> float:
    """Pha mô hình với thị trường. `weight` là phần của THỊ TRƯỜNG, 0..1.

    Trọng số là **giả định chưa kiểm chứng** cho tới khi có đủ vòng đã đóng để so
    sai số hai phía — xem `ODDS_PLAYER_GOALS_WEIGHT` trong config.
    """
    if market_goals is None:
        return model_goals
    w = max(0.0, min(1.0, weight))
    return (1.0 - w) * model_goals + w * market_goals
