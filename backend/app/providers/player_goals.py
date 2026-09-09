"""Adapter The Odds API cho thị trường `player_goal_scorer_anytime`.

Tài liệu chính thức (đọc 2026-09-09):
  * https://the-odds-api.com/sports-odds-data/betting-markets.html — khoá thị trường
    là `player_goal_scorer_anytime` ("Anytime Goal Scorer (Yes/No)"), và *"Player
    props can be accessed one event at a time using the /events/{eventId}/odds
    endpoint"*.
  * https://the-odds-api.com/liveapi/guides/v4/ — `/v4/sports/{sport}/events`
    **không tính vào quota**; `/v4/sports/{sport}/events/{eventId}/odds` tính
    `số thị trường × số region` **cho mỗi event**; phản hồi rỗng không bị tính.
    Outcome của kèo cầu thủ có thêm trường `description` chứa tên cầu thủ. Header
    `x-requests-remaining` / `x-requests-used` báo quota còn lại.

**Vì sao phải có ngân sách gọi.** Kèo đội (`h2h,totals,spreads`) tốn 3 credit mỗi
lần đồng bộ. Kèo cầu thủ tốn **1 credit cho MỖI trận**, tức ~10 credit một vòng.
Với lịch đồng bộ 6 tiếng (120 lần/tháng) thì riêng phần này đã là ~1.200 credit —
gấp đôi gói 500/tháng. Nên module này:

  * chỉ lấy các trận của **vòng sắp tới**, xếp theo giờ thi đấu gần nhất;
  * chỉ gọi lại sau `ODDS_PLAYER_GOALS_MIN_REFRESH_HOURS` (mặc định 24) kể từ lần
    lấy thành công gần nhất của chính trận đó — nên lịch 6 tiếng vẫn chỉ tốn ~10
    credit mỗi ngày;
  * **dừng hẳn** khi `x-requests-remaining` xuống dưới mức dự trữ, để phần kèo đội
    (vốn ảnh hưởng tới λ của MỌI cầu thủ) không bị phần này ăn hết quota.

Mọi thất bại đều là "không có dữ liệu", không phải ngoại lệ: thiếu key, hết quota,
nhà cái chưa ra kèo, hay tên cầu thủ không khớp đều trả về rỗng và engine chạy y
như trước.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.engine.goal_market import devig_yes_no

ODDS_BASE = "https://api.the-odds-api.com/v4"
SPORT = "soccer_epl"
MARKET_KEY = "player_goal_scorer_anytime"
DOCS_URL = "https://the-odds-api.com/sports-odds-data/betting-markets.html"


@dataclass
class PlayerGoalQuote:
    """Một giá của MỘT nhà cái cho MỘT cầu thủ trong MỘT trận."""

    player_name: str            # tên đúng như nhà cái phát ra
    bookmaker: str
    price_yes: float | None
    price_no: float | None
    prob: float | None          # đã loại biên khi có cả hai cửa
    devigged: bool
    last_update: datetime | None = None


@dataclass
class EventGoalMarket:
    """Toàn bộ kèo ghi bàn của một trận."""

    event_id: str
    home_name: str
    away_name: str
    commence_time: str
    quotes: list[PlayerGoalQuote] = field(default_factory=list)
    n_bookmakers: int = 0
    fetched_at: datetime | None = None


@dataclass
class FetchReport:
    """Kết quả một lượt lấy dữ liệu, đủ để ghi log và hiện lên giao diện."""

    enabled: bool
    events: list[EventGoalMarket] = field(default_factory=list)
    credits_used: int = 0
    requests_remaining: int | None = None
    requests_used: int | None = None
    skipped_fresh: int = 0        # trận còn mới, không cần gọi lại
    skipped_budget: int = 0       # trận bị bỏ vì hết ngân sách/quota
    errors: list[str] = field(default_factory=list)
    detail: str = ""


# ------------------------------------------------------------ khớp tên -------
def normalise_player(name: str) -> str:
    """Bỏ dấu, bỏ hậu tố, còn lại chữ thường + khoảng trắng.

    Dùng NFD để tách dấu. Lưu ý cái bẫy đã gặp ở chatbot: **"đ" tiếng Việt không
    tách được bằng NFD** — ở đây không liên quan (tên cầu thủ Ngoại hạng), nhưng
    giữ `replace` cho chắc vì danh sách có thể có tên Việt hoá.
    """
    n = (name or "").strip().lower().replace("đ", "d")
    n = unicodedata.normalize("NFD", n)
    n = "".join(ch for ch in n if unicodedata.category(ch) != "Mn")
    n = re.sub(r"\b(jr|sr|ii|iii)\b", " ", n)
    n = re.sub(r"[^a-z' ]", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def match_player_id(odds_name: str, candidates: list) -> int | None:
    """Khớp tên nhà cái với một cầu thủ FPL trong TẬP ỨNG VIÊN của đúng trận đó.

    Giới hạn ứng viên vào hai đội của trận là điều quan trọng nhất ở đây: toàn giải
    có nhiều cặp họ trùng nhau, và gán bàn thắng cho sai cầu thủ là lỗi âm thầm.

    Thứ tự thử: khớp nguyên cả tên -> khớp `web_name` -> khớp họ DUY NHẤT trong tập
    ứng viên. Họ trùng trong cùng một trận thì bỏ qua, không đoán.
    """
    target = normalise_player(odds_name)
    if not target or not candidates:
        return None

    full: dict[str, list] = {}
    web: dict[str, list] = {}
    last: dict[str, list] = {}
    for p in candidates:
        f = normalise_player(f"{p.first_name or ''} {p.second_name or ''}")
        w = normalise_player(p.web_name or "")
        if f:
            full.setdefault(f, []).append(p)
        if w:
            web.setdefault(w, []).append(p)
        surname = (p.second_name or "").strip().split(" ")[-1] if p.second_name else ""
        s = normalise_player(surname)
        if s:
            last.setdefault(s, []).append(p)

    for index in (full, web):
        hit = index.get(target)
        if hit and len(hit) == 1:
            return hit[0].id

    # tên nhà cái có thể là "B. Fernandes" hoặc chỉ "Haaland"
    tokens = target.split()
    if tokens:
        hit = last.get(tokens[-1])
        if hit and len(hit) == 1:
            return hit[0].id

    # Chứa trọn tên ngắn: "erling haaland" vs web_name "haaland".
    #
    # So theo TỪ, không theo chuỗi con. `"ola" in "nicolas"` là đúng về chuỗi và sai
    # về người: một cầu thủ tên viết tắt "Ola" sẽ nuốt mọi giá của "Nicolas ...".
    # Gán giá cho nhầm cầu thủ là lỗi âm thầm tệ nhất ở đây — nó không báo lỗi, chỉ
    # làm xP của hai người cùng sai.
    matches = [
        p for key, ps in web.items() if _contains_words(tokens, key) for p in ps
    ]
    if len(matches) == 1:
        return matches[0].id
    return None


def _contains_words(tokens: list[str], key: str) -> bool:
    """`key` xuất hiện trong `tokens` như một DÃY TỪ trọn vẹn."""
    kt = key.split()
    if not kt or len(kt) > len(tokens):
        return False
    return any(
        tokens[i : i + len(kt)] == kt for i in range(len(tokens) - len(kt) + 1)
    )


# ------------------------------------------------------------- provider ------
class PlayerGoalsProvider:
    """Lấy `player_goal_scorer_anytime` theo từng trận, có ngân sách credit."""

    name = "odds_api_player_goals"

    def __init__(
        self,
        api_key: str,
        region: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.api_key = api_key
        self.region = region or settings.odds_region
        self.timeout = timeout or settings.http_timeout_seconds
        self.requests_remaining: int | None = None
        self.requests_used: int | None = None

    # ---- lớp HTTP mỏng, để test thay bằng giả lập ----
    def _get(self, url: str, params: dict) -> tuple[object, dict]:
        r = httpx.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json(), dict(r.headers)

    def _track_quota(self, headers: dict) -> None:
        for key, attr in (
            ("x-requests-remaining", "requests_remaining"),
            ("x-requests-used", "requests_used"),
        ):
            raw = headers.get(key) or headers.get(key.title())
            if raw is None:
                continue
            try:
                setattr(self, attr, int(float(raw)))
            except (TypeError, ValueError):
                pass

    def list_events(self) -> list[dict]:
        """Các trận sắp tới. Endpoint này KHÔNG tính vào quota (theo tài liệu)."""
        data, headers = self._get(
            f"{ODDS_BASE}/sports/{SPORT}/events", {"apiKey": self.api_key}
        )
        self._track_quota(headers)
        return data if isinstance(data, list) else []

    def event_goal_market(self, event: dict) -> EventGoalMarket | None:
        """Kèo ghi bàn của một trận. Tốn 1 credit (1 thị trường × 1 region)."""
        event_id = str(event.get("id") or "")
        if not event_id:
            return None
        data, headers = self._get(
            f"{ODDS_BASE}/sports/{SPORT}/events/{event_id}/odds",
            {
                "apiKey": self.api_key,
                "regions": self.region,
                "markets": MARKET_KEY,
                "oddsFormat": "decimal",
            },
        )
        self._track_quota(headers)
        if not isinstance(data, dict):
            return None
        return parse_event_market(data)


def _parse_update(raw) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_event_market(payload: dict) -> EventGoalMarket:
    """Đọc một phản hồi `/events/{id}/odds` thành các giá theo từng nhà cái.

    Cấu trúc outcome của kèo cầu thủ: `name` là "Yes"/"No", `description` là tên
    cầu thủ (tài liệu v4). Một số nhà cái chỉ ra cửa "Yes" — trường hợp đó được giữ
    lại nhưng `devigged=False`, và phía sau sẽ không cho nó vào xP.
    """
    out = EventGoalMarket(
        event_id=str(payload.get("id") or ""),
        home_name=payload.get("home_team") or "",
        away_name=payload.get("away_team") or "",
        commence_time=payload.get("commence_time") or "",
        fetched_at=datetime.now(timezone.utc),
    )
    books = payload.get("bookmakers") or []
    out.n_bookmakers = len(books)
    for bk in books:
        book_name = bk.get("title") or bk.get("key") or "?"
        for m in bk.get("markets") or []:
            if m.get("key") != MARKET_KEY:
                continue
            updated = _parse_update(m.get("last_update"))
            # gom Yes/No của CÙNG một cầu thủ trong CÙNG một nhà cái
            per_player: dict[str, dict[str, float]] = {}
            for o in m.get("outcomes") or []:
                who = (o.get("description") or "").strip()
                side = (o.get("name") or "").strip().lower()
                price = o.get("price")
                if not who or side not in ("yes", "no"):
                    continue
                try:
                    price = float(price)
                except (TypeError, ValueError):
                    continue
                if price <= 1:
                    continue
                per_player.setdefault(who, {})[side] = price
            for who, sides in per_player.items():
                prob, devigged = devig_yes_no(sides.get("yes"), sides.get("no"))
                if prob is None:
                    continue
                out.quotes.append(
                    PlayerGoalQuote(
                        player_name=who,
                        bookmaker=book_name,
                        price_yes=sides.get("yes"),
                        price_no=sides.get("no"),
                        prob=prob,
                        devigged=devigged,
                        last_update=updated,
                    )
                )
    return out


def get_player_goals_provider() -> PlayerGoalsProvider | None:
    """Provider đã cấu hình, hoặc None => tính năng tắt, engine chạy như cũ."""
    if not settings.odds_api_key or not settings.odds_player_goals_enabled:
        return None
    return PlayerGoalsProvider(settings.odds_api_key)


def fetch_player_goal_markets(
    provider: PlayerGoalsProvider,
    *,
    wanted: dict[tuple[str, str], object],
    fresh_until: dict[tuple[str, str], bool] | None = None,
    max_events: int | None = None,
    reserve_credits: int | None = None,
) -> FetchReport:
    """Lấy kèo ghi bàn cho các trận mong muốn, trong ngân sách credit.

    `wanted` khoá theo (tên đội nhà đã chuẩn hoá, tên đội khách đã chuẩn hoá) —
    việc khớp sang fixture id do tầng ingestion làm, vì chỉ nó biết bảng `teams`.
    `fresh_until[key] = True` nghĩa là dữ liệu của trận đó còn mới, bỏ qua để tiết
    kiệm credit.
    """
    from app.providers.probability import normalise_team

    max_events = settings.odds_player_goals_max_fixtures if max_events is None else max_events
    reserve = settings.odds_player_goals_reserve_credits if reserve_credits is None else reserve_credits
    fresh_until = fresh_until or {}
    report = FetchReport(enabled=True)

    try:
        events = provider.list_events()
    except Exception as exc:                      # noqa: BLE001 - mọi lỗi là "không có dữ liệu"
        report.errors.append(f"danh sách trận: {str(exc)[:160]}")
        report.detail = "Không lấy được danh sách trận từ The Odds API"
        return report

    report.requests_remaining = provider.requests_remaining
    report.requests_used = provider.requests_used

    # Chỉ những trận ta thực sự cần, xếp theo giờ đá gần nhất trước.
    picked: list[dict] = []
    for ev in events:
        key = (
            normalise_team(ev.get("home_team") or ""),
            normalise_team(ev.get("away_team") or ""),
        )
        if key not in wanted:
            continue
        if fresh_until.get(key):
            report.skipped_fresh += 1
            continue
        picked.append(ev)
    picked.sort(key=lambda e: e.get("commence_time") or "")

    for ev in picked:
        if len(report.events) >= max_events:
            report.skipped_budget += 1
            continue
        rem = provider.requests_remaining
        if rem is not None and rem <= reserve:
            # Dừng hẳn: phần kèo đội quan trọng hơn vì nó đổi λ của mọi cầu thủ.
            report.skipped_budget += 1
            continue
        try:
            market = provider.event_goal_market(ev)
        except Exception as exc:                  # noqa: BLE001
            report.errors.append(f"{ev.get('home_team')} vs {ev.get('away_team')}: {str(exc)[:120]}")
            continue
        # Phản hồi rỗng không bị tính credit (theo tài liệu), nên chỉ cộng khi có giá.
        if market and market.quotes:
            report.credits_used += 1
            report.events.append(market)
        elif market:
            report.events.append(market)

    report.requests_remaining = provider.requests_remaining
    report.requests_used = provider.requests_used
    n_quotes = sum(len(e.quotes) for e in report.events)
    report.detail = (
        f"{len(report.events)} trận, {n_quotes} giá cầu thủ-nhà cái, "
        f"{report.credits_used} credit · bỏ qua {report.skipped_fresh} trận còn mới, "
        f"{report.skipped_budget} trận vì ngân sách"
        + (
            f" · quota còn {report.requests_remaining}"
            if report.requests_remaining is not None
            else ""
        )
    )
    return report
