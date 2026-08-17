"""BƯỚC 1 — Prior sức mạnh đội đầu mùa.

Đặc tả yêu cầu một prior gộp từ năm nguồn bằng chứng, có trọng số:

    PriorStrength = 45% opponent-adjusted competitive xG/xGA
                  + 25% market/Elo strength
                  + 15% squad-quality change
                  + 10% manager/system continuity
                  +  5% quality-adjusted preseason underlying data

Hai quyết định thiết kế quyết định toàn bộ module này:

**1. Gộp trong không gian log.** Kết quả của BƯỚC 1 đi thẳng vào BƯỚC 2 dưới dạng
số hạng cộng của `log λ`. Nếu ở đây trộn tuyến tính rồi ở kia lấy log thì "45%"
không còn là 45% của thứ mà mô hình thực sự dùng. Trộn log ngay từ đầu giữ đúng
nghĩa: một đội có tấn công 1.2 và một đội 0.8 thì trung bình là 0.98, không phải
1.0 — đúng với bản chất nhân của mô hình bàn thắng.

**2. Trọng số được CHUẨN HOÁ LẠI theo dữ liệu thật sự có.** Không thành phần nào
được phép "mặc định bằng trung bình giải" để giữ nguyên 45/25/15/10/5, vì như vậy
là lén kéo mọi đội về mức trung bình rồi vẫn khai là đã dùng đủ năm nguồn. Thành
phần thiếu dữ liệu bị loại khỏi mẫu số, và `TeamPrior.components` ghi rõ nguồn nào
đã vào, nguồn nào không, kèm lý do.

Nguồn nào thật sự có, tính đến dữ liệu FPL API cấp:

| Thành phần            | Trước vòng 1                        | Trong mùa                        |
|-----------------------|-------------------------------------|----------------------------------|
| xG/xGA đã hiệu chỉnh  | CÓ, nhưng **chưa hiệu chỉnh đối thủ**| CÓ, hiệu chỉnh đối thủ bằng IPF |
| market / Elo          | Elo thay thế (strength ratings)     | Thị trường nếu đủ dày, không thì Elo |
| squad quality         | CÓ (định giá FPL, độ dốc khớp tại chỗ) | CÓ                            |
| manager continuity    | CÓ (danh sách người vận hành khai)  | CÓ                               |
| preseason underlying  | CHỈ đội mới lên hạng (Championship) | tắt dần rồi mất                  |

Không có giao hữu tiền mùa trong FPL API — không có xG, không có đội hình, không
có kết quả. Nên với 17 đội còn lại, thành phần thứ năm **không tồn tại**, và nó
được ghi là không tồn tại chứ không được bịa.

Quy ước hướng, dùng thống nhất cả file và cả BƯỚC 2:
    * `attack`  cao = ghi nhiều bàn hơn.
    * `defence` cao = **thủng lưới ít hơn** (mạnh hơn), không phải ngược lại.
Trung bình giải của cả hai = 1.0.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# ---------------------------------------------------------------- hằng số ---
NOMINAL_WEIGHTS: dict[str, float] = {
    "xg_adjusted": 0.45,
    "market_elo": 0.25,
    "squad_quality": 0.15,
    "manager_continuity": 0.10,
    "preseason": 0.05,
}

# Trần/sàn cho mọi chỉ số thành phần. Không đội nào ở Ngoại hạng mạnh gấp đôi
# trung bình giải về bàn thắng; một thành phần vượt khoảng này là dấu hiệu mẫu
# nhỏ chứ không phải phát hiện.
INDEX_FLOOR = 0.55
INDEX_CEIL = 1.70

# Đội mới lên hạng: mức nền khi KHÔNG có dữ liệu Ngoại hạng nào để học.
PROMOTED_ATTACK = 0.80
PROMOTED_DEFENCE = 0.80

# "Không có lịch sử Ngoại hạng" phải xét TƯƠNG ĐỐI với phần còn lại của giải, vì
# FPL reset thống kê mỗi mùa — một ngưỡng phút tuyệt đối sẽ gắn cờ cả 20 đội sau
# vòng 1.
NO_HISTORY_RATIO = 0.35

# Số phút thủ môn tối thiểu để xGC/90 của anh ta được coi là mốc hàng thủ của đội.
MIN_GK_MINUTES_FOR_PROXY = 900

# Số trận mỗi đội tối thiểu để phép hiệu chỉnh đối thủ có nghĩa. Dưới mức này thì
# 40 tham số (20 đội × công/thủ) đang được khớp bằng quá ít quan sát, và nghiệm
# chỉ là tiếng ồn được sắp xếp gọn gàng.
MIN_MATCHES_FOR_ADJUSTMENT = 4

# Championship nhạt dần rồi tắt hẳn khi đội mới lên hạng đã có trận Ngoại hạng thật.
CHAMP_FADE_MATCHES = 5

# Độ dốc mặc định khi không khớp được tại chỗ: giá đội hình gấp đôi => sản lượng
# gấp đôi. Chỉ dùng khi có dưới 8 đội đủ dữ liệu để hồi quy.
DEFAULT_PRICE_ELASTICITY = 1.0
MIN_TEAMS_FOR_ELASTICITY = 8


def clamp_index(v: float) -> float:
    return max(INDEX_FLOOR, min(INDEX_CEIL, v))


def _geo_mean(values: list[float]) -> float:
    vals = [v for v in values if v > 0]
    if not vals:
        return 1.0
    return math.exp(sum(math.log(v) for v in vals) / len(vals))


@dataclass
class Component:
    """Một nguồn bằng chứng cho prior của MỘT đội."""

    name: str
    attack: float | None
    defence: float | None
    weight: float           # trọng số danh nghĩa (trước chuẩn hoá)
    detail: str             # vì sao có / vì sao không

    @property
    def available(self) -> bool:
        return self.attack is not None and self.defence is not None


@dataclass
class TeamPrior:
    team_id: int
    attack: float
    defence: float
    no_pl_history: bool
    components: dict[str, Component] = field(default_factory=dict)
    # Tổng trọng số danh nghĩa đã thật sự vào phép gộp. 1.0 = đủ cả năm nguồn.
    # Số này là thước đo độ chắc chắn của prior và được BƯỚC 5 dùng để phạt.
    evidence_weight: float = 0.0

    def as_dict(self) -> dict:
        return {
            "team_id": self.team_id,
            "attack": round(self.attack, 4),
            "defence": round(self.defence, 4),
            "no_pl_history": self.no_pl_history,
            "evidence_weight": round(self.evidence_weight, 3),
            "components": {
                k: {
                    "attack": round(c.attack, 4) if c.attack is not None else None,
                    "defence": round(c.defence, 4) if c.defence is not None else None,
                    "weight": c.weight,
                    "available": c.available,
                    "detail": c.detail,
                }
                for k, c in self.components.items()
            },
        }


# ------------------------------------------------- hiệu chỉnh theo đối thủ ---
@dataclass
class Observation:
    """Một lần "đội `attacker` tạo ra `value` bàn kỳ vọng trước `defender`"."""

    attacker: int
    defender: int
    value: float
    attacker_home: bool


def solve_attack_defence(
    obs: list[Observation], team_ids: list[int], iters: int = 120
) -> tuple[dict[int, float], dict[int, float], float] | None:
    """Tách sản lượng thành (công của mình) × (yếu của đối thủ) × (sân bãi).

    Mô hình nhân, khớp bằng lặp tỉ lệ (IPF — cùng họ với ước lượng hợp lý cực đại
    của hồi quy Poisson log-tuyến tính, nhưng không cần thư viện tối ưu):

        value(i tấn công j) ≈ base · att[i] · weak[j] · h^(+1 nếu i sân nhà, -1 nếu khách)

    Vì sao phải có bước này: **12 bàn kỳ vọng gặp nhóm cuối bảng và 12 bàn gặp
    nhóm đầu bảng không phải cùng một bằng chứng.** Cộng thẳng xG cả mùa — cách
    làm cũ — coi hai thứ đó như nhau, nên đội gặp lịch nhẹ ở đầu mùa được chấm
    mạnh hơn thực tế đúng bằng mức nhẹ của lịch. Đây chính là chữ
    "opponent-adjusted" trong đặc tả.

    Trả None khi đồ thị trận đấu quá thưa để tách được hai nhóm tham số — thà
    không trả lời còn hơn trả lời bằng một nghiệm mà dữ liệu không xác định.
    `weak[j]` là mức **dễ bị thủng lưới**; nghịch đảo của nó mới là sức mạnh phòng
    ngự theo quy ước của file này.
    """
    if not obs or not team_ids:
        return None

    played: dict[int, int] = {t: 0 for t in team_ids}
    for o in obs:
        played[o.attacker] = played.get(o.attacker, 0) + 1
        played[o.defender] = played.get(o.defender, 0) + 1
    # mỗi trận cho 2 quan sát nên số trận = số quan sát / 2 cho mỗi đội
    if min(played.get(t, 0) for t in team_ids) < MIN_MATCHES_FOR_ADJUSTMENT:
        return None

    base = sum(o.value for o in obs) / len(obs)
    if base <= 0:
        return None

    home_vals = [o.value for o in obs if o.attacker_home]
    away_vals = [o.value for o in obs if not o.attacker_home]
    if home_vals and away_vals:
        mh, ma = sum(home_vals) / len(home_vals), sum(away_vals) / len(away_vals)
        h = math.sqrt(max(mh, 1e-6) / max(ma, 1e-6))
    else:
        h = 1.0
    h = max(0.85, min(1.35, h))

    att = {t: 1.0 for t in team_ids}
    weak = {t: 1.0 for t in team_ids}

    for _ in range(iters):
        # cập nhật tấn công: tổng thực tế / tổng kỳ vọng nếu đội này ở mức trung bình
        num: dict[int, float] = {t: 0.0 for t in team_ids}
        den: dict[int, float] = {t: 0.0 for t in team_ids}
        for o in obs:
            venue = h if o.attacker_home else 1.0 / h
            num[o.attacker] += o.value
            den[o.attacker] += base * weak[o.defender] * venue
        for t in team_ids:
            if den[t] > 1e-9:
                att[t] = num[t] / den[t]
        g = _geo_mean(list(att.values()))
        for t in team_ids:
            att[t] = att[t] / g

        num = {t: 0.0 for t in team_ids}
        den = {t: 0.0 for t in team_ids}
        for o in obs:
            venue = h if o.attacker_home else 1.0 / h
            num[o.defender] += o.value
            den[o.defender] += base * att[o.attacker] * venue
        for t in team_ids:
            if den[t] > 1e-9:
                weak[t] = num[t] / den[t]
        g = _geo_mean(list(weak.values()))
        for t in team_ids:
            weak[t] = weak[t] / g

    return att, weak, h


# ------------------------------------------------------- các thành phần ------
def _gk_xgc90(players: list, exclude_ids: set[str]) -> dict[int, float]:
    """xGC/90 của thủ môn đá nhiều nhất mỗi đội — mốc hàng thủ của cả đội.

    Vì sao là thủ môn: xG **đi theo cầu thủ** (tiền đạo đổi CLB thì bàn của anh ta
    thành sản lượng kỳ vọng của CLB mới) còn xGC là thuộc tính của **ĐỘI** và
    không đi theo ai. Lấy `max(expected_goals_conceded)` toàn đội từng chấm hàng
    thủ Man City bằng 53.6 xGC mà Elliot Anderson tích ở Nottingham Forest. Thủ
    môn ít bị xoay vòng nhất, và xGC/90 của anh ta *chính là* mức bị uy hiếp của
    đội trong lúc anh ta trên sân — một tỷ lệ, không phụ thuộc số phút.
    """
    best: dict[int, object] = {}
    for p in players:
        if getattr(p, "element_type", None) != 1:
            continue
        if str(getattr(p, "id", "")) in exclude_ids:
            continue
        mins = getattr(p, "minutes", 0) or 0
        if mins < MIN_GK_MINUTES_FOR_PROXY:
            continue
        tid = getattr(p, "team_id", None)
        if tid is None:
            continue
        cur = best.get(tid)
        if cur is None or mins > (getattr(cur, "minutes", 0) or 0):
            best[tid] = p

    out: dict[int, float] = {}
    for tid, gk in best.items():
        mins = getattr(gk, "minutes", 0) or 0
        xgc = getattr(gk, "expected_goals_conceded", 0.0) or 0.0
        if mins:
            out[tid] = xgc / (mins / 90.0)
    return out


def _fit_log_slope(xs: list[float], ys: list[float]) -> float | None:
    """Độ dốc hồi quy qua gốc của log(y) theo log(x), cả hai đã chuẩn hoá về 1.

    Dùng để hỏi dữ liệu — chứ không tự đặt — rằng "đội hình đắt gấp đôi thì ghi
    được gấp mấy". Qua gốc vì cả hai trục đã chia cho trung bình giải: đội trung
    bình tiền phải ứng với đội trung bình bàn thắng, đó là ràng buộc chứ không
    phải tham số.
    """
    if len(xs) < MIN_TEAMS_FOR_ELASTICITY:
        return None
    sxy = sum(x * y for x, y in zip(xs, ys))
    sxx = sum(x * x for x in xs)
    if sxx <= 1e-9:
        return None
    return max(0.0, min(2.5, sxy / sxx))


def _squad_quality(
    players: list, team_ids: list[int], no_history: dict[int, bool] | None = None
) -> tuple[dict[int, tuple[float, float]], str]:
    """Chất lượng đội hình theo ĐỊNH GIÁ FPL, quy đổi sang thang bàn thắng.

    Vì sao dùng giá: FPL định giá lại toàn bộ đội hình mỗi mùa hè, và giá đó là
    ước lượng của chính nhà điều hành về sản lượng mùa TỚI — nó đã nuốt vào cả
    chuyển nhượng đến lẫn đi. Đó là thứ gần nhất với "squad-quality change" mà API
    thật sự công bố: API **không** công bố lịch sử chuyển nhượng, nên không thể
    lấy hiệu đội hình năm nay trừ đội hình năm ngoái.

    Giới hạn phải nói thẳng: giá cũng phản ánh điểm mùa trước, nên thành phần này
    không độc lập hoàn toàn với thành phần xG. Đó là lý do nó chỉ chiếm 15%.

    Độ dốc giá→bàn thắng được **khớp tại chỗ** trên chính 20 đội đang có trong DB,
    không ghi cứng.
    """
    att_price: dict[int, float] = {t: 0.0 for t in team_ids}
    def_price: dict[int, float] = {t: 0.0 for t in team_ids}
    by_team: dict[int, list] = {t: [] for t in team_ids}
    for p in players:
        tid = getattr(p, "team_id", None)
        if tid in by_team:
            by_team[tid].append(p)

    for tid, squad in by_team.items():
        def _price(pl) -> float:
            return (getattr(pl, "now_cost", 0) or 0) / 10.0

        attackers = sorted(
            (p for p in squad if getattr(p, "element_type", 0) in (3, 4)),
            key=_price, reverse=True,
        )[:6]
        gk = sorted(
            (p for p in squad if getattr(p, "element_type", 0) == 1),
            key=_price, reverse=True,
        )[:1]
        defs = sorted(
            (p for p in squad if getattr(p, "element_type", 0) == 2),
            key=_price, reverse=True,
        )[:4]
        att_price[tid] = sum(_price(p) for p in attackers)
        def_price[tid] = sum(_price(p) for p in gk + defs)

    m_att = _geo_mean([v for v in att_price.values() if v > 0])
    m_def = _geo_mean([v for v in def_price.values() if v > 0])
    if m_att <= 0 or m_def <= 0:
        return {}, "không có dữ liệu giá cầu thủ"

    # Khớp độ dốc trên chính dữ liệu đang có — RIÊNG cho từng vế. Tiền đổ vào hàng
    # công và tiền đổ vào hàng thủ không mua được cùng một mức sản lượng, nên dùng
    # chung một độ dốc là ép hàng thủ theo hệ số đo trên hàng công. Đo được: độ dốc
    # chung chạm trần 2.5 và đẩy chỉ số phòng ngự của nhóm đầu bảng kịch trần 1.70.
    xg_by_team: dict[int, float] = {t: 0.0 for t in team_ids}
    for p in players:
        tid = getattr(p, "team_id", None)
        if tid in xg_by_team:
            xg_by_team[tid] += getattr(p, "expected_goals", 0.0) or 0.0
    m_xg = _geo_mean([v for v in xg_by_team.values() if v > 0])

    xgc90 = _gk_xgc90(players, set())
    m_xgc = _geo_mean([v for v in xgc90.values() if v > 0])

    # Đội mới lên hạng bị loại khỏi PHÉP KHỚP (không khỏi kết quả). xG Ngoại hạng
    # của họ bằng ~0 vì mùa trước họ đá giải khác, chứ không phải vì đội hình rẻ.
    # Để lại thì đúng một điểm đó lái cả độ dốc: đo được 3.54 với Ipswich trong
    # mẫu (xG bằng 2% trung bình giải trong khi giá bằng 86%), tức là hồi quy đang
    # học "đội mới lên hạng thì rẻ", không phải "tiền mua được bao nhiêu bàn".
    no_history = no_history or {}
    xs_a, ys_a, xs_d, ys_d = [], [], [], []
    for t in team_ids:
        if no_history.get(t):
            continue
        if m_xg > 0 and att_price[t] > 0 and xg_by_team[t] > 0:
            xs_a.append(math.log(att_price[t] / m_att))
            ys_a.append(math.log(xg_by_team[t] / m_xg))
        gk = xgc90.get(t)
        if m_xgc > 0 and def_price[t] > 0 and gk:
            xs_d.append(math.log(def_price[t] / m_def))
            # vế y phải cùng hướng với chỉ số: cao = thủng ÍT = mạnh
            ys_d.append(math.log(m_xgc / gk))

    slope_a = _fit_log_slope(xs_a, ys_a)
    slope_d = _fit_log_slope(xs_d, ys_d)
    fitted_a, fitted_d = slope_a is not None, slope_d is not None
    slope_a = DEFAULT_PRICE_ELASTICITY if slope_a is None else slope_a
    slope_d = DEFAULT_PRICE_ELASTICITY if slope_d is None else slope_d

    out: dict[int, tuple[float, float]] = {}
    for t in team_ids:
        a = clamp_index((att_price[t] / m_att) ** slope_a) if att_price[t] > 0 else None
        d = clamp_index((def_price[t] / m_def) ** slope_d) if def_price[t] > 0 else None
        if a is not None and d is not None:
            out[t] = (a, d)

    def _tag(slope: float, fitted: bool, n: int) -> str:
        return (
            f"{slope:.2f} (khớp trên {n} đội)"
            if fitted
            else f"{slope:.2f} (mặc định — chưa đủ {MIN_TEAMS_FOR_ELASTICITY} đội để khớp)"
        )

    detail = (
        "định giá FPL; độ dốc giá→sản lượng: công "
        f"{_tag(slope_a, fitted_a, len(xs_a))}, thủ {_tag(slope_d, fitted_d, len(xs_d))}"
    )
    return out, detail


def _elo_from_ratings(teams: list) -> tuple[dict[int, tuple[float, float]], str]:
    """Chỉ số công/thủ từ strength ratings của FPL — thứ gần Elo nhất API có.

    Trả rỗng trước vòng 1: FPL chưa phát rating, cả 20 đội đều là 0/None, và một
    bảng toàn 0 chuẩn hoá ra 1.0 cho tất cả — tức là giả vờ có bằng chứng.
    """
    vals_ah = [getattr(t, "strength_attack_home", 0) or 0 for t in teams]
    vals_aa = [getattr(t, "strength_attack_away", 0) or 0 for t in teams]
    vals_dh = [getattr(t, "strength_defence_home", 0) or 0 for t in teams]
    vals_da = [getattr(t, "strength_defence_away", 0) or 0 for t in teams]
    if not any(vals_ah) or not any(vals_dh):
        return {}, "FPL chưa phát strength ratings (trước vòng 1 cả 20 đội đều rỗng)"

    def _mean(vs: list[float]) -> float:
        live = [v for v in vs if v]
        return sum(live) / len(live) if live else 1.0

    m_ah, m_aa = _mean(vals_ah), _mean(vals_aa)
    m_dh, m_da = _mean(vals_dh), _mean(vals_da)
    out: dict[int, tuple[float, float]] = {}
    for t in teams:
        ah = getattr(t, "strength_attack_home", 0) or 0
        aa = getattr(t, "strength_attack_away", 0) or 0
        dh = getattr(t, "strength_defence_home", 0) or 0
        da = getattr(t, "strength_defence_away", 0) or 0
        if not (ah and dh):
            continue
        att = _geo_mean([ah / m_ah, (aa / m_aa) if aa else ah / m_ah])
        dfc = _geo_mean([dh / m_dh, (da / m_da) if da else dh / m_dh])
        out[t.id] = (clamp_index(att), clamp_index(dfc))
    return out, "FPL strength ratings (trung bình hình học sân nhà/khách)"


# ------------------------------------------------------------- gộp prior ----
def build_priors(
    teams: list,
    players: list,
    *,
    match_xg: list[Observation] | None = None,
    market_obs: list[Observation] | None = None,
    matches_played: dict[int, int] | None = None,
    new_manager_shorts: set[str] | None = None,
    new_signing_ids: set[str] | None = None,
    championship: dict[int, tuple[float, float]] | None = None,
    championship_damping: float = 0.35,
    manager_continuity_weight: float = 0.6,
    history_shrink_minutes: float = 8000.0,
    match_shrink: float = 6.0,
) -> dict[int, TeamPrior]:
    """Prior của cả giải: {team_id: TeamPrior}.

    `match_xg` là xG **theo từng trận** (từ `PlayerGameweekStat`), thứ duy nhất cho
    phép hiệu chỉnh đối thủ. Thiếu nó — trước vòng 1, hoặc khi chưa bật đồng bộ chi
    tiết — thành phần xG rơi về tổng cả mùa và tự khai là *chưa* hiệu chỉnh đối thủ.

    `manager_continuity_weight` là phần trọng số của thành phần xG còn giữ lại với
    CLB đã đổi huấn luyện viên. Phần bị cắt **chuyển sang** thành phần
    manager_continuity, vốn neo ở trung bình giải — nên đổi HLV kéo prior của đội
    về giữa, chứ không tự động chấm đội đó yếu đi. Đây là chỗ duy nhất trong hệ
    thống phát biểu điều đó, và nó dùng đúng hệ số mà mô hình xMins đã dùng cho
    từng cầu thủ của các CLB ấy.
    """
    team_ids = [t.id for t in teams]
    if not team_ids:
        return {}

    matches_played = matches_played or {}
    new_manager_shorts = new_manager_shorts or set()
    new_signing_ids = new_signing_ids or set()
    championship = championship or {}

    # --- đội nào chưa có lịch sử Ngoại hạng ---
    team_minutes: dict[int, int] = {t: 0 for t in team_ids}
    for p in players:
        tid = getattr(p, "team_id", None)
        if tid in team_minutes:
            team_minutes[tid] += getattr(p, "minutes", 0) or 0
    reference_minutes = max(team_minutes.values()) if team_minutes else 0
    no_history = {
        t: reference_minutes > 0 and team_minutes[t] < NO_HISTORY_RATIO * reference_minutes
        for t in team_ids
    }

    # --- 1) xG/xGA (hiệu chỉnh đối thủ nếu có dữ liệu theo trận) ---
    xg_idx, xg_detail = _xg_component(
        team_ids, players, match_xg, no_history, new_signing_ids,
        team_minutes, history_shrink_minutes, match_shrink,
    )

    # --- 2) market / Elo ---
    mk_idx, mk_detail = _market_elo_component(teams, team_ids, market_obs)

    # --- 3) chất lượng đội hình ---
    sq_idx, sq_detail = _squad_quality(players, team_ids, no_history)

    # --- 5) dữ liệu tiền mùa "đã hiệu chỉnh chất lượng" ---
    pre_idx, pre_detail = _preseason_component(
        team_ids, no_history, championship, championship_damping, matches_played
    )

    priors: dict[int, TeamPrior] = {}
    for tid in team_ids:
        changed_manager = False
        short = next(
            (getattr(t, "short_name", "") or "" for t in teams if t.id == tid), ""
        )
        if short and short.upper() in new_manager_shorts:
            changed_manager = True

        w_xg = NOMINAL_WEIGHTS["xg_adjusted"]
        w_mgr = NOMINAL_WEIGHTS["manager_continuity"]
        if changed_manager:
            # Chiết khấu phần trọng số của "đội bóng cũ chơi thế nào" và chuyển
            # đúng phần đó sang cái neo trung bình giải.
            moved = w_xg * (1.0 - manager_continuity_weight)
            w_xg -= moved
            w_mgr += moved

        comps: dict[str, Component] = {
            "xg_adjusted": Component(
                "xg_adjusted", *(xg_idx.get(tid) or (None, None)),
                weight=w_xg, detail=xg_detail,
            ),
            "market_elo": Component(
                "market_elo", *(mk_idx.get(tid) or (None, None)),
                weight=NOMINAL_WEIGHTS["market_elo"], detail=mk_detail,
            ),
            "squad_quality": Component(
                "squad_quality", *(sq_idx.get(tid) or (None, None)),
                weight=NOMINAL_WEIGHTS["squad_quality"], detail=sq_detail,
            ),
            "preseason": Component(
                "preseason", *(pre_idx.get(tid) or (None, None)),
                weight=NOMINAL_WEIGHTS["preseason"], detail=pre_detail.get(tid, ""),
            ),
        }

        evidence = [c for c in comps.values() if c.weight > 0]
        attack, w_att = _blend([(c.weight, c.attack) for c in evidence])
        defence, w_def = _blend([(c.weight, c.defence) for c in evidence])

        # --- thành phần thứ tư: tính liên tục của huấn luyện viên / hệ thống -----
        # Nó KHÔNG mang hướng. "Đổi HLV" không phải bằng chứng đội yếu đi, mà là
        # bằng chứng dữ liệu cũ mô tả đội hiện tại kém đi — nên nó neo ở trung bình
        # giải và kéo prior về giữa. Ngược lại, **giữ nguyên HLV nghĩa là thành
        # phần này đồng ý với phần còn lại**, và một quan sát trùng khớp với đồng
        # thuận hiện có thì theo định nghĩa không làm đồng thuận đó dịch đi: nó
        # được cộng vào tổng bằng chứng nhưng không đổi con số. Cách viết này giữ
        # đúng 10% của đặc tả mà không lén chính quy hoá cả 20 đội về 1.0.
        if changed_manager:
            attack, w_att = _blend([(w_att, attack), (w_mgr, 1.0)])
            defence, w_def = _blend([(w_def, defence), (w_mgr, 1.0)])
        else:
            w_att += w_mgr if w_att > 0 else 0.0
            w_def += w_mgr if w_def > 0 else 0.0

        comps["manager_continuity"] = Component(
            "manager_continuity",
            1.0 if changed_manager else attack,
            1.0 if changed_manager else defence,
            weight=w_mgr,
            detail=(
                "ĐÃ đổi HLV — chuyển bớt trọng số của xG mùa trước sang neo trung "
                "bình giải, tức kéo prior về giữa"
                if changed_manager
                else "giữ HLV — xác nhận các nguồn kia, không dịch prior"
            ),
        )

        priors[tid] = TeamPrior(
            team_id=tid,
            attack=attack if attack is not None else 1.0,
            defence=defence if defence is not None else 1.0,
            no_pl_history=no_history[tid],
            components=comps,
            evidence_weight=round(max(w_att, w_def), 4),
        )
    return priors


def _blend(pairs: list[tuple[float, float | None]]) -> tuple[float, float]:
    """Trung bình hình học có trọng số, bỏ qua nguồn không có dữ liệu.

    Trả `(giá trị, tổng trọng số đã dùng)`. Tổng trọng số chính là mẫu số của phép
    chuẩn hoá lại, và cũng là thước đo "prior này dựa trên bao nhiêu bằng chứng" mà
    BƯỚC 5 dùng để phạt độ bất định.

    Một nguồn duy nhất được trả **nguyên văn**, không đi vòng qua log rồi exp: chỗ
    đó không thêm thông tin gì và chỉ thêm sai số làm tròn vào một con số mà nơi
    khác kiểm tra bằng phép so sánh bằng.
    """
    live = [(w, v) for w, v in pairs if v is not None and w > 0]
    if not live:
        return 1.0, 0.0
    den = sum(w for w, _ in live)
    if len(live) == 1:
        return clamp_index(live[0][1]), den
    num = sum(w * math.log(max(v, 1e-6)) for w, v in live)
    return clamp_index(math.exp(num / den)), den


def _shrink_to_league(index: float, weight: float) -> float:
    """Kéo chỉ số về 1.0 theo cỡ mẫu, trong không gian log: `index^weight`.

    Vì sao bắt buộc phải có: sau vòng 1, một đội ghi 3 bàn có tỷ lệ xG gấp bốn lần
    trung bình giải. Không co giãn thì prior của đội đó chạm trần 1.70 và ô lịch
    của mọi đối thủ của họ đổi màu — từ **một** trận. Số mũ nhỏ hơn 1 nói đúng
    điều mà cỡ mẫu cho phép nói: "có tín hiệu, nhưng chưa đủ để kết luận".
    """
    weight = max(0.0, min(1.0, weight))
    return clamp_index(max(index, 1e-6) ** weight)


def _xg_component(
    team_ids: list[int],
    players: list,
    match_xg: list[Observation] | None,
    no_history: dict[int, bool],
    new_signing_ids: set[str],
    team_minutes: dict[int, int],
    history_shrink_minutes: float,
    match_shrink: float,
) -> tuple[dict[int, tuple[float | None, float | None]], str]:
    """45% — xG/xGA. Hiệu chỉnh đối thủ khi có dữ liệu theo trận, không thì thôi.

    Tấn công và phòng ngự được trả **độc lập**: có đội chỉ đo được một trong hai
    (mốc hàng thủ cần một thủ môn đủ 900 phút, thứ mà đội mới lên hạng không có).
    Ghép hai vế thành một điều kiện "cùng có hoặc cùng không" sẽ vứt bỏ vế đo được
    chỉ vì vế kia thiếu.
    """
    if match_xg:
        solved = solve_attack_defence(match_xg, team_ids)
        if solved:
            att, weak, _h = solved
            n_matches = len(match_xg) // 2
            per_team: dict[int, int] = {}
            for o in match_xg:
                per_team[o.attacker] = per_team.get(o.attacker, 0) + 1
            out: dict[int, tuple[float | None, float | None]] = {}
            for t in team_ids:
                mp = per_team.get(t, 0)
                w = mp / (mp + match_shrink) if mp else 0.0
                out[t] = (
                    _shrink_to_league(clamp_index(att.get(t, 1.0)), w),
                    _shrink_to_league(
                        clamp_index(1.0 / max(weak.get(t, 1.0), 1e-6)), w
                    ),
                )
            return out, f"xG theo trận, đã hiệu chỉnh đối thủ (IPF trên {n_matches} trận)"

    # Không đủ trận để tách công/thủ khỏi chất lượng đối thủ -> tổng cả mùa.
    xg_total: dict[int, float] = {t: 0.0 for t in team_ids}
    for p in players:
        tid = getattr(p, "team_id", None)
        if tid in xg_total:
            xg_total[tid] += getattr(p, "expected_goals", 0.0) or 0.0
    xgc90 = _gk_xgc90(players, new_signing_ids)

    m_xg = _geo_mean([v for v in xg_total.values() if v > 0])
    m_xgc = _geo_mean([v for v in xgc90.values() if v > 0])

    out: dict[int, tuple[float | None, float | None]] = {}
    for t in team_ids:
        if no_history[t]:
            # Không có gì để học -> mức nền đội mới lên hạng, KHÔNG chia cho mẫu ~0.
            # Không co giãn: đây vốn đã là một prior, không phải ước lượng từ mẫu.
            out[t] = (PROMOTED_ATTACK, PROMOTED_DEFENCE)
            continue
        mins = team_minutes.get(t, 0)
        w = mins / (mins + history_shrink_minutes) if mins else 0.0
        a = (
            _shrink_to_league(clamp_index(xg_total[t] / m_xg), w)
            if (m_xg > 0 and xg_total[t] > 0)
            else None
        )
        gk = xgc90.get(t)
        d = _shrink_to_league(clamp_index(m_xgc / gk), w) if (gk and m_xgc > 0) else None
        if a is not None or d is not None:
            out[t] = (a, d)
    return out, (
        "tổng xG cả mùa + xGC/90 của thủ môn — CHƯA hiệu chỉnh đối thủ "
        f"(cần ≥{MIN_MATCHES_FOR_ADJUSTMENT} trận/đội có dữ liệu xG theo trận)"
    )


def _market_elo_component(
    teams: list, team_ids: list[int], market_obs: list[Observation] | None
) -> tuple[dict[int, tuple[float, float]], str]:
    """25% — sức mạnh theo thị trường, hoặc Elo thay thế.

    Kèo nhà cái chỉ được dùng ở ĐÂY khi đủ dày để tách công/thủ khỏi đối thủ. Kèo
    thường chỉ phủ 1–2 vòng tới, tức 10–20 quan sát cho 40 tham số — bài toán vô
    định, và IPF vẫn sẽ trả về *một* nghiệm nào đó, chỉ là nghiệm mà dữ liệu không
    xác định. Khi kèo mỏng thì nó vẫn được dùng, nhưng ở BƯỚC 3 (hiệu chuẩn từng
    trận) — đúng chỗ mà một trận có giá thì chỉ trận đó được hưởng.
    """
    if market_obs:
        solved = solve_attack_defence(market_obs, team_ids)
        if solved:
            att, weak, _h = solved
            out = {
                t: (clamp_index(att.get(t, 1.0)), clamp_index(1.0 / max(weak.get(t, 1.0), 1e-6)))
                for t in team_ids
            }
            return out, f"λ nhà cái, hiệu chỉnh đối thủ ({len(market_obs) // 2} trận có giá)"

    idx, detail = _elo_from_ratings(teams)
    if idx:
        return idx, f"kèo chưa đủ dày để tách công/thủ → {detail}"
    return {}, f"không có: kèo chưa đủ dày, và {detail}"


def _preseason_component(
    team_ids: list[int],
    no_history: dict[int, bool],
    championship: dict[int, tuple[float, float]],
    damping: float,
    matches_played: dict[int, int],
) -> tuple[dict[int, tuple[float, float]], dict[int, str]]:
    """5% — sản lượng tiền mùa "đã hiệu chỉnh chất lượng đối thủ".

    FPL API **không có giao hữu tiền mùa**: không xG, không đội hình, không kết
    quả. Với 17 đội trụ hạng, thành phần này đơn giản là không tồn tại, và trọng
    số 5% của nó được chia lại cho bốn nguồn kia.

    Với ba đội mới lên hạng thì có một thứ đúng nghĩa "underlying data từ một giải
    khác, đã hiệu chỉnh chất lượng": thành tích Championship mùa trước. Hiệu chỉnh
    chất lượng chính là số mũ `damping` — Championship không phải Ngoại hạng, nên
    vượt trội ở đó không quy đổi 1:1. Trần 1.0: dữ liệu hạng dưới không bao giờ đủ
    để chấm một đội mới lên hạng ngang mức trung bình Ngoại hạng.
    """
    out: dict[int, tuple[float, float]] = {}
    details: dict[int, str] = {}
    for t in team_ids:
        if not no_history[t]:
            details[t] = "không có: FPL API không công bố dữ liệu giao hữu tiền mùa"
            continue
        champ = championship.get(t)
        fade = max(0.0, 1.0 - matches_played.get(t, 0) / CHAMP_FADE_MATCHES)
        if not champ or fade <= 0:
            details[t] = (
                "không có: chưa đồng bộ dữ liệu Championship"
                if not champ
                else "đã tắt: đội đã có đủ trận Ngoại hạng thật"
            )
            continue
        d = damping * fade
        a_idx = max(champ[0], 0.2) ** d
        d_idx = max(champ[1], 0.2) ** d
        out[t] = (
            min(PROMOTED_ATTACK * a_idx, 1.0),
            min(PROMOTED_DEFENCE * d_idx, 1.0),
        )
        details[t] = (
            f"Championship mùa trước, hệ số hiệu chỉnh chất lượng {d:.2f} "
            f"(nhạt dần sau {CHAMP_FADE_MATCHES} trận Ngoại hạng)"
        )
    return out, details
