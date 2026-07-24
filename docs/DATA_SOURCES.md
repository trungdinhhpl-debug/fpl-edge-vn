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

- **The Odds API** (`probability.py`): xác suất trận (h2h). Có key → dùng thật;
  không có → model Poisson nội bộ, **gắn nhãn `model_estimate`** (không giả làm giá thị trường).
- **Understat** (Phase 2): xG/npxG/xA nâng cao. Hiện dựa vào xG/xA của chính FPL để tự chứa & hợp pháp.

## Cấp 3 — Phân tích FPL

Chỉ dùng API/RSS hợp lệ, nội dung công khai, link nguồn, tóm tắt ngắn tự viết.
**Không** scrape paywall, **không** sao chép nguyên bài.

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
