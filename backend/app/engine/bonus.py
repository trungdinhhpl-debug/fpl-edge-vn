"""Bonus points — chia một quỹ CỐ ĐỊNH trong từng trận, không phải công thức rời.

Vì sao phải viết lại: bản trước tính bonus như một đại lượng độc lập cho từng cầu
thủ (`0.35·involvement + 0.0016·bps90·minutes_frac`). Đo trên mùa 2025/26 thì nó
hụt khoảng 60% ở mọi vị trí — thủ môn 33%, hậu vệ 40%, tiền vệ 46%, tiền đạo 38%
so với bonus thực nhận. Kiểm chứng bằng luật bảo toàn cho thấy rõ chỗ sai: mỗi
trận FPL phát **6 điểm bonus** (3 + 2 + 1), tổng cả mùa 2115 ≈ 380 × 6, nhưng mô
hình chỉ phân bổ **2.47 điểm** cho 22 cầu thủ đá chính.

Nguyên nhân là kiểu mô hình, không phải hệ số: bonus là một **cuộc tranh giành
trong một trận cụ thể**, không phải thuộc tính của cầu thủ. Ba người có BPS cao
nhất trận lấy hết 6 điểm, ai đứng thứ tư được 0 — dù BPS của họ cao bao nhiêu.
Một công thức tính riêng từng người không có cách nào tôn trọng chuyện đó.

Mô hình mới:

    trọng số_i = (BPS kỳ vọng của i trong trận này) ^ CONCENTRATION
    bonus_i    = 6 · trọng số_i / Σ trọng số (toàn bộ cầu thủ CẢ HAI ĐỘI)

Tổng luôn đúng 6 điểm mỗi trận, theo đúng luật — không cần hiệu chỉnh lại về sau.

`CONCENTRATION` **đo từ dữ liệu**, không phải chọn tay. Hồi quy log-log bonus/90
theo BPS/90 trên 252 cầu thủ đá từ 900 phút mùa 2025/26 cho số mũ **1.99**
(R² = 0.45 trên thang gốc). Số mũ lớn hơn 1 chính là dấu vết của cơ chế top-3:
BPS gấp đôi thì bonus gấp khoảng bốn lần, chứ không phải gấp đôi. Mô hình tuyến
tính cho R² 0.47 — nhích hơn — nhưng hệ số chặn âm (−0.37) nên nó dự báo bonus âm
cho người BPS thấp, dùng không được.
"""
from __future__ import annotations

from dataclasses import dataclass

# Luật FPL: 3 + 2 + 1 điểm mỗi trận. Đồng đội hoà BPS thì cách chia đổi, nhưng
# tổng phát ra không đổi.
BONUS_POOL_PER_MATCH = 6.0

# Số mũ tập trung, khớp từ mùa 2025/26 (xem docstring).
CONCENTRATION = 1.99

# Hệ số của dạng rời rạc dùng khi KHÔNG biết cả trận (xem `standalone_bonus`).
# bonus/90 = STANDALONE_SCALE · (BPS/90) ^ CONCENTRATION
STANDALONE_SCALE = 0.000996

# BPS cho bàn thắng KHÔNG phải penalty, theo vị trí (1 GK, 2 DEF, 3 MID, 4 FWD).
# Nguồn: thông báo BPS của Premier League — tiền đạo 24, tiền vệ 18, hậu vệ 12.
# NGƯỢC thứ tự với thang điểm FPL (thủ môn 10 / tiền đạo 4): điểm FPL bù cho việc
# hậu vệ ghi bàn hiếm, còn BPS đo đóng góp trong trận. Penalty được 12 BPS bất kể
# vị trí, nhưng ta không tách được tỷ lệ penalty trong xG nên dùng mức chung.
GOAL_BPS = {1: 12.0, 2: 12.0, 3: 18.0, 4: 24.0}

# Hai giá trị dưới đây lấy từ bảng BPS được công bố rộng rãi nhưng KHÔNG xác thực
# lại được từ nguồn chính thức trong lần rà này — Premier League chỉ công bố phần
# THAY ĐỔI của mỗi mùa, không công bố lại toàn bảng. Chúng chỉ ảnh hưởng tới độ
# nghiêng giữa các vị trí, không ảnh hưởng tổng quỹ 6 điểm mỗi trận.
ASSIST_BPS = 9.0
CLEAN_SHEET_BPS = 12.0


@dataclass
class BonusEntry:
    """Một cầu thủ trong một trận, kèm BPS kỳ vọng của trận đó."""

    player_id: int
    expected_bps: float


