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

**Cập nhật 2026-10-20 — khi BIẾT số quả thật, tách theo từng cầu thủ.** Lập luận ở
trên đúng cho dữ liệu FPL, nhưng API Premier League có số quả 11m đã đá của từng
người (`app/providers/pl_penalties.py`). Khi có số đó, engine trừ đúng
`0.79 × số quả` khỏi xG tổng (`non_penalty_xg`) cho TỪNG mùa — mẫu mùa này lẫn nền
mùa trước — rồi cộng lại phần 11m kỳ vọng theo `penalties_order` như cũ. Cách trừ
theo tỷ lệ vẫn giữ làm đường dự phòng khi chưa có số. Lý do phải đổi, đo được:
Le Fée được FPL xếp đá 11m SỐ 2 nhưng đã đá 6 quả trong ~40 trận (3.16/5.22 xG mùa
trước là 11m); cách trừ theo tỷ lệ chỉ bớt ~0.05/90 nên engine tưởng anh ta tạo ra
~0.20 xG bóng sống mỗi 90 phút, trong khi thật là ~0.07.
"""
from __future__ import annotations

from dataclasses import dataclass

# Tỷ lệ vào bóng của chấm 11m ở Ngoại hạng Anh — ĐO ĐƯỢC: 574/708 quả = 0.811 trên 7
# mùa 2019/20–2025/26 (API Premier League, `att_pen_goal` / tổng bốn kết cục). FPL
# không có mẫu số này. Dùng ở hai chỗ: quy `số quả hỏng -> số quả đã đá` ở đường dự
# phòng, và tính số quả HỎNG kỳ vọng (−2 điểm mỗi quả theo luật FPL).
PENALTY_CONVERSION = 0.81

# xG của một quả 11m. Đây là con số chuẩn trong mọi mô hình xG công khai và trùng
# với tỷ lệ vào bóng ở trên — đúng theo định nghĩa của xG.
PENALTY_XG = 0.79

# Mức nền (xG 11m mỗi trận-đội) khi không đo được tại chỗ. Bằng số ĐẾM THẬT mùa
# 2025/26: 92 quả × 0.79 / 760 trận-đội. Con số cũ 0.069 suy từ số quả hỏng với tỷ lệ
# vào 79% nên đếm thiếu 27% số quả.
FALLBACK_PENALTY_GOALS_PER_TEAM_MATCH = 0.096

# Tổng số quả hỏng tối thiểu của cả giải trước khi tin vào số đo tại chỗ. Dưới mức
# này thì mẫu quá nhỏ và mức nền hợp lý hơn.
MIN_MISSES_FOR_RATE = 6

# Số quả 11m một đội được hưởng co giãn theo SỨC TẤN CÔNG của trận, so với NỀN CỦA
# GIẢI — ĐO ĐƯỢC (2026-10-20), thay cho hằng số 0.5 đặt tay trước đây.
#
# Hồi quy Poisson trên 140 đội-mùa 2019/20–2025/26 (API Premier League, 708 quả 11m,
# hiệu ứng cố định theo mùa):
#
#   * theo số bàn KHÔNG tính 11m mỗi trận: e = 0.74 ± 0.27 (95%);
#   * số bàn một mùa là thước đo NHIỄU của sức tấn công thật (độ tin cậy 0.78 trên
#     thang log, phần nhiễu Poisson ≈ 1/số bàn), nên e thật ≈ 0.74/0.78 = 0.95;
#   * theo số lần chạm bóng trong vòng cấm đối phương: e = 1.09 ± 0.34.
#   * nhìn thô: 1/3 đội yếu nhất 0.109 quả/trận, 1/3 mạnh nhất 0.175.
#
# Ở đây áp cho λ của trận (đã là ước lượng co về prior, ít nhiễu hơn một mùa bàn
# thắng) nên dùng con số đã hiệu chỉnh nhiễu. Hai điều mà hằng số cũ làm sai: mốc so
# là trung bình CỦA CHÍNH ĐỘI (nên Hull và Man City cùng xuất phát từ một mức 11m),
# và độ co giãn 0.5 chỉ bằng nửa thực tế — người đá 11m của đội yếu được cộng quá tay.
PENALTY_ATTACK_ELASTICITY = 0.95

# Chặn tỷ số λ/nền khi lấy luỹ thừa — lá chắn cho λ vô lý, không phải ràng buộc
# thường trực (λ thật nằm trong ~0.4–3.0 lần nền giải).
_ATTACK_RATIO_BOUNDS = (0.25, 3.0)

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
    # Đơn vị xG (0.79 mỗi quả) — cùng đơn vị với `measured_penalty_rate` và với phần
    # bị trừ khỏi xG tổng; số quả hỏng kỳ vọng tách riêng ở `expected_penalty_misses`.
    xg = taken * PENALTY_XG
    rate = xg / team_matches
    return PenaltyRate(
        goals_per_team_match=rate,
        n_misses=misses,
        n_team_matches=team_matches,
        measured=True,
        detail=(
            f"đo tại chỗ: {misses} quả hỏng -> ~{taken:.0f} quả đã đá -> "
            f"{xg:.0f} xG trên {team_matches} trận-đội = {rate:.3f} xG/trận-đội"
        ),
    )


def measured_penalty_rate(samples: list[tuple[int, int]]) -> PenaltyRate:
    """Tỷ lệ 11m của giải khi BIẾT số quả đã đá (không phải suy từ số quả hỏng).

    `samples` = [(số quả đã đá, số trận-đội), …] — mùa này và mùa trước, gộp lại.
    Gộp vì mùa này còn mỏng (vòng 7: 11 quả / 140 trận-đội), còn tỷ lệ 11m là hằng
    số của GIẢI chứ không của mùa: mùa trước tự nhiên làm nền, và mùa này lấn dần
    khi số trận tăng.

    Đơn vị là **xG** (`PENALTY_XG` mỗi quả), cùng đơn vị với phần đã trừ khỏi xG
    tổng — nên phần cộng lại và phần trừ đi đo cùng một thứ.

    Vì sao không dùng `league_penalty_rate` khi có số thật: nó quy số quả hỏng về số
    quả đã đá bằng một tỷ lệ vào giả định, mà tỷ lệ vào dao động theo mùa (2025/26 là
    77/92 = 84%) — với 79% nó đếm 67 quả thay vì 92.
    """
    taken = sum(max(0, a) for a, _ in samples)
    matches = sum(max(0, m) for _, m in samples)
    if matches <= 0:
        return PenaltyRate(
            goals_per_team_match=FALLBACK_PENALTY_GOALS_PER_TEAM_MATCH,
            n_misses=0, n_team_matches=0, measured=False,
            detail="chưa có trận nào để chia — dùng mức nền",
        )
    rate = taken * PENALTY_XG / matches
    return PenaltyRate(
        goals_per_team_match=rate,
        n_misses=0,
        n_team_matches=matches,
        measured=True,
        detail=(
            f"đếm thật: {taken} quả 11m trên {matches} trận-đội × {PENALTY_XG} xG "
            f"= {rate:.3f} xG/trận-đội"
        ),
    )


def non_penalty_xg(xg_total: float, attempts: int) -> float:
    """xG bóng sống = xG tổng − `PENALTY_XG` × số quả 11m ĐÃ ĐÁ (không âm).

    Đây là phép trừ đúng — trừ cái đã thực sự xảy ra trong mẫu — thay cho trừ một
    tỷ lệ trung bình. Với người đá 11m đúng bằng mức trung bình thì hai cách trùng
    nhau; với người đá nhiều hơn (Le Fée: 6 quả trong ~40 trận khi chỉ được xếp số
    2) thì cách cũ để lọt cả phần thừa vào bóng sống.

    0.79 là xG FPL/Opta gán cho MỘT quả 11m — kiểm trên dữ liệu: trận chỉ có đúng
    một cú sút là quả 11m ra đúng 0.79 (Gibbs-White vòng 2, Rudoni vòng 6, 2026/27).
    """
    return max(0.0, (xg_total or 0.0) - PENALTY_XG * max(0, attempts or 0))


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
    attack_ratio: float,
    elasticity: float = PENALTY_ATTACK_ELASTICITY,
) -> float:
    """Phần 11m kỳ vọng (đơn vị xG) trong MỘT trận, đã tính số phút và sức tấn công.

    `attack_ratio` = λ bàn thắng của đội trong trận này / nền bàn thắng của GIẢI.
    `pen_xg90_rate` là mức của một đội TRUNG BÌNH; đội tấn công yếu được hưởng ít
    11m hơn gần tỷ lệ thuận — xem `PENALTY_ATTACK_ELASTICITY`.
    """
    if pen_xg90_rate <= 0 or minutes_frac <= 0:
        return 0.0
    lo, hi = _ATTACK_RATIO_BOUNDS
    ratio = min(hi, max(lo, attack_ratio))
    return pen_xg90_rate * minutes_frac * ratio ** max(0.0, elasticity)


def expected_penalty_misses(exp_pen_xg: float) -> float:
    """Số quả 11m HỎNG kỳ vọng, suy từ phần 11m kỳ vọng (đơn vị xG).

    Mỗi quả mang `PENALTY_XG` xG, nên số quả đá = xG / 0.79; hỏng = đá × (1 − tỷ lệ
    vào). FPL trừ 2 điểm mỗi quả hỏng — trước đây engine cộng phần bàn của quả 11m
    mà không trừ phần hỏng, tức coi mọi quả 11m là lời chắc.
    """
    if exp_pen_xg <= 0:
        return 0.0
    return exp_pen_xg / PENALTY_XG * (1.0 - PENALTY_CONVERSION)
