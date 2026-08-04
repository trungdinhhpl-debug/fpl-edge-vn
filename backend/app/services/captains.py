"""Captaincy service (spec §11).

Ranking captains by expected points alone answers only one of the four
questions a manager actually has, and the answers differ:

  * **EV**       — most points on average.
  * **Safety**   — protect a lead: floor, minutes security, low blank risk.
  * **Ceiling**  — the score that wins a gameweek outright.
  * **Chasing**  — most points *relative to the field*, which is a function of
                   effective ownership, not of xP.

The old code ranked by EV, cut to the top 20, then tagged that slice — so a
genuine ceiling or differential pick sitting 25th on EV could never surface. Each
list is now scored and ranked over the whole candidate pool independently.

`differential_edge` is the one number that makes the chase list honest. If you
captain X and the field's effective ownership of X is EO, your points from X are
2·xP while the average manager gets (EO/100)·xP, so your edge is

    edge = xP · (2 − EO/100)

At EO = 200% (everyone owns him and everyone captains him) the edge is exactly
zero — captaining the template cannot gain you rank, no matter how high his xP.
"""
from __future__ import annotations

import math

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExpectedMinutes, Player, PlayerProjection
from app.services.common import planning_start_gw, player_public, team_lookup

# How sharply captaincy concentrates on the best EV option, per point of captain
# xP. Calibrated against the published shape of FPL captaincy in a normal week —
# a dominant premium takes roughly half the armbands and the runner-up a tenth,
# with a long tail. It is a model of crowd behaviour, not observed data, so the
# EO it feeds carries its own confidence label (see project_effective_ownership).
# Sanity check when changing it: 1.35 collapses to a 99%/1% split, which no real
# gameweek has ever looked like.
CAPTAINCY_CONCENTRATION = 0.35
# A player must be at least this likely to start to be captainable at all.
MIN_START_PROB = 0.4


# ------------------------------------------------------- effective ownership --
def project_effective_ownership(cands: list[dict]) -> None:
    """Attach projected EO (= ownership + projected captaincy share) in place.

    FPL publishes ownership but NOT captaincy counts before a deadline, so the
    captaincy half has to be modelled: managers can only captain someone they
    own, and they crowd onto the best EV option. We spread 100 captaincies over
    the pool with

        share(p) ∝ ownership(p) · exp(k · (EV(p) − EV_best))

    and label the result a projection with its own confidence, because a model
    number must never be presented as a measured one.
    """
    if not cands:
        return
    best_ev = max(c["captain_xp"] for c in cands)
    weights = []
    for c in cands:
        w = (c["selected_by_percent"] or 0.0) * math.exp(
            CAPTAINCY_CONCENTRATION * (c["captain_xp"] - best_ev)
        )
        weights.append(max(w, 0.0))
    total = sum(weights)
    for c, w in zip(cands, weights):
        # Captaincy shares across all players sum to 100% of managers.
        share = (100.0 * w / total) if total > 0 else 0.0
        own = c["selected_by_percent"] or 0.0
        c["projected_captaincy"] = round(share, 1)
        c["projected_eo"] = round(own + share, 1)
        c["eo_method"] = "ownership (FPL) + captaincy share (mô hình)"
        # We trust it least exactly where it matters most: the crowded picks.
        c["eo_confidence"] = "Low" if share > 25 else "Medium"


# ------------------------------------------------------------ sub / minutes ---
def _sub_risk(p_start: float, p_60: float) -> tuple[float, str]:
    """P(hooked before 60 | started), plus a label.

    Being withdrawn before 60 costs the second appearance point and any clean
    sheet, so for a captain it is a distinct risk from not starting at all.
    """
    if p_start <= 0.01:
        return 0.0, "Không rõ"
    risk = max(0.0, (p_start - p_60)) / p_start
    label = "Cao" if risk >= 0.30 else "Trung bình" if risk >= 0.15 else "Thấp"
    return round(risk, 3), label


# ------------------------------------------------------------------ scoring ---
def _safety_score(c: dict) -> float:
    """Downside-first: the floor you can bank on, after availability risk.

    Uses the 25th-percentile outcome rather than the mean, then discounts it by
    the chance he does not start and the chance he is hooked early, and penalises
    a fat blank tail.
    """
    floor = 2 * c["floor_raw"]
    availability = c["p_start"] * (1 - 0.5 * c["substitution_risk"])
    return floor * availability - 4.0 * c["p_blank"]


