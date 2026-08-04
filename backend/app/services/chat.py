"""Trợ lý hỏi–đáp bám dữ liệu (spec §17, §23 giai đoạn 3).

Nguyên tắc: KHÔNG sinh văn bản tự do. Mỗi câu trả lời được lắp từ chính số liệu
trong DB (xP, xMins, giá, rủi ro, lịch, tin chấn thương) nên không thể bịa.
Câu nào không đủ dữ liệu thì nói thẳng là chưa trả lời được.

Luồng: chuẩn hoá câu hỏi -> nhận diện ý định -> truy vấn dữ liệu -> lắp câu trả lời
kèm số liệu + nguồn + gợi ý câu hỏi tiếp theo.
"""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Player
from app.services.captains import captain_ranking
from app.services.common import (
    horizon_xp,
    next_gameweek,
    planning_start_gw,
    projections_for_gw,
    team_lookup,
)
from app.services.fixtures import fixture_ticker
from app.services.news import news_feed
from app.services.players import player_detail

POS_VI = {"GK": "thủ môn", "DEF": "hậu vệ", "MID": "tiền vệ", "FWD": "tiền đạo"}
POS_KEYS = {
    "GK": ["thu mon", "goalkeeper", "gk", "keeper"],
    "DEF": ["hau ve", "defender", "def", "hậu vệ"],
    "MID": ["tien ve", "midfielder", "mid"],
    "FWD": ["tien dao", "forward", "fwd", "striker", "tien đao"],
}


def strip_accents(text: str) -> str:
    """Bỏ dấu tiếng Việt để so khớp câu hỏi gõ có dấu lẫn không dấu.

    Lưu ý: 'đ/Đ' (U+0111/U+0110) KHÔNG tách được bằng NFD nên phải thay tay —
    thiếu bước này thì "đội trưởng" sẽ không khớp với từ khoá "doi truong".
    """
    text = (text or "").replace("đ", "d").replace("Đ", "D")
    nfkd = unicodedata.normalize("NFD", text)
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn").lower()


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", strip_accents(text)).strip()


def _has(q: str, *words: str) -> bool:
    return any(w in q for w in words)


# Từ tiếng Việt/tiếng Anh hay xuất hiện trong câu hỏi — không được coi là tên
# cầu thủ, tránh khớp nhầm (vd "cao", "the", "son").
_NAME_STOPWORDS = {
    "cau", "thu", "doi", "nen", "mua", "ban", "gia", "hay", "tot", "kem", "cao",
    "thap", "con", "lam", "the", "nao", "khong", "cho", "nay", "gio", "vong",
    "the", "and", "vs", "hoac", "voi", "cua", "toi", "minh", "gi", "sao", "moi",
    "best", "who", "should", "captain", "week", "team", "player",
}


# ------------------------------------------------------------ tìm cầu thủ ----
def find_players(db: Session, question: str, limit: int = 3) -> list[Player]:
    """Dò tên cầu thủ xuất hiện trong câu hỏi (bỏ dấu, khớp theo từ)."""
    q_tokens = set(re.findall(r"[a-z0-9']+", _norm(question)))
    if not q_tokens:
        return []

    # Khớp theo TỪNG PHẦN của tên, vì FPL hay viết tắt ("B.Fernandes"): hỏi
    # "Bruno Fernandes" phải ra đúng cầu thủ MUN chứ không phải người trùng họ.
    matches: list[tuple[int, int, str, Player]] = []
    for p in db.scalars(select(Player)).all():
        name_tokens = {
            t
            for t in re.findall(
                r"[a-z0-9']+", _norm(f"{p.first_name} {p.second_name} {p.web_name}")
            )
            if len(t) >= 3 and t not in _NAME_STOPWORDS
        }
        # tên hiển thị khớp nguyên vẹn (vd "Son", "Cash", "ARS-23") luôn được tính,
        # kể cả khi ngắn — đây là cách người dùng gõ tên chính xác nhất
        web = _norm(p.web_name)
        exact = bool(web) and re.search(rf"(^|\W){re.escape(web)}(\W|$)", _norm(question))

        hit = name_tokens & q_tokens
        if not hit and not exact:
            continue
        # nếu không khớp nguyên tên, cần ít nhất một phần tên đủ đặc trưng (>=4 ký tự)
        if not exact and not any(len(t) >= 4 for t in hit):
            continue
        if exact:
            hit = hit | {web}
        key = max(hit, key=len)  # gom những người trùng họ vào cùng nhóm
        matches.append((len(hit), min(p.minutes, 4000), key, p))

    # mỗi nhóm (họ) chỉ giữ người khớp nhiều phần tên nhất, rồi tới nhiều phút nhất
    best_per_key: dict[str, tuple[int, int, str, Player]] = {}
    for m in matches:
        cur = best_per_key.get(m[2])
        if cur is None or (m[0], m[1]) > (cur[0], cur[1]):
            best_per_key[m[2]] = m

    ranked = sorted(best_per_key.values(), key=lambda m: (-m[0], -m[1]))
    return [m[3] for m in ranked[:limit]]


def _pos_filter(q: str) -> str | None:
    for pos, keys in POS_KEYS.items():
        if _has(q, *keys):
            return pos
    return None


def _budget(q: str) -> float | None:
    """Bắt 'dưới 6 triệu', 'under 7.5', '<8m'."""
    m = re.search(r"(?:duoi|under|<|toi da|max)\s*£?\s*(\d+(?:[.,]\d+)?)", q)
    if m:
        return float(m.group(1).replace(",", "."))
    return None


# ----------------------------------------------------------- các câu trả lời --
def _fmt_money(price: float) -> str:
    return f"£{price:.1f}m"


def _answer_captain(db: Session) -> dict:
    gw = planning_start_gw(db)
    top = captain_ranking(db, gw, limit=5)["candidates"]
    if not top:
        return _no_data("chưa có dự báo cho vòng tới")
    lines = [f"**Đội trưởng nên chọn — vòng {gw}:**", ""]
    for i, c in enumerate(top, 1):
        lines.append(
            f"{i}. **{c['name']}** ({c['team']}) — captain xP **{c['captain_xp']:.1f}**, "
            f"ceiling {c['ceiling']:.0f}, P(≥20đ) {round(c['p_haul'] * 100)}%, "
            f"xMins {c['xmins']:.0f}′"
            + (f" · {', '.join(c['tags'])}" if c.get("tags") else "")
        )
    best = top[0]
    lines += [
        "",
        f"→ Lựa chọn có kỳ vọng cao nhất là **{best['name']}**. "
        f"Nếu muốn an toàn, ưu tiên người có xMins cao và rủi ro thấp; "
        f"muốn bứt phá thì nhìn cột ceiling.",
    ]
    return _ok("\n".join(lines), players=[c["id"] for c in top[:3]],
               suggestions=["Ai có ceiling cao nhất?", "Lịch thi đấu đội nào dễ nhất?"])


