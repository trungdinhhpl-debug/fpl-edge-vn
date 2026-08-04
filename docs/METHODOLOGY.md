# Phương pháp mô hình — FPL Edge VN

## 1. Expected Minutes (xMins)

Đầu vào: số trận đá chính & số phút mùa này, phút các trận gần nhất (nếu có),
trạng thái ra sân (`status`), `chance_of_playing_next_round`, congestion (DGW).

```
avail = f(status, chance)                 # a→1.0, d→chance/50%, i/s/u→chance/0%
start_signal = 0.6·recent_start + 0.4·season_start_rate
P(start)  = clamp(start_signal · avail, 0, 0.985)
P(appear) = clamp(appear_signal · avail, P(start), 0.99)
P(sub)    = P(appear) − P(start)
xMins     = P(start)·84' + P(sub)·20'      # ×2 nếu Double GW
```

Đầu ra kèm `P(start)`, `P(sub)`, `P(no_play)`, `P(≥60')`, khoảng tin cậy, và
**lý do chính** (nailed / rotation / availability / congestion). Confidence:
`High` khi mẫu lớn & ổn định, `Low` khi có cờ chấn thương hoặc mẫu nhỏ.

## 2. Expected Points (xP) — theo vị trí

```
xP = appearance + goals + assists + clean_sheet + saves + bonus + defcon
     − cards − conceded
```

Tỷ lệ per-90 được **Bayesian shrinkage** về mức nền theo vị trí:

```
rate_shrunk = w·(season_rate) + (1−w)·prior,   w = minutes / (minutes + 540)
```

rồi nhân với `xMins/90` và hệ số độ khó trận:

```
fixture_adj = clamp(λ_team_goals(fixture) / λ_team_goals(season_avg), 0.4, 1.8)
goal_EV     = xg90_shrunk · (xMins/90) · fixture_adj · pen_bump · goal_pts[pos]
assist_EV   = xa90_shrunk · (xMins/90) · fixture_adj · 3
appearance  = P(≥60')·2 + (P(appear) − P(≥60'))·1
clean_sheet = e^(−λ_conceded) · P(≥60') · cs_pts[pos]
conceded    = (λ_conceded / 2) · (−1) · P(≥60')          # GK/DEF
saves_EV    = (saves90 · xMins/90 · shot_adj) / 3         # GK
defcon_EV   = 2 · P(actions ≥ ngưỡng)                     # Poisson, luật 2025/26
bonus_EV    = 0.35·involvement + 0.0016·bps90·(xMins/90)  # ≤ 3
```

**Luật tính điểm** đọc từ `app/scoring.py` (mùa hiện tại 2025/26, gồm
Defensive Contribution) — không hard-code luật mùa cũ.

`λ_team_goals` và `λ_conceded` từ mô hình sức mạnh đội (`team_strength.py`):
kết hợp strength ratings của FPL với xG/xGA thực nghiệm, blend theo số trận đã đá
(shrinkage), có điều chỉnh sân nhà/khách (Poisson).

### 2b. Nghịch đảo kèo nhà cái → λ mỗi đội (`providers/probability.py`)

Vòng nào có kèo thì λ được **khớp đồng thời hai tham số** với cả ba thị trường
trên **một** ma trận tỷ số, thay vì giải lần lượt từng thị trường:

```
min over (λ_home, λ_away):
      w1 · MSE( 1X2 )  +  w2 · MSE( tài/xỉu )  +  w3 · MSE( kèo châu Á )
→ team expected goals = λ_home, λ_away
```

Ma trận tỷ số dùng **Dixon–Coles** (1997):

```
P(i,j) = τ(i,j) · Poisson(i; λ_home) · Poisson(j; λ_away)

τ(0,0) = 1 − λ_home·λ_away·ρ     τ(0,1) = 1 + λ_home·ρ
τ(1,0) = 1 + λ_away·ρ            τ(1,1) = 1 − ρ        τ = 1 với mọi tỷ số khác
```

ρ < 0 (mặc định −0.13) **nâng 0-0 và 1-1, hạ 1-0 và 0-1** — đúng chỗ mà Poisson
độc lập sai nhiều nhất: nó định giá hụt các trận hòa ít bàn. ρ **không** được
khớp: giá của một trận đơn lẻ không đủ để nhận dạng ρ (bài gốc ước lượng từ cả
mùa), nên nó là hằng số cấu hình — vì vậy bài toán chỉ có **hai tham số**.

Vài điểm cần biết:

- **τ bảo toàn phân phối biên.** Vì `P(1;μ) = μ·P(0;μ)`, hiệu chỉnh ở ô 0-0 và
  0-1 triệt tiêu nhau khi cộng theo hàng. Nghĩa là `λ` vẫn đúng là kỳ vọng bàn
  thắng của mỗi đội (không phải xấp xỉ), và `clean sheet = exp(−λ_thủng lưới)` ở
  `xpoints.py` vẫn nhất quán — Dixon–Coles chỉ đổi **quan hệ phụ thuộc** giữa hai
  đội, tức đúng phần mà 1X2 / tài xỉu / chấp cần tới.