def _chase_score(c: dict) -> float:
    """Rank-chasing needs upside the field does not have.

    Ceiling rather than mean (a chaser needs a haul, not an average week),
    weighted by how little of that haul the field already banks through
    effective ownership.
    """
    return c["ceiling_raw"] * max(0.0, 2 - c["projected_eo"] / 100.0)


def _rank(cands: list[dict], key, limit: int) -> list[dict]:
    ordered = sorted(cands, key=key, reverse=True)[:limit]
    return [{**c, "rank": i + 1} for i, c in enumerate(ordered)]


# ------------------------------------------------------------------- public ---
def _build_candidates(db: Session, gw: int) -> list[dict]:
    teams = team_lookup(db)
    projs = db.scalars(
        select(PlayerProjection).where(PlayerProjection.gameweek == gw)
    ).all()
    players = {p.id: p for p in db.scalars(select(Player)).all()}
    xmins = {
        r.player_id: r for r in db.scalars(
            select(ExpectedMinutes).where(ExpectedMinutes.gameweek == gw)
        ).all()
    }

    cands: list[dict] = []
    for pr in projs:
        p = players.get(pr.player_id)
        if not p or pr.xp <= 0 or pr.p_start < MIN_START_PROB:
            continue
        est = xmins.get(pr.player_id)
        p_60 = est.p_60_plus if est else pr.p_start
        sub_risk, sub_label = _sub_risk(pr.p_start, p_60)
        xp = pr.xp
        cands.append({
            **player_public(p, teams.get(p.team_id)),
            "captain_xp": round(xp * 2, 2),
            "xp": round(xp, 2),
            "xmins": round(pr.xmins, 1),
            "p_start": round(pr.p_start, 3),
            "p_60_plus": round(p_60, 3),
            "p_blank": round(pr.p_blank, 3),        # <=2 pts, so <=4 as captain
            "p_10_plus": round(pr.p_haul, 3),       # >=10 -> >=20 as captain
            "p_15_plus": round(pr.p_15 or 0.0, 3),  # >=15 -> >=30 as captain
            "ceiling": round(pr.mc_ceiling * 2, 1),
            "floor": round(pr.mc_p25 * 2, 1),
            "ceiling_raw": pr.mc_ceiling,
            "floor_raw": pr.mc_p25,
            "penalty_order": p.penalties_order,
            "penalty_duty": (
                "Số 1" if p.penalties_order == 1
                else f"Số {p.penalties_order}" if p.penalties_order
                else "Không"
            ),
            "substitution_risk": sub_risk,
            "substitution_risk_label": sub_label,
            "goal_prob": round(pr.goal_prob, 3),
            "assist_prob": round(pr.assist_prob, 3),
            "clean_sheet_prob": round(pr.clean_sheet_prob, 3),
            # how much of his xP rides on a clean sheet — fragile if high
            "clean_sheet_dependence": round(pr.xp_clean_sheet / xp, 3) if xp > 0.01 else 0.0,
            "goal_dependence": round(pr.xp_goals / xp, 3) if xp > 0.01 else 0.0,
            "variance": round(pr.variance, 2),
            "confidence": round(pr.confidence, 2),
            "confidence_label": (
                "Cao" if pr.confidence >= 0.7
                else "Trung bình" if pr.confidence >= 0.45 else "Thấp"
            ),
            "overall_risk": pr.overall_risk,
            "opponent_team": pr.opponent_team,
            "was_home": pr.was_home,
        })

    project_effective_ownership(cands)
    for c in cands:
        c["differential_edge"] = round(
            c["xp"] * max(0.0, 2 - c["projected_eo"] / 100.0), 2
        )
    return cands