def _answer_player(db: Session, p: Player) -> dict:
    d = player_detail(db, p.id)
    if not d:
        return _no_data("không tìm thấy cầu thủ")
    pl, v, u = d["player"], d["verdict"], d["underlying"]
    nxt = d["horizon"][0] if d["horizon"] else None
    teams = team_lookup(db)

    lines = [
        f"**{pl['name']}** — {pl['team_name']} · {POS_VI.get(pl['position'], pl['position'])} · "
        f"{_fmt_money(pl['price'])} · sở hữu {pl['selected_by_percent']:.1f}%",
        "",
    ]
    if nxt:
        opp = teams.get(nxt["opponent"]) if isinstance(nxt["opponent"], int) else None
        lines += [
            f"- **xP vòng tới:** {nxt['xp']:.1f} (đối thủ {nxt.get('opponent') or '—'}"
            f"{', sân nhà' if nxt.get('was_home') else ', sân khách' if nxt.get('was_home') is not None else ''})",
            f"- **xMins:** {nxt['xmins']:.0f}′ · ceiling {nxt['ceiling']:.0f} · "
            f"P(haul ≥10đ) {round((nxt.get('p_haul') or 0) * 100)}%",
        ]
        if pl["position"] in ("GK", "DEF"):
            lines.append(f"- **Xác suất sạch lưới:** {round((nxt.get('clean_sheet_prob') or 0) * 100)}%")
    lines += [
        f"- **xP 5 vòng:** {v['xp_next5']:.1f}",
        f"- **Dữ liệu nền:** xG {u['expected_goals']:.1f} · xA {u['expected_assists']:.1f} · "
        f"{u['goals_scored']} bàn / {u['assists']} kiến tạo",
    ]
    if u.get("penalties_order") == 1:
        lines.append("- **Đá penalty số 1** ✅")
    if abs(u.get("xg_overperformance", 0)) > 1.5:
        lines.append(
            f"- ⚠️ Đang ghi {'vượt' if u['xg_overperformance'] > 0 else 'kém'} xG "
            f"{abs(u['xg_overperformance']):.1f} — có thể hồi quy về mức trung bình"
        )
    if pl.get("news"):
        lines.append(f"- ⚠️ **Tin:** {pl['news']}")

    lines += ["", f"**Kết luận: {v['label']}**"] + [f"- {r}" for r in v["reasons"]]
    return _ok("\n".join(lines), players=[p.id],
               suggestions=[f"So sánh {p.web_name} và Salah", "Ai nên làm đội trưởng?"])


def _answer_compare(db: Session, players: list[Player]) -> dict:
    gw = planning_start_gw(db)
    projs = projections_for_gw(db, gw)
    gws = list(range(gw, gw + 5))
    hx = horizon_xp(db, gws)
    teams = team_lookup(db)

    lines = [f"**So sánh (vòng {gw}, và tổng 5 vòng):**", ""]
    rows = []
    for p in players:
        pr = projs.get(p.id)
        rows.append((p, pr, hx.get(p.id, 0.0)))
    for p, pr, xp5 in rows:
        t = teams.get(p.team_id)
        lines.append(
            f"- **{p.web_name}** ({t.short_name if t else '?'}, {_fmt_money(p.now_cost / 10)}): "
            f"xP {pr.xp:.1f} · xP5 **{xp5:.1f}** · xMins {pr.xmins:.0f}′ · "
            f"ceiling {pr.mc_ceiling:.0f} · rủi ro {pr.overall_risk}"
            if pr else f"- **{p.web_name}**: chưa có dự báo"
        )
    ordered = sorted(rows, key=lambda r: -r[2])
    best = ordered[0]
    runner_up = ordered[1] if len(ordered) > 1 else None
    diff = best[2] - runner_up[2] if runner_up else 0.0
    verdict = (
        f"→ Theo dữ liệu, **{best[0].web_name}** nhỉnh hơn "
        f"**{runner_up[0].web_name}** {diff:.1f} điểm xP trong 5 vòng."
        if runner_up
        else f"→ **{best[0].web_name}** có xP 5 vòng {best[2]:.1f}."
    )
    if runner_up and diff < 2:
        verdict += (
            " Chênh lệch nhỏ — chưa đáng để tốn một lượt chuyển nhượng, "
            "càng không đáng nếu phải chịu -4 điểm."
        )
    elif runner_up:
        verdict += " Chênh lệch đủ lớn để cân nhắc chuyển nhượng nếu cấu trúc đội cho phép."
    lines += ["", verdict]
    return _ok("\n".join(lines), players=[p.id for p in players],
               suggestions=["Ai nên làm đội trưởng?", "Cầu thủ nào đang chấn thương?"])


def _answer_best(db: Session, pos: str | None, budget: float | None, gw_count: int = 5) -> dict:
    gw = planning_start_gw(db)
    gws = list(range(gw, gw + gw_count))
    hx = horizon_xp(db, gws)
    projs = projections_for_gw(db, gw)
    teams = team_lookup(db)

    cands = []
    for p in db.scalars(select(Player)).all():
        if pos and {"GK": 1, "DEF": 2, "MID": 3, "FWD": 4}[pos] != p.element_type:
            continue
        if budget and p.now_cost / 10 > budget:
            continue
        pr = projs.get(p.id)
        if not pr or pr.xmins < 25:
            continue
        cands.append((hx.get(p.id, 0.0), p, pr))
    cands.sort(key=lambda x: -x[0])
    if not cands:
        return _no_data("không có cầu thủ nào khớp điều kiện")

    label = POS_VI.get(pos, "cầu thủ") if pos else "cầu thủ"
    head = f"**Top {label} theo xP {gw_count} vòng"
    head += f" (giá ≤ {_fmt_money(budget)})**:" if budget else "**:"
    lines = [head, ""]
    for i, (xp5, p, pr) in enumerate(cands[:6], 1):
        t = teams.get(p.team_id)
        lines.append(
            f"{i}. **{p.web_name}** ({t.short_name if t else '?'}, {_fmt_money(p.now_cost / 10)}) — "
            f"xP5 **{xp5:.1f}** · xP vòng tới {pr.xp:.1f} · xMins {pr.xmins:.0f}′ · "
            f"{xp5 / max(p.now_cost / 10, 0.1):.1f} điểm/triệu · rủi ro {pr.overall_risk}"
        )
    lines += ["", "→ Cột **điểm/triệu** cho biết mức đáng tiền; rủi ro cao thường đi kèm xMins thấp."]
    return _ok("\n".join(lines), players=[c[1].id for c in cands[:5]],
               suggestions=["Ai là differential tốt?", "Đội nào có lịch dễ?"])