- **Kèo chấp và kèo tài/xỉu mức nguyên hoàn tiền khi hòa vốn (push).** Giá đã khử
  vig của chúng vì thế là xác suất **có điều kiện không push**; phía mô hình cũng
  được lấy điều kiện y hệt trước khi tính sai số. Mức lẻ 1/4 (−0.25, −0.75) tách
  đôi sang hai mức kề, đúng cách nhà cái quyết toán.
- **Thiếu thị trường nào thì bỏ hẳn thị trường đó**, không thay bằng giá trị mặc
  định. Hai xác suất tự do của 1X2 đã đủ nhận dạng chính xác hai tham số, và đủ
  ổn định: lệch 1 điểm % ở giá hòa chỉ làm tổng bàn đổi ~0.18. Bản cũ ghim tổng
  bàn về mức trung bình giải mỗi khi thiếu kèo tài/xỉu — như vậy là vứt đi thông
  tin thật.
- Mỗi mức kèo là **một quan sát riêng**; giá được trung bình theo từng mức chứ
  không trộn giữa các mức khác nhau. Mức nào quá ít nhà cái treo so với mức chính
  thì loại.
- Sai số khớp cuối cùng được ghi vào log đồng bộ. Sai số lớn nghĩa là ba thị
  trường mâu thuẫn nhau nhiều hơn mức một mô hình tỷ số có thể dung hòa.

## 3. Monte Carlo

Mô phỏng ở cấp **trận đấu của đội** để giữ tương quan:

```
team_goals    ~ Poisson(λ_for)      # dùng chung cho các cầu thủ cùng đội
team_conceded ~ Poisson(λ_against)  # clean sheet dùng chung GK + hậu vệ
player_goals  ~ Binomial(team_goals, share_goal)   # share = xG cầu thủ / xG đội
```

→ phân phối điểm mỗi cầu thủ: mean, median, P25/P75/P90, ceiling (P95),
P(blank ≤2), P(≥5), P(≥10 = haul), phương sai. Không giả định cầu thủ độc lập.

## 4. Rủi ro (3 chỉ số)

- **Minutes risk:** từ `P(start)`, `P(no_play)`, cờ trạng thái.
- **Performance risk:** mẫu nhỏ + phụ thuộc bàn thắng + vượt xG (regression) + phương sai.
- **Structural risk:** đánh giá ở cấp đội hình (tập trung 1 CLB, ngân sách, bench yếu).

Tổng hợp → Low / Medium / High / Very High.

## 5. Tối ưu đội hình (MILP — PuLP/CBC)

### Squad / Free Hit / Wildcard (1 mục tiêu)

```
max   Σ value·start + Σ cap_value·cap + bench_w·Σ value·(squad−start)
s.t.  Σ squad = 15;  theo vị trí = {GK2, DEF5, MID5, FWD3}
      Σ start = 11;  sơ đồ: GK=1, DEF∈[3,5], MID∈[2,5], FWD∈[1,3]
      Σ cap = 1;  cap ≤ start ≤ squad
      Σ price·squad ≤ budget;  mỗi CLB ≤ 3
```

Chế độ Free Hit đổi vector `value`: `max_ep` = xP; `balanced` = xP − risk; `aggressive` = xP + 0.5·(ceiling − xP), captain dùng ceiling.

### Next-GW transfer

Thêm biến `hits ≥ n_transfers − free_transfers`, trừ `4·hits` vào mục tiêu; so sánh **hành động ngay vs giữ (roll)**.

### Long-term (đa vòng, spec §9)

```
max  Σ_t discount^t·(XI_t + cap_t + bench_w·bench_t) − 4·Σ hits_t + 0.6·FT_cuối
s.t. chuyển trạng thái own[p,t] − own[p,t−1] = tin − tout
     banking free transfer: FT_t ≤ FT_{t−1} − transfers_{t−1} + 1, ≤ 5
     ràng buộc squad/sơ đồ/ngân sách/CLB mỗi vòng
```

Ba chiến lược **safe/balanced/aggressive** khác nhau ở `risk_weight`,
`ceiling_weight`, `discount`, `max_hits`. Universe rút gọn (current squad + top N/vị trí) để giải nhanh; CBC có time limit.

## 6. Backtest & chống data leakage (spec §18)

- Dự báo GW *t* chỉ dùng dữ liệu có **trước deadline** GW *t* (`data_cutoff`).
- Đánh giá: MAE, RMSE, Spearman rank, Brier (goal/CS), calibration, captain hit-rate,
  top-10 precision, tỷ lệ cầu thủ không ra sân.
- So baseline: tổng điểm mùa, form chính thức, FDR chính thức, ownership cao nhất, "không chuyển nhượng".

## 7. Giới hạn đã biết

- Selling price của manager không có trên API công khai → xấp xỉ bằng giá hiện tại.
- Free transfer banked ước lượng từ lịch sử (người dùng có thể chỉnh tay).
- Khi chưa cấu hình provider tỷ lệ, xác suất là **model estimate**, không phải giá thị trường.
- Expert signals mặc định là **mock có nhãn**; thay bằng RSS/API hợp lệ để dùng thật.
