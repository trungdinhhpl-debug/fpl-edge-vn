"""Tách phần bàn thắng từ chấm 11m ra khỏi bóng sống.

**Vấn đề.** FPL công bố `expected_goals` là xG **tổng**, gồm cả chấm 11m, và không
công bố `penalties_scored` cũng không công bố npxG. Hệ quả trong mô hình cũ:

1. `fixture_adj = λ_for(trận) / nền_giải` co giãn **toàn bộ** xG của cầu thủ theo
   độ khó trận. Nhưng một quả 11m đáng 0.79 bàn dù đối thủ là ai — nó không co lại
   khi gặp Man City theo cách một pha dứt điểm bóng sống co lại.
2. `pen_bump` nhân thêm 12% cho người đá 11m số 1 — **đếm hai lần**, vì những quả
   11m anh ta đã đá vốn đã nằm sẵn trong `expected_goals` của chính anh ta.

**Vì sao KHÔNG tách theo từng cầu thủ.** Dấu vết duy nhất là `penalties_missed`, và
đo trên dữ liệu thật thì nó quá thưa để dùng ở cấp cá nhân:

    15/20 người đá 11m số 1 hỏng ĐÚNG 0 quả  -> ước ra 0 quả đã đá, sai hiển nhiên
    B.Fernandes hỏng 2 quả                   -> ước ra 9.5 quả = 70% xG cả mùa của
                                                anh ta là penalty, sai hiển nhiên

Chia cho `1 − tỷ_lệ_vào` khuếch đại một biến đếm nguyên 0/1/2 thành một ước lượng
vô nghĩa. Đây chính là điều mà ghi chú giới hạn cũ nói đúng.

**Cách làm ở đây: tách ở tầng có đủ mẫu.** 14 quả hỏng của cả giải trên 760 trận-đội
là một mẫu dùng được, nên **tỷ lệ** được suy ở cấp GIẢI, còn việc ai đá thì đọc từ
`penalties_order` — dữ liệu FPL công bố thật, không phải giả định. Phần còn lại là
"ai đang trên sân", và nó lấy thẳng từ mô hình phút thi đấu chứ không từ một tỷ lệ
chia bịa ra.

Tỷ lệ được **tính tại chỗ từ dữ liệu trong DB**, không ghi cứng: mùa sau số khác thì
nó tự đổi theo.
"""
from __future__ import annotations

from dataclasses import dataclass

# Tỷ lệ vào bóng của chấm 11m ở Ngoại hạng Anh. KHÔNG suy được từ dữ liệu FPL —
# API cho số quả hỏng nhưng không cho số quả vào, nên không có mẫu số. Đây là hằng
# số của giải đấu, lấy từ mức ~78–80% được thống kê rộng rãi nhiều mùa liên tiếp.
# Nó chỉ dùng để quy `số quả hỏng -> số quả đã đá`, nên sai 2 điểm phần trăm ở đây
# làm lệch tỷ lệ penalty khoảng 10%, tức khoảng 0.5% tổng bàn của một đội.
PENALTY_CONVERSION = 0.79

# xG của một quả 11m. Đây là con số chuẩn trong mọi mô hình xG công khai và trùng
# với tỷ lệ vào bóng ở trên — đúng theo định nghĩa của xG.
PENALTY_XG = 0.79

# Mức nền khi DB chưa có `penalties_missed` (dữ liệu đồng bộ trước khi có cột này).
# Bằng đúng con số đo được trên mùa 2025/26 — xem `league_penalty_rate`.
FALLBACK_PENALTY_GOALS_PER_TEAM_MATCH = 0.069

# Tổng số quả hỏng tối thiểu của cả giải trước khi tin vào số đo tại chỗ. Dưới mức
# này thì mẫu quá nhỏ và mức nền hợp lý hơn.
MIN_MISSES_FOR_RATE = 6

