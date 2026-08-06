"""Luật Bonus Points System (BPS) theo từng mùa — KHÔNG có trong API.

`bootstrap-static.game_config.scoring` công bố điểm cho từng hạng mục, nhưng
trọng số BPS của từng hành động thì FPL **không** phát qua API (trường `bps`
trong đó chỉ là "1 BPS đổi ra bao nhiêu điểm" = 0). Vì vậy bộ trọng số BPS buộc
phải nằm trong code, và buộc phải được **đánh phiên bản theo mùa** — đó là lý do
có bảng `season_rules` (cột `bps_rules_version`).

Tại sao chuyện này quan trọng, chứ không chỉ là dọn dẹp:
`players.bps` mà engine đọc là tổng BPS **cả mùa**. Trước vòng 1 của mùa mới,
FPL vẫn phát tổng của mùa trước (đã kiểm chứng 2026-08-05: Haaland 2953 phút,
239 điểm trong khi hạn vòng 1 là 2026-08-21). Nghĩa là engine đang lấy BPS kiếm
được theo luật MÙA TRƯỚC để dự báo bonus của MÙA NÀY. Mùa 2026/27 hạ BPS từ
clearances/blocks/interceptions nên trung vệ bị định giá cao hơn thực tế nếu
dùng thẳng số cũ.

`equivalent_bps()` quy đổi tổng BPS cũ về "tương đương luật hiện hành" trước khi
đưa vào mô hình bonus.

Nguồn luật 2026/27:
  https://www.premierleague.com/en/news/4679946/whats-new-in-202627-fantasy-changes-to-bonus-points-system
"""
from __future__ import annotations

from dataclasses import dataclass

BPS_HELP_URL = "https://fantasy.premierleague.com/help/rules"
BPS_2026_SOURCE_URL = (
    "https://www.premierleague.com/en/news/4679946/"
    "whats-new-in-202627-fantasy-changes-to-bonus-points-system"
)


@dataclass(frozen=True)
class BPSRules:
    """Các trọng số BPS mà chúng ta thực sự dùng để quy đổi giữa hai mùa.

    Đây KHÔNG phải bảng BPS đầy đủ (bảng đầy đủ có ~30 hạng mục); chỉ gồm những
    hạng mục đã đổi giữa các mùa và có dữ liệu để quy đổi.
    """

    version: str
    season: str
    source_url: str
    # Ngày FPL CÔNG BỐ bộ luật này (ISO), không phải ngày ta đồng bộ. None = chưa
    # tra được ngày công bố; giao diện phải hiện "—" chứ không đoán.
    effective_from: str | None

    # số hành động clearance/block/interception để được 1 BPS
    cbi_per_bps: int
    # BPS mỗi lần bị đối phương qua người (âm); 0 = đã bỏ hạng mục
    tackled_bps: int
    # thủ môn
    save_in_box_bps: int
    save_other_bps: int
    big_chance_save_bps: int
    penalty_save_bps: int


# 2025/26 — mùa đầu có Defensive Contribution.
BPS_2025_26 = BPSRules(
    version="2025.1",
    season="2025/26",
    source_url=BPS_HELP_URL,
    effective_from=None,   # chưa tra được ngày công bố chính thức của mùa đó
    cbi_per_bps=2,
    tackled_bps=-1,
    save_in_box_bps=3,
    save_other_bps=2,      # trước đây là "cứu thua từ ngoài vòng cấm"
    big_chance_save_bps=0,  # chưa tồn tại
    penalty_save_bps=8,
)

# 2026/27 — hạ BPS phòng ngự để giảm trùng lặp với DefCon, bỏ trừ điểm khi bị
# qua người, thêm thưởng cứu thua từ big chance.
BPS_2026_27 = BPSRules(
    version="2026.1",
    season="2026/27",
    source_url=BPS_2026_SOURCE_URL,
    # FPL công bố loạt thay đổi luật 2026/27 ngày 20/07/2026
    effective_from="2026-07-20",
    cbi_per_bps=3,
    tackled_bps=0,
    save_in_box_bps=3,
    save_other_bps=2,
    big_chance_save_bps=1,
    penalty_save_bps=7,     # 7 + 1 (big chance) = 8 như cũ
)