def _answer_fixtures(db: Session) -> dict:
    t = fixture_ticker(db, n_gws=6)
    atk = sorted(t["rows"], key=lambda r: -(r["sum_proj_goals"] or 0))[:5]
    dfc = sorted(t["rows"], key=lambda r: -(r["sum_clean_sheet_prob"] or 0))[:5]
    lines = ["**Lịch thi đấu 6 vòng tới:**", "", "*Tấn công tốt nhất (tổng bàn kỳ vọng):*"]
    lines += [f"- {r['team_name']}: **{r['sum_proj_goals']:.1f}** bàn" for r in atk]
    lines += ["", "*Giữ sạch lưới tốt nhất:*"]
    lines += [f"- {r['team_name']}: **{r['sum_clean_sheet_prob']:.2f}** trận sạch lưới kỳ vọng" for r in dfc]
    lines += [
        "",
        "→ Ưu tiên cầu thủ tấn công của nhóm trên, và hậu vệ/thủ môn của nhóm dưới. "
        "Ô có dấu • trên trang Lịch thi đấu là số liệu dựa trên kèo nhà cái.",
    ]
    return _ok("\n".join(lines), suggestions=["Tiền đạo nào tốt nhất?", "Ai nên làm đội trưởng?"])


def _answer_differential(db: Session, max_own: float = 10.0) -> dict:
    gw = planning_start_gw(db)
    gws = list(range(gw, gw + 5))
    hx = horizon_xp(db, gws)
    projs = projections_for_gw(db, gw)
    teams = team_lookup(db)
    cands = []
    for p in db.scalars(select(Player)).all():
        if p.selected_by_percent > max_own:
            continue
        pr = projs.get(p.id)
        if not pr or pr.xmins < 55:
            continue
        cands.append((hx.get(p.id, 0.0), p, pr))
    cands.sort(key=lambda x: -x[0])
    if not cands:
        return _no_data("chưa tìm được differential phù hợp")
    lines = [
        f"**Differential (sở hữu dưới {max_own:.0f}%, xMins ≥ 55′):**",
        "",
    ]
    for i, (xp5, p, pr) in enumerate(cands[:6], 1):
        t = teams.get(p.team_id)
        lines.append(
            f"{i}. **{p.web_name}** ({t.short_name if t else '?'}, {_fmt_money(p.now_cost / 10)}) — "
            f"sở hữu {p.selected_by_percent:.1f}% · xP5 **{xp5:.1f}** · ceiling {pr.mc_ceiling:.0f}"
        )
    lines += [
        "",
        "→ Lưu ý: ít người sở hữu **không** tự động là tốt. Chỉ chọn khi xP đủ cao — "
        "danh sách trên đã lọc bỏ người có xP thấp hoặc ít phút thi đấu.",
    ]
    return _ok("\n".join(lines), players=[c[1].id for c in cands[:5]],
               suggestions=["Cầu thủ nào an toàn nhất?", "Ai đang chấn thương?"])


def _answer_news(db: Session) -> dict:
    rows = [n for n in news_feed(db, limit=40) if n["impact"] in ("Critical", "High")][:8]
    if not rows:
        return _ok("Hiện không có cảnh báo chấn thương/treo giò mức cao.",
                   suggestions=["Ai nên làm đội trưởng?"])
    lines = ["**Cảnh báo ra sân đáng chú ý:**", ""]
    for n in rows:
        chance = f" · khả năng ra sân {n['chance_of_playing']}%" if n.get("chance_of_playing") is not None else ""
        lines.append(f"- **{n['name']}** ({n['team']}) — {n['impact']}{chance}: {n.get('news') or '—'}")
    lines += ["", "→ Nguồn: FPL chính thức. Tin chưa xác nhận sẽ được đánh dấu trên trang Tin tức."]
    return _ok("\n".join(lines), suggestions=["Tiền đạo nào tốt nhất?"])


def _answer_gameweek(db: Session) -> dict:
    gw = next_gameweek(db)
    if not gw:
        return _no_data("chưa xác định được vòng đấu")
    dl = gw.deadline_time.isoformat() if gw.deadline_time else None
    txt = f"**Vòng đấu tiếp theo: {gw.name}**"
    if dl:
        txt += f"\n\n- Hạn chót: `{dl}` (hiển thị giờ Việt Nam trên trang chủ)"
    txt += "\n- Sau hạn chót, FPL mới công khai đội hình nên trang **Đội của tôi** sẽ tải được squad."
    return _ok(txt, suggestions=["Ai nên làm đội trưởng?", "Đội nào có lịch dễ?"])


# Cách người Việt hay viết tên đội, khác với tên FPL dùng
TEAM_ALIASES = {
    "tottenham": "TOT", "tottenham hotspur": "TOT",
    "manchester city": "MCI", "man city": "MCI",
    "manchester united": "MUN", "man united": "MUN", "manu": "MUN", "quy do": "MUN",
    "nottingham forest": "NFO", "nottm forest": "NFO",
    "newcastle united": "NEW", "brighton hove albion": "BHA",
    "coventry city": "COV", "hull city": "HUL", "ipswich town": "IPS",
    "leeds united": "LEE", "phao thu": "ARS", "the kop": "LIV",
}


def _find_team(db: Session, question: str) -> int | None:
    """Dò tên đội trong câu hỏi.

    Mã viết tắt CHỈ được nhận khi viết HOA trong câu gốc: nhiều mã trùng với từ
    tiếng Việt thông dụng sau khi bỏ dấu ("tốt" -> tot = Tottenham,
    "chê" -> che = Chelsea), sẽ cướp nhầm ý định của câu hỏi.
    """
    q = _norm(question)
    upper_tokens = set(re.findall(r"[A-Z]{2,4}", question))
    by_short = {t.short_name.upper(): tid for tid, t in team_lookup(db).items()}

    best, best_len = None, 0

    # 1) tên gọi quen thuộc
    for alias, short in TEAM_ALIASES.items():
        if re.search(rf"(^|\W){re.escape(alias)}(\W|$)", q) and len(alias) > best_len:
            tid = by_short.get(short)
            if tid:
                best, best_len = tid, len(alias)

    # 2) tên đầy đủ theo FPL
    for tid, t in team_lookup(db).items():
        n = _norm(t.name)
        if n and re.search(rf"(^|\W){re.escape(n)}(\W|$)", q) and len(n) > best_len:
            best, best_len = tid, len(n)

    # 3) mã viết tắt — chỉ khi VIẾT HOA
    if best is None:
        for code in upper_tokens:
            if code in by_short:
                return by_short[code]
    return best