def captain_ranking(db: Session, gw: int | None = None, limit: int = 20) -> dict:
    """Four independent rankings over the same candidate pool."""
    gw = gw or planning_start_gw(db)
    cands = _build_candidates(db, gw)

    lists = {
        "ev": {
            "title": "EV cao nhất",
            "desc": "Nhiều điểm nhất tính trung bình. Xếp theo xP × 2.",
            "players": _rank(cands, lambda c: c["captain_xp"], limit),
        },
        "safe": {
            "title": "An toàn nhất",
            "desc": ("Bảo vệ thứ hạng: sàn điểm cao, gần như chắc đá chính và đá "
                     "đủ 60 phút, ít khả năng tịt ngòi."),
            "players": _rank(cands, _safety_score, limit),
        },
        "ceiling": {
            "title": "Ceiling cao nhất",
            "desc": "Kịch bản tốt nhất (bách phân vị 95) — điểm đủ để thắng cả vòng.",
            "players": _rank(cands, lambda c: c["ceiling_raw"], limit),
        },
        "chase": {
            "title": "Đuổi hạng tốt nhất",
            "desc": ("Điểm hơn được đám đông, không phải điểm tuyệt đối. "
                     "Ceiling × (2 − EO/100): EO càng cao thì càng khó bứt lên."),
            "players": _rank(cands, _chase_score, limit),
        },
    }
    return {
        "gameweek": gw,
        "n_candidates": len(cands),
        "lists": lists,
        "eo_note": ("EO dự phóng = tỷ lệ sở hữu (số thật của FPL) + tỷ lệ bắt "
                    "băng đội trưởng (MÔ HÌNH — FPL không công khai trước hạn "
                    "chót). Con số càng đông người chọn thì càng kém chắc chắn."),
    }


# ------------------------------------------------------------ head to head ---
# (label, key, higher_is_better, formatter)
_DIMENSIONS: list[tuple[str, str, bool, str]] = [
    ("Phút thi đấu kỳ vọng", "xmins", True, "mins"),
    ("Khả năng đá chính", "p_start", True, "pct"),
    ("Đá đủ 60 phút", "p_60_plus", True, "pct"),
    ("Rủi ro bị thay ra", "substitution_risk", False, "pct"),
    ("Đá phạt đền", "penalty_rank", True, "pen"),
    ("Xác suất ghi bàn", "goal_prob", True, "pct"),
    ("Khả năng kiến tạo", "assist_prob", True, "pct"),
    ("Điểm kỳ vọng (EV)", "captain_xp", True, "pts"),
    ("Ceiling", "ceiling", True, "pts"),
    ("Sàn điểm", "floor", True, "pts"),
    ("Nguy cơ tịt ngòi", "p_blank", False, "pct"),
    ("Cơ hội bùng nổ (≥20đ)", "p_10_plus", True, "pct"),
    # EO cuts both ways, so both sides get their own row: a crowded pick shields
    # you when he hauls, a light one is the only way to actually gain rank.
    ("Che chắn thứ hạng (EO cao)", "projected_eo", True, "pct_raw"),
    ("Lợi thế bứt phá (điểm hơn đám đông)", "differential_edge", True, "pts"),
    ("Ít phụ thuộc clean sheet", "clean_sheet_dependence", False, "pct"),
    ("Độ tin cậy dự báo", "confidence", True, "pct"),
]

# A gap must clear BOTH bars to count as an edge: a share of the larger value,
# and an absolute floor in the dimension's own units. Relative alone declares
# "5% vs 7% substitution risk" an advantage, which is noise dressed as a finding.
MEANINGFUL_GAP = 0.08
MIN_ABS_GAP = {
    "pct": 0.03,        # 3 percentage points
    "pct_raw": 3.0,     # EO, already in percent
    "mins": 3.0,        # minutes
    "pts": 0.3,         # points
    "pen": 0.5,         # penalty rank is ordinal — any real step counts
}


def _fmt(kind: str, v: float) -> str:
    if kind == "pct":
        return f"{v * 100:.0f}%"
    if kind == "pct_raw":
        return f"{v:.0f}%"
    if kind == "mins":
        return f"{v:.0f}'"
    if kind == "pts":
        return f"{v:.1f}đ"
    if kind == "pen":
        return {3: "Số 1", 2: "Số 2", 1: "Số 3+"}.get(int(v), "Không")
    return f"{v:.2f}"


