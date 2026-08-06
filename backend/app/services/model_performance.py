"""Model Performance — đo chất lượng dự báo bằng kết quả thật.

Mọi chỉ số ở đây cần **kết quả đã xảy ra**. Mùa 2026/27 chưa đá vòng nào (hạn vòng
1: 2026-08-21), nên hôm nay **không một ô nào có số** — và đó là câu trả lời đúng,
không phải lỗi. Mỗi chỉ số vì thế trả về `status` + `unlock`: điều kiện cụ thể để
nó có số, thay vì một dấu gạch ngang không giải thích gì.

Ba trạng thái, cố tình phân biệt:

  * `ok`             — có số, kèm `n` là cỡ mẫu.
  * `no_data`        — chỉ số này đo được, nhưng chưa đủ dữ liệu. `unlock` nói cần gì.
  * `not_applicable` — chỉ số này **không định nghĩa được** cho cột đó. Ví dụ Brier
                       P(start) cho baseline "FPL form": form là một con số điểm,
                       nó không phát ra xác suất ra sân nào để mà chấm. Gộp nó vào
                       `no_data` sẽ khiến người đọc chờ một con số không bao giờ tới.

Điều kiện nền: dự báo phải được **đóng băng trước deadline** (`ProjectionSnapshot`).
`player_projections` bị xoá và ghi lại mỗi lần chạy engine, nên nếu không chụp
trước thì sau vòng đấu không còn gì để so — xem docstring của bảng đó.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    Gameweek,
    Player,
    PlayerGameweekStat,
    PlayerProjection,
    ProjectionSnapshot,
)

# Chỉ chấm những cầu thủ mô hình thật sự dự báo là có ra sân. Gộp cả 500 cầu thủ
# xP≈0 vào sẽ làm MAE trông rất đẹp mà không nói gì về chất lượng: đoán "0 điểm"
# cho một hậu vệ dự bị hầu như luôn đúng.
MIN_XMINS_FOR_SCORING = 20.0

# Cỡ mẫu tối thiểu để công bố một chỉ số. Spearman trên 3 cầu thủ là số vô nghĩa.
MIN_SAMPLE = 30


@dataclass
class Metric:
    """Một ô trong bảng: hoặc có số, hoặc nói rõ vì sao chưa có."""

    label: str
    value: float | None = None
    n: int = 0
    status: str = "no_data"
    unlock: str = ""
    better: str = "high"        # high | low — hướng nào là tốt hơn
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "value": None if self.value is None else round(self.value, 4),
            "n": self.n,
            "status": self.status,
            "unlock": self.unlock,
            "better": self.better,
            "note": self.note,
        }


def _na(label: str, why: str, better: str = "high") -> Metric:
    return Metric(label=label, status="not_applicable", unlock=why, better=better)


# ------------------------------------------------------------ toán thống kê ----
def spearman(pred: list[float], actual: list[float]) -> float | None:
    """Tương quan hạng Spearman = Pearson trên hạng, có xử lý đồng hạng.

    Đồng hạng phải nhận hạng trung bình, không phải thứ tự xuất hiện: rất nhiều cầu
    thủ cùng 2 điểm, xử lý sai thì hệ số phụ thuộc vào thứ tự đọc từ database.
    """
    n = len(pred)
    if n < 3 or len(actual) != n:
        return None

    def ranks(xs: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: xs[i])
        out = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and xs[order[j + 1]] == xs[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                out[order[k]] = avg
            i = j + 1
        return out

    rp, ra = ranks(pred), ranks(actual)
    mp, ma = sum(rp) / n, sum(ra) / n
    num = sum((rp[i] - mp) * (ra[i] - ma) for i in range(n))
    dp = math.sqrt(sum((rp[i] - mp) ** 2 for i in range(n)))
    da = math.sqrt(sum((ra[i] - ma) ** 2 for i in range(n)))
    if dp == 0 or da == 0:
        return None
    return num / (dp * da)


def mae(pred: list[float], actual: list[float]) -> float | None:
    if not pred:
        return None
    return sum(abs(p - a) for p, a in zip(pred, actual)) / len(pred)


def rmse(pred: list[float], actual: list[float]) -> float | None:
    if not pred:
        return None
    return math.sqrt(sum((p - a) ** 2 for p, a in zip(pred, actual)) / len(pred))


def top_k_precision(pred: list[float], actual: list[float], k: int = 10) -> float | None:
    """Trong k người mô hình xếp cao nhất, bao nhiêu phần trăm thuộc top-k thật.

    Đồng điểm ở biên top-k thật được tính là thuộc top-k (nếu không, hai cầu thủ
    cùng điểm sẽ được đối xử khác nhau chỉ vì thứ tự sắp xếp).
    """
    n = len(pred)
    if n < k or len(actual) != n:
        return None
    pred_top = sorted(range(n), key=lambda i: -pred[i])[:k]
    cutoff = sorted(actual, reverse=True)[k - 1]
    hit = sum(1 for i in pred_top if actual[i] >= cutoff)
    return hit / k


def brier(probs: list[float], outcomes: list[bool]) -> float | None:
    """Brier score = trung bình (xác suất − kết quả)². Càng THẤP càng tốt."""
    if not probs:
        return None
    return sum((p - (1.0 if o else 0.0)) ** 2 for p, o in zip(probs, outcomes)) / len(probs)


def calibration_error(probs: list[float], outcomes: list[bool],
                      bins: int = 10) -> tuple[float | None, list[dict]]:
    """Sai số hiệu chuẩn kỳ vọng (ECE) + chi tiết từng khoảng.

    Chia xác suất dự báo thành các khoảng, so tần suất xảy ra THẬT trong mỗi khoảng
    với xác suất trung bình mà mô hình nói. Mô hình hiệu chuẩn tốt thì "nói 20%" sẽ
    xảy ra khoảng 20% số lần. ECE là bình quân gia quyền của độ lệch đó — càng
    THẤP càng tốt. Trả kèm từng khoảng vì một ECE gộp có thể che mất việc mô hình
    quá tự tin ở đầu trên và quá dè dặt ở đầu dưới.
    """
    if not probs:
        return None, []
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(bins)]
    for p, o in zip(probs, outcomes):
        idx = min(bins - 1, max(0, int(p * bins)))
        buckets[idx].append((p, o))

    total = len(probs)
    ece = 0.0
    detail = []
    for i, b in enumerate(buckets):
        if not b:
            continue
        avg_p = sum(x[0] for x in b) / len(b)
        freq = sum(1 for x in b if x[1]) / len(b)
        ece += (len(b) / total) * abs(avg_p - freq)
        detail.append({
            "bin": f"{i / bins:.0%}–{(i + 1) / bins:.0%}",
            "n": len(b),
            "predicted": round(avg_p, 4),
            "observed": round(freq, 4),
        })
    return ece, detail


# --------------------------------------------------------------- chụp ảnh ------
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def capture_snapshots(db: Session, gameweek: int | None = None) -> dict:
    """Đóng băng dự báo của vòng sắp tới, kèm baseline chụp cùng lúc.

    Gọi được nhiều lần: trước deadline thì cập nhật (dự báo mới nhất trước giờ
    chốt là bản đáng chấm), sau deadline thì **khoá** và không sửa nữa. Việc khoá
    chính là điều bảo đảm chống data leakage — một lần chạy sau khi biết đội hình
    ra sân không thể lặng lẽ sửa lại dự báo.
    """
    from app import scoring

    season = scoring.SEASON
    if gameweek is None:
        nxt = db.scalar(select(Gameweek).where(Gameweek.is_next.is_(True)))
        if nxt is None:
            nxt = db.scalars(
                select(Gameweek).where(Gameweek.finished.is_(False))
                .order_by(Gameweek.id)
            ).first()
        if nxt is None:
            return {"ok": False, "reason": "Không xác định được vòng sắp tới."}
        gameweek = nxt.id

    gw_row = db.get(Gameweek, gameweek)
    deadline = _aware(gw_row.deadline_time) if gw_row else None
    past_deadline = bool(deadline and _now() >= deadline)

    projs = db.scalars(
        select(PlayerProjection).where(PlayerProjection.gameweek == gameweek)
    ).all()
    if not projs:
        return {"ok": False, "reason": f"Chưa có dự báo cho GW{gameweek}."}

    players = {p.id: p for p in db.scalars(select(Player)).all()}
    existing = {
        s.player_id: s
        for s in db.scalars(
            select(ProjectionSnapshot).where(
                ProjectionSnapshot.season == season,
                ProjectionSnapshot.gameweek == gameweek,
            )
        ).all()
    }

    written = locked = skipped = 0
    for pr in projs:
        row = existing.get(pr.player_id)
        if row is not None and row.is_locked:
            skipped += 1
            continue
        if row is None:
            row = ProjectionSnapshot(
                season=season, gameweek=gameweek, player_id=pr.player_id
            )
            db.add(row)
        p = players.get(pr.player_id)
        row.captured_at = _now()
        row.deadline_time = deadline
        row.model_version = pr.model_version or settings.model_version
        row.xp = pr.xp
        row.p_start = pr.p_start
        row.p_haul = pr.p_haul
        row.xmins = pr.xmins
        row.baseline_form = float(p.form) if p and p.form is not None else None
        # baseline kèo chưa nối — xem `market_baseline_status()`
        row.is_locked = past_deadline
        written += 1
        if past_deadline:
            locked += 1

    db.flush()
    return {
        "ok": True,
        "season": season,
        "gameweek": gameweek,
        "written": written,
        "locked": locked,
        "skipped_already_locked": skipped,
        "deadline": deadline.isoformat() if deadline else None,
        "past_deadline": past_deadline,
    }


def fill_outcomes(db: Session, gameweek: int | None = None) -> dict:
    """Đổ kết quả thật vào snapshot, từ `player_gameweek_stats`.

    Chỉ đổ cho vòng đã `finished`: điểm giữa vòng còn đổi (bonus chốt muộn, VAR,
    điều chỉnh dữ liệu), chấm mô hình bằng số chưa chốt là tự tạo nhiễu.
    """
    from app import scoring

    season = scoring.SEASON
    q = select(Gameweek).where(Gameweek.finished.is_(True))
    if gameweek is not None:
        q = q.where(Gameweek.id == gameweek)
    finished_gws = [g.id for g in db.scalars(q).all()]
    if not finished_gws:
        return {"ok": False, "reason": "Chưa có vòng nào kết thúc."}

    filled = 0
    missing_stats = 0
    for gw in finished_gws:
        stats = {
            s.player_id: s
            for s in db.scalars(
                select(PlayerGameweekStat).where(PlayerGameweekStat.gameweek == gw)
            ).all()
        }
        if not stats:
            missing_stats += 1
            continue
        rows = db.scalars(
            select(ProjectionSnapshot).where(
                ProjectionSnapshot.season == season,
                ProjectionSnapshot.gameweek == gw,
            )
        ).all()
        for r in rows:
            s = stats.get(r.player_id)
            if s is None:
                # không có dòng thống kê = không nằm trong đội hình đăng ký
                r.actual_points = 0
                r.actual_minutes = 0
                r.actual_started = False
            else:
                r.actual_points = int(s.total_points or 0)
                r.actual_minutes = int(s.minutes or 0)
                r.actual_started = bool((s.starts or 0) > 0)
            r.outcome_filled_at = _now()
            filled += 1

    db.flush()
    return {
        "ok": True,
        "gameweeks": finished_gws,
        "filled": filled,
        "gameweeks_without_stats": missing_stats,
        "note": (
            "Cần đồng bộ chi tiết (`player_gameweek_stats`) mới có kết quả từng "
            "vòng; đồng bộ nhanh không lấy bảng đó."
        ) if missing_stats else "",
    }


def market_baseline_status() -> dict:
    """Baseline kèo: nói rõ là CHƯA nối, chứ không để ô trống ngầm.

    Định nghĩa dự kiến: xP tính lại với sức mạnh đội lấy **hoàn toàn từ kèo**
    (`market_weight = 1.0`), giữ nguyên phần mô hình cầu thủ. Nhà cái không ra giá
    cho điểm FPL của từng cầu thủ nên không thể lấy trực tiếp — mọi baseline kèo ở
    cấp cầu thủ đều phải đi qua một mô hình phân bổ, và đây là cách trung thực nhất
    để tách riêng phần "sức mạnh đội đến từ đâu".

    Chưa nối vì cần một lượt chạy engine thứ hai cho mỗi vòng để đóng băng cùng
    lúc với dự báo chính; chụp lệch thời điểm là so gian lận.
    """
    return {
        "wired": False,
        "definition": (
            "xP với sức mạnh đội lấy hoàn toàn từ kèo (market_weight = 1.0), "
            "phần mô hình cầu thủ giữ nguyên."
        ),
        "why_not_yet": (
            "Cần chạy engine lần hai mỗi vòng để đóng băng baseline cùng thời điểm "
            "với dự báo chính. Chụp lệch thời điểm là so gian lận."
        ),
    }


# ------------------------------------------------------------- bảng chỉ số ----
@dataclass
class _Scored:
    """Tập mẫu đã có cả dự báo lẫn kết quả."""

    xp: list[float] = field(default_factory=list)
    form: list[float] = field(default_factory=list)
    actual: list[float] = field(default_factory=list)
    p_start: list[float] = field(default_factory=list)
    started: list[bool] = field(default_factory=list)
    p_haul: list[float] = field(default_factory=list)
    hauled: list[bool] = field(default_factory=list)
    n_form: int = 0
    gameweeks: set[int] = field(default_factory=set)


def _collect(db: Session) -> _Scored:
    from app import scoring

    rows = db.scalars(
        select(ProjectionSnapshot).where(
            ProjectionSnapshot.season == scoring.SEASON,
            ProjectionSnapshot.actual_points.is_not(None),
        )
    ).all()
    out = _Scored()
    for r in rows:
        if (r.xmins or 0) < MIN_XMINS_FOR_SCORING:
            continue
        actual = float(r.actual_points or 0)
        out.xp.append(r.xp)
        out.actual.append(actual)
        out.p_start.append(r.p_start)
        out.started.append(bool(r.actual_started))
        out.p_haul.append(r.p_haul)
        out.hauled.append(actual >= 10)
        out.gameweeks.add(r.gameweek)
        if r.baseline_form is not None:
            out.form.append(r.baseline_form)
            out.n_form += 1
        else:
            out.form.append(float("nan"))
    return out


def _need(n: int, what: str) -> str:
    return (
        f"Cần ít nhất {MIN_SAMPLE} quan sát đã có kết quả (hiện {n}). {what}"
    )


def _is_degenerate(xs: list[float]) -> bool:
    """Dự báo không có phương sai thì không phải một dự báo.

    FPL đặt lại `form` về 0 cho MỌI cầu thủ khi mùa mới mở (khác với tổng cả-mùa,
    vẫn giữ số của mùa trước — một bất đối xứng dễ sập bẫy). Trước vòng 1 cột
    baseline form vì thế là hằng số 0: Spearman không định nghĩa được, còn MAE/RMSE
    vẫn ra số nhưng chúng chỉ đang đo điểm trung bình của giải, không đo baseline
    nào cả. In những con số đó ra là mời người đọc so mô hình với một cột rỗng.
    """
    if len(xs) < 2:
        return True
    first = xs[0]
    return all(x == first for x in xs)


def player_metrics(db: Session) -> dict:
    """Sáu chỉ số dự báo cầu thủ × ba cột (mô hình / form FPL / kèo)."""
    s = _collect(db)
    n = len(s.xp)
    ready = n >= MIN_SAMPLE
    unlock = _need(n, "Snapshot được chấm sau khi vòng đấu kết thúc.")

    # cặp (dự báo, thực tế) chỉ gồm những dòng có baseline form
    form_pairs = [
        (f, a) for f, a in zip(s.form, s.actual) if not math.isnan(f)
    ]
    fp = [x[0] for x in form_pairs]
    fa = [x[1] for x in form_pairs]
    form_degenerate = _is_degenerate(fp) if fp else True
    form_ready = len(fp) >= MIN_SAMPLE and not form_degenerate
    if form_degenerate and len(fp) >= MIN_SAMPLE:
        form_unlock = (
            "Cột này chưa có thông tin: FPL đặt lại `form` về 0 cho MỌI cầu thủ khi "
            "mùa mới mở, nên trước vòng 1 nó là hằng số. MAE/RMSE vẫn ra số nhưng "
            "chúng chỉ đo điểm trung bình của giải, không đo baseline nào — nên để "
            "trống. Cột sẽ có số sau vài vòng, khi `form` bắt đầu phân hoá."
        )
    else:
        form_unlock = _need(
            len(fp), "Baseline form được đóng băng cùng lúc với dự báo."
        )

    mk = market_baseline_status()
    mk_unlock = mk["why_not_yet"]

    def model(label: str, fn, better: str = "high", note: str = "") -> Metric:
        if not ready:
            return Metric(label=label, status="no_data", unlock=unlock, better=better,
                          note=note)
        v = fn()
        if v is None:
            return Metric(label=label, status="no_data", unlock=unlock, better=better,
                          note=note)
        return Metric(label=label, value=v, n=n, status="ok", better=better, note=note)

    def base_form(label: str, fn, better: str = "high") -> Metric:
        if not form_ready:
            return Metric(label=label, status="no_data", unlock=form_unlock,
                          better=better)
        v = fn()
        if v is None:
            return Metric(label=label, status="no_data", unlock=form_unlock,
                          better=better)
        return Metric(label=label, value=v, n=len(fp), status="ok", better=better)

    def base_market(label: str, better: str = "high") -> Metric:
        return Metric(label=label, status="no_data", unlock=mk_unlock, better=better)

    ece, ece_detail = (
        calibration_error(s.p_haul, s.hauled) if ready else (None, [])
    )

    rows = [
        {
            "metric": "Spearman rank correlation",
            "explain": (
                "Tương quan hạng giữa xP và điểm thật. Đo khả năng XẾP ĐÚNG THỨ TỰ "
                "cầu thủ — thứ quyết định chọn ai, chứ không phải đoán đúng con số."
            ),
            "model": model("Spearman", lambda: spearman(s.xp, s.actual)).as_dict(),
            "baseline_form": base_form("Spearman", lambda: spearman(fp, fa)).as_dict(),
            "baseline_market": base_market("Spearman").as_dict(),
        },
        {
            "metric": "MAE xP",
            "explain": "Sai số tuyệt đối trung bình giữa xP và điểm thật, đơn vị điểm.",
            "model": model("MAE", lambda: mae(s.xp, s.actual), "low").as_dict(),
            "baseline_form": base_form("MAE", lambda: mae(fp, fa), "low").as_dict(),
            "baseline_market": base_market("MAE", "low").as_dict(),
        },
        {
            "metric": "RMSE",
            "explain": (
                "Căn bậc hai của bình phương sai số. Trừng phạt sai lớn nặng hơn MAE, "
                "nên nó bắt được những lần bỏ sót cầu thủ bùng nổ."
            ),
            "model": model("RMSE", lambda: rmse(s.xp, s.actual), "low").as_dict(),
            "baseline_form": base_form("RMSE", lambda: rmse(fp, fa), "low").as_dict(),
            "baseline_market": base_market("RMSE", "low").as_dict(),
        },
        {
            "metric": "Top-10 precision",
            "explain": (
                "Trong 10 cầu thủ mô hình xếp cao nhất, bao nhiêu phần trăm thật sự "
                "thuộc top 10 điểm của vòng đó."
            ),
            "model": model(
                "Top-10", lambda: top_k_precision(s.xp, s.actual, 10)
            ).as_dict(),
            "baseline_form": base_form(
                "Top-10", lambda: top_k_precision(fp, fa, 10)
            ).as_dict(),
            "baseline_market": base_market("Top-10").as_dict(),
        },
        {
            "metric": "Brier score P(start)",
            "explain": (
                "Chấm xác suất đá chính: trung bình (xác suất − kết quả)². Càng thấp "
                "càng tốt; 0.25 là mức của việc luôn đoán 50%."
            ),
            "model": model(
                "Brier", lambda: brier(s.p_start, s.started), "low"
            ).as_dict(),
            "baseline_form": _na(
                "Brier",
                "Chỉ số `form` của FPL là một con số điểm, nó không phát ra xác suất "
                "đá chính nào để chấm. Không phải chưa có dữ liệu — không định nghĩa được.",
                "low",
            ).as_dict(),
            "baseline_market": _na(
                "Brier",
                "Kèo ra giá cho kết quả trận, không ra giá cho việc một cầu thủ có đá "
                "chính hay không.",
                "low",
            ).as_dict(),
        },
        {
            "metric": "Calibration P(10+)",
            "explain": (
                "Sai số hiệu chuẩn (ECE) của xác suất đạt 10+ điểm: khi mô hình nói "
                "20%, chuyện đó có xảy ra khoảng 20% số lần không."
            ),
            "model": (
                Metric(label="ECE", value=ece, n=n, status="ok", better="low").as_dict()
                if ready and ece is not None
                else Metric(label="ECE", status="no_data", unlock=unlock,
                            better="low").as_dict()
            ),
            "baseline_form": _na(
                "ECE",
                "`form` không phát ra xác suất đạt 10+ điểm.",
                "low",
            ).as_dict(),
            "baseline_market": _na(
                "ECE",
                "Kèo không ra giá cho mốc điểm FPL của từng cầu thủ.",
                "low",
            ).as_dict(),
        },
    ]

    return {
        "rows": rows,
        "sample": {
            "n_scored": n,
            "gameweeks_scored": sorted(s.gameweeks),
            "min_xmins": MIN_XMINS_FOR_SCORING,
            "min_sample": MIN_SAMPLE,
            "note": (
                f"Chỉ chấm cầu thủ có xMins ≥ {MIN_XMINS_FOR_SCORING}. Gộp cả những "
                f"người mô hình dự báo gần như không ra sân sẽ làm MAE trông rất đẹp "
                f"mà không nói gì về chất lượng: đoán 0 điểm cho hậu vệ dự bị hầu như "
                f"luôn đúng."
            ),
        },
        "calibration_bins": ece_detail,
        "baseline_market": market_baseline_status(),
    }


def decision_metrics(db: Session) -> dict:
    """Sáu chỉ số chất lượng QUYẾT ĐỊNH — cần khuyến nghị đã lưu + kết quả thật."""
    from app.models import OptimizationRun

    runs = db.scalars(select(OptimizationRun)).all()
    by_kind: dict[str, int] = {}
    for r in runs:
        by_kind[r.kind] = by_kind.get(r.kind, 0) + 1

    finished = db.scalars(
        select(Gameweek).where(Gameweek.finished.is_(True))
    ).all()
    n_finished = len(finished)

    def blocked(label: str, kind: str, what: str, better: str = "high") -> Metric:
        have = by_kind.get(kind, 0)
        if n_finished == 0:
            why = (
                f"Chưa vòng nào kết thúc nên chưa có kết quả để chấm. Ngoài ra cần "
                f"khuyến nghị `{kind}` đã lưu trước deadline (hiện {have})."
            )
        elif have == 0:
            why = (
                f"Chưa có khuyến nghị `{kind}` nào được lưu. {what} Mỗi lần bạn chạy "
                f"nó, hệ thống lưu vào `optimization_runs` và vòng sau chấm được."
            )
        else:
            why = (
                f"Có {have} khuyến nghị `{kind}` đã lưu nhưng chưa cái nào rơi vào "
                f"vòng đã kết thúc."
            )
        return Metric(label=label, status="no_data", unlock=why, better=better)

    rows = [
        {
            "metric": "Điểm transfer ròng sau hit",
            "explain": (
                "Tổng điểm mà cầu thủ vào ghi thêm so với cầu thủ ra, TRỪ 4 điểm mỗi "
                "hit. Số dương nghĩa là chuyển nhượng đã bù được phí."
            ),
            "result": blocked(
                "Điểm ròng", "next_gw",
                "Cần chạy tối ưu chuyển nhượng ở trang Đội của tôi.",
            ).as_dict(),
        },
        {
            "metric": "Tỷ lệ transfer tốt hơn roll",
            "explain": (
                "Bao nhiêu phần trăm khuyến nghị chuyển nhượng thật sự hơn việc GIỮ "
                "NGUYÊN đội và cất free transfer."
            ),
            "result": blocked(
                "Tỷ lệ", "next_gw",
                "Cần chạy tối ưu chuyển nhượng ở trang Đội của tôi.",
            ).as_dict(),
        },
        {
            "metric": "Captain top pick hit rate",
            "explain": (
                "Bao nhiêu phần trăm số vòng mà đội trưởng được đề xuất là người ghi "
                "điểm cao nhất trong đội."
            ),
            "result": Metric(
                label="Hit rate",
                status="no_data",
                unlock=(
                    "Khuyến nghị đội trưởng hiện KHÔNG được lưu lại — trang Đội trưởng "
                    "tính tại chỗ mỗi lần mở. Cần lưu lựa chọn trước deadline mới chấm "
                    "được; đây là việc còn thiếu ở phía hệ thống, không phải chờ dữ liệu."
                ),
            ).as_dict(),
        },
        {
            "metric": "Điểm Free Hit tăng thêm",
            "explain": (
                "Điểm đội Free Hit được đề xuất, trừ điểm đội thật của bạn ở đúng vòng đó."
            ),
            "result": blocked(
                "Điểm tăng thêm", "free_hit",
                "Cần chạy Free Hit Lab cho vòng bạn định dùng chip.",
            ).as_dict(),
        },
        {
            "metric": "Wildcard gain sau 5 vòng",
            "explain": (
                "Tổng điểm đội Wildcard được đề xuất trừ đội thật, tính trên 5 vòng "
                "sau khi dùng chip."
            ),
            "result": blocked(
                "Gain 5 vòng", "wildcard",
                "Cần chạy tối ưu Wildcard cho vòng bạn định dùng chip.",
            ).as_dict(),
        },
        {
            "metric": "Bench order points gained",
            "explain": (
                "Điểm thu được nhờ thứ tự ghế dự bị được đề xuất, so với thứ tự bạn "
                "đang đặt, khi có cầu thủ không ra sân và autosub kích hoạt."
            ),
            "result": Metric(
                label="Điểm thu được",
                status="no_data",
                unlock=(
                    "Cần lưu thứ tự ghế dự bị đề xuất trước deadline, và cần dữ liệu "
                    "autosub thật của vòng đó. Cả hai hiện đều chưa lưu."
                ),
            ).as_dict(),
        },
    ]
    return {
        "rows": rows,
        "runs_archived": by_kind,
        "gameweeks_finished": n_finished,
    }


def model_performance(db: Session) -> dict:
    """Toàn bộ trang Model Performance."""
    from app import scoring

    snapshots = db.scalar(
        select(ProjectionSnapshot).where(ProjectionSnapshot.season == scoring.SEASON)
    )
    n_snapshots = len(
        db.scalars(
            select(ProjectionSnapshot.id).where(
                ProjectionSnapshot.season == scoring.SEASON
            )
        ).all()
    )
    n_scored = len(
        db.scalars(
            select(ProjectionSnapshot.id).where(
                ProjectionSnapshot.season == scoring.SEASON,
                ProjectionSnapshot.actual_points.is_not(None),
            )
        ).all()
    )
    finished = db.scalars(select(Gameweek).where(Gameweek.finished.is_(True))).all()

    return {
        "season": scoring.SEASON,
        "model_version": settings.model_version,
        "state": {
            "snapshots": n_snapshots,
            "snapshots_scored": n_scored,
            "gameweeks_finished": len(finished),
            "archiving_active": snapshots is not None,
        },
        "player_forecasting": player_metrics(db),
        "decisions": decision_metrics(db),
        "how_it_works": [
            "Dự báo được ĐÓNG BĂNG trước deadline vào bảng `projection_snapshots`, "
            "rồi khoá lại khi deadline qua. Không có bước này thì các chỉ số ở đây "
            "vĩnh viễn không đo được: `player_projections` bị xoá và ghi lại mỗi lần "
            "chạy engine, nên dự báo cũ không còn tồn tại để so.",
            "Kết quả thật được đổ vào sau khi vòng đấu `finished` — không dùng điểm "
            "giữa vòng, vì bonus chốt muộn và dữ liệu còn được điều chỉnh.",
            f"Chỉ chấm cầu thủ có xMins ≥ {MIN_XMINS_FOR_SCORING}, và chỉ công bố chỉ "
            f"số khi có ít nhất {MIN_SAMPLE} quan sát.",
            "Ô ghi 'không áp dụng' là chỉ số KHÔNG định nghĩa được cho cột đó (vd "
            "Brier P(start) cho baseline form), khác với 'chưa có dữ liệu' — nếu gộp "
            "hai thứ, người đọc sẽ chờ một con số không bao giờ tới.",
        ],
    }