def _answer_team_players(db: Session, team_id: int) -> dict:
    """Cầu thủ tốt nhất của một đội cụ thể."""
    gw = planning_start_gw(db)
    gws = list(range(gw, gw + 5))
    hx = horizon_xp(db, gws)
    projs = projections_for_gw(db, gw)
    teams = team_lookup(db)
    team = teams.get(team_id)

    squad = [p for p in db.scalars(select(Player)).all() if p.team_id == team_id]
    rows = []
    for p in squad:
        pr = projs.get(p.id)
        if not pr or pr.xmins < 20:
            continue
        rows.append((hx.get(p.id, 0.0), p, pr))
    rows.sort(key=lambda r: -r[0])
    if not rows:
        return _no_data(f"chưa có dự báo cho {team.name if team else 'đội này'}")

    lines = [f"**Cầu thủ đáng chú ý của {team.name}** (xP 5 vòng):", ""]
    for i, (xp5, p, pr) in enumerate(rows[:7], 1):
        pen = " · đá pen" if p.penalties_order == 1 else ""
        lines.append(
            f"{i}. **{p.web_name}** ({POS_VI.get(_pos_name(p.element_type), '')}, "
            f"{_fmt_money(p.now_cost / 10)}) — xP5 **{xp5:.1f}** · xP vòng tới {pr.xp:.1f} · "
            f"xMins {pr.xmins:.0f}′ · rủi ro {pr.overall_risk}{pen}"
        )
    return _ok("\n".join(lines), players=[r[1].id for r in rows[:5]],
               suggestions=[f"Lịch {team.short_name} thế nào?", "Ai nên làm đội trưởng?"])


def _answer_team_fixtures(db: Session, team_id: int) -> dict:
    """Lịch thi đấu sắp tới của một đội."""
    t = fixture_ticker(db, n_gws=6)
    teams = team_lookup(db)
    row = next((r for r in t["rows"] if r["team_id"] == team_id), None)
    if not row:
        return _no_data("không tìm thấy lịch của đội này")
    lines = [f"**Lịch 6 vòng tới của {row['team_name']}:**", ""]
    for gw in t["gameweeks"]:
        cells = row["cells"].get(str(gw), [])
        if not cells:
            lines.append(f"- GW{gw}: **nghỉ (blank)**")
            continue
        for c in cells:
            venue = "sân nhà" if c["is_home"] else "sân khách"
            mk = " · theo kèo nhà cái" if c.get("has_market") else ""
            lines.append(
                f"- GW{gw}: gặp **{c['opponent']}** ({venue}) — ghi {c['proj_goals_for']:.1f} / "
                f"thủng {c['proj_goals_against']:.1f} · sạch lưới {round(c['clean_sheet_prob'] * 100)}%{mk}"
            )
    lines += [
        "",
        f"→ Tổng 6 vòng: **{row['sum_proj_goals']:.1f}** bàn kỳ vọng, "
        f"**{row['sum_clean_sheet_prob']:.2f}** trận sạch lưới kỳ vọng.",
    ]
    return _ok("\n".join(lines),
               suggestions=[f"Cầu thủ {teams[team_id].short_name} nào tốt nhất?",
                            "Đội nào có lịch dễ?"])


def _answer_penalties(db: Session) -> dict:
    """Danh sách người đá phạt đền / phạt góc."""
    gw = planning_start_gw(db)
    projs = projections_for_gw(db, gw)
    teams = team_lookup(db)
    takers = [
        p for p in db.scalars(select(Player)).all()
        if p.penalties_order == 1 and p.status == "a"
    ]
    takers.sort(key=lambda p: -(projs[p.id].xp if p.id in projs else 0))
    if not takers:
        return _no_data("chưa có dữ liệu người đá penalty")
    lines = ["**Người đá phạt đền số 1 của các đội:**", ""]
    for p in takers[:12]:
        pr = projs.get(p.id)
        t = teams.get(p.team_id)
        extra = f" · xP {pr.xp:.1f}" if pr else ""
        sp = " · đá phạt góc" if p.corners_and_indirect_freekicks_order == 1 else ""
        lines.append(
            f"- **{p.web_name}** ({t.short_name if t else '?'}, "
            f"{_fmt_money(p.now_cost / 10)}){extra}{sp}"
        )
    lines += ["", "→ Đá penalty là một trong những yếu tố ổn định nhất để tăng điểm kỳ vọng."]
    return _ok("\n".join(lines), players=[p.id for p in takers[:5]],
               suggestions=["Ai nên làm đội trưởng?", "Tiền đạo nào tốt nhất?"])


def _answer_value(db: Session) -> dict:
    """Cầu thủ đáng tiền nhất (điểm trên mỗi triệu)."""
    gw = planning_start_gw(db)
    gws = list(range(gw, gw + 5))
    hx = horizon_xp(db, gws)
    projs = projections_for_gw(db, gw)
    teams = team_lookup(db)
    rows = []
    for p in db.scalars(select(Player)).all():
        pr = projs.get(p.id)
        if not pr or pr.xmins < 45:
            continue
        xp5 = hx.get(p.id, 0.0)
        if xp5 <= 0:
            continue
        rows.append((xp5 / (p.now_cost / 10), xp5, p, pr))
    rows.sort(key=lambda r: -r[0])
    if not rows:
        return _no_data("chưa đủ dự báo")
    lines = ["**Đáng tiền nhất — điểm kỳ vọng trên mỗi triệu (5 vòng):**", ""]
    for i, (val, xp5, p, pr) in enumerate(rows[:8], 1):
        t = teams.get(p.team_id)
        lines.append(
            f"{i}. **{p.web_name}** ({t.short_name if t else '?'}, "
            f"{POS_VI.get(_pos_name(p.element_type), '')}, {_fmt_money(p.now_cost / 10)}) — "
            f"**{val:.1f}** điểm/triệu · xP5 {xp5:.1f} · xMins {pr.xmins:.0f}′"
        )
    lines += [
        "",
        "→ Cầu thủ rẻ mà hiệu quả giúp dồn tiền cho vị trí premium. Đã lọc bỏ người "
        "có xMins dưới 45′ để tránh 'rẻ vì không được đá'.",
    ]
    return _ok("\n".join(lines), players=[r[2].id for r in rows[:5]],
               suggestions=["Ai là differential tốt?", "Hậu vệ nào giữ sạch lưới tốt?"])


