"""Nguồn "chuyên gia" THẬT, lấy từ chính API công khai của FPL.

Bản trước chạy bằng một dàn diễn viên tự bịa ("Nguồn demo A–E") với các phát biểu
đóng dấu `[DEMO]`. Nó tồn tại để phần toán đồng thuận có gì mà chạy, nhưng nó
không nói được điều gì về bóng đá. Module này thay chúng bằng hai nguồn thật.

**Vì sao là hai nguồn này.** Ràng buộc không nằm ở kỹ thuật mà ở tính chính đáng:
chúng tôi không cào nội dung trả phí hay nội dung sau đăng nhập, và không gắn phát
biểu bịa cho người thật. Thứ còn lại thoả mãn cả hai điều kiện — và lại là bằng
chứng mạnh hơn hẳn một dòng tweet — là dữ liệu **first-party** của chính FPL:

1. **Mô hình riêng của FPL** (`ep_next`). Một dự báo độc lập, công bố công khai
   cho từng cầu thủ, và **chấm được** so với điểm thật sau mỗi vòng. Có sẵn ngay
   từ tiền mùa.

2. **Đồng thuận của nhóm dẫn đầu thế giới** (giải đấu 314 = bảng xếp hạng tổng →
   đội hình từng người). Đây là điều các nhà quản lý giỏi nhất **thật sự đã làm**
   với thứ hạng của chính họ, chứ không phải điều ai đó nói nên làm. Và thứ hạng
   của họ *chính là* track record đã được kiểm chứng — thứ mà không nguồn bình
   luận nào có.

**Giới hạn phải nói thẳng, và đã đo:** nguồn thứ hai **chưa dùng được cho tới sau
hạn chót vòng 1**. Đo ngày 2026-08-11: bảng xếp hạng tổng trả về **0 người** (chưa
ai có thứ hạng), và `entry/{id}/event/1/picks/` trả **404** (FPL giấu đội hình tới
khi hết hạn chuyển nhượng). Đó là sự thật của dữ liệu, không phải lỗi — nên adapter
trả về rỗng KÈM LÝ DO thay vì lấp bằng số bịa.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

from app.config import settings
from app.providers.expert_provider import ExpertSignalSeed, ExpertSourceSeed

FPL_API = "https://fantasy.premierleague.com/api"
# Giải đấu 314 là bảng xếp hạng TỔNG của FPL — mọi người chơi đều ở trong đó.
OVERALL_LEAGUE_ID = 314
STANDINGS_PAGE_SIZE = 50

# Số cầu thủ dẫn đầu theo `ep_next` được phát thành tín hiệu. Phát cho cả 519 cầu
# thủ có `ep_next > 0` là biến bảng đồng thuận thành tiếng ồn; đây là "mô hình FPL
# đang khuyên ai", nên chỉ phần đầu bảng mới là một khuyến nghị.
FPL_MODEL_TOP_N = 25

# Ngưỡng để một cầu thủ trở thành tín hiệu từ nhóm dẫn đầu.
TOP_MANAGER_CAPTAIN_MIN_SHARE = 0.05   # ≥5% nhóm dẫn đầu bắt băng
TOP_MANAGER_OWN_MIN_SHARE = 0.15       # ≥15% nhóm dẫn đầu sở hữu
# Chênh lệch sở hữu so với toàn thể người chơi để thành tín hiệu "mua"/"tránh".
TOP_MANAGER_EDGE = 0.10


# Độ tin cậy NỀN theo LOẠI nguồn, không phải lời khẳng định về tỷ lệ đúng của ai.
# Tỷ lệ đúng chỉ đến từ `ExpertTrackRecord` sau khi đã chấm trên mẫu thật.
#
# `ep_next` của FPL cố ý ở mức khiêm tốn: chính trang Chất lượng mô hình của dự án
# này coi chỉ số form của FPL là một **baseline để vượt qua**, nên đặt nó ngang
# hàng một nhà phân tích kỳ cựu là mâu thuẫn với chỗ khác trong cùng hệ thống.
FPL_MODEL_RELIABILITY = 0.55
TOP_MANAGER_RELIABILITY = 0.72


def real_sources(n_managers: int | None = None) -> list[ExpertSourceSeed]:
    n = n_managers or settings.expert_top_manager_count
    return [
        ExpertSourceSeed(
            name="Mô hình riêng của FPL (ep_next)",
            source_type="model",
            url=f"{FPL_API}/bootstrap-static/",
            reliability=FPL_MODEL_RELIABILITY,
            historical_accuracy=0.0,
            expertise="statistics,captaincy",
            independence=1.0,
            verified_track_record=False,
        ),
        ExpertSourceSeed(
            name=f"Top {n} FPL toàn cầu",
            source_type="community",
            url=f"{FPL_API}/leagues-classic/{OVERALL_LEAGUE_ID}/standings/",
            reliability=TOP_MANAGER_RELIABILITY,
            historical_accuracy=0.0,
            expertise="captaincy,statistics,chip_planning",
            # Không phải một tiếng nói lặp lại: đây là N người quyết định riêng rẽ,
            # nên nó là nguồn độc lập nhất trong cả hệ thống.
            independence=1.0,
            verified_track_record=False,
        ),
    ]


# ------------------------------------------------- mô hình riêng của FPL -----
def fpl_model_signals(elements: list[dict], top_n: int = FPL_MODEL_TOP_N) -> list[ExpertSignalSeed]:
    """Tín hiệu từ `ep_next` — dự báo điểm vòng tới do chính FPL công bố.

    `ep_next` là một CON SỐ, không phải một câu nói, nên nó không có "origin_ref"
    và không bao giờ là tiếng vọng của ai. Độ tự tin quy từ chính con số đó, chuẩn
    hoá theo người dẫn đầu, nên nó phản ánh đúng mức mà FPL đang đặt vào cầu thủ.

    **`ep_next` thô hơn vẻ ngoài của nó, và điều đó quyết định cách đọc.** Đo ngày
    2026-08-11: 519 cầu thủ nhưng chỉ **24 giá trị phân biệt**, chặn trên ở 4.0, và
    **4 người đồng hạng nhất** — Raya (GK), Gabriel (DEF), Haaland (FWD),
    B.Fernandes (MID). Bản đầu gán "đội trưởng" cho người đứng đầu danh sách đã
    sắp xếp, tức là **khuyến nghị bắt băng đội trưởng một thủ môn** thuần tuý do
    thứ tự tie-break.

    Nên tín hiệu `captain` chỉ phát khi người dẫn đầu **hơn hẳn** người thứ hai.
    Đồng hạng nghĩa là mô hình của FPL đang không tách ra ai, và nói đúng như vậy
    thì tốt hơn là bịa ra một lựa chọn rồi gắn tên FPL vào.

    Cố ý KHÔNG lọc theo vị trí. Nếu FPL thật sự chấm một thủ môn cao nhất *một cách
    rõ ràng*, việc của module này là thuật lại trung thực chứ không phải sửa lưng
    nguồn mà nó đang trích dẫn.
    """
    scored = [
        (e, float(e.get("ep_next") or 0.0))
        for e in elements
        if float(e.get("ep_next") or 0.0) > 0
    ]
    if not scored:
        return []
    # Thứ tự phụ theo id: cùng dữ liệu vào phải cho cùng kết quả ra, nếu không hai
    # lần đồng bộ liên tiếp lại đổi danh sách vì một lý do vô hình.
    scored.sort(key=lambda r: (-r[1], r[0].get("id", 0)))
    best = scored[0][1]
    n_tied = sum(1 for _, ep in scored if ep == best)
    strict_leader = n_tied == 1

    out: list[ExpertSignalSeed] = []
    for i, (e, ep) in enumerate(scored[:top_n]):
        tie_note = (
            ""
            if ep != best or strict_leader
            else f" FPL đang để {n_tied} người đồng hạng nhất nên không tách ra ai."
        )
        out.append(
            ExpertSignalSeed(
                source_name="Mô hình riêng của FPL (ep_next)",
                # Chỉ có "đội trưởng" khi thật sự có MỘT người dẫn đầu.
                signal_type="captain" if (strict_leader and i == 0) else "buy",
                web_name=e.get("web_name", ""),
                confidence=round(min(1.0, ep / best), 3),
                summary=f"FPL dự báo {ep:.1f} điểm ở vòng tới (ep_next).{tie_note}",
                published_hours_ago=0.5,
                link=f"{FPL_API}/bootstrap-static/",
                origin_ref=None,
            )
        )
    return out


# --------------------------------------------- đồng thuận nhóm dẫn đầu -------
@dataclass
class TopManagerConsensus:
    """Nhóm dẫn đầu đã thật sự làm gì. Rỗng thì `reason` nói vì sao rỗng."""

    gameweek: int | None = None
    n_managers: int = 0
    # element_id -> tỷ lệ sở hữu / bắt băng đội trưởng trong nhóm
    owned: dict[int, float] = field(default_factory=dict)
    captained: dict[int, float] = field(default_factory=dict)
    reason: str = ""

    @property
    def available(self) -> bool:
        return self.n_managers > 0


def _get(client: httpx.Client, path: str) -> dict | None:
    try:
        r = client.get(f"{FPL_API}{path}", timeout=settings.http_timeout_seconds)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def fetch_top_manager_consensus(
    n_managers: int | None = None, gameweek: int | None = None
) -> TopManagerConsensus:
    """Đội hình thật của N người đứng đầu bảng xếp hạng tổng.

    Hai chỗ trả rỗng, và cả hai đều là trạng thái HỢP LỆ của dữ liệu chứ không
    phải lỗi — nên chúng ghi lý do vào `reason` để giao diện nói đúng sự thật:

    * bảng xếp hạng rỗng → mùa giải chưa có vòng nào kết thúc, chưa ai có thứ hạng;
    * `picks` trả 404    → chưa tới hạn chót, FPL còn giấu đội hình.
    """
    n = n_managers or settings.expert_top_manager_count
    out = TopManagerConsensus(gameweek=gameweek)

    with httpx.Client(headers={"User-Agent": "fpl-edge-vn/1.0"}) as client:
        entries: list[int] = []
        page = 1
        while len(entries) < n:
            data = _get(
                client,
                f"/leagues-classic/{OVERALL_LEAGUE_ID}/standings/?page_standings={page}",
            )
            rows = ((data or {}).get("standings") or {}).get("results") or []
            if not rows:
                break
            entries.extend(r["entry"] for r in rows)
            if not ((data or {}).get("standings") or {}).get("has_next"):
                break
            page += 1
            time.sleep(settings.fpl_request_delay_ms / 1000.0)

        if not entries:
            out.reason = (
                "Bảng xếp hạng tổng của FPL đang rỗng — mùa giải chưa có vòng nào "
                "kết thúc nên chưa ai có thứ hạng. Nguồn này sẽ có dữ liệu ngay "
                "sau vòng 1."
            )
            return out

        entries = entries[:n]
        if gameweek is None:
            out.reason = "Chưa xác định được vòng đấu để lấy đội hình."
            return out

        owned: dict[int, int] = {}
        captained: dict[int, int] = {}
        seen = 0
        for entry_id in entries:
            picks = _get(client, f"/entry/{entry_id}/event/{gameweek}/picks/")
            time.sleep(settings.fpl_request_delay_ms / 1000.0)
            if not picks or not picks.get("picks"):
                continue
            seen += 1
            for pk in picks["picks"]:
                el = pk["element"]
                owned[el] = owned.get(el, 0) + 1
                if pk.get("is_captain"):
                    captained[el] = captained.get(el, 0) + 1

    if seen == 0:
        out.reason = (
            f"FPL chưa mở đội hình của vòng {gameweek} — đội hình chỉ công khai "
            "sau hạn chót chuyển nhượng."
        )
        return out

    out.n_managers = seen
    out.owned = {k: v / seen for k, v in owned.items()}
    out.captained = {k: v / seen for k, v in captained.items()}
    out.reason = f"Đội hình thật của {seen} người dẫn đầu ở vòng {gameweek}."
    return out


def top_manager_signals(
    consensus: TopManagerConsensus,
    elements: list[dict],
    source_name: str,
) -> list[ExpertSignalSeed]:
    """Quy đồng thuận của nhóm dẫn đầu thành tín hiệu.

    Tín hiệu mạnh nhất không phải "ai được sở hữu nhiều nhất" mà là **chênh lệch**
    giữa nhóm dẫn đầu và toàn thể người chơi: một cầu thủ mà 60% nhóm đầu bảng có
    còn cả giải chỉ 20% là một phát biểu; một cầu thủ mà cả hai đều 60% thì không.
    """
    if not consensus.available:
        return []
    by_id = {e["id"]: e for e in elements}
    out: list[ExpertSignalSeed] = []

    for el, share in sorted(consensus.captained.items(), key=lambda kv: -kv[1]):
        e = by_id.get(el)
        if not e or share < TOP_MANAGER_CAPTAIN_MIN_SHARE:
            continue
        out.append(ExpertSignalSeed(
            source_name=source_name, signal_type="captain",
            web_name=e.get("web_name", ""), confidence=round(share, 3),
            summary=f"{share:.0%} nhóm dẫn đầu bắt băng đội trưởng.",
            published_hours_ago=1.0, origin_ref=None,
        ))

    for el, share in sorted(consensus.owned.items(), key=lambda kv: -kv[1]):
        e = by_id.get(el)
        if not e or share < TOP_MANAGER_OWN_MIN_SHARE:
            continue
        crowd = float(e.get("selected_by_percent") or 0.0) / 100.0
        edge = share - crowd
        if edge >= TOP_MANAGER_EDGE:
            out.append(ExpertSignalSeed(
                source_name=source_name, signal_type="buy",
                web_name=e.get("web_name", ""),
                confidence=round(min(1.0, edge / 0.4), 3),
                summary=(
                    f"Nhóm dẫn đầu sở hữu {share:.0%} so với {crowd:.0%} toàn giải "
                    f"(+{edge:.0%})."
                ),
                published_hours_ago=1.0, origin_ref=None,
            ))
        elif edge <= -TOP_MANAGER_EDGE:
            out.append(ExpertSignalSeed(
                source_name=source_name, signal_type="avoid",
                web_name=e.get("web_name", ""),
                confidence=round(min(1.0, -edge / 0.4), 3),
                summary=(
                    f"Nhóm dẫn đầu chỉ sở hữu {share:.0%} trong khi toàn giải "
                    f"{crowd:.0%} ({edge:.0%})."
                ),
                published_hours_ago=1.0, origin_ref=None,
            ))
    return out
