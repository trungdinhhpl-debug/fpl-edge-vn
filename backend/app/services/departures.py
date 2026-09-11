"""Ai đã rời CLB — và vì sao `status` một mình không trả lời được.

FPL **không** xoá cầu thủ đã chuyển đi khỏi `bootstrap-static`. Watkins sang Al
Hilal vẫn nằm nguyên trong feed với giá £7.8, tỷ lệ sở hữu 3.2% và đúng một dòng
`news` báo chuyện đó. Sync dùng `merge` nên họ ở lại DB vĩnh viễn, và mọi đường
đọc mặc định coi họ như cầu thủ bình thường có xP = 0.

Đo trên dữ liệu thật 2026-09-11 (sau kỳ chuyển nhượng hè): 100/655 cầu thủ mang
status `"u"`, tức 100 dòng rác trong trang /players, 22 người trong số đó ở mức
£4.0 — đúng hình dạng mà optimizer thích chọn làm ghế dự bị giá rẻ — và 60/100
tin đầu tiên của News Centre là "Has joined ...", tức tin chuyển nhượng đang
được trình bày như tin chấn thương.

`status == "u"` một mình thì KHÔNG đủ. FPL dùng `"u"` cho cả người còn ở CLB
nhưng không được đăng ký thi đấu — Richarlison, news "not included in squad."
Người đó có thể được đăng ký lại vào tháng Một, nên anh ta chưa "chuyển đi".
Điều kiện vì vậy là `"u"` **cộng** một dòng news có hình dạng rời đi.

Đây là một vị từ trên dữ liệu, không phải một cột trong DB: khi cầu thủ trở lại
(hết hạn cho mượn, được đăng ký lại) FPL đổi `status` và dòng news, và họ tự
xuất hiện lại — không cần ai đi dọn.
"""
from __future__ import annotations

import re

from app.models import Player

# Những cách FPL viết một lần rời đi, lấy từ chính feed:
#   "Has joined Al Hilal permanently"
#   "Has joined Borussia Dortmund on loan for the rest of the season"
#   "Has joined Oxford United on loan until January"
#   "has joined  Kasimpasa on loan for the rest of the seaon."   (sic)
#   "has departed the club as a free agent."
#   "has returned to Getafe CF"
_DEPARTED = re.compile(
    r"\b(?:joined|departed|transferred)\b"
    r"|\bhas\s+left\b"
    r"|\breturned\s+to\b"
    r"|\bon\s+loan\b",
    re.I,
)

# Nếu một mùa nào đó FPL đổi cách viết và "joined" mang nghĩa NGƯỢC LẠI, ta muốn
# hỏng theo hướng GIỮ cầu thủ lại chứ không giấu nhầm người vẫn đang đá.
_STAYING = re.compile(
    r"\b(?:joined|rejoined)\s+(?:the\s+)?(?:squad|club|group|training|first[- ]team)\b",
    re.I,
)


def has_left_club(status: str | None, news: str | None) -> bool:
    """True khi cầu thủ đã rời CLB (chuyển nhượng, cho mượn, hết hợp đồng)."""
    if (status or "") != "u":
        return False
    text = (news or "").strip()
    if not text or _STAYING.search(text):
        return False
    return bool(_DEPARTED.search(text))


def player_departed(p: Player) -> bool:
    """Bản nhận thẳng đối tượng Player, cho các đường đọc đã có sẵn model."""
    return has_left_club(p.status, p.news)


def departed_ids(players) -> set[int]:
    """Tập id đã rời CLB trong một iterable Player — dùng để lọc pool optimizer."""
    return {p.id for p in players if player_departed(p)}
