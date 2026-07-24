"""Static shareable HTML report generator.

Produces a self-contained (no external requests) HTML snapshot of the current
gameweek — top xP, captain picks, an optimised Free Hit XI and fixture swings —
so it can be hosted anywhere (GitHub Gist + githack, GitHub Pages, an Artifact,
or just opened locally) and shared. Regenerate each gameweek:

    python -m app.cli report
"""
from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.services import team as team_svc
from app.services.fixtures import fixture_ticker
from app.services.gameweek import dashboard, gameweek_status

VN = timezone(timedelta(hours=7))  # Asia/Ho_Chi_Minh (avoid tzdata dependency)


def _vn(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(VN).strftime("%H:%M %d/%m/%Y") + " (giờ VN)"
    except ValueError:
        return iso


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


POS_BG = {"GK": "#f59e0b", "DEF": "#0ea5e9", "MID": "#10b981", "FWD": "#f43f5e"}


def _chip(name: str, sub: str, val: str, cap: bool = False) -> str:
    badge = '<span class="cap">C</span>' if cap else ""
    return f"""<div class="chip">{badge}<div class="chip-name">{_e(name)}</div>
      <div class="chip-sub">{_e(sub)}</div><div class="chip-val">{_e(val)}</div></div>"""


def render_report_body(db: Session) -> str:
    gw = gameweek_status(db)
    dash = dashboard(db)
    ticker = fixture_ticker(db, n_gws=6)
    try:
        fh = team_svc.optimize_free_hit(db, budget=1000, mode="max_ep")
    except Exception:
        fh = None

    next_gw = gw.get("next") or {}
    gw_id = next_gw.get("id", "?")
    deadline = _vn(gw.get("deadline"))
    generated = datetime.now(VN).strftime("%H:%M %d/%m/%Y")

    # top predicted rows
    top_rows = "".join(
        f"""<tr><td class="rank">{i+1}</td><td class="pl">{_e(p['name'])}
        <span class="pos" style="background:{POS_BG.get(p['position'],'#888')}">{_e(p['position'])}</span></td>
        <td>{_e(p['team'])}</td><td class="num">£{p['price']:.1f}</td>
        <td class="num strong">{(p.get('xp_next') or 0):.1f}</td>
        <td class="num dim">{(p.get('xmins') or 0):.0f}'</td>
        <td class="num dim">{(p.get('ceiling') or 0):.1f}</td></tr>"""
        for i, p in enumerate(dash.get("top_predicted", [])[:12])
    )

    # captain cards
    cap_cards = "".join(
        f"""<div class="capcard"><div class="capn">{i+1}. {_e(c['name'])}</div>
        <div class="caps">{_e(c['team'])} · {_e(c['position'])}</div>
        <div class="capv">{c['captain_xp']:.1f}<span>Cap xP</span></div>
        <div class="capm">Ceiling {c.get('ceiling',0):.0f} · P(≥20đ) {round((c.get('p_haul') or 0)*100)}%</div></div>"""
        for i, c in enumerate(dash.get("captain_top", [])[:6])
    )

    # free hit pitch
    fh_html = ""
    if fh and fh.get("starting"):
        rows = {1: [], 2: [], 3: [], 4: []}
        for p in fh["starting"]:
            rows[p["element_type"]].append(
                _chip(p["name"], f"{p['team']} £{p['price']:.1f}", f"{p.get('xp',0):.1f}", p.get("is_captain"))
            )
        pitch_rows = "".join(f'<div class="prow">{"".join(rows[t])}</div>' for t in (1, 2, 3, 4))
        bench = "".join(
            _chip(p["name"], f"{p['team']} £{p['price']:.1f}", f"{p.get('xp',0):.1f}")
            for p in fh.get("bench", [])
        )
        fh_html = f"""
        <div class="fh">
          <div class="pitch">{pitch_rows}</div>
          <div class="bench"><div class="bench-t">Dự bị</div><div class="prow">{bench}</div></div>
        </div>
        <div class="fhmeta">Sơ đồ <b>{_e(fh['formation'])}</b> · Tổng xP (XI) <b>{fh['xi_xp']:.1f}</b> · Chi phí <b>£{fh['total_cost']:.1f}m</b></div>"""

    # fixtures
    atk = "".join(
        f"<li><span>{i+1}. {_e(r['team_name'])}</span><b>{r['sum_proj_goals']:.1f} bàn KV</b></li>"
        for i, r in enumerate(ticker.get("best_attack", [])[:6])
    )
    dfc = "".join(
        f"<li><span>{i+1}. {_e(r['team_name'])}</span><b>{r['sum_clean_sheet_prob']:.1f} CS KV</b></li>"
        for i, r in enumerate(ticker.get("best_defence", [])[:6])
    )

    # injuries
    inj = "".join(
        f"<li><b>{_e(n['name'])}</b> ({_e(n['team'])}) — <span class='imp'>{_e(n['impact'])}</span> {_e(n.get('news') or '')}</li>"
        for n in dash.get("injury_alerts", [])[:8]
    ) or "<li class='dim'>Không có cảnh báo mức cao.</li>"

    return f"""
<style>{_CSS}</style>
<div class="wrap">
  <header class="hero">
    <div class="brand">⚡ FPL Edge VN</div>
    <h1>Báo cáo Gameweek {gw_id}</h1>
    <div class="meta">Hạn chót: <b>{deadline}</b></div>
    <div class="disc">Quyết định dựa trên dữ liệu · Sản phẩm độc lập, không liên kết Premier League/FPL ·
      Mọi con số là dự báo có độ bất định, không phải khẳng định chắc chắn.</div>
  </header>

  <section>
    <h2>🎯 Điểm kỳ vọng cao nhất (vòng {gw_id})</h2>
    <div class="tablewrap"><table>
      <thead><tr><th>#</th><th>Cầu thủ</th><th>Đội</th><th>Giá</th><th>xP</th><th>xMins</th><th>Ceiling</th></tr></thead>
      <tbody>{top_rows}</tbody>
    </table></div>
  </section>

  <section>
    <h2>👑 Đội trưởng nên chọn</h2>
    <div class="capgrid">{cap_cards}</div>
    <p class="note">Xếp theo captain EV (2×xP) sau khi xét xMins, ceiling và độ bất định — không theo phong độ 1 trận hay danh tiếng.</p>
  </section>

  <section>
    <h2>⚡ Đội hình Free Hit tối ưu (Max xP, vòng {gw_id})</h2>
    {fh_html or '<p class="dim">Chưa tính được (thiếu dữ liệu).</p>'}
    <p class="note">Tối ưu bằng quy hoạch nguyên (MILP) tuân thủ luật FPL: ngân sách £100m, 2/5/5/3, tối đa 3 cầu thủ/CLB, sơ đồ hợp lệ.</p>
  </section>

  <div class="two">
    <section><h2>📈 Lịch tấn công tốt (6 vòng)</h2><ul class="rl">{atk}</ul></section>
    <section><h2>🛡️ Lịch giữ sạch lưới tốt</h2><ul class="rl">{dfc}</ul></section>
  </div>

  <section><h2>🚑 Cảnh báo chấn thương / ra sân</h2><ul class="rl injl">{inj}</ul></section>

  <footer>
    <div>Tạo lúc {generated} (giờ VN) · model {_e(settings.model_version)} · nguồn: FPL API công khai</div>
    <div class="dim">Ownership không phải bằng chứng “cầu thủ tốt”. Dự báo kèm độ bất định. Chơi FPL có trách nhiệm.</div>
  </footer>
</div>
"""


_CSS = """
*{box-sizing:border-box}
.wrap{max-width:900px;margin:0 auto;padding:16px;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
  color:#e5e7eb;background:#0b1220;line-height:1.5}
.hero{background:linear-gradient(135deg,#065f46,#0b1220);border:1px solid #1f2937;border-radius:16px;padding:24px;margin-bottom:20px}
.brand{font-weight:800;color:#34d399;letter-spacing:.3px}
.hero h1{margin:6px 0;font-size:28px}
.hero .meta{color:#cbd5e1}
.hero .disc{margin-top:10px;font-size:12px;color:#94a3b8}
section{background:#0f172a;border:1px solid #1f2937;border-radius:14px;padding:16px;margin-bottom:16px}
h2{font-size:17px;margin:0 0 12px}
.tablewrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:14px;min-width:520px}
th{text-align:left;color:#94a3b8;font-size:11px;text-transform:uppercase;padding:6px 8px;border-bottom:1px solid #1f2937}
td{padding:8px;border-bottom:1px solid #16202e}
.rank{color:#64748b;font-weight:700;width:28px}
.pl{font-weight:600}
.pos{font-size:10px;color:#04120b;padding:1px 6px;border-radius:8px;margin-left:6px;font-weight:800;vertical-align:middle}
.num{text-align:right;font-variant-numeric:tabular-nums}
.strong{color:#34d399;font-weight:800}.dim{color:#94a3b8}
.capgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
.capcard{background:#0b1220;border:1px solid #1f2937;border-radius:12px;padding:12px}
.capn{font-weight:700}.caps{font-size:12px;color:#94a3b8}
.capv{font-size:24px;font-weight:800;color:#34d399;margin-top:6px}
.capv span{font-size:10px;color:#94a3b8;margin-left:6px;font-weight:600}
.capm{font-size:11px;color:#94a3b8;margin-top:4px}
.fh{background:linear-gradient(180deg,#134e33,#0f3d28);border-radius:14px;padding:14px}
.prow{display:flex;justify-content:center;gap:8px;flex-wrap:wrap;margin:8px 0}
.chip{position:relative;width:84px;background:#0b1220cc;border:1px solid #1f2937;border-radius:10px;padding:6px 4px;text-align:center}
.chip-name{font-size:12px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.chip-sub{font-size:9px;color:#94a3b8}
.chip-val{font-size:12px;font-weight:800;color:#34d399}
.cap{position:absolute;top:-6px;right:-6px;background:#34d399;color:#04120b;font-size:10px;font-weight:800;border-radius:50%;width:18px;height:18px;line-height:18px}
.bench{margin-top:10px;background:#0b1220;border:1px dashed #334155;border-radius:12px;padding:8px}
.bench-t{font-size:11px;color:#94a3b8;text-align:center}
.fhmeta{margin-top:10px;font-size:13px;color:#cbd5e1;text-align:center}
.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:640px){.two{grid-template-columns:1fr}}
.rl{list-style:none;margin:0;padding:0}
.rl li{display:flex;justify-content:space-between;gap:8px;padding:6px 0;border-bottom:1px solid #16202e;font-size:14px}
.rl b{color:#34d399}
.injl li{display:block}.imp{color:#f59e0b;font-weight:700}
.note{font-size:12px;color:#94a3b8;margin:10px 0 0}
footer{text-align:center;font-size:12px;color:#94a3b8;padding:16px}
"""


def render_report_page(db: Session) -> str:
    """Full standalone HTML document (for Gist/githack/local)."""
    body = render_report_body(db)
    return f"""<!DOCTYPE html>
<html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FPL Edge VN — Báo cáo Gameweek</title>
<meta name="description" content="Báo cáo Fantasy Premier League dựa trên dữ liệu: xP, xMins, đội trưởng, Free Hit tối ưu.">
</head><body style="margin:0;background:#0b1220">{body}</body></html>"""
