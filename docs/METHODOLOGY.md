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
