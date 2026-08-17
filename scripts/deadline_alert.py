#!/usr/bin/env python3
"""Cảnh báo trước hạn chót FPL: đội của bạn có ai vừa đổi trạng thái không.

Điểm mất nhiều nhất trong FPL hiếm khi là chọn sai mô hình. Nó là buổi họp báo
tối thứ Sáu: một người trong đội hình xuất phát dính chấn thương, bạn không kịp
biết, và ăn 0 điểm ở một suất đá chính. Cả website chỉ nằm chờ bạn vào xem, nên
nó không cứu được đúng tình huống đó — thứ cứu được là một tin nhắn tự tìm đến
bạn.

Script này CỐ TÌNH không phụ thuộc gì vào backend:

  * chỉ dùng thư viện chuẩn (urllib) — chạy được với bất kỳ Python 3.9+ nào,
    không cần cài gói, không cần venv của dự án;
  * chỉ đọc FPL API công khai — không cần database, không cần server đang chạy.

Lý do là độ tin cậy: một cảnh báo phải chạy được vào lúc mọi thứ khác đang hỏng.
Backend nằm trên gói Render miễn phí và ngủ sau 15 phút không ai dùng, nên đặt
báo động vào trong nó là đặt báo động vào thứ hay ngủ nhất hệ thống.

Cách dùng:

    python deadline_alert.py --team-id 1234567
    python deadline_alert.py --watch "Haaland,B.Fernandes,Gabriel" --dry-run

Biến môi trường:

    FPL_TEAM_ID          Team ID của bạn (thay cho --team-id)
    FPL_WATCHLIST        Danh sách tên/id cách nhau bởi dấu phẩy (thay cho --watch)
    TELEGRAM_BOT_TOKEN   Token bot Telegram
    TELEGRAM_CHAT_ID     Chat id nhận tin

Đặt lịch trên Windows (Task Scheduler), chạy mỗi 30 phút:

    schtasks /create /tn "FPL deadline alert" /tr "python \"D:\\claude ai\\fpl-planner\\scripts\\deadline_alert.py\" --team-id 1234567" /sc minute /mo 30
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

FPL_API = "https://fantasy.premierleague.com/api"
STATE_FILE = Path(__file__).with_name(".deadline_alert_state.json")
USER_AGENT = "FPL-Edge-VN deadline-alert (independent fan project)"

# Giờ Việt Nam. Hạn chót FPL luôn phát ra ở UTC; đọc nó ở +07 là việc của người
# nhận tin nhắn, không phải việc của họ lúc 1 giờ sáng.
VN_TZ = timezone(timedelta(hours=7))

# Trạng thái FPL. 'a' = sẵn sàng; còn lại đều là lý do để nhìn lại đội hình.
STATUS_TEXT = {
    "a": "sẵn sàng",
    "d": "chưa chắc chắn",
    "i": "chấn thương",
    "s": "treo giò",
    "u": "không thi đấu",
    "n": "không đủ điều kiện",
}
# Những chuyển biến đáng gọi là NGHIÊM TRỌNG: mất hẳn suất đá.
CRITICAL_STATUS = {"i", "s", "u", "n"}


def _get(path: str) -> dict | list:
    req = urllib.request.Request(f"{FPL_API}{path}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            # State hỏng thì coi như chạy lần đầu: thà báo thừa một lượt còn hơn
            # im lặng vì không đọc được file.
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def resolve_squad(bootstrap: dict, team_id: int | None, watch: str | None) -> tuple[list[int], str]:
    """Danh sách cầu thủ cần theo dõi, và nói rõ nó đến từ đâu.

    Đội hình thật chỉ công khai SAU khi một vòng kết thúc, nên trước vòng 1 (và
    trong lúc vòng đang diễn ra) không có cách nào đọc được đội của bạn từ API
    công khai. Đó chính là lúc watchlist gõ tay là thứ duy nhất dùng được — nên
    nó là đường đi chính thức, không phải đường phụ.
    """
    elements = bootstrap["elements"]
    if watch:
        by_name = {e["web_name"].lower(): e["id"] for e in elements}
        ids, unknown = [], []
        for token in (t.strip() for t in watch.split(",") if t.strip()):
            if token.isdigit():
                ids.append(int(token))
            elif token.lower() in by_name:
                ids.append(by_name[token.lower()])
            else:
                unknown.append(token)
        if unknown:
            print(f"[!] Không nhận ra: {', '.join(unknown)}", file=sys.stderr)
        return ids, "watchlist"

    if team_id:
        try:
            history = _get(f"/entry/{team_id}/history/")
            current = history.get("current") or []
            if current:
                gw = current[-1]["event"]
                picks = _get(f"/entry/{team_id}/event/{gw}/picks/")
                return [p["element"] for p in picks.get("picks", [])], f"đội hình GW{gw}"
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
    return [], "không có"


def next_deadline(bootstrap: dict) -> tuple[int | None, datetime | None]:
    for ev in bootstrap.get("events", []):
        if ev.get("is_next") or (not ev.get("finished") and not ev.get("is_current")):
            raw = ev.get("deadline_time")
            if raw:
                return ev["id"], datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return None, None


def snapshot(element: dict) -> dict:
    return {
        "status": element.get("status"),
        "chance": element.get("chance_of_playing_next_round"),
        "news": (element.get("news") or "").strip(),
        "cost": element.get("now_cost"),
    }


def describe_change(name: str, old: dict, new: dict) -> str | None:
    """Một dòng cho mỗi thay đổi đáng đọc. Không có gì đổi thì trả None."""
    bits: list[str] = []
    critical = False

    if old.get("status") != new.get("status"):
        before = STATUS_TEXT.get(old.get("status"), old.get("status"))
        after = STATUS_TEXT.get(new.get("status"), new.get("status"))
        bits.append(f"{before} → <b>{after}</b>")
        critical = new.get("status") in CRITICAL_STATUS

    if old.get("chance") != new.get("chance"):
        after = new.get("chance")
        if after is None:
            bits.append("khả năng ra sân: đã gỡ cảnh báo")
        else:
            # None nghĩa là FPL không treo cảnh báo nào, không phải 0% — in ra
            # "None%" thì đọc như thể trước đó anh ta cũng không đá được.
            before = "không cảnh báo" if old.get("chance") is None else f"{old['chance']}%"
            bits.append(f"khả năng ra sân {before} → <b>{after}%</b>")
            if after <= 50:
                critical = True

    if old.get("news") != new.get("news") and new.get("news"):
        bits.append(f"tin: {new['news']}")

    if old.get("cost") != new.get("cost") and old.get("cost") and new.get("cost"):
        arrow = "tăng" if new["cost"] > old["cost"] else "giảm"
        bits.append(f"giá {arrow} £{old['cost'] / 10:.1f} → £{new['cost'] / 10:.1f}")

    if not bits:
        return None
    mark = "🔴 " if critical else "• "
    return f"{mark}<b>{name}</b>: " + "; ".join(bits)


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    payload = urllib.parse.urlencode({
        "chat_id": chat_id, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=payload,
        headers={"User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8")).get("ok", False)
    except urllib.error.HTTPError as exc:
        print(f"[!] Telegram lỗi {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}",
              file=sys.stderr)
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Cảnh báo trạng thái cầu thủ trước hạn chót FPL")
    ap.add_argument("--team-id", type=int, default=os.getenv("FPL_TEAM_ID") or None)
    ap.add_argument("--watch", default=os.getenv("FPL_WATCHLIST"),
                    help="Tên hoặc id cầu thủ, cách nhau bởi dấu phẩy")
    ap.add_argument("--hours-before", type=float, default=3.0,
                    help="Nhắc một lần khi còn dưới ngần này giờ (mặc định 3)")
    ap.add_argument("--dry-run", action="store_true",
                    help="In ra màn hình, KHÔNG gửi Telegram và KHÔNG ghi state")
    ap.add_argument("--force", action="store_true",
                    help="Gửi cả khi không có gì đổi (để thử đường dây)")
    args = ap.parse_args()

    bootstrap = _get("/bootstrap-static/")
    elements = {e["id"]: e for e in bootstrap["elements"]}
    squad, source = resolve_squad(bootstrap, args.team_id, args.watch)
    if not squad:
        print("Không có cầu thủ nào để theo dõi. Dùng --team-id (sau khi vòng đầu "
              "kết thúc) hoặc --watch để khai danh sách.", file=sys.stderr)
        return 2

    gw, deadline = next_deadline(bootstrap)
    now = datetime.now(timezone.utc)
    hours_left = (deadline - now).total_seconds() / 3600 if deadline else None

    state = load_state()
    seen: dict = state.get("players", {})
    lines: list[str] = []
    new_seen: dict = {}

    for pid in squad:
        el = elements.get(pid)
        if not el:
            continue
        snap = snapshot(el)
        new_seen[str(pid)] = snap
        old = seen.get(str(pid))
        if old is None:
            continue  # lần đầu thấy người này: ghi nhận, không báo động
        change = describe_change(el["web_name"], old, snap)
        if change:
            lines.append(change)

    # Nhắc hạn chót: đúng một lần cho mỗi vòng, kèm bản tóm tắt ai đang có vấn đề.
    remind_key = f"reminded_gw{gw}"
    remind = (
        hours_left is not None and 0 < hours_left <= args.hours_before
        and not state.get(remind_key)
    )

    if not lines and not remind and not args.force:
        print(f"Không có gì thay đổi ({len(squad)} cầu thủ, nguồn: {source}).")
        if not args.dry_run:
            save_state({**state, "players": new_seen})
        return 0

    header = f"⚽ <b>FPL · GW{gw}</b>"
    if deadline:
        local = deadline.astimezone(VN_TZ)
        header += f"\nHạn chót: {local:%H:%M %d/%m} giờ VN"
        if hours_left is not None:
            header += (f" (còn {hours_left:.1f} giờ)" if hours_left > 0
                       else " — ĐÃ QUA HẠN")
    body = [header, f"<i>Theo dõi {len(squad)} cầu thủ · {source}</i>", ""]

    if lines:
        body.append("<b>Thay đổi từ lần kiểm tra trước:</b>")
        body.extend(lines)
    elif not seen:
        # Lần chạy đầu chưa có gì để so sánh. Nói thẳng, đừng để "không có thay
        # đổi" ngụ ý là đã kiểm tra xong.
        body.append("Lần chạy đầu tiên — mới ghi nhận trạng thái hiện tại, "
                    "chưa có mốc cũ để so sánh.")
    else:
        body.append("Không có thay đổi nào từ lần kiểm tra trước.")

    if remind:
        flagged = [
            f"🔴 <b>{elements[p]['web_name']}</b>: "
            f"{STATUS_TEXT.get(elements[p]['status'], elements[p]['status'])}"
            + (f" ({elements[p]['chance_of_playing_next_round']}%)"
               if elements[p].get("chance_of_playing_next_round") is not None else "")
            for p in squad
            if p in elements and elements[p].get("status") != "a"
        ]
        body.append("")
        body.append("<b>Kiểm tra lần cuối trước hạn chót:</b>")
        body.extend(flagged or ["Cả đội đang ở trạng thái sẵn sàng."])

    text = "\n".join(body)

    if args.dry_run:
        print(text.replace("<b>", "").replace("</b>", "")
                  .replace("<i>", "").replace("</i>", ""))
        print("\n[dry-run] Không gửi Telegram, không ghi state.")
        return 0

    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("Thiếu TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID — in ra màn hình thay vì gửi.\n",
              file=sys.stderr)
        print(text)
        return 1

    ok = send_telegram(token, chat, text)
    # Chỉ ghi nhận "đã nhắc" khi tin thật sự đi được. Ghi trước khi gửi thành
    # công là cách chắc chắn nhất để nuốt mất đúng cảnh báo quan trọng nhất.
    if ok:
        state = {**state, "players": new_seen}
        if remind:
            state[remind_key] = True
        save_state(state)
        print(f"Đã gửi {len(lines)} thay đổi.")
        return 0
    print("Gửi thất bại — giữ nguyên state để lần sau báo lại.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # console Windows mặc định không phải UTF-8
    except Exception:
        pass
    raise SystemExit(main())