# Độ co giãn của chấm 11m theo độ khó trận, so với bóng sống (bóng sống = 1.0).
#
# 0 nghĩa là penalty hoàn toàn không phụ thuộc đối thủ; 1.0 là hành vi cũ (co giãn
# y hệt bóng sống). Đội áp đảo đúng là được hưởng nhiều 11m hơn — nên không phải 0
# — nhưng quan hệ đó yếu và nhiễu hơn hẳn xG bóng sống, vì một quả 11m còn phụ
# thuộc trọng tài và một tình huống đơn lẻ.
#
# Con số này **không khớp được từ dữ liệu đang có**: 14 quả hỏng cả giải là quá ít
# để hồi quy số 11m theo sức mạnh tấn công của đội. Nó là hằng số cấu hình, cố ý
# đặt ở giữa, và cả hai đầu mút đều là hành vi có nghĩa nên chỉnh nó không bao giờ
# tạo ra trạng thái vô lý.
PENALTY_FIXTURE_ELASTICITY = 0.5

# Phần penalty bị chặn ở đúng xG mà cầu thủ thật sự có — không bao giờ trừ nhiều
# hơn cái đang có, để xG bóng sống không âm.
#
# Bản đầu đặt trần ở 45% xG, với lập luận "bảo vệ người MỚI nhận vai đá 11m, vì xG
# lịch sử của anh ta chưa chứa quả nào". Lập luận đó sai chiều, và đo được ngay:
# Robinson (Fulham, hậu vệ đá 11m) có xg90 = 0.065, bị trần chặn nên chỉ trừ 0.029
# nhưng vẫn được cộng lại 0.069 — **phồng 62% mối đe doạ ghi bàn từ không khí**, và
# đẩy anh ta lên trong bài toán tối ưu.
#
# Bất đối xứng thật sự nằm ở chỗ khác. Với người xG THẤP, trừ quá tay gần như vô
# hại (trừ đi X rồi cộng lại xấp xỉ X), còn trừ thiếu thì thổi phồng. Với người xG
# CAO, 0.069 chỉ là ~9% nên sai kiểu nào cũng nhỏ. Nghĩa là chặn ở chính xg90 luôn
# tốt hơn chặn ở một tỷ lệ.
MAX_PENALTY_SHARE_OF_XG = 1.0


@dataclass
class PenaltyRate:
    """Tỷ lệ penalty của giải, kèm nguồn gốc con số."""

    goals_per_team_match: float
    n_misses: int
    n_team_matches: int
    measured: bool
    detail: str


def league_penalty_rate(players: list, team_matches: int) -> PenaltyRate:
    """Số bàn từ chấm 11m mà MỘT đội ghi trong MỘT trận, suy từ dữ liệu thật.

        số quả đã đá = tổng số quả hỏng / (1 − tỷ lệ vào)
        số bàn       = số quả đã đá × tỷ lệ vào
        tỷ lệ        = số bàn / số trận-đội

    Cộng toàn giải nên mẫu là 14 quả hỏng trên 760 trận-đội thay vì 0–2 quả cho mỗi
    cầu thủ. Đó là toàn bộ lý do phép tách này đứng được, trong khi phép tách theo
    từng cầu thủ thì không.

    `team_matches` = số trận đã đá × 2 (mỗi trận cho hai đội một trận-đội).
    """
    misses = sum(int(getattr(p, "penalties_missed", 0) or 0) for p in players)
    if misses < MIN_MISSES_FOR_RATE or team_matches <= 0:
        return PenaltyRate(
            goals_per_team_match=FALLBACK_PENALTY_GOALS_PER_TEAM_MATCH,
            n_misses=misses,
            n_team_matches=team_matches,
            measured=False,
            detail=(
                f"mức nền {FALLBACK_PENALTY_GOALS_PER_TEAM_MATCH} — mới có {misses} "
                f"quả hỏng trên {team_matches} trận-đội, chưa đủ để đo tại chỗ"
            ),
        )
    taken = misses / (1.0 - PENALTY_CONVERSION)
    goals = taken * PENALTY_CONVERSION
    rate = goals / team_matches
    return PenaltyRate(
        goals_per_team_match=rate,
        n_misses=misses,
        n_team_matches=team_matches,
        measured=True,
        detail=(
            f"đo tại chỗ: {misses} quả hỏng -> ~{taken:.0f} quả đã đá -> "
            f"{goals:.0f} bàn trên {team_matches} trận-đội = {rate:.3f} bàn/trận-đội"
        ),
    )


