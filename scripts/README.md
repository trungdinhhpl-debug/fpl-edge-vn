# Cảnh báo deadline qua Telegram

`deadline_alert.py` theo dõi trạng thái cầu thủ trong đội bạn và nhắn Telegram khi có
gì đổi: chấn thương mới, treo giò, khả năng ra sân tụt, giá đổi — cộng một lần nhắc
trước hạn chót.

Nó **không cần backend, không cần database, không cần cài gói nào**: chỉ thư viện
chuẩn của Python và FPL API công khai. Lý do là độ tin cậy — backend nằm trên gói
Render miễn phí và ngủ sau 15 phút không ai dùng, nên đặt báo động vào trong nó là
đặt báo động vào thứ hay ngủ nhất hệ thống.

## 1. Tạo bot Telegram

1. Nhắn `/newbot` cho [@BotFather](https://t.me/BotFather), đặt tên → nhận **token**.
2. Nhắn một câu bất kỳ cho bot vừa tạo (bot không nhắn trước được cho người lạ).
3. Lấy **chat id**: mở `https://api.telegram.org/bot<TOKEN>/getUpdates`, tìm `"chat":{"id":...}`.

## 2. Chạy thử — không gửi gì cả

```bash
python deadline_alert.py --watch "Haaland,B.Fernandes,Saliba" --dry-run
```

`--dry-run` in ra màn hình, **không gửi Telegram và không ghi state** — chạy bao nhiêu
lần cũng không ảnh hưởng lần chạy thật.

## 3. Chọn nguồn danh sách cầu thủ

| Cách | Khi nào dùng |
|---|---|
| `--watch "Tên1,Tên2"` hoặc id | **Trước vòng 1**, hoặc khi vòng đang diễn ra |
| `--team-id 1234567` | Sau khi có ít nhất một vòng đã kết thúc |

FPL chỉ mở đội hình của một người **sau khi vòng đấu kết thúc**, nên trước vòng 1
không có cách nào đọc đội của bạn từ API công khai. Watchlist gõ tay là đường đi
chính thức trong giai đoạn đó, không phải đường phụ.

## 4. Đặt lịch trên Windows

```powershell
schtasks /create /tn "FPL deadline alert" /tr "python \"D:\claude ai\fpl-planner\scripts\deadline_alert.py\" --team-id 1234567" /sc minute /mo 30
```

Đặt token vào biến môi trường người dùng (một lần):

```powershell
setx TELEGRAM_BOT_TOKEN "123456:ABC..."
setx TELEGRAM_CHAT_ID "987654321"
```

Chạy 30 phút một lần là đủ dày: tin đội hình thường ra trong buổi họp báo chiều thứ
Sáu, và cái bạn cần là biết trong vòng nửa tiếng chứ không phải trong vòng nửa phút.

## Nó gửi cái gì

```
⚽ FPL · GW1
Hạn chót: 00:30 22/08 giờ VN (còn 2.4 giờ)
Theo dõi 15 cầu thủ · đội hình GW1

Thay đổi từ lần kiểm tra trước:
🔴 Saliba: sẵn sàng → chấn thương; khả năng ra sân không cảnh báo → 0%; tin: Back injury
• Haaland: giá tăng £15.4 → £15.5
```

🔴 = mất hẳn suất đá (chấn thương, treo giò, không thi đấu) hoặc khả năng ra sân ≤ 50%.

## Vài điều cần biết

- **Lần chạy đầu không báo động gì** — chưa có mốc cũ để so sánh, nó chỉ ghi nhận
  trạng thái hiện tại.
- **State chỉ ghi khi Telegram gửi thành công.** Ghi trước là cách chắc chắn nhất để
  nuốt mất đúng cảnh báo quan trọng nhất.
- **Nhắc hạn chót chỉ một lần mỗi vòng** (mặc định khi còn dưới 3 giờ, đổi bằng
  `--hours-before`).
- File state nằm cạnh script (`.deadline_alert_state.json`). Xoá nó = quên hết,
  lần chạy sau coi như lần đầu.