BY_SEASON: dict[str, BPSRules] = {
    BPS_2025_26.season: BPS_2025_26,
    BPS_2026_27.season: BPS_2026_27,
}

# Dùng khi gặp mùa chưa khai báo: giữ bộ mới nhất và đánh dấu là suy đoán.
LATEST = BPS_2026_27


def for_season(season: str | None) -> BPSRules:
    """Bộ BPS của một mùa; mùa lạ thì trả bộ mới nhất (xem `is_known`)."""
    if season and season in BY_SEASON:
        return BY_SEASON[season]
    return LATEST


def is_known(season: str | None) -> bool:
    return bool(season) and season in BY_SEASON


def previous_season(season: str | None) -> str | None:
    """'2026/27' -> '2025/26'. Trả None nếu không phân tích được."""
    if not season or "/" not in season:
        return None
    head = season.split("/")[0]
    if not head.isdigit():
        return None
    y = int(head) - 1
    return f"{y}/{str(y + 1)[-2:]}"


# ------------------------------------------------------------------ quy đổi ----
#
# Chỉ MỘT thành phần quy đổi được tính từ dữ liệu đang có: CBI. FPL công bố
# `clearances_blocks_interceptions` theo mùa cho từng cầu thủ, nên chênh lệch
# BPS do đổi mẫu số là số học thuần:
#
#     ΔBPS = CBI · (1/cbi_mới − 1/cbi_cũ)          (2→3 nghĩa là −CBI/6)
#
# Hai thay đổi còn lại KHÔNG quy đổi được và mặc định để 0, có chú thích rõ:
#
#   * "bị qua người" (−1 BPS): FPL không phát số lần cầu thủ bị qua người
#     (`tackles` trong API là số lần TẮC BÓNG THÀNH CÔNG, thuộc DefCon). Bỏ hạng
#     mục này có lợi cho cầu thủ hay rê dắt, nhưng không có dữ liệu để định
#     lượng nên không bịa hệ số.
#   * "cứu thua từ big chance" (+1 BPS): FPL không phát số big chance mà thủ môn
#     đã cứu. Hướng tác động là dương và nhỏ; để 0 cho tới khi có nguồn số.
#
# Hai núm `tackled_uplift_per_90` và `big_chance_saves_per_save` để lộ ra ngoài
# để khi có nguồn số thì đặt giá trị mà không phải sửa engine.
TACKLED_UPLIFT_PER_90 = 0.0
BIG_CHANCE_SAVES_PER_SAVE = 0.0


def equivalent_bps(
    *,
    bps: float,
    cbi: float,
    saves: float,
    minutes: float,
    from_rules: BPSRules,
    to_rules: BPSRules,
) -> float:
    """Quy tổng BPS kiếm theo `from_rules` về tương đương `to_rules`.

    Xấp xỉ ở chỗ BPS thật tính theo từng trận rồi làm tròn xuống, còn đây chia
    trên tổng cả mùa. Vì ta lấy CHÊNH LỆCH của hai phép chia nên phần lẻ phần
    lớn triệt tiêu; sai số còn lại nhỏ hơn 1 BPS mỗi trận và không lệch hệ thống
    theo một hướng.
    """
    if from_rules.version == to_rules.version:
        return float(bps)

    out = float(bps)

    # CBI — thành phần duy nhất tính được chính xác từ dữ liệu FPL
    if from_rules.cbi_per_bps != to_rules.cbi_per_bps:
        out += cbi * (1.0 / to_rules.cbi_per_bps - 1.0 / from_rules.cbi_per_bps)

    # bỏ trừ điểm khi bị qua người (mặc định 0 — xem chú thích ở trên)
    if from_rules.tackled_bps != to_rules.tackled_bps and TACKLED_UPLIFT_PER_90:
        out += TACKLED_UPLIFT_PER_90 * (minutes / 90.0)

    # thưởng cứu thua từ big chance (mặc định 0 — xem chú thích ở trên)
    if (
        to_rules.big_chance_save_bps != from_rules.big_chance_save_bps
        and BIG_CHANCE_SAVES_PER_SAVE
    ):
        out += (
            saves
            * BIG_CHANCE_SAVES_PER_SAVE
            * (to_rules.big_chance_save_bps - from_rules.big_chance_save_bps)
        )

    return max(0.0, out)