def _answer_ceiling(db: Session) -> dict:
    """Cầu thủ có trần điểm cao / dễ ăn đậm."""
    gw = planning_start_gw(db)
    projs = projections_for_gw(db, gw)
    teams = team_lookup(db)
    players = {p.id: p for p in db.scalars(select(Player)).all()}
    rows = [
        (pr.mc_ceiling, pr, players[pid])
        for pid, pr in projs.items()
        if pid in players and pr.xmins >= 45
    ]
    rows.sort(key=lambda r: -r[0])
    if not rows:
        return _no_data("chưa đủ dự báo")
    lines = [f"**Trần điểm cao nhất vòng {gw}** (mô phỏng Monte Carlo):", ""]
    for i, (ceil, pr, p) in enumerate(rows[:8], 1):
        t = teams.get(p.team_id)
        lines.append(
            f"{i}. **{p.web_name}** ({t.short_name if t else '?'}, "
            f"{_fmt_money(p.now_cost / 10)}) — ceiling **{ceil:.0f}** · "
            f"P(≥10đ) {round(pr.p_haul * 100)}% · xP {pr.xp:.1f} · "
            f"P(tịt ngòi) {round(pr.p_blank * 100)}%"
        )
    lines += [
        "",
        "→ Ceiling cao hợp với người cần bứt phá thứ hạng, nhưng thường đi kèm "
        "xác suất tịt ngòi cao hơn. Muốn an toàn thì nhìn xP và xMins.",
    ]
    return _ok("\n".join(lines), players=[r[2].id for r in rows[:5]],
               suggestions=["Ai nên làm đội trưởng?", "Cầu thủ nào an toàn nhất?"])


def _answer_safe(db: Session) -> dict:
    """Lựa chọn an toàn: xMins cao, rủi ro thấp."""
    gw = planning_start_gw(db)
    gws = list(range(gw, gw + 5))
    hx = horizon_xp(db, gws)
    projs = projections_for_gw(db, gw)
    teams = team_lookup(db)
    players = {p.id: p for p in db.scalars(select(Player)).all()}
    rows = [
        (hx.get(pid, 0.0), pr, players[pid])
        for pid, pr in projs.items()
        if pid in players
        and pr.overall_risk == "Low"
        and pr.xmins >= 70
        and players[pid].status == "a"
    ]
    rows.sort(key=lambda r: -r[0])
    if not rows:
        return _no_data("chưa có cầu thủ nào đạt mức rủi ro thấp")
    lines = ["**Lựa chọn an toàn** (rủi ro Thấp, xMins ≥ 70′):", ""]
    for i, (xp5, pr, p) in enumerate(rows[:8], 1):
        t = teams.get(p.team_id)
        lines.append(
            f"{i}. **{p.web_name}** ({t.short_name if t else '?'}, "
            f"{POS_VI.get(_pos_name(p.element_type), '')}, {_fmt_money(p.now_cost / 10)}) — "
            f"xP5 **{xp5:.1f}** · xMins {pr.xmins:.0f}′ · tin cậy {round(pr.confidence * 100)}%"
        )
    return _ok("\n".join(lines), players=[r[2].id for r in rows[:5]],
               suggestions=["Ai có ceiling cao nhất?", "Ai có nguy cơ bị xoay tua?"])


def _answer_rotation_risk(db: Session) -> dict:
    """Cầu thủ nổi tiếng nhưng có nguy cơ xoay tua / ít phút."""
    gw = planning_start_gw(db)
    projs = projections_for_gw(db, gw)
    teams = team_lookup(db)
    players = {p.id: p for p in db.scalars(select(Player)).all()}
    rows = [
        (players[pid].selected_by_percent, pr, players[pid])
        for pid, pr in projs.items()
        if pid in players
        and players[pid].selected_by_percent >= 5
        and (pr.minutes_risk in ("High", "Very High") or pr.xmins < 55)
    ]
    rows.sort(key=lambda r: -r[0])
    if not rows:
        return _ok("Hiện không có cầu thủ phổ biến nào bị gắn cờ rủi ro phút thi đấu cao.",
                   suggestions=["Lựa chọn an toàn là ai?"])
    lines = ["**Cảnh báo rủi ro phút thi đấu** (nhiều người sở hữu nhưng xMins thấp):", ""]
    for own, pr, p in rows[:8]:
        t = teams.get(p.team_id)
        lines.append(
            f"- **{p.web_name}** ({t.short_name if t else '?'}) — sở hữu {own:.1f}% · "
            f"xMins {pr.xmins:.0f}′ · rủi ro phút {pr.minutes_risk} · xP {pr.xp:.1f}"
        )
    lines += ["", "→ Sở hữu cao không đảm bảo cầu thủ sẽ ra sân. Kiểm tra tin đội hình trước deadline."]
    return _ok("\n".join(lines), players=[r[2].id for r in rows[:5]],
               suggestions=["Lựa chọn an toàn là ai?", "Cầu thủ nào đang chấn thương?"])


def _answer_ownership(db: Session) -> dict:
    """Cầu thủ được sở hữu nhiều nhất (template)."""
    gw = planning_start_gw(db)
    projs = projections_for_gw(db, gw)
    teams = team_lookup(db)
    ps = sorted(db.scalars(select(Player)).all(), key=lambda p: -p.selected_by_percent)
    lines = ["**Được sở hữu nhiều nhất (đội hình template):**", ""]
    for i, p in enumerate(ps[:8], 1):
        pr = projs.get(p.id)
        t = teams.get(p.team_id)
        lines.append(
            f"{i}. **{p.web_name}** ({t.short_name if t else '?'}, {_fmt_money(p.now_cost / 10)}) — "
            f"**{p.selected_by_percent:.1f}%**" + (f" · xP {pr.xp:.1f}" if pr else "")
        )
    lines += [
        "",
        "→ Sở hữu cao **không phải** bằng chứng cầu thủ tốt. Nó chỉ cho biết mức an toàn "
        "về thứ hạng: bỏ qua một cầu thủ template là chấp nhận rủi ro tụt hạng nếu họ ghi điểm.",
    ]
    return _ok("\n".join(lines), players=[p.id for p in ps[:5]],
               suggestions=["Ai là differential tốt?", "Ai đang được mua nhiều nhất?"])


