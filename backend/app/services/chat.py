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


def _answer_help() -> dict:
    return _ok(
        "Mình trả lời dựa trên đúng số liệu của web (xP, xMins, lịch, kèo nhà cái). "
        "Bạn có thể hỏi kiểu:\n\n"
        "- *Ai nên làm đội trưởng?*\n"
        "- *Haaland có nên mua không?*\n"
        "- *So sánh Saka và Palmer*\n"
        "- *Tiền đạo nào tốt nhất dưới 7 triệu?*\n"
        "- *Đội nào có lịch dễ?*\n"
        "- *Ai là differential tốt?*\n"
        "- *Cầu thủ nào đang chấn thương?*\n"
        "- *Khi nào tới hạn chót?*",
        suggestions=["Ai nên làm đội trưởng?", "Đội nào có lịch dễ?", "Ai là differential tốt?"],
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

    if _has(q, "giup", "help", "lam gi", "hoi gi", "huong dan"):
        return _answer_help()

    # deadline / gameweek
    if _has(q, "deadline", "han chot", "khi nao", "vong may", "gameweek nao"):
        return _answer_gameweek(db)

    # injuries
    if _has(q, "chan thuong", "injur", "treo gio", "suspend", "vang mat"):
        return _answer_news(db)

    # captain
    if _has(q, "doi truong", "captain", "cap ", " c "):
        return _answer_captain(db)

    # differential
    if _has(q, "differential", "it nguoi", "khac biet", "dif "):
        return _answer_differential(db)

    # fixtures
    if _has(q, "lich thi dau", "lich dep", "lich de", "fixture", "doi nao de"):
        return _answer_fixtures(db)

    players = find_players(db, question)
    wants_compare = _has(q, "so sanh", " vs ", " hay ", "compare", " or ")

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
