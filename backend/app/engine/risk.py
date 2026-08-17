"""Risk indices (spec §19): minutes, performance, structural.

Each returns one of: Low / Medium / High / Very High.
"""
from __future__ import annotations

_LEVELS = ["Low", "Medium", "High", "Very High"]


def _bucket(score: float) -> str:
    """score in 0..1 -> level."""
    if score < 0.25:
        return "Low"
    if score < 0.5:
        return "Medium"
    if score < 0.75:
        return "High"
    return "Very High"


def minutes_risk(p_start: float, status: str, p_no_play: float) -> str:
    score = 0.0
    score += (1.0 - p_start) * 0.6
    score += p_no_play * 0.4
    if status != "a":
        score += 0.25
    return _bucket(min(score, 1.0))


def performance_risk(
    *,
    minutes_season: int,
    xp: float,
    goal_dependency: float,   # share of xP coming from goals
    goals_scored: int,
    expected_goals: float,
    variance: float,
) -> str:
    """High when points rely on a small/over-performing sample or are goal-heavy."""
    score = 0.0
    # small sample
    if minutes_season < 450:
        score += 0.3
    elif minutes_season < 900:
        score += 0.15
    # goal dependency (boom-or-bust)
    score += min(goal_dependency, 1.0) * 0.3
    # overperformance vs xG (regression risk)
    if expected_goals > 0.5:
        over = (goals_scored - expected_goals) / max(expected_goals, 1.0)
        if over > 0.4:
            score += min(over, 1.0) * 0.3
    # raw variance relative to mean
    if xp > 0 and variance / max(xp, 1.0) > 4:
        score += 0.15
    return _bucket(min(score, 1.0))


def combine(minutes: str, performance: str) -> str:
    """Overall = the worse of the two, nudged up if both are elevated."""
    mi = _LEVELS.index(minutes)
    pi = _LEVELS.index(performance)
    idx = max(mi, pi)
    if mi >= 1 and pi >= 1:
        idx = min(idx + 1, len(_LEVELS) - 1)
    return _LEVELS[idx]


# Trần độ tin cậy khi mùa giải CHƯA đá trận nào. Phải nằm dưới ngưỡng mà giao diện
# gọi là "Cao" (0.70) — xem `services/captains.py`.
PRESEASON_CONFIDENCE_CAP = 0.6


def confidence_from(
    minutes_conf: str,
    minutes_season: int,
    has_recent: bool,
    team_matches_played: int | None = None,
) -> float:
    """Độ tin cậy 0..1 của một dự báo.

    `team_matches_played` là số trận CLB đã đá **trong mùa này**, và nó cần thiết
    vì `minutes_season` không tự nói lên điều đó: trước vòng 1, FPL vẫn phát tổng
    phút của mùa TRƯỚC. Nguyên trạng, phần thưởng "mẫu lớn" (+0.1 khi > 900 phút)
    được trao cho một mẫu chưa hề tồn tại.

    Hậu quả đo được: 241 cầu thủ nằm đúng ở 0.70 và giao diện gắn nhãn **"Tin cậy:
    Cao"** cho toàn bộ nhóm ứng viên đội trưởng — ngay bên cạnh tấm banner của
    chính hệ thống ghi *"PRE-SEASON · 100% dự báo dựa trên prior · Confidence:
    Low"*. Hai con số cùng một trang nói ngược nhau, và người dùng đọc cái ở cạnh
    cầu thủ chứ không đọc banner.

    Bỏ trống tham số này = giữ hành vi cũ, để chỗ gọi lẻ (test, thăm dò) không gãy.
    """
    base = {"High": 0.8, "Medium": 0.6, "Low": 0.4}.get(minutes_conf, 0.5)
    preseason = team_matches_played == 0

    # Phần thưởng cỡ mẫu phải kiếm được trong mùa NÀY; hình phạt mẫu bé thì không:
    # một cầu thủ ít phút mùa trước vẫn thật sự là một ẩn số lớn hơn.
    if minutes_season > 900 and not preseason:
        base += 0.1
    elif minutes_season < 300:
        base -= 0.15
    if has_recent:
        base += 0.05

    if preseason:
        # Chưa quả bóng nào lăn thì không dự báo nào xứng đáng nhãn "Cao".
        base = min(base, PRESEASON_CONFIDENCE_CAP)
    return round(max(0.15, min(0.95, base)), 2)