def _answer_trending(db: Session) -> dict:
    """Cầu thủ đang được mua/bán nhiều nhất tuần này."""
    gw = planning_start_gw(db)
    projs = projections_for_gw(db, gw)
    teams = team_lookup(db)
    ps = db.scalars(select(Player)).all()
    ins = sorted(ps, key=lambda p: -p.transfers_in_event)[:6]
    outs = sorted(ps, key=lambda p: -p.transfers_out_event)[:6]

    def fmt(p):
        pr = projs.get(p.id)
        t = teams.get(p.team_id)
        return (f"- **{p.web_name}** ({t.short_name if t else '?'}) — "
                f"{p.transfers_in_event / 1000:.0f}k lượt" +
                (f" · xP {pr.xp:.1f}" if pr else ""))

    lines = ["**Được mua nhiều nhất tuần này:**", ""]
    lines += [fmt(p) for p in ins]
    lines += ["", "**Bị bán nhiều nhất:**", ""]
    lines += [
        f"- **{p.web_name}** ({teams[p.team_id].short_name}) — "
        f"{p.transfers_out_event / 1000:.0f}k lượt"
        for p in outs
    ]
    lines += [
        "",
        "→ Đây là dòng chuyển nhượng của đám đông, **không phải** khuyến nghị. "
        "Hãy đối chiếu với cột xP trước khi chạy theo.",
    ]
    return _ok("\n".join(lines), players=[p.id for p in ins[:5]],
               suggestions=["Cầu thủ nào đáng tiền nhất?", "Ai nên làm đội trưởng?"])


def _answer_clean_sheet(db: Session) -> dict:
    """Hậu vệ / thủ môn có xác suất giữ sạch lưới tốt nhất."""
    gw = planning_start_gw(db)
    projs = projections_for_gw(db, gw)
    teams = team_lookup(db)
    players = {p.id: p for p in db.scalars(select(Player)).all()}
    rows = [
        (pr.clean_sheet_prob, pr, players[pid])
        for pid, pr in projs.items()
        if pid in players and players[pid].element_type in (1, 2) and pr.xmins >= 60
    ]
    rows.sort(key=lambda r: (-r[0], -r[1].xp))
    if not rows:
        return _no_data("chưa đủ dự báo phòng ngự")
    lines = [f"**Cơ hội giữ sạch lưới tốt nhất vòng {gw}:**", ""]
    seen_team = set()
    shown = 0
    for cs, pr, p in rows:
        if p.team_id in seen_team and shown >= 4:
            continue
        seen_team.add(p.team_id)
        t = teams.get(p.team_id)
        lines.append(
            f"- **{p.web_name}** ({t.short_name if t else '?'}, "
            f"{POS_VI.get(_pos_name(p.element_type), '')}, {_fmt_money(p.now_cost / 10)}) — "
            f"sạch lưới **{round(cs * 100)}%** · xP {pr.xp:.1f} · xMins {pr.xmins:.0f}′"
        )
        shown += 1
        if shown >= 8:
            break
    return _ok("\n".join(lines), players=[r[2].id for r in rows[:5]],
               suggestions=["Đội nào có lịch dễ?", "Thủ môn nào tốt nhất?"])


def _answer_blank_double(db: Session) -> dict:
    """Blank / Double gameweek sắp tới."""
    from app.services.common import blank_double_gws

    start = planning_start_gw(db)
    bd = blank_double_gws(db, start, start + 8)
    teams = team_lookup(db)
    if not bd:
        return _ok(
            f"Từ vòng {start} đến {start + 8} **chưa ghi nhận** Blank hay Double Gameweek nào. "
            "Lịch có thể thay đổi khi các giải cúp xác định lịch đá lại — hệ thống sẽ tự cập nhật.",
            suggestions=["Đội nào có lịch dễ?", "Ai nên làm đội trưởng?"],
        )
    lines = ["**Blank & Double Gameweek sắp tới:**", ""]
    for gw, v in sorted(bd.items()):
        if v.get("double"):
            names = ", ".join(teams[t].short_name for t in v["double"] if t in teams)
            lines.append(f"- **GW{gw} — Double:** {names}")
        if v.get("blank"):
            names = ", ".join(teams[t].short_name for t in v["blank"] if t in teams)
            lines.append(f"- **GW{gw} — Blank (nghỉ):** {names}")
    lines += ["", "→ Double Gameweek là thời điểm hợp lý để cân nhắc Bench Boost / Triple Captain; "
                  "Blank Gameweek thường dùng Free Hit."]
    return _ok("\n".join(lines), suggestions=["Free Hit nên chọn ai?", "Đội nào có lịch dễ?"])


def _answer_optimal_xi(db: Session) -> dict:
    """Đội hình tối ưu trong ngân sách (dùng chính bộ tối ưu của Free Hit Lab)."""
    from app.services import team as team_svc

    try:
        res = team_svc.optimize_free_hit(db, budget=1000, mode="max_ep")
    except Exception:
        return _no_data("bộ tối ưu chưa chạy được lúc này")
    if not res.get("starting"):
        return _no_data("chưa dựng được đội hình")

    by_pos: dict[str, list[str]] = {}
    for s in res["starting"]:
        by_pos.setdefault(s["position"], []).append(
            f"{s['name']} ({s['team']} {_fmt_money(s['price'])}"
            + (", **C**" if s.get("is_captain") else "")
            + f", xP {s['xp']:.1f})"
        )
    lines = [
        f"**Đội hình tối ưu vòng {res['gameweek']}** — sơ đồ {res['formation']}, "
        f"tổng {_fmt_money(res['total_cost'])}, xP đội hình chính **{res['xi_xp']:.1f}**",
        "",
    ]
    for pos in ("GK", "DEF", "MID", "FWD"):
        if pos in by_pos:
            lines.append(f"- **{POS_VI[pos].title()}:** " + "; ".join(by_pos[pos]))
    bench = ", ".join(f"{b['name']} ({b['team']})" for b in res.get("bench", []))
    if bench:
        lines += ["", f"*Dự bị:* {bench}"]
    lines += [
        "",
        "→ Tối ưu bằng quy hoạch nguyên, tuân thủ đủ luật FPL (£100m, 2/5/5/3, tối đa 3 "
        "cầu thủ mỗi CLB). Vào **Free Hit Lab** để đổi ngân sách và chế độ (an toàn / mạo hiểm).",
    ]
    return _ok("\n".join(lines), players=[s["id"] for s in res["starting"][:5]],
               suggestions=["Ai nên làm đội trưởng?", "Cầu thủ nào đáng tiền nhất?"])


