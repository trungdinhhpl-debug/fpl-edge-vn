"""Prior RIÊNG TỪNG CẦU THỦ, dựng từ mùa trước.

Không có phần này, mô hình phải trả lời câu "người này có đá chính không" bằng
một hằng số dùng chung cho cả giải (`xmins.PRIOR_START_RATE = 0.45`), và câu
"người này ghi bàn nhiều đến đâu" bằng một prior theo VỊ TRÍ. Ở giữa mùa điều đó
vô hại vì dữ liệu mùa này đã đè lên prior; ở những vòng đầu thì prior LÀ câu trả
lời, nên mọi người ra gần như cùng một con số. Đo vòng 1 mùa 2026/27: Haaland,
Isak, Szoboszlai và hàng loạt hậu vệ cùng 65–66 phút.

Nguồn là `player_season_history` (từ `element-summary.history_past`), thứ duy nhất
còn sót lại sau khi FPL xoá tổng cả-mùa tại hạn vòng 1.

Ba giới hạn đã biết, khai ra chứ không giấu:

  * **Mẫu số là 38 trận**, vì `history_past` không nói câu lạc bộ cũ đá bao nhiêu
    trận trong lúc cầu thủ còn ở đó. Người ký hợp đồng tháng Giêng vì thế bị hạ
    tỷ lệ đá chính. Không sửa được bằng dữ liệu FPL.
  * **Chỉ lấy mùa liền trước.** Một người nghỉ dài cả mùa trước rồi trở lại sẽ bị
    đánh giá theo mùa anh ta vắng mặt. Cộng dồn nhiều mùa nghe hợp lý hơn nhưng
    lại kéo theo câu hỏi "bao nhiêu mùa thì hết liên quan", mà không có gì để
    kiểm chứng — nên giữ một mùa và nói rõ.
  * **Đổi câu lạc bộ / đổi huấn luyện viên** làm prior kém liên quan đi. Phần này
    đã có `season_state.prior_reliability()`, và ở đây prior riêng được kéo về
    prior chung đúng theo hệ số đó.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import bps_rules
from app.models import Player, PlayerSeasonHistory

# Số trận một mùa Ngoại hạng. Mẫu số của tỷ lệ đá chính.
SEASON_MATCHES = 38

# Dưới ngần này phút ở mùa trước thì mẫu quá mỏng để nói lên vai trò: một người
# đá 200 phút có thể là tân binh trẻ, có thể là trụ cột chấn thương cả mùa.
MIN_MINUTES_FOR_ROLE = 450


@dataclass(frozen=True)
class PlayerPrior:
    """Prior của một cầu thủ, đã tính đến độ liên quan của mùa trước."""
    start_rate: float | None      # tỷ lệ đá chính, None = không đủ mẫu
    xg90: float | None
    xa90: float | None
    minutes: int
    season: str
    # xG BÓNG SỐNG/90 = (xG − 0.79 × số quả 11m thật) / số 90 phút. None = chưa
    # biết số quả 11m của mùa đó -> người dùng prior phải trừ theo tỷ lệ.
    npxg90: float | None = None


def load_priors(db: Session, current_season: str | None,
                reliability: dict[int, float] | None = None,
                penalty_attempts: dict[int, int] | None = None,
                ) -> dict[int, PlayerPrior]:
    """{player_id: PlayerPrior} dựng từ mùa liền trước mùa đang chơi.

    `reliability` là hệ số 0..1 từ `season_state.prior_reliability()`. Nó KHÔNG
    được áp ở đây — người gọi trộn với prior chung, vì "kéo về đâu" là câu hỏi của
    từng chỗ dùng (xMins kéo về tỷ lệ đá chính nền, xP kéo về xG/90 theo vị trí).
    Tham số này chỉ để lọc bớt: hệ số quá thấp thì prior riêng không còn nghĩa.

    `penalty_attempts` = {player_id: số quả 11m đã đá ở MÙA TRƯỚC}. Có thì
    `npxg90` được điền: `history_past` của FPL là xG tổng, nên người đá nhiều 11m
    mùa trước mang cả phần đó vào nền bóng sống (Le Fée 2025/26: 3.16/5.22 xG là
    11m). Cầu thủ vắng trong dict -> `npxg90 = None` (chưa biết, không phải 0).
    """
    from app.engine.penalties import non_penalty_xg

    prev = bps_rules.previous_season(current_season)
    if not prev:
        return {}

    rows = db.scalars(
        select(PlayerSeasonHistory).where(PlayerSeasonHistory.season_name == prev)
    ).all()
    out: dict[int, PlayerPrior] = {}
    for r in rows:
        mins90 = r.minutes / 90.0
        enough = r.minutes >= MIN_MINUTES_FOR_ROLE
        out[r.player_id] = PlayerPrior(
            start_rate=(
                min(0.95, max(0.05, r.starts / SEASON_MATCHES)) if enough else None
            ),
            # xG/90 thì cần ít mẫu hơn tỷ lệ đá chính để có nghĩa, nhưng chia cho
            # một mẫu số bé vẫn nổ — nên vẫn đòi có phút, chỉ là ngưỡng thấp hơn.
            xg90=(r.expected_goals / mins90) if r.minutes >= 180 else None,
            xa90=(r.expected_assists / mins90) if r.minutes >= 180 else None,
            minutes=r.minutes,
            season=prev,
            npxg90=(
                non_penalty_xg(r.expected_goals, penalty_attempts[r.player_id]) / mins90
                if r.minutes >= 180
                and penalty_attempts is not None
                and r.player_id in penalty_attempts
                else None
            ),
        )
    return out


def blended(prior_value: float | None, fallback: float, reliability: float) -> float:
    """Kéo prior riêng về prior chung theo mức còn liên quan của mùa trước.

    `reliability = 1` (vẫn CLB cũ, vẫn HLV cũ) thì dùng nguyên prior riêng;
    `reliability = 0.4` (tân binh) thì phần lớn quay về mức nền. Thiếu prior riêng
    thì trả thẳng mức nền — không bịa.
    """
    if prior_value is None:
        return fallback
    w = max(0.0, min(1.0, reliability))
    return w * prior_value + (1.0 - w) * fallback


def coverage(db: Session, current_season: str | None) -> dict:
    """Bao nhiêu cầu thủ đã có nền lịch sử — để trang nói được nó đang đứng ở đâu."""
    from sqlalchemy import func

    n_players = db.scalar(select(func.count()).select_from(Player)) or 0
    fetched = db.scalar(
        select(func.count()).select_from(Player)
        .where(Player.history_fetched_at.isnot(None))
    ) or 0
    prev = bps_rules.previous_season(current_season)
    with_prior = db.scalar(
        select(func.count()).select_from(PlayerSeasonHistory)
        .where(PlayerSeasonHistory.season_name == prev)
    ) or 0 if prev else 0
    return {
        "players": n_players,
        "history_fetched": fetched,
        "with_previous_season": with_prior,
        "previous_season": prev,
    }