def expected_fixture_bps(
    *,
    bps90: float,
    minutes_frac: float,
    exp_goals: float = 0.0,
    exp_assists: float = 0.0,
    cs_prob: float = 0.0,
    p_60_plus: float = 0.0,
    element_type: int = 3,
) -> float:
    """BPS kỳ vọng của một cầu thủ trong MỘT trận.

    Nền là `bps90 · minutes_frac`: chính con số cầu thủ đã tự chứng minh, đã quy
    đổi sang luật BPS mùa này (xem `app/bps_rules.py`).

    Rồi cộng phần **lệch riêng của trận này** so với mức nền đó. `bps90` là trung
    bình cả mùa, còn một trận cụ thể có độ khó khác: gặp đội yếu thì cơ hội ghi bàn
    và giữ sạch lưới cao hơn mức trung bình của chính cầu thủ đó. Chỉ phần LỆCH
    được cộng thêm, với hệ số bằng số BPS mà FPL trả cho hành động tương ứng, nên
    không cộng trùng phần đã nằm trong `bps90`:

      * bàn thắng (không tính penalty): tiền đạo 24, tiền vệ 18, hậu vệ/thủ môn 12
      * kiến tạo: 9 BPS
      * sạch lưới: 12 BPS cho thủ môn/hậu vệ đá đủ 60 phút

    Chú ý thứ tự của bàn thắng NGƯỢC với thang điểm FPL (thủ môn 10 điểm, tiền đạo
    4 điểm). Điểm FPL bù cho việc hậu vệ ghi bàn hiếm; BPS thì đo đóng góp trong
    trận nên tiền đạo được nhiều nhất. Ghi ngược cặp này làm tiền đạo bị chia hụt
    bonus một nửa — đã đo được đúng như vậy trước khi sửa.

    Đây là xấp xỉ: mức nền của chính cầu thủ đã chứa một phần các hành động này ở
    tần suất trung bình. Vì trọng số cuối cùng được CHUẨN HOÁ trong nội bộ trận,
    phần cộng trùng chung cho mọi người phần lớn triệt tiêu; cái còn lại là chênh
    lệch tương đối giữa các cầu thủ trong trận, đúng thứ quyết định ai vào top 3.
    """
    base = max(0.0, bps90) * max(0.0, minutes_frac)

    goal_bps = GOAL_BPS.get(element_type, 18.0)
    cs_bps = CLEAN_SHEET_BPS if element_type in (1, 2) else 0.0

    fixture_specific = (
        goal_bps * max(0.0, exp_goals)
        + ASSIST_BPS * max(0.0, exp_assists)
        + cs_bps * max(0.0, cs_prob) * max(0.0, p_60_plus)
    )
    return base + fixture_specific


def allocate(entries: list[BonusEntry], pool: float = BONUS_POOL_PER_MATCH,
             max_bonus: float = 3.0) -> dict[int, float]:
    """Chia `pool` điểm bonus cho các cầu thủ trong một trận.

    `entries` phải gồm cầu thủ của **cả hai đội** — bonus là cuộc tranh giành trong
    trận, nên bỏ một bên là chia sai mẫu số.

    Trần `max_bonus` (3 điểm) là trần thật của luật. Chạm trần thì phần vượt được
    **chia lại** cho những người còn dưới trần, nên tổng vẫn đúng bằng `pool`.
    """
    weights: dict[int, float] = {}
    for e in entries:
        bps = max(0.0, e.expected_bps)
        weights[e.player_id] = bps ** CONCENTRATION if bps > 0 else 0.0

    total = sum(weights.values())
    if total <= 0:
        return {e.player_id: 0.0 for e in entries}

    out = {pid: pool * w / total for pid, w in weights.items()}

    # Chia lại phần vượt trần, tối đa vài lượt (hội tụ rất nhanh với 22 cầu thủ).
    for _ in range(4):
        excess = sum(v - max_bonus for v in out.values() if v > max_bonus)
        if excess <= 1e-9:
            break
        room = {pid: w for pid, w in weights.items() if out[pid] < max_bonus and w > 0}
        room_total = sum(room.values())
        if room_total <= 0:
            break
        for pid in out:
            if out[pid] > max_bonus:
                out[pid] = max_bonus
        for pid, w in room.items():
            out[pid] = min(max_bonus, out[pid] + excess * w / room_total)
    return out


def standalone_bonus(*, bps90: float, minutes_frac: float,
                     max_bonus: float = 3.0) -> float:
    """Bonus khi KHÔNG biết 21 cầu thủ còn lại của trận.

    Dùng cho những chỗ gọi `expected_points` lẻ (test, thăm dò) mà không có ngữ
    cảnh cả trận. Đây là dạng rời rạc đã khớp trực tiếp với dữ liệu mùa 2025/26,
    nên nó KHÔNG bảo toàn quỹ 6 điểm mỗi trận — chỉ đúng ở mức trung bình dân số.
    Đường tính chính (`engine/projections.py`) luôn dùng `allocate()`.

    Số mũ áp cho `bps90` (một TỶ LỆ, đúng như lúc khớp), rồi mới nhân số phút thực
    đá. Nếu áp số mũ cho `bps90 · minutes_frac` thì số phút bị trừng phạt hai lần:
    người đá nửa trận sẽ nhận 1/4 chứ không phải 1/2.
    """
    if bps90 <= 0 or minutes_frac <= 0:
        return 0.0
    per_90 = STANDALONE_SCALE * (bps90 ** CONCENTRATION)
    return min(max_bonus, per_90 * minutes_frac)