def _answer_rules(db: Session, question: str) -> dict:
    """Giải thích luật tính điểm mùa hiện tại (đọc từ cấu hình, không viết cứng)."""
    from app import scoring
    from app.scoring import RULES

    q = _norm(question)
    if _has(q, "defensive contribution", "defcon", "dong gop phong ngu"):
        return _ok(
            f"**Defensive Contribution** (luật mới mùa {scoring.SEASON}): cầu thủ được "
            f"**+{RULES.defcon_points} điểm** khi đạt ngưỡng hành động phòng ngự trong trận.\n\n"
            f"- Hậu vệ: **{RULES.defcon_threshold_def}** lần (phá bóng, cản phá, cắt bóng, tắc bóng)\n"
            f"- Tiền vệ & tiền đạo: **{RULES.defcon_threshold_att}** lần (tính thêm cả thu hồi bóng)\n\n"
            "Mô hình đã tính hạng mục này vào xP — xem cột phân rã ở trang chi tiết cầu thủ.",
            suggestions=["Luật tính điểm thế nào?", "Hậu vệ nào giữ sạch lưới tốt?"],
        )
    g = RULES.goal_points
    cs = RULES.clean_sheet_points
    return _ok(
        f"**Luật tính điểm FPL mùa {scoring.SEASON}:**\n\n"
        f"- Ra sân dưới 60′: **+{RULES.points_play_under_60}** · từ 60′ trở lên: **+{RULES.points_play_60_plus}**\n"
        f"- Ghi bàn: thủ môn **+{g[1]}**, hậu vệ **+{g[2]}**, tiền vệ **+{g[3]}**, tiền đạo **+{g[4]}**\n"
        f"- Kiến tạo: **+{RULES.assist_points}**\n"
        f"- Sạch lưới (cần 60′): thủ môn **+{cs[1]}**, hậu vệ **+{cs[2]}**, tiền vệ **+{cs[3]}**\n"
        f"- Thủ môn: cứ {RULES.saves_per_point} lần cứu thua **+1**, cản penalty **+{RULES.penalty_save_points}**\n"
        f"- Thủ môn/hậu vệ thủng lưới: cứ 2 bàn **{RULES.points_per_two_conceded}**\n"
        f"- Thẻ vàng **{RULES.yellow_card_points}** · thẻ đỏ **{RULES.red_card_points}** · "
        f"phản lưới **{RULES.own_goal_points}** · hỏng penalty **{RULES.penalty_miss_points}**\n"
        f"- Điểm thưởng: tối đa **+{RULES.max_bonus}** mỗi trận\n"
        f"- **Defensive Contribution: +{RULES.defcon_points}** (luật mới)\n\n"
        "Toàn bộ hạng mục trên đều được tính trong mô hình xP.",
        suggestions=["Defensive Contribution là gì?", "Ai nên làm đội trưởng?"],
    )


CHIP_VI = {
    "wildcard": "Wildcard",
    "freehit": "Free Hit",
    "bboost": "Bench Boost",
    "3xc": "Triple Captain",
    "manager": "Assistant Manager",
}


def _answer_chips(db: Session) -> dict:
    """Chip của mùa hiện tại — đọc từ FPL, gồm cả việc chia hai nửa mùa."""
    import json

    from sqlalchemy import select as _select

    from app import scoring
    from app.models import Season

    season = db.scalar(_select(Season).where(Season.is_current.is_(True)))
    if not season or not season.chips_json:
        return _no_data("chưa đồng bộ được danh sách chip từ FPL")
    try:
        chips = json.loads(season.chips_json)
    except ValueError:
        return _no_data("dữ liệu chip không đọc được")

    windows: dict[tuple[int, int], list[str]] = {}
    for c in chips:
        key = (c.get("start_event"), c.get("stop_event"))
        windows.setdefault(key, []).append(CHIP_VI.get(c.get("name"), c.get("name")))

    lines = [f"**Chip mùa {scoring.SEASON}:**", ""]
    for (start, stop), names in sorted(windows.items(), key=lambda x: (x[0][0] or 0)):
        uniq = sorted(set(names))
        lines.append(f"- **GW{start}–{stop}:** {', '.join(uniq)}")
    lines += [
        "",
        "→ Mùa này chip được chia thành **hai bộ cho hai nửa mùa**: bộ nửa đầu hết hạn "
        "khi sang nửa sau, dùng không kịp là mất. Mỗi bộ có Wildcard, Free Hit, "
        "Bench Boost và Triple Captain riêng.",
        f"→ Bạn cũng được giữ tối đa **{scoring.GAME.max_free_transfers} free transfer**.",
    ]
    return _ok("\n".join(lines),
               suggestions=["Có Double Gameweek nào không?", "Đội hình tối ưu là gì?"])


def _answer_howto(db: Session) -> dict:
    """Hướng dẫn dùng web / nhập Team ID."""
    return _ok(
        "**Cách dùng FPL Edge VN:**\n\n"
        "- **Đội của tôi:** nhập FPL Team ID để tải đội hình. Tìm ID trong URL trang "
        "*Points* trên fantasy.premierleague.com — dãy số sau `/entry/`.\n"
        "- **Free Hit Lab:** dựng đội hình tối ưu cho một vòng, 3 chế độ an toàn / cân bằng / mạo hiểm.\n"
        "- **Kế hoạch dài hạn:** lập kế hoạch chuyển nhượng 3–8 vòng với 3 chiến lược.\n"
        "- **Cầu thủ:** lọc theo đội, vị trí, giá; bấm tiêu đề cột để sắp xếp cao ↔ thấp.\n"
        "- **Lịch thi đấu:** độ khó riêng cho tấn công và phòng ngự; ô có dấu • là dựa trên kèo nhà cái.\n\n"
        "*Lưu ý:* FPL chỉ công khai đội hình sau hạn chót mỗi vòng, nên trước deadline đầu tiên "
        "trang **Đội của tôi** chưa tải được squad.",
        suggestions=["Khi nào tới hạn chót?", "Đội hình tối ưu là gì?"],
    )


def _pos_name(element_type: int) -> str:
    return {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}.get(element_type, "")


def _answer_help() -> dict:
    return _ok(
        "Mình trả lời dựa trên đúng số liệu của web (xP, xMins, lịch, kèo nhà cái). "
        "Bạn có thể hỏi kiểu:\n\n"
        "**Chọn người**\n"
        "- *Ai nên làm đội trưởng?*\n"
        "- *Haaland có nên mua không?*\n"
        "- *So sánh Saka và Palmer*\n"
        "- *Tiền đạo nào tốt nhất dưới 7 triệu?*\n"
        "- *Cầu thủ Arsenal nào tốt nhất?*\n\n"
        "**Chiến thuật**\n"
        "- *Đội hình tối ưu là gì?*\n"
        "- *Cầu thủ nào đáng tiền nhất?*\n"
        "- *Ai có ceiling cao nhất?*\n"
        "- *Lựa chọn an toàn là ai?*\n"
        "- *Ai là differential tốt?*\n\n"
        "**Thông tin**\n"
        "- *Lịch Man City thế nào?*\n"
        "- *Ai đá penalty?*\n"
        "- *Hậu vệ nào giữ sạch lưới tốt?*\n"
        "- *Ai đang được mua nhiều nhất?*\n"
        "- *Ai có nguy cơ bị xoay tua?*\n"
        "- *Cầu thủ nào đang chấn thương?*\n"
        "- *Có Double Gameweek nào không?*\n"
        "- *Defensive Contribution là gì?*\n"
        "- *Khi nào tới hạn chót?*\n"
        "- *Cách dùng web này?*",
        suggestions=[
            "Ai nên làm đội trưởng?",
            "Đội hình tối ưu là gì?",
            "Cầu thủ nào đáng tiền nhất?",
        ],
    )


