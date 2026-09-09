"""Điểm yếu phòng ngự của đối thủ **theo trục vị trí**, đo từ xG bị cho phép.

Mô hình xP hiện tại đã xét sức mạnh phòng ngự **tổng thể** của đối thủ hai lần rồi:
`fixture_adj = λ_bàn_thắng(trận) / nền_của_đội` co giãn toàn bộ sản lượng tấn công,
còn `λ_conceded` lo phần sạch lưới và bàn thua. Nên nếu module này đo "đối thủ cho
phép bao nhiêu xG" rồi nhân vào, nó sẽ **đếm hai lần** cùng một thông tin.

Đó là lý do con số ở đây là **TỶ LỆ TƯƠNG ĐỐI GIỮA CÁC TRỤC trong cùng một đối
thủ**, không phải mức tuyệt đối::

    relative[T][a] = (allowed[T][a] / league[a]) / (allowed[T][mọi trục] / league[mọi trục])

Một hàng phòng ngự rò rỉ đều khắp sẽ ra **đúng 1.0 ở mọi trục** — phần rò rỉ đó đã
nằm trong λ. Chỉ hàng phòng ngự **LỆCH** (yếu cánh trái hơn hẳn phần còn lại của
chính nó) mới lệch khỏi 1.0. Hệ quả phụ đáng giá: vì mẫu số là chính đội đó, các hệ
số của một đối thủ xấp xỉ trung bình 1.0, nên phép điều chỉnh gần như bảo toàn tổng
bàn của đội tấn công thay vì lặng lẽ thổi nó lên.

**Ưu tiên xG, không phải bàn thua.** Đầu vào là `expected_goals` theo từng trận của
cầu thủ đối phương (`player_gameweek_stats`), không phải `goals_conceded` — một bàn
thua là một biến đếm cực thưa, còn xG dùng được sau vài trận.

**Hiệu chỉnh theo sức mạnh đối thủ đã gặp.** Cho phép Man City 0.5 xG khác hẳn cho
phép Burnley 0.5 xG, nên mỗi quan sát được chia cho sức mạnh tấn công của đội đã
tạo ra nó (lấy từ mô hình sức mạnh đội sẵn có).

**Mẫu nhỏ thì kéo về 1.0**, theo trọng số `n / (n + PRIOR_90S)` — cùng dạng
`_shrink()` của `xpoints.py`. Không có mẫu thì hệ số **đúng bằng 1.0**, không phải
một phỏng đoán.

Toàn bộ phần này **chỉ chạy được khi có nguồn vai trò chiến thuật** (xem
`app/engine/roles.py`): không biết ai đá cánh phải thì không thể biết cánh trái của
đối thủ có bị khai thác hay không. Thiếu vai trò -> `configured=False`, hệ số 1.0,
giao diện hiện "Chưa đủ dữ liệu".
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from app.engine import roles as roles_mod

# Số "90 phút" cần thu thập trên một trục trước khi tin nửa đường vào số đo. 20
# lượt-90 trên một trục ≈ 5–7 trận của một đối thủ (3–4 cầu thủ cùng trục mỗi
# trận), tức khoảng thời gian mà một điểm yếu chiến thuật mới đáng gọi là có thật.
# Nó cùng tinh thần với SHRINK_MINUTES=540 của xpoints (6 trận) nhưng đo theo đơn
# vị của chính phép đo này.
PRIOR_90S = 20.0

# Trần/sàn của hệ số theo TRỤC. Dải này rộng hơn hệ số cuối cùng của cầu thủ vì
# `finishing_share` sẽ kéo nó lại gần 1.0 một lần nữa.
AXIS_MIN, AXIS_MAX = 0.70, 1.35

# Trần/sàn của hệ số áp cho MỘT cầu thủ. Đây là lá chắn cho mẫu bẩn, không phải
# một ràng buộc thường trực: với shrinkage ở trên, mẫu thật hiếm khi chạm vào.
PLAYER_MIN, PLAYER_MAX = 0.75, 1.30

# Dưới ngưỡng này thì nói thẳng là chưa đủ dữ liệu thay vì đưa ra một con số.
MIN_90S_TO_REPORT = 4.0


@dataclass
class AxisMeasure:
    """Số đo một trục của một đối thủ, kèm cỡ mẫu và độ tin cậy."""

    axis: str
    factor: float                 # hệ số sau shrinkage, 1.0 = không lệch
    raw_relative: float | None    # tỷ lệ trước shrinkage (None = không có mẫu)
    n_90s: float                  # cỡ mẫu, tính bằng lượt-90
    reliability: str              # "Chưa đủ dữ liệu" | "Thấp" | "Trung bình" | "Cao"

    @property
    def has_data(self) -> bool:
        return self.raw_relative is not None and self.n_90s >= MIN_90S_TO_REPORT


@dataclass
class OpponentProfile:
    """Toàn bộ trục của MỘT đội khi họ phòng ngự."""

    team_id: int
    by_axis: dict[str, AxisMeasure] = field(default_factory=dict)

    def axis(self, axis: str | None) -> AxisMeasure | None:
        return self.by_axis.get(axis) if axis else None


@dataclass
class WeaknessTable:
    """Bảng điểm yếu theo trục cho mọi đội, kèm trạng thái nguồn."""

    by_team: dict[int, OpponentProfile]
    configured: bool
    n_observations: int = 0
    n_teams_with_data: int = 0
    detail: str = ""
    needs: str | None = None

    def profile(self, team_id: int) -> OpponentProfile | None:
        return self.by_team.get(team_id)

    def status(self) -> dict:
        return {
            "configured": self.configured,
            "observations": self.n_observations,
            "teams_with_data": self.n_teams_with_data,
            "detail": self.detail,
            "needs": self.needs,
            "method": (
                "xG bị cho phép theo trục vị trí, chia cho mức tổng của chính đội đó "
                "(nên hàng phòng ngự rò rỉ đều ra 1.0 — phần đó đã nằm trong λ), "
                "hiệu chỉnh theo sức mạnh tấn công của các đội đã gặp, kéo về 1.0 "
                f"theo cỡ mẫu n/(n+{PRIOR_90S:.0f}) lượt-90."
            ),
        }


def _reliability(n_90s: float) -> str:
    if n_90s < MIN_90S_TO_REPORT:
        return "Chưa đủ dữ liệu"
    if n_90s < PRIOR_90S:
        return "Thấp"
    if n_90s < 3 * PRIOR_90S:
        return "Trung bình"
    return "Cao"


def _empty_measure(axis: str) -> AxisMeasure:
    return AxisMeasure(
        axis=axis, factor=1.0, raw_relative=None, n_90s=0.0,
        reliability="Chưa đủ dữ liệu",
    )


def build_weakness_table(
    *,
    stats: list,
    role_table: roles_mod.RoleTable,
    player_team: dict[int, int],
    team_attack_strength: dict[int, float] | None = None,
) -> WeaknessTable:
    """Đo điểm yếu theo trục từ số liệu từng trận đã đá.

    `stats` là các dòng `PlayerGameweekStat` (cần `player_id`, `opponent_team`,
    `minutes`, `expected_goals`). `player_team` để biết đội tấn công của mỗi quan
    sát; `team_attack_strength` là sức mạnh tấn công (vd số bàn ghi trung bình mỗi
    trận) dùng để hiệu chỉnh đối thủ đã gặp — thiếu thì bỏ qua bước hiệu chỉnh
    (và nói ra trong `detail`).
    """
    if not role_table.configured:
        return WeaknessTable(
            by_team={}, configured=False,
            detail=(
                "Chưa có nguồn vai trò chiến thuật nên không thể gán quan sát vào "
                "trục nào — hệ số điều chỉnh theo vị trí giữ ở 1.0."
            ),
            needs=role_table.status().get("needs"),
        )

    strength = team_attack_strength or {}
    # Mức tham chiếu của phép hiệu chỉnh: trung bình sức mạnh tấn công toàn giải.
    ref_strength = 0.0
    if strength:
        vals = [v for v in strength.values() if v and v > 0]
        ref_strength = sum(vals) / len(vals) if vals else 0.0

    # (đội phòng ngự, trục) -> [xG đã hiệu chỉnh, số lượt-90]
    xg_sum: dict[tuple[int, str], float] = defaultdict(float)
    n90_sum: dict[tuple[int, str], float] = defaultdict(float)
    # tổng của riêng đội đó, trên MỌI trục (mẫu số của tỷ lệ tương đối)
    team_xg: dict[int, float] = defaultdict(float)
    team_n90: dict[int, float] = defaultdict(float)
    # mức giải, dùng để so trục với trục
    league_xg: dict[str, float] = defaultdict(float)
    league_n90: dict[str, float] = defaultdict(float)
    league_xg_all = league_n90_all = 0.0

    n_obs = 0
    for s in stats:
        opp = getattr(s, "opponent_team", None)
        mins = getattr(s, "minutes", 0) or 0
        if not opp or mins <= 0:
            continue
        assign = role_table.get(s.player_id)
        axis = assign.axis
        if not axis:
            continue
        xg = float(getattr(s, "expected_goals", 0.0) or 0.0)
        n90 = mins / 90.0

        # Hiệu chỉnh theo sức mạnh tấn công của đội đã tạo ra quan sát này: cho
        # phép một đội mạnh 0.5 xG là thành tích phòng ngự tốt hơn hẳn so với cho
        # phép một đội yếu 0.5 xG.
        adj = 1.0
        if ref_strength > 0:
            own = strength.get(player_team.get(s.player_id, -1), 0.0)
            if own and own > 0:
                # Chặn [0.5, 2.0] cùng lý do `team_goal_scale` bị chặn: ngoài dải
                # đó là dữ liệu mâu thuẫn, ép khớp chỉ bóp méo thêm.
                adj = max(0.5, min(2.0, ref_strength / own))
        xg_adj = xg * adj

        xg_sum[(opp, axis)] += xg_adj
        n90_sum[(opp, axis)] += n90
        team_xg[opp] += xg_adj
        team_n90[opp] += n90
        league_xg[axis] += xg_adj
        league_n90[axis] += n90
        league_xg_all += xg_adj
        league_n90_all += n90
        n_obs += 1

    if n_obs == 0:
        return WeaknessTable(
            by_team={}, configured=False,
            detail=(
                "Có nguồn vai trò nhưng chưa có số liệu xG theo từng trận để đo "
                "(bảng player_gameweek_stats trống hoặc chưa có cầu thủ nào khớp "
                "vai trò) — hệ số giữ ở 1.0."
            ),
            needs=(
                "player_gameweek_stats đã nạp (SYNC_PLAYERS_DETAIL=true) cho các "
                "vòng đã đá, và vai trò cho chính những cầu thủ đã ra sân."
            ),
        )

    league_rate = {
        a: (league_xg[a] / league_n90[a]) for a in league_n90 if league_n90[a] > 0
    }
    league_rate_all = (
        league_xg_all / league_n90_all if league_n90_all > 0 else 0.0
    )

    out: dict[int, OpponentProfile] = {}
    teams_with_data = 0
    for team_id in set(team_n90) | {t for t, _ in xg_sum}:
        prof = OpponentProfile(team_id=team_id)
        # Mức tổng của chính đội này, quy về mức giải -> "đội này rò rỉ hơn/kém
        # trung bình bao nhiêu", tức phần λ đã biết.
        own_all = (
            (team_xg[team_id] / team_n90[team_id]) / league_rate_all
            if team_n90[team_id] > 0 and league_rate_all > 0
            else None
        )
        any_axis_data = False
        for axis in roles_mod.AXES:
            n90 = n90_sum.get((team_id, axis), 0.0)
            lr = league_rate.get(axis, 0.0)
            if n90 <= 0 or lr <= 0 or own_all is None or own_all <= 0:
                prof.by_axis[axis] = _empty_measure(axis)
                continue
            own_axis = (xg_sum[(team_id, axis)] / n90) / lr
            relative = own_axis / own_all
            w = n90 / (n90 + PRIOR_90S)
            factor = 1.0 + (relative - 1.0) * w
            factor = max(AXIS_MIN, min(AXIS_MAX, factor))
            rel_label = _reliability(n90)
            prof.by_axis[axis] = AxisMeasure(
                axis=axis, factor=factor, raw_relative=relative, n_90s=n90,
                reliability=rel_label,
            )
            if n90 >= MIN_90S_TO_REPORT:
                any_axis_data = True
        if any_axis_data:
            teams_with_data += 1
        out[team_id] = prof

    sos = (
        "có hiệu chỉnh sức mạnh đội đã gặp"
        if ref_strength > 0
        else "CHƯA hiệu chỉnh sức mạnh đội đã gặp (thiếu số liệu sức mạnh tấn công)"
    )
    return WeaknessTable(
        by_team=out, configured=True, n_observations=n_obs,
        n_teams_with_data=teams_with_data,
        detail=(
            f"{n_obs} quan sát cầu thủ-trận có vai trò, {teams_with_data} đội đủ "
            f"mẫu trên ít nhất một trục · {sos}"
        ),
    )


@dataclass
class PositionalAdjustment:
    """Hệ số áp cho thành phần GHI BÀN BÓNG SỐNG của một cầu thủ trong một trận."""

    factor: float                      # 1.0 = không điều chỉnh
    role: str | None
    axis: str | None
    axis_factor: float | None          # hệ số thô của trục (trước finishing_share)
    finishing_share: float
    n_90s: float
    reliability: str
    source: str | None
    reason: str

    @property
    def has_data(self) -> bool:
        return self.axis_factor is not None


NO_ADJUSTMENT = PositionalAdjustment(
    factor=1.0, role=None, axis=None, axis_factor=None, finishing_share=0.0,
    n_90s=0.0, reliability="Chưa đủ dữ liệu", source=None,
    reason="Chưa có vai trò chiến thuật cho cầu thủ này",
)


def positional_adjustment(
    *,
    assignment: roles_mod.RoleAssignment,
    table: WeaknessTable,
    opponent_team_id: int,
) -> PositionalAdjustment:
    """Hệ số điểm yếu theo vị trí cho một cầu thủ trước một đối thủ cụ thể.

    Ba lý do khiến kết quả là **đúng 1.0**, và cả ba đều được nói ra chứ không ẩn
    đi: chưa có vai trò; chưa có số đo cho trục đó; hoặc vai trò này là vai trò
    TẠO CƠ HỘI nên phần điểm yếu gần như không chảy vào khả năng ghi bàn.
    """
    if not assignment.role:
        return NO_ADJUSTMENT
    axis = assignment.axis
    share = assignment.finishing_share
    prof = table.profile(opponent_team_id)
    measure = prof.axis(axis) if prof else None
    if measure is None or not measure.has_data:
        return PositionalAdjustment(
            factor=1.0, role=assignment.role, axis=axis, axis_factor=None,
            finishing_share=share, n_90s=measure.n_90s if measure else 0.0,
            reliability="Chưa đủ dữ liệu", source=assignment.source,
            reason=(
                "Chưa đủ mẫu xG bị cho phép trên trục "
                f"{roles_mod.AXIS_LABEL_VI.get(axis, axis or '?')}"
            ),
        )

    # Chỉ phần dành cho DỨT ĐIỂM được chảy vào thành phần ghi bàn. Phần còn lại là
    # lợi thế tạo cơ hội — nó thuộc về kiến tạo, và thành phần kiến tạo không bị
    # module này chạm tới.
    factor = 1.0 + (measure.factor - 1.0) * share
    factor = max(PLAYER_MIN, min(PLAYER_MAX, factor))
    direction = "yếu hơn" if measure.factor > 1.0 else "mạnh hơn"
    return PositionalAdjustment(
        factor=factor,
        role=assignment.role,
        axis=axis,
        axis_factor=measure.factor,
        finishing_share=share,
        n_90s=measure.n_90s,
        reliability=measure.reliability,
        source=assignment.source,
        reason=(
            f"{roles_mod.ROLE_LABEL_VI.get(assignment.role, assignment.role)} đối đầu "
            f"{roles_mod.AXIS_LABEL_VI.get(axis, axis)}: {direction} mức tổng của "
            f"chính đối thủ {abs(measure.factor - 1) * 100:.0f}% "
            f"(mẫu {measure.n_90s:.0f} lượt-90, {measure.reliability}); "
            f"phần dứt điểm {share * 100:.0f}% -> hệ số {factor:.3f}"
        ),
    )
