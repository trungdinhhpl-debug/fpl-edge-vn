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

`app/providers/expert_provider.py` (danh bạ) + `app/services/experts.py` (phân tích).

### Gộp tiếng vọng trước, rồi mới tính đồng thuận

Đếm bài đăng là lỗi nguy hiểm nhất ở khu vực này. Nhiều tài khoản dẫn lại **một**
phát biểu là MỘT bằng chứng được lan truyền, không phải nhiều người cùng đồng ý.

```
8 bài đăng nhắc A
→ 3 nguồn độc lập (2 tài khoản dẫn lại cùng một phát biểu của HLV)
→ đồng thuận thực 61%, không phải 8/8
```

`ExpertSignal.origin_ref` là thứ làm được điều đó: các tín hiệu truy về cùng một
phát biểu gốc dùng chung một `origin_ref`, nên cả nhóm chỉ được **một phiếu**,
mang trọng số của thành viên đáng tin nhất. Giao diện hiện cả hai con số — đồng
thuận thực và "đếm thô" — để thấy rõ mức chênh.

Trọng số theo **lĩnh vực**, không theo âm lượng: giỏi dự đoán đội hình không nói
lên gì về kế hoạch chip. Năm lĩnh vực: `injury`, `lineup`, `chip_planning`,
`captaincy`, `statistics`. Số người theo dõi **không** phải đầu vào.

Ý kiến trái chiều được **nêu riêng**, không bị bình quân hoá cho biến mất.

### Độ chính xác phải kiếm được, không được gán

Bảng `expert_track_record` lưu từng dự đoán cụ thể rồi chấm điểm khi có kết quả.
Độ chính xác trên trang **chỉ** đến từ đó. Dưới 10 dự đoán đã chấm, nguồn hiện
"chưa đủ dữ liệu" chứ không hiện con số.

> **Lý do:** các nguồn trong danh bạ là người và tổ chức **có thật**. Bản trước
> ship sẵn `historical_accuracy` 0.72–0.80 và nhãn `verified` cho họ — những con
> số chưa từng đo, tức là gán một tuyên bố về hiệu suất cho người có danh tính.
> Nay tất cả đặt về 0.0/False và chỉ tăng khi có bằng chứng.
>
> `reliability` còn lại là tiên nghiệm theo **loại** nguồn (toà soạn có quy trình
> đính chính so với diễn đàn ẩn danh), không phải đánh giá cá nhân.
>
> **Thứ hạng FPL để trống**: FPL không có API xác thực thứ hạng của tài khoản bên
> thứ ba, nên chép lại con số tự khai là không kiểm chứng được.

> Signal demo mang nhãn `is_mock=true` và được gán cho **nguồn demo tổng hợp**
> ("Nguồn demo A/B/C…"). Bản trước đặt lời vào miệng người thật ("Ben Crellin:
> captain Salah") — dữ liệu minh hoạ tuyệt đối không được làm vậy.
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