# ------------------------------------------------------------------ khung ----
def _ok(answer: str, players: list[int] | None = None,
        suggestions: list[str] | None = None) -> dict:
    return {
        "answer": answer,
        "players": players or [],
        "suggestions": suggestions or [],
        "grounded": True,
        "source": "Dữ liệu FPL + mô hình xP/xMins của FPL Edge VN",
    }


def _no_data(reason: str) -> dict:
    return {
        "answer": (
            f"Mình chưa trả lời được vì {reason}. Dữ liệu có thể đang được cập nhật — "
            "bạn thử lại sau hoặc hỏi câu khác nhé."
        ),
        "players": [],
        "suggestions": ["Ai nên làm đội trưởng?", "Đội nào có lịch dễ?"],
        "grounded": True,
        "source": "—",
    }


def answer_question(db: Session, question: str) -> dict:
    """Điểm vào: nhận diện ý định rồi lắp câu trả lời từ dữ liệu."""
    q = _norm(question)
    if not q:
        return _answer_help()

    if _has(q, "giup", "help", "hoi gi", "cach dung", "su dung web", "team id o dau"):
        return _answer_howto(db) if _has(q, "cach dung", "su dung web", "team id") else _answer_help()

    # chip
    if _has(q, "chip", "bench boost", "triple captain", "wildcard", "free hit",
            "assistant manager"):
        # "Free Hit nên chọn ai" là hỏi đội hình, không phải hỏi luật chip
        if not _has(q, "chon ai", "doi hinh", "toi uu", "xay doi"):
            return _answer_chips(db)

    # luật tính điểm
    if _has(q, "luat", "tinh diem", "defensive contribution", "defcon", "duoc may diem",
            "bao nhieu diem", "quy dinh"):
        return _answer_rules(db, question)

    # blank / double gameweek
    if _has(q, "double gameweek", "blank gameweek", " dgw", " bgw", "double gw", "blank gw",
            "da hai tran", "nghi vong"):
        return _answer_blank_double(db)

    # deadline / gameweek
    if _has(q, "deadline", "han chot", "khi nao", "vong may", "gameweek nao"):
        return _answer_gameweek(db)

    # injuries
    if _has(q, "chan thuong", "injur", "treo gio", "suspend", "vang mat"):
        return _answer_news(db)

    # rủi ro xoay tua
    if _has(q, "xoay tua", "rotation", "rui ro phut", "it phut", "co the khong da"):
        return _answer_rotation_risk(db)

    # lựa chọn an toàn
    if _has(q, "an toan", "safe", "chac suat", "on dinh", "it rui ro"):
        return _answer_safe(db)

    # ceiling / haul
    if _has(q, "ceiling", "tran diem", "an dam", "haul", "bung no", "diem cao nhat"):
        return _answer_ceiling(db)

    # penalty / set-piece
    if _has(q, "penalty", "phat den", "da pen", "set piece", "phat goc", "da phat"):
        return _answer_penalties(db)

    # giá trị / đáng tiền
    if _has(q, "dang tien", "gia tri", "value", "re ma tot", "diem tren moi trieu",
            "tiet kiem", "gia re"):
        return _answer_value(db)

    # sạch lưới
    if _has(q, "sach luoi", "clean sheet", "giu sach", " cs "):
        return _answer_clean_sheet(db)

    # ownership / template
    if _has(q, "so huu nhieu", "ownership", "template", "nhieu nguoi chon", "pho bien"):
        return _answer_ownership(db)

    # xu hướng chuyển nhượng
    if _has(q, "mua nhieu", "ban nhieu", "trending", "dang duoc mua", "chuyen nhuong nhieu"):
        return _answer_trending(db)

    # đội hình tối ưu
    if _has(q, "doi hinh toi uu", "toi uu", "free hit", "wildcard", "xay doi", "doi hinh tot nhat"):
        return _answer_optimal_xi(db)

    # captain
    if _has(q, "doi truong", "captain", "cap ", " c "):
        return _answer_captain(db)

    # differential
    if _has(q, "differential", "it nguoi", "khac biet", "dif "):
        return _answer_differential(db)

    # Tên cầu thủ được ưu tiên hơn tên đội: hỏi "Saka của Arsenal thế nào" thì
    # người dùng muốn biết về cầu thủ, không phải về cả đội.
    players = find_players(db, question)
    wants_compare = _has(q, "so sanh", " vs ", " hay ", "compare", " or ")

    if not players:
        # hỏi về một đội cụ thể
        team_id = _find_team(db, question)
        if team_id:
            if _has(q, "lich", "fixture", "gap ai", "doi thu"):
                return _answer_team_fixtures(db, team_id)
            if _has(q, "cau thu", "nen mua", "tot nhat", "ai", "player", "dang mua"):
                return _answer_team_players(db, team_id)

    # fixtures chung
    if _has(q, "lich thi dau", "lich dep", "lich de", "fixture", "doi nao de"):
        return _answer_fixtures(db)

    if len(players) >= 2 and wants_compare:
        return _answer_compare(db, players[:3])

    if players:
        res = _answer_player(db, players[0])
        if wants_compare:
            # chỉ nhận ra một cái tên -> nói rõ thay vì im lặng bỏ qua
            res["answer"] += (
                f"\n\n_Ghi chú: trong câu hỏi mình chỉ nhận ra **{players[0].web_name}**. "
                "Cầu thủ còn lại có thể đang không thi đấu ở Ngoại hạng Anh mùa này, "
                "hoặc bạn thử viết tên theo cách FPL hiển thị._"
            )
        return res

    # top theo vị trí / ngân sách
    pos = _pos_filter(q)
    budget = _budget(q)
    if pos or budget or _has(q, "nen mua", "tot nhat", "best", "goi y", "mua ai"):
        return _answer_best(db, pos, budget)

    return _answer_help()
