# ⚡ FPL Edge VN

**Website hỗ trợ quyết định Fantasy Premier League dựa trên dữ liệu** — expected points, expected minutes, mô phỏng Monte Carlo và tối ưu đội hình bằng quy hoạch nguyên (MILP).

> Sản phẩm độc lập của người hâm mộ. Không liên kết với Premier League hoặc Fantasy Premier League. Mọi dự báo đều kèm **mức độ tin cậy** — không phải lời khẳng định chắc chắn.

---

## Triết lý (điều quan trọng nhất)

Hệ thống **tách bạch expected points khỏi ý kiến đám đông**. Mọi khuyến nghị dựa trên:

- Khả năng & số phút ra sân kỳ vọng (xMins) — biến số hạng nhất, không phải nhãn phụ.
- Dữ liệu tấn công/phòng ngự nền tảng (xG, xA, def. contribution) có **Bayesian shrinkage**.
- Độ khó lịch riêng cho tấn công và phòng ngự, sân nhà/khách, mô hình Poisson theo sức mạnh đội.
- Vai trò penalty/set-piece, nguy cơ xoay tua, chấn thương/treo giò, Blank/Double GW.
- Giá trị trên mỗi triệu, cấu trúc đội, số free transfer & giá trị giữ lại, chi phí hit.

Những điều hệ thống **không** làm: không xếp hạng bằng tổng điểm đơn thuần hay form 3–5 trận; không coi ownership là bằng chứng "cầu thủ tốt"; không khẳng định chắc chắn ai sẽ ghi bàn/giữ sạch lưới/đá chính.

---

## Tính năng

| Trang | Mô tả |
|---|---|
| **Dashboard** | GW hiện tại, đếm ngược deadline (giờ VN), top xP, captain, cảnh báo chấn thương, Blank/Double, độ mới dữ liệu |
| **My Team** | Nhập FPL Team ID → tải squad, phân tích điểm mạnh/yếu, tối ưu vòng tới |
| **Long-term Planner** | Kế hoạch 3–8 vòng, 3 chiến lược (an toàn / cân bằng / mạo hiểm), tính giá trị giữ FT & hit |
| **Free Hit Lab** | Tối ưu 1 vòng, 3 chế độ (Max EP / Balanced / Aggressive), sơ đồ sân |
| **Captaincy** | Xếp hạng captain theo EV, floor/ceiling, P(haul), Effective Ownership |
| **Player Explorer** | Bảng lọc theo xP/xMins/giá trị/rủi ro; trang chi tiết có biểu đồ + phân rã xP |
| **Fixture Ticker** | Độ khó tấn công & phòng ngự riêng, 1–8 vòng, projected goals & CS% |
| **News & Injuries** | Chấn thương/treo giò kèm mức ảnh hưởng, nguồn, thời gian, trạng thái xác nhận |
| **Expert Consensus** | Chuyển nhận định thành tín hiệu có điểm tin cậy, chống "echo chamber" |
| **Methodology** | Minh bạch mô hình: dùng gì, không dùng gì, giới hạn |

---

## Kiến trúc

```
Next.js (App Router, TS, Tailwind, Recharts)   ──/api proxy──►   FastAPI
  vi/en · dark mode · responsive                                   │
                                                                   ▼
                              ┌────────────────────────────────────────────┐
                              │ Ingestion  → PostgreSQL/SQLite ← Projection │
                              │ (FPL API)     (SQLAlchemy)       Engine     │
                              │                                  (xMins/xP/ │
                              │                                   MonteCarlo)│
                              │                    │                        │
                              │                    ▼                        │
                              │            Optimizer (PuLP/CBC MILP)        │
                              │  squad · XI · captain · free-hit · plan     │
                              └────────────────────────────────────────────┘
```

Chi tiết: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/METHODOLOGY.md](docs/METHODOLOGY.md) · [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md)

**Stack:** Next.js 15 · TypeScript · Tailwind · Recharts — FastAPI · SQLAlchemy 2 · PuLP/CBC · NumPy · PostgreSQL/SQLite · Redis (tùy chọn) · Docker.

---

## Chạy nhanh (local, không cần Docker)

Yêu cầu: **Python 3.11+** và **Node 18+**.

### 1) Backend

```bash
cd backend
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate
pip install -r requirements.txt

# Lần đầu chạy sẽ tự đồng bộ dữ liệu FPL thật (cần internet, ~30s)
uvicorn app.main:app --reload --port 8000
```

Backend chạy tại http://localhost:8000 · tài liệu API: http://localhost:8000/docs

**Không có internet?** Dùng dữ liệu demo offline (có nhãn mock):

```bash
python -m app.cli seed-demo
```

**Đồng bộ thủ công / dựng lại projection:**

```bash
python -m app.cli sync          # kéo FPL + tính projection
python -m app.cli project       # chỉ tính lại projection
```

