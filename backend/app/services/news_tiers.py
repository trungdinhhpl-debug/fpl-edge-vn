"""Provenance tiers for team news, and the xMins impact of each item.

A severity filter (Critical/High/…) answers "how bad", never "how much should I
believe this" or "what do I do now". Two things turn a feed into decisions:

  1. **Where it came from.** A club statement and a rumour are not the same
     evidence, and ranking them identically is how a news page misleads.
  2. **What it moved.** The number that matters is the change in expected
     minutes, because that is what propagates into xP and therefore into a
     hold/sell call.

`xmins_before` is a genuine counterfactual, not a remembered value: the minutes
model is a pure function of availability plus form, so we run it twice on the
same inputs — once with the player's real status, once with the flag cleared —
and the difference is exactly what this news did. That works on the first sync
after a story breaks, without waiting for a second model run to diff against.

Honesty about coverage: only tiers with a configured feed are ever populated.
Today that is FPL's own status feed and this project's own model. The remaining
tiers are declared with what would fill them and reported as `configured: false`
rather than quietly rendering as empty and implying nothing happened.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.engine.xmins import estimate_minutes


@dataclass(frozen=True)
class Tier:
    key: str
    label: str
    rank: int              # 1 = most direct evidence
    reliability: float     # 0..1, how much weight the tier earns
    description: str
    feed: str | None       # None => nothing wired up yet
    needs: str = ""        # what would populate it


TIERS: tuple[Tier, ...] = (
    Tier("club_official", "Chính thức từ CLB", 1, 0.98,
         "Thông báo chính thức hoặc trạng thái được CLB xác nhận.",
         "FPL status feed"),
    Tier("manager_presser", "Họp báo HLV", 2, 0.92,
         "Phát biểu trực tiếp của HLV trước trận.",
         None, "RSS/API họp báo trước trận của từng CLB."),
    Tier("club_reporter", "Nhà báo đội bóng", 3, 0.75,
         "Phóng viên chuyên trách một CLB, có lịch sử kiểm chứng được.",
         None, "Danh sách nhà báo theo CLB + nguồn RSS/API có bản quyền."),
    Tier("predicted_lineup", "Predicted lineup", 4, 0.60,
         "Đội hình dự kiến do bên thứ ba dựng, chưa ai xác nhận.",
         None, "Nguồn predicted lineup (vd Fantasy Football Scout) — hiện chỉ "
               "có seed MOCK trong expert_provider, KHÔNG dùng làm tin thật."),
    Tier("rumour", "Tin đồn", 5, 0.30,
         "Chưa có nguồn chịu trách nhiệm; chỉ để tham khảo, không tự hành động.",
         None, "Nguồn mạng xã hội có chấm điểm độ tin cậy."),
    # Not "less true" than a rumour — a different kind of evidence entirely.
    # It is the only tier whose accuracy this project can actually measure.
    Tier("model_inference", "Suy luận mô hình", 6, 0.55,
         "Không ai đưa tin, nhưng mô hình phút thi đấu thấy dấu hiệu xoay tua.",
         "engine xMins"),
)

BY_KEY = {t.key: t for t in TIERS}

# Feeds we know about -> tier. New feeds add one line here.
ORIGIN_TIER = {
    "fpl official": "club_official",
    "fpl": "club_official",
    "model": "model_inference",
    "fpl edge model": "model_inference",
}


def classify_origin(source_name: str | None) -> str:
    """Map a feed name onto a tier; unknown feeds are treated as rumour.

    Defaulting an unrecognised source DOWN to rumour is deliberate: an unknown
    origin has not earned trust, and the failure that matters here is believing
    something too much, not too little.
    """
    return ORIGIN_TIER.get((source_name or "").strip().lower(), "rumour")


# ------------------------------------------------------------ xMins impact ---
def xmins_before_after(*, element_type: int, status: str,
                       chance_of_playing: int | None, season_starts: int,
                       season_minutes: int, team_matches_played: int,
                       recent_minutes: list[int] | None,
                       n_fixtures: int = 1) -> tuple[float, float]:
    """(xMins as if the news never landed, xMins given the news).

    Both sides run the same model on the same inputs; only availability moves,
    so the gap is attributable to this news and nothing else.
    """
    common = dict(
        element_type=element_type,
        season_starts=season_starts,
        season_minutes=season_minutes,
        team_matches_played=team_matches_played,
        recent_minutes=recent_minutes or None,
        n_fixtures_this_gw=n_fixtures,
    )
    before = estimate_minutes(status="a", chance_of_playing=None, **common)
    after = estimate_minutes(status=status or "a",
                             chance_of_playing=chance_of_playing, **common)
    return round(before.xmins, 1), round(after.xmins, 1)


# --------------------------------------------------------------- the action --
def recommend(xmins_before: float, xmins_after: float, status: str,
              ownership: float) -> dict:
    """Turn the minutes change into a hold/sell call, with the reason.

    Thresholds are on the DROP, not on the absolute level: a fringe player
    falling 20 minutes is noise, a nailed starter falling 20 minutes is a story.
    """
    drop = xmins_before - xmins_after
    pct = drop / xmins_before if xmins_before > 1 else 0.0

    if status in ("i", "s", "u") or xmins_after < 15:
        to, why = "Bán", "Gần như chắc chắn không ra sân vòng này."
    elif pct >= 0.45:
        to, why = "Bán", f"Mất {drop:.0f}′ kỳ vọng ({pct * 100:.0f}%) — quá nhiều để giữ."
    elif pct >= 0.25:
        to, why = "Cân nhắc bán", f"Mất {drop:.0f}′ kỳ vọng ({pct * 100:.0f}%)."
    elif pct >= 0.10:
        to, why = "Theo dõi", f"Mới giảm {drop:.0f}′ — chờ họp báo trước khi động tay."
    else:
        to, why = "Giữ", "Chưa đủ thay đổi để phải làm gì."

    if to != "Giữ" and ownership >= 15:
        why += f" Sở hữu {ownership:.0f}% nên phần đông cũng chịu chung."

    return {"from": "Giữ", "to": to, "label": f"Giữ → {to}" if to != "Giữ" else "Giữ",
            "why": why, "xmins_drop": round(drop, 1)}