def compare_captains(db: Session, a_id: int, b_id: int,
                     gw: int | None = None) -> dict:
    """Head-to-head: exactly what A wins on, what B wins on, and by how much."""
    gw = gw or planning_start_gw(db)
    cands = {c["id"]: c for c in _build_candidates(db, gw)}
    a, b = cands.get(a_id), cands.get(b_id)
    if not a or not b:
        missing = [i for i in (a_id, b_id) if i not in cands]
        return {
            "gameweek": gw,
            "error": (f"Không có dữ liệu đội trưởng cho cầu thủ {missing} ở GW{gw} "
                      f"(có thể đang chấn thương, vòng trống, hoặc khả năng đá "
                      f"chính dưới {int(MIN_START_PROB * 100)}%)."),
        }

    # penalty order is "lower is better"; flip it into a score so every
    # dimension can share one comparison rule
    for c in (a, b):
        order = c.get("penalty_order")
        c["penalty_rank"] = {1: 3, 2: 2}.get(order, 1 if order else 0)

    a_better, b_better, even = [], [], []
    for label, key, higher_better, kind in _DIMENSIONS:
        av, bv = a.get(key) or 0.0, b.get(key) or 0.0
        scale = max(abs(av), abs(bv))
        row = {
            "dimension": label,
            "a": av, "b": bv,
            "a_display": _fmt(kind, av), "b_display": _fmt(kind, bv),
            "better_is": "higher" if higher_better else "lower",
        }
        gap = abs(av - bv)
        if (scale == 0 or gap / scale < MEANINGFUL_GAP
                or gap < MIN_ABS_GAP.get(kind, 0.0)):
            even.append(row)
            continue
        a_wins = (av > bv) if higher_better else (av < bv)
        (a_better if a_wins else b_better).append(row)

    margin = round(a["captain_xp"] - b["captain_xp"], 2)
    pick, other = (a, b) if margin >= 0 else (b, a)
    verdict = {
        "pick": "a" if margin >= 0 else "b",
        "pick_name": pick["name"],
        "margin_xp": abs(margin),
        "reason": _verdict_reason(pick, other, abs(margin)),
    }
    return {
        "gameweek": gw, "a": a, "b": b,
        "a_better": a_better, "b_better": b_better, "even": even,
        "verdict": verdict,
    }


def _verdict_reason(pick: dict, other: dict, margin: float) -> str:
    """One sentence that names the real trade-off, not just the bigger number."""
    if margin < 0.5:
        base = (f"Chênh lệch EV chỉ {margin}đ — quá sát để coi là hơn kém. "
                f"Hãy chọn theo tình thế:")
        if pick["projected_eo"] > other["projected_eo"] + 10:
            return (f"{base} {pick['name']} an toàn thứ hạng hơn (EO "
                    f"{pick['projected_eo']}% so với {other['projected_eo']}%), "
                    f"{other['name']} là lựa chọn để bứt lên.")
        return (f"{base} {pick['name']} nhỉnh về EV, {other['name']} có "
                f"ceiling {other['ceiling']}đ so với {pick['ceiling']}đ.")
    bits = [f"{pick['name']} hơn {margin}đ EV"]
    if pick["p_start"] > other["p_start"] + 0.1:
        bits.append(f"lại chắc suất đá chính hơn ({pick['p_start'] * 100:.0f}% "
                    f"so với {other['p_start'] * 100:.0f}%)")
    sentence = ", ".join(bits) + "."

    # EO is the trade-off that decides these calls, and it cuts both ways —
    # naming only the case where the higher-EV pick is also the differential
    # would hide exactly the choice the manager is making.
    eo_gap = pick["projected_eo"] - other["projected_eo"]
    if eo_gap < -10:
        sentence += (f" Đồng thời ít bị đám đông chiếm chỗ hơn (EO "
                     f"{pick['projected_eo']:.0f}% so với {other['projected_eo']:.0f}%) "
                     f"— vừa hơn điểm vừa dễ bứt lên.")
    elif eo_gap > 10:
        sentence += (f" Nhưng {pick['name']} là lựa chọn của đám đông (EO "
                     f"{pick['projected_eo']:.0f}% so với {other['projected_eo']:.0f}%): "
                     f"chọn {pick['name']} là giữ thứ hạng, chọn {other['name']} "
                     f"({other['differential_edge']}đ hơn đám đông so với "
                     f"{pick['differential_edge']}đ) là đánh cược để bứt lên.")
    return sentence