### 2) Frontend

```bash
cd frontend
npm install
npm run dev
```

Mở http://localhost:3000. Frontend proxy `/api` sang backend (đặt `BACKEND_URL` trong `frontend/.env.local`).

---

## Chạy bằng Docker (đầy đủ Postgres + Redis)

```bash
cp .env.example .env      # điền ODDS_API_KEY nếu có
docker compose up --build
```

- Frontend: http://localhost:3000
- Backend: http://localhost:8000
- Backend tự đồng bộ dữ liệu FPL khi khởi động lần đầu (DB rỗng).

---

## Triển khai production (chi phí thấp)

📘 **Hướng dẫn từng bước để có link 24/7 gửi bạn bè:** [docs/DEPLOY.md](docs/DEPLOY.md)

- **Frontend → Vercel:** import thư mục `frontend`, đặt env `NEXT_PUBLIC_API_URL` = URL backend.
- **Backend → Railway / Render / Fly.io:** build từ `backend/Dockerfile`, đặt `DATABASE_URL`, `REDIS_URL`, `ODDS_API_KEY`.
- **PostgreSQL → Supabase / Neon:** đặt `DATABASE_URL=postgresql+psycopg://...`.
- Migration: `alembic upgrade head` (hoặc để app tự `create_all` ở dev).
- **Không đặt API key trong mã nguồn** — tất cả qua biến môi trường.

---

## Biến môi trường chính

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./fpl_edge.db` | SQLite (dev) hoặc Postgres (prod) |
| `REDIS_URL` | *(trống)* | Trống → cache in-memory |
| `AUTO_SYNC_ON_STARTUP` | `true` | Tự sync khi DB rỗng |
| `ENABLE_SCHEDULER` | `false` | Bật job làm mới định kỳ 6h |
| `PROJECTION_HORIZON` | `8` | Số vòng dự báo |
| `MONTECARLO_ITERATIONS` | `10000` | Số lần mô phỏng |
| `ODDS_API_KEY` | *(trống)* | Provider tỷ lệ; trống → model nội bộ ("model estimate") |

Xem đầy đủ: [backend/.env.example](backend/.env.example) · [frontend/.env.example](frontend/.env.example)

---

## API nội bộ (trích)

```
GET  /api/gameweek/current          GET  /api/captains
GET  /api/players                   GET  /api/news
GET  /api/players/{id}              GET  /api/expert-consensus
GET  /api/players/{id}/projections  POST /api/team/import
GET  /api/fixtures                  POST /api/team/analyze
GET  /api/fixtures/ticker           POST /api/optimizer/next-gameweek
GET  /api/model/health              POST /api/optimizer/long-term
GET  /api/sources/health            POST /api/optimizer/free-hit
POST /api/admin/refresh             POST /api/optimizer/wildcard
```

Tài liệu tương tác (Swagger): `/docs`.

---

## Kiểm thử

```bash
cd backend && pytest         # 22 test: luật squad, optimizer, engine, API
cd frontend && npm run build # kiểm tra type + build
```

Test khẳng định mọi đầu ra solver là **squad hợp lệ** (ngân sách, 2/5/5/3, tối đa 3/CLB, sơ đồ hợp lệ).

---

## Đối chiếu tiêu chí nghiệm thu (spec §24)

- ✅ Nhập FPL Team ID, tải đúng squad (giá/vị trí/CLB)
- ✅ Solver tuân thủ luật FPL hiện tại (có unit test)
- ✅ Free Hit tạo squad hợp lệ · Long-term tạo ≥3 kế hoạch
- ✅ Mỗi khuyến nghị có số liệu + lý do · có xP/xMins/confidence
- ✅ Hiển thị độ mới dữ liệu · cảnh báo nguồn lỗi/cũ
- ✅ Không dùng dữ liệu tương lai trong backtest (chống leakage — xem Methodology)
- ✅ Responsive điện thoại · không có API key trong source · có README

---

## Lộ trình

- **Giai đoạn 1 (xong):** FPL API, import squad, Explorer, Ticker, xMins/xP, captain, Next-GW/Free-Hit/Long-term optimizer, hiển thị nguồn.
- **Giai đoạn 2:** Understat, provider tỷ lệ, expert RSS thật, Monte Carlo nâng cao, backtest dashboard.
- **Giai đoạn 3:** Wildcard optimizer, mini-league strategy, price-change prediction, thông báo Telegram/Email, AI assistant.

---

## Pháp lý

Chỉ dùng **FPL API công khai**; không vượt paywall, không scrape nội dung cần đăng nhập, không sao chép nguyên bài viết. Ảnh cầu thủ dùng CDN chính thức nếu có, nếu không dùng avatar chữ. Đây là sản phẩm độc lập, không giả danh trang chính thức.
