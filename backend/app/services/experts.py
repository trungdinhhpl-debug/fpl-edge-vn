"""Expert signals with echo-chamber accounting (spec §4).

Counting posts is the failure mode this module exists to prevent. Eight accounts
repeating one manager quote is ONE piece of evidence that has been retweeted, not
eight experts agreeing — but a naive tally reports 8/8 and reads as certainty.

    8 bài đăng nhắc A
    → 3 nguồn độc lập (2 tài khoản dẫn lại cùng một phát biểu của HLV)
    → đồng thuận thực 61%, không phải 100%

Two rules make that number honest:

  1. **Collapse echoes first.** Signals sharing an `origin_ref` trace to the same
     primary statement, so the group gets one vote, weighted by its most
     reliable member — not one vote each.
  2. **Weight by domain, not by volume.** A lineup specialist's view on a lineup
     counts for more than a generalist's, and neither is worth more for being
     louder. Follower count is never an input.

On track record: accuracy comes only from `ExpertTrackRecord` — predictions this
project recorded and later scored. Sources are real, named people; publishing an
accuracy figure we never measured would attach an invented performance claim to
them. Below `MIN_SCORED_SAMPLE` resolved predictions a source reports
`accuracy: None` and the UI must say "chưa đủ dữ liệu" rather than show a number.
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ExpertSignal, ExpertSource, ExpertTrackRecord, Player,
)
from app.services.common import player_public, team_lookup

# Domains a source can be good at. Being right about injuries says nothing about
# being right on chip strategy, so expertise is tracked per domain, never as one
# global "expert score".
EXPERTISE_DOMAINS = {
    "injury": "Chấn thương",
    "lineup": "Đội hình ra sân",
    "chip_planning": "Kế hoạch chip",
    "captaincy": "Đội trưởng",
    "statistics": "Thống kê",
}

# Which domain each signal type is evidence about.
SIGNAL_DOMAIN = {
    "start": "lineup", "injury": "injury", "setpiece": "lineup",
    "penalty": "lineup", "captain": "captaincy", "freehit": "chip_planning",
    "buy": "statistics", "sell": "statistics", "hold": "statistics",
    "avoid": "statistics", "differential": "statistics",
}

# Signals that argue FOR owning/starting a player vs AGAINST. `hold` is neither,
# and is excluded from the consensus split rather than silently counted as a yes.
POSITIVE = {"start", "captain", "buy", "differential", "setpiece", "penalty"}
NEGATIVE = {"sell", "avoid", "injury"}

# Below this many RESOLVED predictions, a hit rate is noise dressed as a metric.
MIN_SCORED_SAMPLE = 10
# Weight multiplier when a source is opining inside / outside its declared domain.
IN_DOMAIN_BONUS = 1.0
OUT_OF_DOMAIN = 0.6


# ------------------------------------------------------------ track record ---
def track_records(db: Session) -> dict[int, dict]:
    """{source_id: {domain: {correct, resolved, pending, accuracy|None}}}."""
    rows = db.scalars(select(ExpertTrackRecord)).all()
    agg: dict[int, dict[str, dict]] = defaultdict(lambda: defaultdict(
        lambda: {"correct": 0, "resolved": 0, "pending": 0, "accuracy": None}
    ))
    for r in rows:
        cell = agg[r.source_id][r.domain]
        if r.correct is None:
            cell["pending"] += 1
            continue
        cell["resolved"] += 1
        cell["correct"] += 1 if r.correct else 0
    for by_domain in agg.values():
        for cell in by_domain.values():
            # A number only appears once it has been earned on a real sample.
            if cell["resolved"] >= MIN_SCORED_SAMPLE:
                cell["accuracy"] = round(cell["correct"] / cell["resolved"], 3)
    return {sid: dict(d) for sid, d in agg.items()}


def _domains(source: ExpertSource) -> list[str]:
    raw = (source.expertise or "").lower()
    return [d for d in EXPERTISE_DOMAINS if d in raw]


def source_weight(source: ExpertSource, domain: str, record: dict | None) -> float:
    """How much this source's opinion counts, in THIS domain.

    Reliability is a prior on the kind of outlet. A measured hit rate, when one
    exists, moves the weight; when it does not, the prior stands alone rather
    than being multiplied by an invented accuracy.
    """
    w = source.reliability or 0.5
    w *= IN_DOMAIN_BONUS if domain in _domains(source) else OUT_OF_DOMAIN
    cell = (record or {}).get(domain)
    if cell and cell["accuracy"] is not None:
        # centre on 0.5: measured 50% leaves the prior untouched
        w *= 0.5 + cell["accuracy"]
    return round(w, 4)


# ----------------------------------------------------------- echo analysis ---
def analyse_player(signals: list[dict]) -> dict:
    """Collapse echoes, then compute the consensus the evidence actually supports."""
    posts = len(signals)

    # Group by primary statement. A signal with no origin_ref is an original
    # take and stands as its own group.
    groups: dict[str, list[dict]] = defaultdict(list)
    for i, s in enumerate(signals):
        groups[s["origin_ref"] or f"__original__{i}"].append(s)

    independent = len(groups)
    echoed = posts - independent
    origins_with_echo = [k for k, v in groups.items()
                         if len(v) > 1 and not k.startswith("__original__")]

    # One vote per group, carrying its most heavily-weighted member.
    for_w = against_w = 0.0
    votes = []
    for key, members in groups.items():
        lead = max(members, key=lambda m: m["weight"])
        direction = ("for" if lead["signal_type"] in POSITIVE
                     else "against" if lead["signal_type"] in NEGATIVE else "neutral")
        if direction == "for":
            for_w += lead["weight"]
        elif direction == "against":
            against_w += lead["weight"]
        votes.append({
            "origin_ref": None if key.startswith("__original__") else key,
            "direction": direction,
            "weight": round(lead["weight"], 4),
            "n_posts": len(members),
            "sources": [m["source"] for m in members],
            "lead_source": lead["source"],
        })

    decided = for_w + against_w
    consensus = round(100 * for_w / decided, 1) if decided > 0 else None
    dissent = [v for v in votes if v["direction"] == "against"]

    return {
        "posts": posts,
        "independent_sources": independent,
        "echo_accounts": echoed,
        "echoed_origins": origins_with_echo,
        "consensus_pct": consensus,
        "naive_consensus_pct": (
            round(100 * sum(1 for s in signals if s["signal_type"] in POSITIVE)
                  / posts, 1) if posts else None
        ),
        "votes": votes,
        "dissent": dissent,
        "has_dissent": bool(dissent),
    }


# ------------------------------------------------------------------ public ---
def expert_consensus(db: Session, limit: int = 50) -> dict:
    teams = team_lookup(db)
    players = {p.id: p for p in db.scalars(select(Player)).all()}
    sources = {s.id: s for s in db.scalars(select(ExpertSource)).all()}
    records = track_records(db)
    signals = db.scalars(select(ExpertSignal)).all()

    by_player: dict[int, list[dict]] = defaultdict(list)
    for s in signals:
        if s.player_id is None or s.player_id not in players:
            continue
        src = sources.get(s.source_id)
        if not src:
            continue
        domain = SIGNAL_DOMAIN.get(s.signal_type, "statistics")
        by_player[s.player_id].append({
            "source": src.name,
            "source_id": src.id,
            "source_type": src.source_type,
            "signal_type": s.signal_type,
            "domain": domain,
            "domain_label": EXPERTISE_DOMAINS.get(domain, domain),
            "confidence": s.confidence,
            "summary": s.summary,
            "link": s.link,
            "origin_ref": s.origin_ref,
            "is_mock": s.is_mock,
            "weight": source_weight(src, domain, records.get(src.id)),
        })

    rows = []
    for pid, sigs in by_player.items():
        p = players[pid]
        analysis = analyse_player(sigs)
        rows.append({
            **player_public(p, teams.get(p.team_id)),
            "signals": sigs,
            **analysis,
        })
    # Most-discussed first, but independence breaks ties — a story carried by
    # three separate sources outranks one shouted by eight accounts.
    rows.sort(key=lambda r: (r["independent_sources"], r["posts"]), reverse=True)

    return {
        "players": rows[:limit],
        "sources": [_source_payload(s, records.get(s.id)) for s in sources.values()],
        "domains": [{"key": k, "label": v} for k, v in EXPERTISE_DOMAINS.items()],
        "min_scored_sample": MIN_SCORED_SAMPLE,
        "disclaimer": (
            "Đồng thuận được tính SAU khi gộp các tài khoản dẫn lại cùng một nguồn "
            "gốc — nhiều bài đăng về một phát biểu là MỘT bằng chứng, không phải "
            "nhiều. Độ chính xác chỉ hiện khi đã có đủ dự đoán được chấm điểm; "
            "không gán sẵn con số cho người thật."
        ),
    }


def _source_payload(s: ExpertSource, record: dict | None) -> dict:
    domains = _domains(s)
    per_domain = []
    for d in domains:
        cell = (record or {}).get(d) or {"correct": 0, "resolved": 0,
                                         "pending": 0, "accuracy": None}
        per_domain.append({
            "domain": d, "label": EXPERTISE_DOMAINS[d],
            "accuracy": cell["accuracy"],
            "resolved": cell["resolved"], "pending": cell["pending"],
            # the honest reason a number is missing, so the UI need not guess
            "status": ("đã đo" if cell["accuracy"] is not None
                       else f"chưa đủ dữ liệu ({cell['resolved']}/{MIN_SCORED_SAMPLE})"),
        })
    return {
        "id": s.id, "name": s.name, "type": s.source_type, "url": s.url,
        "reliability": s.reliability,
        "reliability_basis": "Tiên nghiệm theo LOẠI nguồn, không phải hiệu suất cá nhân.",
        "independence": s.independence,
        "expertise": per_domain,
        # Verified only when we hold evidence for it — never asserted from a seed.
        "verified_track_record": bool(s.verified_track_record),
        "fpl_rank_verified": None,
        "fpl_rank_note": ("FPL không công khai API xác thực thứ hạng của một tài "
                          "khoản bên thứ ba, nên mục này để trống thay vì chép lại "
                          "con số tự khai."),
    }
