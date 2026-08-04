# Nguồn dữ liệu — FPL Edge VN

Thứ tự ưu tiên & trọng số tin cậy (spec §3–§5). Dữ liệu chính thức luôn có trọng số cao nhất.

## Cấp 1 — Chính thức (trọng số cao nhất)

**FPL API công khai** (`app/providers/fpl_client.py`):

| Endpoint | Dùng cho |
|---|---|
| `/bootstrap-static/` | cầu thủ, đội, gameweeks, luật, giá, ownership, xG/xA, def. contribution |
| `/fixtures/` | lịch, độ khó, kết quả, blank/double |
| `/element-summary/{id}/` | lịch sử từng vòng (tùy chọn, `SYNC_PLAYERS_DETAIL`) |
| `/event/{gw}/live/` | điểm live khi có trận |
| `/entry/{id}/`, `/…/picks/`, `/…/history/`, `/…/transfers/` | import đội người chơi |

Mọi lần fetch ghi `source_fetch_logs` (source, url, status, rows, fetched_at) để
UI hiển thị độ mới và cảnh báo nguồn lỗi/cũ.

## Cấp 2 — Thống kê (tùy chọn)

- **The Odds API** (`probability.py`): 1X2 (`h2h`), tài/xỉu (`totals`) và kèo châu
  Á (`spreads`). Cả ba được khớp đồng thời ra `λ` mỗi đội trên một ma trận tỷ số
  Dixon–Coles — xem METHODOLOGY §2b. Có key → dùng thật; không có → model Poisson
  nội bộ, **gắn nhãn `model_estimate`** (không giả làm giá thị trường).
  *Hạn mức:* mỗi request tốn (số thị trường × số region) credit, nên thêm kèo chấp
  đưa một lần đồng bộ từ 2 lên 3 credit. Đặt `ODDS_INCLUDE_HANDICAP=false` để quay
  lại 2 (khi đó chỉ khớp 1X2 + tài/xỉu).
- **Understat** (Phase 2): xG/npxG/xA nâng cao. Hiện dựa vào xG/xA của chính FPL để tự chứa & hợp pháp.

## Cấp 3 — Phân tích FPL

Chỉ dùng API/RSS hợp lệ, nội dung công khai, link nguồn, tóm tắt ngắn tự viết.
**Không** scrape paywall, **không** sao chép nguyên bài.

### Tầng nguồn tin đội hình (`services/news_tiers.py`)

Mọi tin đều được xếp vào một trong sáu tầng theo **mức trực tiếp của bằng chứng**.
Chỉ tầng có nguồn thật mới được điền; các tầng còn lại khai báo rõ `configured:
false` kèm thứ cần có để lấp, thay vì hiện rỗng và ngầm ám chỉ là "không có tin".

| # | Tầng | Tin cậy | Nguồn hiện tại |
|---|---|---|---|
| 1 | Chính thức từ CLB | 0.98 | FPL status feed ✅ |
| 2 | Họp báo HLV | 0.92 | *chưa có* — cần RSS/API họp báo trước trận |
| 3 | Nhà báo đội bóng | 0.75 | *chưa có* — cần danh sách nhà báo theo CLB + nguồn có bản quyền |
| 4 | Predicted lineup | 0.60 | *chưa có* — `expert_provider` mới chỉ có seed **MOCK**, không dùng làm tin thật |
| 5 | Tin đồn | 0.30 | *chưa có* |
| 6 | Suy luận mô hình | 0.55 | engine xMins ✅ |

Nguồn lạ **mặc định rơi xuống tầng "tin đồn"**: nguồn chưa rõ thì chưa có gì để
tin, và sai lầm đáng sợ ở đây là tin quá nhiều chứ không phải tin quá ít.

## Cấp 4 — Chuyên gia & cộng đồng

`app/providers/expert_provider.py` — roster cấu hình được, mỗi nguồn có:
tên, loại, uy tín, độ chính xác lịch sử, chuyên môn, hệ số độc lập, ngày cập nhật, link.

```
signal_score = reliability × recency × specificity × historical_accuracy × independence
```

- `recency`: nửa đời ~48h (tin sát deadline trọng số cao hơn).
- `independence`: chống **echo chamber** — 20 tài khoản chép 1 nguồn ≠ 20 nguồn độc lập.
- Tín hiệu cộng đồng **không** ghi đè dữ liệu chính thức / tin ra sân đã xác nhận.

> Mặc định các signal là **mock có nhãn** (`is_mock=true`) để minh hoạ UI & toán tin cậy.
> Nối một nguồn RSS/API hợp lệ vào provider này để dùng dữ liệu thật.

## Độ mới & cache (spec §5)

| Loại dữ liệu | Gợi ý tần suất |
|---|---|
| Cầu thủ & fixture | định kỳ (mặc định 6h nếu bật scheduler) |
| Chấn thương | dày hơn khi gần deadline |
| Live | chỉ khi đang có trận |
| Expert signals | ưu tiên 48h trước deadline |

Không gọi API liên tục khi dữ liệu chưa đổi (có cache TTL). Không đưa khuyến nghị
mạnh khi dữ liệu quan trọng đã quá cũ (UI gắn cờ `stale`).

## Pháp lý

Tuân thủ ToS & robots.txt; không vượt paywall; không scrape tài khoản cần đăng nhập;
không bán lại dữ liệu không có giấy phép; không giả danh trang chính thức PL/FPL.
Ảnh cầu thủ dùng CDN chính thức nếu có, nếu không dùng avatar chữ.