def penalty_xg90(
    *,
    rate_per_team_match: float,
    penalties_order: int | None,
    lead_taker_on_pitch: float | None = None,
) -> float:
    """xG từ chấm 11m mà cầu thủ này nhận **trên mỗi 90 phút có mặt trên sân**.

    Đây là con số trung tâm của cả module, và nó được dùng ở **cả hai chiều**: trừ
    khỏi nền lịch sử, rồi cộng lại như thành phần riêng. Một con số dùng hai lần thì
    hai phía không có cách nào nói khác nhau — nếu tính riêng mỗi phía một kiểu thì
    phần thừa/thiếu sẽ lặng lẽ chui vào xG bóng sống.

    Ai đá suy từ `penalties_order` — dữ liệu FPL công bố thật — chứ không từ một tỷ
    lệ chia bịa ra: người số 1 đá mọi quả khi anh ta trên sân; người số 2 chỉ đá khi
    người số 1 vắng, nên phần của anh ta là `1 − P(số 1 có mặt)`, lấy thẳng từ mô
    hình phút. Từ số 3 trở đi cần cả hai người trên cùng vắng, hiếm tới mức bỏ qua.

    Đơn vị là "trên 90 phút CÓ MẶT", cùng đơn vị với `xg90`, nên trừ trực tiếp được.
    Số phút thật sự đá vào ở chỗ gọi, qua `minutes_frac`.
    """
    if penalties_order is None or penalties_order < 1 or rate_per_team_match <= 0:
        return 0.0
    if penalties_order == 1:
        return rate_per_team_match
    if penalties_order == 2:
        if lead_taker_on_pitch is None:
            return 0.0
        return rate_per_team_match * max(0.0, 1.0 - min(1.0, lead_taker_on_pitch))
    return 0.0


def split_open_play(xg90_total: float, pen_xg90_rate: float) -> tuple[float, float]:
    """(xG bóng sống trên 90, xG chấm 11m trên 90) — tổng luôn bằng đầu vào.

    Không bao giờ trừ nhiều hơn cái cầu thủ thật sự có, nên xG bóng sống không âm.
    Xem `MAX_PENALTY_SHARE_OF_XG` cho lý do vì sao chặn ở chính xg90 chứ không ở một
    tỷ lệ của nó.
    """
    if xg90_total <= 0 or pen_xg90_rate <= 0:
        return max(0.0, xg90_total), 0.0
    pen = min(pen_xg90_rate, MAX_PENALTY_SHARE_OF_XG * xg90_total)
    return xg90_total - pen, pen


def fixture_scaled_penalty(
    pen_xg90_rate: float,
    minutes_frac: float,
    fixture_adj: float,
    elasticity: float = PENALTY_FIXTURE_ELASTICITY,
) -> float:
    """Phần 11m kỳ vọng trong MỘT trận, đã tính số phút và độ khó trận.

    Độ khó vào qua `fixture_adj ** elasticity` — mũ nhỏ hơn 1 nghĩa là chấm 11m
    phản ứng với đối thủ yếu hơn bóng sống. Xem `PENALTY_FIXTURE_ELASTICITY`.
    """
    if pen_xg90_rate <= 0 or minutes_frac <= 0:
        return 0.0
    adj = max(fixture_adj, 1e-6) ** max(0.0, elasticity)
    return pen_xg90_rate * minutes_frac * adj
