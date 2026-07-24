# Triển khai công khai 24/7 (miễn phí)

Mục tiêu: có 1 link `https://...` gửi cho bạn bè, sống kể cả khi tắt máy.

Kiến trúc deploy:
```
Vercel (frontend Next.js)  ──HTTPS──►  Render/Railway (backend FastAPI)  ──►  Neon/Supabase (Postgres)
```

> Bạn tự thao tác các bước tạo tài khoản/bấm deploy (cần tài khoản của bạn).
> Code đã được chuẩn bị sẵn để chạy trơn: tự chuẩn hoá `DATABASE_URL`, tự nghe cổng `$PORT`,
> sync dữ liệu chạy nền để không timeout.

---

## Bước 0 — Đưa code lên GitHub

```bash
cd "D:\claude ai\fpl-planner"
git init
git add .
git commit -m "FPL Edge VN"
```

Tạo repo mới trên github.com (Private cũng được) rồi:

```bash
git remote add origin https://github.com/<tên-bạn>/fpl-edge-vn.git
git branch -M main
git push -u origin main
```

`.gitignore` đã loại `.env`, `.venv`, `node_modules`, `*.db` — secrets không bị đẩy lên.

---

## Bước 1 — Database (Neon, miễn phí, không hết hạn)

1. Vào https://neon.tech → đăng ký → **Create project**.
2. Copy **connection string** (dạng `postgresql://user:pass@ep-xxx.neon.tech/dbname?sslmode=require`).
3. Giữ lại chuỗi này cho Bước 2.

*(Supabase cũng được: Project → Settings → Database → Connection string → URI.)*

---

## Bước 2 — Backend (Render, dùng blueprint có sẵn)

**Cách nhanh (blueprint):**
1. https://render.com → đăng ký → **New → Blueprint** → chọn repo vừa push.
2. Render đọc `render.yaml`, tạo sẵn web service + Postgres. Bấm **Apply**.
3. (Tuỳ chọn) Nếu muốn dùng Neon thay Postgres của Render: vào service → **Environment** →
   sửa `DATABASE_URL` = chuỗi Neon ở Bước 1.
4. Chờ build xong. URL backend dạng `https://fpl-edge-backend.onrender.com`.
5. Kiểm tra: mở `https://fpl-edge-backend.onrender.com/api/health` → thấy `{"status":"ok"}`.
   Lần đầu backend tự sync dữ liệu FPL (~1 phút, chạy nền) — chờ rồi thử `/api/model/health`.

**Cách khác — Railway:** New Project → Deploy from GitHub → chọn repo →
Root Directory = `backend` → thêm Postgres plugin (tự tạo `DATABASE_URL`) →
đặt thêm `AUTO_SYNC_ON_STARTUP=true`, `CORS_ORIGINS=https://<app>.vercel.app`.

---

## Bước 3 — Frontend (Vercel)

1. https://vercel.com → đăng ký → **Add New → Project** → chọn repo.
2. **Root Directory:** chọn `frontend` (quan trọng — code frontend nằm trong thư mục con).
3. **Environment Variables**, thêm:

   | Key | Value |
   |---|---|
   | `NEXT_PUBLIC_API_URL` | `https://fpl-edge-backend.onrender.com` (URL backend Bước 2) |

4. **Deploy**. Xong sẽ có URL dạng `https://fpl-edge-vn.vercel.app` → **đây là link gửi bạn bè**.

---

## Bước 4 — Nối CORS (cho backend chấp nhận frontend)

Quay lại Render/Railway → sửa biến `CORS_ORIGINS` = URL Vercel thật của bạn
(vd `https://fpl-edge-vn.vercel.app`) → service tự deploy lại.

Xong. Mở link Vercel, thử trang Free Hit / Dashboard.

---

## Lưu ý free tier

- **Render free**: web service ngủ sau ~15 phút không dùng; request đầu đánh thức (~50s cold start).
  Postgres free của Render hết hạn sau 90 ngày → nên dùng **Neon** (không hết hạn) cho DB.
- **Vercel free**: đủ dùng cho frontend tĩnh + proxy.
- Muốn luôn bật (không ngủ): nâng cấp gói backend, hoặc dùng Fly.io.

## Cập nhật dữ liệu

- Backend tự sync khi khởi động (DB rỗng). Sync lại thủ công: gọi `POST /api/admin/refresh`
  (nút **Sync** trên Dashboard), hoặc bật `ENABLE_SCHEDULER=true` để tự làm mới mỗi 6h.

## Cập nhật code sau này

Chỉ cần `git push` lên `main` — Vercel và Render tự build lại.
