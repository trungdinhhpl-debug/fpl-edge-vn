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

### 1b. Chế độ tiền mùa (`services/season_state.py`)

Một dự báo làm trước khi bóng lăn và một dự báo làm giữa tháng 12 không phải cùng
loại tuyên bố, nhưng nếu trang hiển thị giống hệt nhau thì người đọc sẽ coi chúng
như nhau. Phase được tính từ dữ liệu thật và gắn nhãn ở **mọi trang**:

| Phase | Điều kiện | Nhãn | Confidence |
|---|---|---|---|
| `preseason` | 0 trận đã đá | **PRE-SEASON PROJECTION** | Low |
| `early` | đội nhiều nhất < 6 trận | EARLY-SEASON PROJECTION | Medium |
| `established` | từ 6 trận trở lên | (không nhãn) | High |

Tỷ lệ "dựa trên prior" được đếm bằng **phần bù** — số dự báo KHÔNG nhắc tới trận
gần đây — chứ không bằng cách khớp một cụm từ. Bản đầu khớp chuỗi "season
averages" và báo 64% trong tuần chưa đá trận nào; nó bỏ sót cầu thủ đội mới lên
hạng và cầu thủ đang dính cờ chấn thương, vốn cũng hoàn toàn là prior. Đếm phần
bù cho ra **100%**, là con số đúng.

**Nhãn tin cậy của TỪNG cầu thủ phải nói cùng một điều với nhãn của cả hệ thống.**
`risk.confidence_from` từng cộng +0.1 cho "mẫu lớn" khi `minutes_season > 900` —
nhưng trước vòng 1 đó là tổng phút của mùa **trước**, nên phần thưởng được trao cho
một mẫu chưa hề tồn tại. Đo được: 241 cầu thủ nằm đúng ở 0.70 và giao diện gắn
**"Tin cậy: Cao"** cho toàn bộ nhóm ứng viên đội trưởng — ngay cạnh tấm banner của
chính hệ thống ghi *"PRE-SEASON · 100% dựa trên prior · Confidence: Low"*. Hai con
số cùng một trang nói ngược nhau, và người dùng đọc cái nằm cạnh cầu thủ.

Giờ phần thưởng cỡ mẫu phải kiếm được **trong mùa này** (`team_matches_played > 0`),
còn hình phạt mẫu bé thì giữ nguyên — một cầu thủ ít phút mùa trước vẫn thật sự là
ẩn số lớn hơn. Và khi chưa quả bóng nào lăn, độ tin cậy bị chặn dưới ngưỡng "Cao".
Kết quả sau khi sửa: 375 "Trung bình", 192 "Thấp", **0 "Cao"**.

### Giảm trọng số dữ liệu mùa trước

Với `prior_reliability = r`, phần `(1 − r)` số trận mùa trước được **thay bằng
mức nền vị trí**:

```
prior_games = PRIOR_GAMES + games_ref · (1 − r)
start_rate  = (season_starts + prior_games · PRIOR_START_RATE) / (games_ref + prior_games)
```

Chỉ nhân `PRIOR_GAMES` lên là **đòn bẩy quá yếu**: 2 trận tưởng tượng so với mẫu
38 trận chỉ dịch p_start của một trụ cột đổi đội đúng 2 điểm %. Buộc prior tỷ lệ
với chính mẫu thì mới thật sự pha loãng — tân binh từ 0.77 xuống 0.66.

Quan trọng: giảm trọng số kéo về **mức nền theo cả hai chiều**. Trụ cột đổi đội bị
kéo xuống (0.77 → 0.66), còn cầu thủ dự bị đổi đội được kéo **lên** (0.17 → 0.27).
Nếu chỉ có kéo xuống thì đó là hình phạt, không phải tái cân trọng số.

**Ai bị giảm trọng số:** FPL API không công bố huấn luyện viên hay lịch sử chuyển
nhượng, nên `NEW_MANAGER_CLUBS` và `NEW_SIGNING_PLAYERS` phải khai bằng tay.
Danh sách rỗng được báo cáo rõ là **"chưa ai khai"**, không bao giờ hiểu thành
"không có ai đổi đội". Riêng cầu thủ 0 phút Ngoại hạng thì phát hiện tự động và
đã ước lượng theo vai trò/giá từ trước.

## 2. Expected Points (xP) — theo vị trí

```
xP = appearance + goals + assists + clean_sheet + saves + bonus + defcon
     − cards − conceded
```

Tỷ lệ per-90 được **Bayesian shrinkage** về mức nền theo vị trí:

```
rate_shrunk = w·(season_rate) + (1−w)·prior,   w = minutes / (minutes + 540)
```

rồi nhân với `xMins/90`. Hệ số độ khó trận **chỉ áp cho bàn thắng và kiến tạo** —
xem chú thích dưới bảng công thức:

```
fixture_adj = clamp(λ_team_goals(fixture) / λ_team_goals(season_avg), 0.4, 1.8)
goal_EV     = xg90_shrunk · (xMins/90) · fixture_adj · pen_bump · goal_pts[pos]
assist_EV   = xa90_shrunk · (xMins/90) · fixture_adj · 3
appearance  = P(≥60')·2 + (P(appear) − P(≥60'))·1
clean_sheet = e^(−λ_conceded) · P(≥60') · cs_pts[pos]
conceded    = (λ_conceded / 2) · (−1) · P(≥60')          # GK/DEF
saves_EV    = (saves90 · xMins/90 · shot_adj) / 3         # GK
defcon_EV   = 2 · P(actions ≥ ngưỡng)                     # Poisson, KHÔNG có fixture_adj
bonus_EV    = chia quỹ 6 điểm trong nội bộ trận            # xem 2c
```

**Defensive Contribution là luật NGƯỠNG, không phải tỷ lệ.** Hậu vệ đạt ≥ 10 hành
động được 2 điểm; tiền vệ và tiền đạo cần ≥ 12; thủ môn không có. Mô hình vì thế
tính `2 · P(số hành động ≥ ngưỡng)` với số hành động ~ Poisson(dc90 · xMins/90) —
không nhân tỷ lệ, và trần 2 điểm được thoả theo cấu tạo vì xác suất ≤ 1. Điểm và
ngưỡng đọc từ API: `{'DEF': 2, 'MID': 2, 'FWD': 2, 'GKP': 0}`.

Rổ hành động khác nhau theo vị trí — hậu vệ tính CBIT (clearances, blocks,
interceptions, tackles), tiền vệ và tiền đạo tính thêm recoveries (CBIRT). Engine
**không phải tự dựng lại** rổ đó: trường `defensive_contribution` của FPL đã đúng rổ
theo vị trí. Đã kiểm chứng trên dữ liệu mùa 2025/26 (cầu thủ ≥ 1500 phút): hậu vệ
khớp CBI+tackles ở 69/71 trường hợp, tiền vệ khớp CBI+tackles+recoveries ở 87/88,
tiền đạo 19/19, thủ môn luôn bằng 0.

**Hai giới hạn của phần này, nói rõ vì chúng không nhìn ra từ công thức:**

- **Không có điều chỉnh theo đối thủ.** `fixture_adj` chỉ áp cho bàn thắng và kiến
  tạo. Hành động phòng ngự thì chưa phản ứng với sức ép: đo được Gabriel (Arsenal)
  có `defcon_EV = 0.293` **giống hệt nhau ở cả 8 vòng**, gặp Coventry hay Chelsea
  không khác gì. Thực tế hậu vệ bị vây hãm sẽ phá bóng và cản phá nhiều hơn. Sửa
  đúng thì phải dùng một hệ số theo **λ bàn thua** (giống `shot_adj` của thủ môn),
  không phải `fixture_adj` vốn xây trên λ bàn thắng của chính đội mình — dùng nhầm
  hệ số đó sẽ cho kết quả ngược.
- **Poisson có thể quá hẹp.** Số hành động mỗi trận thường phân tán rộng hơn Poisson
  (over-dispersed), mà `P(X ≥ 10)` rất nhạy với đuôi phân phối khi trung bình nằm
  sát ngưỡng. Kiểm được điều này cần số liệu **từng trận** (`player_gameweek_stats`,
  hiện rỗng vì `SYNC_PLAYERS_DETAIL` đang tắt); trước khi có, đây là một giả định
  chưa kiểm chứng chứ không phải một lựa chọn đã đo.

**Luật tính điểm** đọc từ `app/scoring.py`, nạp nguyên văn từ
`bootstrap-static.game_config` của mùa đang chạy (gồm Defensive Contribution) —
không hard-code tên mùa hay điểm từng hạng mục.

**Trọng số BPS thì KHÔNG có trong API** (`game_config.scoring.bps` chỉ là "1 BPS
đổi ra bao nhiêu điểm" = 0), nên chúng nằm trong `app/bps_rules.py` và được đánh
phiên bản theo mùa; phiên bản đang áp được ghi vào bảng `season_rules`
(`bps_rules_version`) cạnh `scoring_rules_version`, `assist_rules_version`,
`chip_rules_version` và mốc `effective_from`.

Điều đó quan trọng vì `bps90` ở công thức trên tính từ tổng BPS **cả mùa** mà FPL
phát ra, và **trước vòng 1 thì tổng đó vẫn là của mùa trước** (kiểm chứng
2026-08-05: Haaland 2953 phút / 239 điểm trong khi hạn vòng 1 là 2026-08-21).
Luật BPS 2026/27 đã đổi, nên số cũ được kiếm theo một thước đo khác:

| Hạng mục | 2025/26 | 2026/27 |
|---|---|---|
| Clearances / blocks / interceptions | 1 BPS mỗi 2 | 1 BPS mỗi **3** |
| Bị đối phương qua người | −1 BPS | **bỏ hạng mục** |
| Cứu thua trong vòng cấm | 3 BPS | 3 BPS |
| Cứu thua ngoài vòng cấm | 2 BPS | gộp vào "cứu thua khác" = 2 BPS |
| Cứu thua từ big chance | — | **+1 BPS** |
| Cứu penalty | 8 BPS | 7 BPS (+1 big chance = 8) |

`app/bps_rules.equivalent_bps()` quy tổng cũ về tương đương luật hiện hành trước
khi vào mô hình bonus. Chỉ thành phần CBI quy đổi được bằng số học từ dữ liệu FPL
công bố (`ΔBPS = CBI·(1/3 − 1/2) = −CBI/6`); hai thay đổi còn lại **không có dữ
liệu để định lượng** (FPL không phát số lần cầu thủ bị qua người, cũng không phát
số big chance thủ môn đã cứu) nên hệ số của chúng để **0** kèm núm điều chỉnh,
chứ không bịa số. Nghĩa là phần bù cho thủ môn và cầu thủ hay rê dắt hiện **chưa
được mô hình hoá** — chỉ phần trung vệ bị hạ là đã sửa.

Không quy đổi khi vòng 1 đã có trận kết thúc: từ lúc đó tổng cả-mùa là số của mùa
này, kiếm theo đúng luật đang áp (`services/season_state.stats_season()`).

### 2e. Chấm 11m tách khỏi bóng sống (`engine/penalties.py`)

**Hai lỗi trong bản cũ.** `expected_goals` mà FPL phát là xG **tổng**, gồm cả chấm
11m, và không có `penalties_scored` cũng không có npxG. Hệ quả:

1. `fixture_adj = λ_for(trận)/nền_giải` co giãn **toàn bộ** xG theo độ khó trận.
   Nhưng một quả 11m đáng 0,79 bàn dù đối thủ là ai — nó không co lại khi gặp Man
   City theo cách một pha dứt điểm bóng sống co lại.
2. `pen_bump` nhân thêm 12% cho người đá 11m số 1 — **đếm hai lần**, vì những quả
   anh ta đã đá vốn đã nằm sẵn trong `expected_goals` của chính anh ta.

**Vì sao KHÔNG tách theo từng cầu thủ.** Dấu vết duy nhất là `penalties_missed`, và
đo trên dữ liệu thật thì nó vô dụng ở cấp cá nhân:

| | đo được |
|---|---|
| Người đá 11m số 1 hỏng **đúng 0 quả** | **15/20** → ước ra 0 quả đã đá |
| B.Fernandes hỏng 2 quả | ước ra 9,5 quả = **70% xG cả mùa** là penalty |

Chia một biến đếm nguyên 0/1/2 cho `1 − 0,79` khuếch đại nhiễu thành một ước lượng
vô nghĩa. Ghi chú giới hạn cũ nói đúng ở điểm này.

**Tách ở tầng có đủ mẫu.** 14 quả hỏng của cả giải trên 760 trận-đội là mẫu dùng
được, nên **tỷ lệ** suy ở cấp giải; **ai đá** thì đọc từ `penalties_order` — dữ liệu
FPL công bố thật; **có mặt hay không** lấy thẳng từ mô hình phút. Người số 2 chỉ
nhận phần `1 − P(số 1 có mặt)`, nên không cần một tỷ lệ chia bịa ra.

```
14 quả hỏng / (1 − 0,79) = 67 quả đã đá → 53 bàn / 760 trận-đội = 0,069 bàn/trận-đội
```

Tỷ lệ này **tính tại chỗ từ DB**, không ghi cứng — mùa sau khác thì nó tự đổi.

**Một con số dùng cho cả hai chiều.** `penalty_xg90()` cho ra xG 11m trên 90 phút
có mặt; nó vừa bị **trừ** khỏi nền lịch sử vừa được **cộng lại** như thành phần
riêng. Tính riêng mỗi phía một kiểu thì phần thừa/thiếu sẽ lặng lẽ chui vào xG bóng
sống.

**Một lỗi đã tạo ra rồi sửa trong chính lần này.** Bản đầu chặn phần trừ ở 45% xG,
lập luận là "bảo vệ người MỚI nhận vai đá 11m". Lập luận sai chiều, và đo được ngay:
Robinson (Fulham, hậu vệ đá 11m, xg90 = 0,065) chỉ bị trừ 0,029 nhưng vẫn được cộng
lại 0,069 — **phồng 62% mối đe doạ ghi bàn từ không khí**, +1,57 xP/8 vòng. Bất đối
xứng thật nằm chỗ khác: với người xG thấp, trừ quá tay gần như vô hại (trừ X rồi
cộng lại xấp xỉ X), còn trừ thiếu thì thổi phồng. Chặn ở chính `xg90` đưa Robinson
về đúng 12,5 — giá trị trước khi động vào.

**Tác động đo được** (8 vòng, sau khi dựng lại toàn bộ dự báo):

| Nhóm | n | Đổi trung bình | Khoảng |
|---|---|---|---|
| Đá 11m số 1 | 19 | **−0,34 xP** | −1,56 … +0,01 |
| Đá 11m số 2 | 19 | −0,64 xP | −2,04 … +0,07 |
| Không đá 11m | 408 | +0,06 xP | +0,00 … +0,46 |

Không một người đá 11m nào tăng — đúng như phải thế khi bỏ một khoản đếm hai lần.
Nhóm không đá 11m nhích lên chút vì bảo toàn bàn thắng: đội vẫn ghi đúng λ, nên
phần bị lấy khỏi người đá 11m chuyển sang đồng đội.

### 2f. Vòng đôi: xoay tua và mệt mỏi

Bản cũ rút hai trận **hoàn toàn độc lập** (đo được tương quan +0,0003). Điều đó
không sai ở kỳ vọng — xP là tổng hai kỳ vọng, độc lập hay không thì tổng không đổi
— nhưng sai ở **phương sai**: `Var = 2p(1−p)` với độc lập, còn `2p(1−p)(1+ρ)` với
tương quan âm. Mô hình cũ vì thế thổi phồng cả **trần lẫn sàn** của cầu thủ vòng
đôi, tức thổi phồng đúng hai con số mà Bench Boost và Triple Captain dựa vào.

Hai kênh tách bạch, nên cộng vào không phải đếm hai lần:

* **Mệt mỏi** dịch **kỳ vọng**. Trận thứ hai bị trừ phút theo số ngày nghỉ (đủ 4
  ngày = không trừ; sàn 0,85). Phút mất đi từ suất đá chính chuyển một phần sang
  suất vào sân từ ghế — người bị rút sớm hiếm khi vắng mặt hẳn. Áp cho **cả** đường
  giải tích lẫn Monte Carlo qua cùng một `est`.
* **Xoay tua** dịch **phương sai**, và giữ nguyên kỳ vọng theo cấu tạo:

```
Cov = ρ·√(p₁(1−p₁)·p₂(1−p₂))
P(đá trận 2 | đã đá trận 1)   = p₂ + Cov/p₁
P(đá trận 2 | đã nghỉ trận 1) = p₂ − Cov/(1−p₁)
```

Cộng lại theo `p₁` luôn ra đúng `p₂` với **mọi** ρ, nên xP không đổi một chút nào.

**Giới hạn khả thi là tính năng, không phải khiếm khuyết.** Cả hai xác suất phải nằm
trong [0,1], nên `|Cov| ≤ min(p₁p₂, (1−p₁)(1−p₂))`. Với trụ cột chắc suất 95% điều
đó chặn khoảng cách ở 0,053 — **không còn chỗ nào để xoay**. Haaland không bị xoay
tua, và điều đó rơi ra từ ràng buộc toán chứ không cần thêm tham số.

Đo trên 200 000 lần mô phỏng:

| p_start | khoảng xoay khả thi | TB độc lập → xoay tua | SD độc lập → xoay tua |
|---|---|---|---|
| 0,95 | −0,053 | 10,06 → 10,07 | 5,364 → 5,382 (**+0,3%**) |
| 0,80 | −0,250 | 8,56 → 8,54 | 5,671 → 5,543 (−2,3%) |
| 0,60 | −0,250 | 6,65 → 6,64 | 5,765 → 5,499 (−4,6%) |
| 0,40 | −0,250 | 4,79 → 4,77 | 5,238 → 4,950 (−5,5%) |

Kỳ vọng đứng yên trong nhiễu; phương sai co lại đúng ở nhóm bị xoay.

**Ba hệ số chưa khớp được từ dữ liệu**, và được đối xử như mọi hệ số khác trong hệ
thống này — nhỏ, có chặn, cấu hình được, và hai đầu mút đều có nghĩa: độ co giãn của
penalty theo độ khó trận (0,5), tương quan xoay tua (−0,25), tỷ lệ vào bóng của chấm
11m (79%, API cho số quả hỏng nhưng không cho số quả vào nên không có mẫu số).

### 2b-bis. Hai đường tính phải nói cùng một điều

xP được tính **giải tích** (`engine/xpoints.py`) còn phân phối đến từ **Monte
Carlo** (`engine/montecarlo.py`). Hai đường mô hình cùng một đại lượng, nên trung
bình phải khớp. Đo được trước khi sửa (GW1, xMins ≥ 20, MC/giải tích): thủ môn 88%,
hậu vệ 107%, tiền vệ 92%, **tiền đạo 79%**.

Phân rã theo thành phần cho thấy bốn nguyên nhân, và **hai bên sai ở hai chỗ khác
nhau** — nên không có bên nào "đúng" để lấy làm chuẩn:

| Thành phần | Bên sai | Sai gì |
|---|---|---|
| Cứu thua, bàn thua | giải tích | FPL đếm theo **mốc trọn** (1 điểm mỗi 3 lần cứu, −1 mỗi 2 bàn). `λ/k` cho hai lần cứu thành 0.67 điểm, luật cho 0. Dùng `E[floor(X/k)] = Σ P(X ≥ jk)` |
| Điểm ra sân | Monte Carlo | trao 2 điểm cho mọi người **đá chính**; luật đòi **đủ 60 phút** |
| Tần suất bonus | Monte Carlo | giả định trung bình rút là 2, thực tế nhỏ hơn; và cái chặn `min(1, ·)` khiến người kỳ vọng bonus cao không đạt tới |
| Bảo toàn tổng bàn | giải tích | tổng bàn kỳ vọng của cả đội **không khớp λ** của mô hình sức mạnh đội |
| Mẫu số của share | Monte Carlo | gồm cả cầu thủ **không được mô phỏng**, nên các share cộng lại < 1 |

Hai chỗ đáng nói riêng:

**Bảo toàn tổng bàn.** Bàn thắng trong một trận là đại lượng bảo toàn: tổng của cả
đội phải bằng λ mà mô hình sức mạnh đội ước lượng từ kèo + xG. Nhân tỷ lệ per-90 của
từng người rồi cộng lại không tự thoả điều đó — đo trên GW1: **Chelsea 162% λ, Man
City 140%, Fulham 57%**. Nghĩa là tiền đạo đội mạnh bị thổi phồng và đội yếu bị dìm,
một cách hệ thống. `projections.py` giờ tính hệ số `λ / Σ` cho từng (đội, trận) và
truyền vào `expected_points`, chặn trong [0.5, 2.0] — ra ngoài khoảng đó là dữ liệu
cầu thủ và mô hình đội đang mâu thuẫn nặng, ép khớp bằng mọi giá chỉ bóp méo thêm.

**Mẫu số của share.** `share_goal = xG cầu thủ / Σ xG đội` nhưng Monte Carlo chỉ mô
phỏng người có xMins > 3. Phần xG của những người bị loại biến thành bàn thắng thất
lạc: **5.9% toàn giải, tới 21% ở Liverpool**. Giờ mẫu số chỉ gồm nhóm được mô phỏng,
đúng với ý định vốn có của module (chuyển share của người vắng sang người thay thế).

**Kết quả:** thủ môn 88→**100%**, hậu vệ 107→**103%**, tiền vệ 92→**99%**, tiền đạo
79→**91%**. Top-20 theo tổng xP 8 vòng giữ **20/20** người.

**Còn lệch ở đâu, và vì sao dừng.** Tiền đạo còn 91%, do hai nguyên nhân đã xác định
mà **không** phải lỗi: (1) Monte Carlo không cho một cầu thủ tự kiến tạo cho bàn của
mình, còn giải tích thì có — ở đây MC đúng; (2) hai bên đánh trọng số phân bổ khác
nhau (giải tích: per-90 đã shrink × số phút; MC: share xG cả mùa trong nhóm ra sân).
Khép nốt (2) đòi viết lại cách phân bổ bàn thắng ở một trong hai bên, và chỉnh tiếp
để một con số đẹp hơn là làm vừa mốc, không phải sửa lỗi.

Chế độ chẩn đoán để đo lại: `simulate_fixture(..., collect=dict)` và
`build_projections(..., collect_components=dict)` trả về trung bình **từng thành
phần**, đi qua đúng pipeline thật. Bản dựng lại bằng tay sẽ có xMins khác
(`matches_played`, `role_rank`, `no_pl_history`) và hai bên hết so được với nhau —
đã dính một lần: chẩn đoán bằng tay báo 97% trong khi pipeline thật là 86%.

### 2c. Bonus — chia một quỹ cố định, không phải công thức rời (`engine/bonus.py`)

Bonus là chỗ duy nhất trong engine có **luật bảo toàn kiểm tra được**: mỗi trận FPL
phát đúng **6 điểm** (3 + 2 + 1) cho ba người có BPS cao nhất, ai đứng thứ tư được
0 dù BPS bao nhiêu. Bản trước tính bonus như thuộc tính riêng của từng cầu thủ
(`0.35·involvement + 0.0016·bps90·xMins/90`) nên không có gì buộc nó tôn trọng luật
đó, và nó trôi rất xa:

| | thực tế 2025/26 (/90) | mô hình cũ | mô hình mới |
|---|---|---|---|
| Thủ môn | 0.223 | 33% | **103%** |
| Hậu vệ | 0.264 | 40% | **68%** |
| Tiền vệ | 0.360 | 46% | **82%** |
| Tiền đạo | 0.632 | 38% | **85%** |
| Tổng mỗi trận | 6.00 | **2.47** | **6.00** |

Mô hình mới:

```
BPS kỳ vọng_i = bps90_i · (xMins/90)                       # mức nền của chính cầu thủ
              + goal_bps[pos]·xG_trận + 9·xA_trận          # phần lệch riêng của trận
              + 12·P(sạch lưới)·P(≥60')                    # GK/DEF
trọng số_i    = (BPS kỳ vọng_i) ^ 1.99
bonus_i       = 6 · trọng số_i / Σ trọng số (CẢ HAI ĐỘI)
```

Ba điều đáng nói:

- **Số mũ 1.99 đo từ dữ liệu**, không chọn tay: hồi quy log-log bonus/90 theo
  BPS/90 trên 252 cầu thủ đá từ 900 phút mùa 2025/26 (R² = 0.45 trên thang gốc).
  Số mũ lớn hơn 1 chính là dấu vết cơ chế top-3 — BPS gấp đôi cho bonus gấp khoảng
  bốn lần. Dạng tuyến tính cho R² 0.47, nhích hơn, nhưng hệ số chặn âm (−0.37) nên
  nó dự báo bonus âm cho người BPS thấp.
- **BPS bàn thắng NGƯỢC thang điểm FPL**: tiền đạo 24, tiền vệ 18, hậu vệ/thủ môn
  12 (điểm FPL thì thủ môn 10, tiền đạo 4 — bù cho việc hậu vệ ghi bàn hiếm). Ghi
  ngược cặp này làm tiền đạo bị chia hụt đúng một nửa; đã đo được và đã có test khoá.
- **Phải biết cả 22 người mới tính được.** `expected_points()` một mình không tính
  nổi bonus, nên nó nhận `bonus_override` từ `engine/projections.py` — nơi có cả hai
  đội của trận. Gọi lẻ (test, thăm dò) thì rơi về dạng rời rạc đã khớp trực tiếp
  với dữ liệu; dạng đó đúng ở mức trung bình dân số nhưng **không** bảo toàn quỹ.

**Còn hụt bao nhiêu, và vì sao không tinh chỉnh thêm.** Hậu vệ ở 68% là con số thấp
nhất còn lại. Một phần là đúng: mùa 2026/27 hạ BPS từ CBI nên hậu vệ thật sự kiếm ít
BPS hơn — đo được −6.7%, với số mũ 2 thì tương đương −13% bonus, tức mức "đúng" nên
vào khoảng 87%. Phần còn lại (~19%) chưa giải thích được. Chúng tôi **không** nhân
thêm hệ số theo vị trí để kéo về 100%, vì mốc so sánh có hai khuyết điểm: nó tính
theo luật BPS **mùa cũ**, và nó được chia theo số phút **cuối mùa** — thông tin mà
ở vòng 1 không ai có. Khớp cho vừa một mốc như vậy là làm đẹp số, không phải hiệu
chuẩn.

Tác động lên xP: cầu thủ dự kiến đá chính (xMins ≥ 60) tăng trung bình **+0.213**
điểm mỗi vòng — thủ môn +0.196, hậu vệ +0.139, tiền vệ +0.233, **tiền đạo +0.591**.
Thứ tự gần như không đổi: top-20 theo tổng xP 8 vòng giữ lại 19/20 người.

`λ_team_goals` và `λ_conceded` từ mô hình sức mạnh đội — xem **mục 2d** bên dưới
cho toàn bộ đường đi từ prior đội bóng tới FDR của một ô lịch.

### 2a-bis. Sức mạnh đội trước vòng 1 — hai lỗi đã sửa

Trước vòng 1, FPL **chưa phát chỉ số sức mạnh**: cả 20 đội đều có `strength = None`
và `strength_attack/defence_* = 0`. Engine vì thế rơi sang nhánh tự suy từ dữ liệu
cầu thủ, và nhánh đó có hai lỗi.

**1. Mốc hàng thủ lấy nhầm người.** Bản trước dùng `max(expected_goals_conceded)`
trong toàn đội, với lập luận "một cầu thủ đá cả mùa ≈ xGA của đội". Hai chỗ hỏng:

- `expected_goals_conceded` là số **mùa trước**, còn `team_id` là CLB **hiện tại**,
  và FPL API không cho biết số đó tích luỹ ở đâu. Đo được: hàng thủ Man City bị
  chấm bằng **Elliot Anderson — 53.6 xGC tích luỹ ở Nottingham Forest**. City thành
  0.887 (kém trung bình giải), và Crystal Palace được cho **2.01 bàn kỳ vọng khi
  tiếp City** — ô lịch hiện màu xanh. 4/20 đội dính lỗi này (MCI, CHE, IPS, COV).
- Tổng cả mùa phụ thuộc số phút: 10/20 đội có mốc là cầu thủ ngoài sân, mà xGC của
  họ chỉ tính những phút có mặt — đội hay xoay trung vệ trông như phòng ngự tốt hơn.

Giờ mốc là **xGC/90 của thủ môn đá nhiều nhất**, loại tân binh đã khai. Thủ môn ít
bị xoay vòng nhất, và xGC/90 của họ *chính là* mức bị uy hiếp của đội khi họ trên
sân — một tỷ lệ, không phụ thuộc số phút. Phủ 17/20 đội; ba đội còn lại đúng là
nhóm mới lên hạng, vốn đã đi nhánh `PROMOTED_DEFENCE` riêng.

Kết quả: Man City từ 0.887 lên **1.109 (nhì giải, sau Arsenal 1.572)**, và ô
CRY–MCI đi từ `λ_for 2.01 / độ khó 2.2` xuống **`1.54 / 3.1`** — hết xanh.

**Bất đối xứng cố ý: phía TẤN CÔNG không sửa theo cách này.** xG **đi theo cầu
thủ** — tiền đạo chuyển CLB thì bàn thắng của anh ta trở thành sản lượng kỳ vọng
của CLB mới, nên cộng vào tổng xG của CLB mới là *đúng* (26.9% xG của Chelsea đến
từ tân binh, và đó là con số hợp lệ). Còn xGC là thuộc tính của **đội bóng** và
không đi theo ai cả. Cùng một trường dữ liệu, hai cách đọc khác nhau.

**2. Trọng số tiền mùa tin dữ liệu cũ quá nhiều.** `w_hist = phút/(phút + 8000)`
dùng số phút **mùa trước** làm thước đo cỡ mẫu, nên trước vòng 1 nó cho trọng số
~0.79 vào một mô tả của đội bóng **cũ** — trong khi mùa 2026/27 có **12/20 CLB đổi
huấn luyện viên**. Giờ `w_hist` được nhân thêm `prior_weight_new_manager` (0.6) với
những CLB đó — **đúng hệ số mà mô hình xMins đã dùng** cho từng cầu thủ của chính
các CLB ấy. Một sự thật thì mang cùng một con số ở mọi nơi.

Không đưa về 0: dữ liệu mùa trước vẫn có tín hiệu thật, chỉ là mô tả đội hiện tại
kém đi. Danh sách CLB đổi HLV do người vận hành khai (API không công bố) — rỗng
nghĩa là **chưa ai khai**, và khi đó không chiết khấu ai cả.

**Tác động đo được** (trung bình mỗi cầu thủ mỗi vòng): thủ môn +0.065 xP, hậu vệ
+0.062, tiền vệ −0.042, tiền đạo −0.108; xác suất sạch lưới +1.8 đến +2.3 điểm %.
Đội tăng nhiều nhất FUL/TOT/BHA, giảm nhiều nhất CHE/LEE.

**Điểm yếu còn lại:** việc loại tân binh dựa vào `NEW_SIGNING_PLAYERS` khai tay. FPL
API không cho biết cầu thủ đổi CLB, nên không tự động được — một tân binh chưa có
trong danh sách vẫn sẽ làm lệch mốc hàng thủ của CLB mới.

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
- **Khử vig theo TỪNG nhà cái trước, rồi mới tổng hợp.** Xác suất ẩn được chuẩn
  hoá trong nội bộ các kết cục của chính nhà cái đó, nên biên lợi nhuận của một
  nhà cái không lẫn sang giá của nhà cái khác.
- **Tổng hợp bằng trung vị, không phải trung bình.** Trung bình cho mỗi nhà cái
  quyền dịch đồng thuận 1/n, nên một nhà cái treo giá cũ hoặc lệch là đủ kéo cả
  đồng thuận; trung vị bỏ qua nó. Đo được: 12 nhà cái quanh Over 2.5 = 0.52, thêm
  một nhà cái ở 0.20 thì trung bình xuống 0.4954 (lệch 0.0246) còn trung vị đứng
  nguyên 0.5200 — chênh **0.097 bàn** ở tổng bàn kỳ vọng. Với 1X2, trung vị lấy
  theo từng kết cục rồi **chuẩn hoá lại**, vì ba trung vị độc lập không tự cộng
  thành 1.
- Mỗi mức kèo là **một quan sát riêng**; giá lấy trung vị theo từng mức chứ không
  trộn giữa các mức khác nhau. Mức nào quá ít nhà cái treo so với mức chính thì
  loại.
- **Trọng số thị trường hạ khi thị trường mỏng.** Trọng số blend
  (`odds_market_weight`, mặc định 0.7) được nhân với `min(1, số_nhà_cái / 8)` cho
  từng trận, nên đồng thuận 20 nhà cái và giá lẻ của 2 nhà cái không còn được coi
  là cùng một loại bằng chứng. Dữ liệu hiện tại có 19–20 nhà cái mỗi trận nên
  **λ không đổi một chút nào** (đo được: chênh lệch lớn nhất 0.000000); giả lập
  3 nhà cái thì λ dịch tới **0.19 bàn** về phía mô hình nội bộ. Không có số nhà
  cái (dữ liệu ghi trước khi có cột này) thì giữ trọng số đầy đủ — hạ trọng số vì
  *thiếu thông tin* sẽ âm thầm làm yếu những trận vốn có giá tốt.
- **KHÔNG có** trọng số nhà cái theo thanh khoản hay độ chính xác lịch sử. The
  Odds API không công bố doanh số cũng không công bố kết quả đã quyết toán, nên
  một trọng số như vậy sẽ là số do ta tự bịa. Trung vị đồng trọng số là đồng thuận
  mạnh nhất mà dữ liệu đang có cho phép. Muốn làm thật thì phải lưu closing line
  + kết quả trận theo từng nhà cái rồi tính điểm hiệu chuẩn — giống bảng
  `expert_track_record` đang làm cho các nguồn chuyên gia.
- Sai số khớp cuối cùng được ghi vào log đồng bộ. Sai số lớn nghĩa là ba thị
  trường mâu thuẫn nhau nhiều hơn mức một mô hình tỷ số có thể dung hòa.

## 2d. Độ khó lịch thi đấu — sáu bước

Toàn bộ phần lịch chạy qua sáu bước, mỗi bước một file:

| Bước | Việc | File |
|------|------|------|
| 1 | Prior đội bóng, gộp 5 nguồn có trọng số | `engine/prior_strength.py` |
| 2 | `log λ` cộng 10 số hạng | `engine/team_strength.py` |
| 3 | Hiệu chuẩn & blend theo thị trường | `engine/team_strength.py` |
| 4 | Percentile: Attack / Defence / Role Ease | `engine/fixture_difficulty.py` |
| 5 | Schedule Ease cả cửa sổ | `engine/fixture_difficulty.py` |
| 6 | FDR 1–5 theo ngũ phân vị | `engine/fixture_difficulty.py` |

Đường đi ngược lại — từ một bậc FDR về từng mảnh bằng chứng đã tạo ra nó — mở
bằng `GET /api/fixtures/explain?team_id=..&opponent_id=..&is_home=..`.

### BƯỚC 1 — prior đầu mùa (`prior_strength.py`)

```
PriorStrength = 45% opponent-adjusted competitive xG/xGA
              + 25% market / Elo strength
              + 15% squad-quality
              + 10% manager / system continuity
              +  5% quality-adjusted preseason underlying data
```

Hai quyết định định hình cả module:

**Gộp trong không gian log.** Kết quả của BƯỚC 1 đi thẳng vào BƯỚC 2 làm số hạng
cộng của `log λ`. Trộn tuyến tính ở đây rồi lấy log ở kia thì "45%" không còn là
45% của thứ mô hình thực sự dùng.

**Trọng số được chuẩn hoá lại theo nguồn thật sự có.** Không nguồn nào được phép
mặc định bằng trung bình giải để giữ nguyên mẫu số — làm vậy là lén kéo mọi đội về
giữa rồi vẫn khai là đã dùng đủ năm nguồn. `TeamPrior.evidence_weight` là tổng
trọng số đã vào (1.0 = đủ cả năm), và BƯỚC 5 phạt chính con số đó.

Trạng thái thật của từng nguồn, tính theo dữ liệu FPL API cấp:

| Nguồn | Trước vòng 1 | Trong mùa |
|-------|--------------|-----------|
| xG/xGA | có, **chưa** hiệu chỉnh đối thủ | có, hiệu chỉnh bằng IPF |
| market / Elo | Elo thay thế (strength ratings, rỗng trước vòng 1) | kèo nếu đủ dày, không thì Elo |
| squad quality | có (định giá FPL) | có |
| manager continuity | có (danh sách khai tay) | có |
| preseason underlying | **chỉ** đội mới lên hạng (Championship) | nhạt dần rồi tắt |

- **Hiệu chỉnh đối thủ** dùng lặp tỉ lệ (IPF) trên `value ≈ base · att[i] · weak[j] · h^±1`,
  nguyên liệu là xG **theo từng trận** từ `player_gameweek_stats`. Cần tối thiểu 4
  trận mỗi đội; dưới mức đó hàm trả `None` thay vì trả một nghiệm mà dữ liệu không
  xác định (40 tham số không khớp được bằng 10 quan sát).
- **Kèo chỉ vào BƯỚC 1 khi đủ dày.** Kèo thường chỉ phủ 1–2 vòng tới. Khi mỏng, nó
  vẫn được dùng — nhưng ở BƯỚC 3, đúng chỗ mà chỉ trận có giá được hưởng.
- **Squad quality đo bằng định giá FPL**, vì FPL định giá lại toàn bộ đội hình mỗi
  hè và giá đó đã nuốt vào cả chuyển nhượng đến lẫn đi. API **không** công bố lịch
  sử chuyển nhượng nên không thể lấy hiệu đội hình năm nay trừ năm ngoái. Độ dốc
  giá→sản lượng được **khớp tại chỗ** trên chính 20 đội trong DB, và khớp **riêng**
  cho công và thủ: đo được công 1.91, thủ 1.24 — dùng chung một độ dốc thì vế thủ
  bị ép theo hệ số của vế công và chỉ số phòng ngự nhóm đầu bảng chạm trần 1.70.
  Đội mới lên hạng bị loại khỏi *phép khớp* (không khỏi kết quả): xG Ngoại hạng của
  họ bằng ~0 vì mùa trước đá giải khác, và để lại thì đúng một điểm đó lái độ dốc
  lên 3.54, tức hồi quy đang học "đội mới lên hạng thì rẻ".
- **Manager continuity không mang hướng.** Đổi HLV không phải bằng chứng đội yếu
  đi, mà là bằng chứng dữ liệu cũ mô tả đội hiện tại kém đi. Nên nó chuyển
  `45% × (1 − 0.6)` = 18 điểm phần trăm từ nguồn xG sang một cái neo ở trung bình
  giải — cùng hệ số `prior_weight_new_manager` mà mô hình xMins dùng cho từng cầu
  thủ của chính các CLB ấy. Giữ nguyên HLV thì thành phần này **đồng ý với phần
  còn lại**, nên nó được cộng vào tổng bằng chứng mà không dịch con số: 10% của
  đặc tả được giữ đúng, mà không lén chính quy hoá cả 20 đội về 1.0.
- **Preseason: 17/20 đội không có.** FPL API không có giao hữu tiền mùa — không
  xG, không đội hình, không kết quả. Nguồn này được ghi là *không tồn tại* chứ
  không được bịa; 5% của nó chia lại cho bốn nguồn kia. Với ba đội mới lên hạng thì
  có một thứ đúng nghĩa "underlying data từ giải khác, đã hiệu chỉnh chất lượng":
  thành tích Championship, hiệu chỉnh bằng số mũ `damping`, trần 1.0, nhạt dần sau
  5 trận Ngoại hạng thật.
- **Co giãn theo cỡ mẫu** (`index^w`, `w = phút/(phút+8000)` hoặc `trận/(trận+6)`):
  sau vòng 1 một đội ghi 3 bàn có tỷ lệ xG gấp bốn lần trung bình giải; không co
  giãn thì prior chạm trần và ô lịch của **mọi** đối thủ của họ đổi màu — từ một
  trận.

### BƯỚC 2 — `log λ` cộng 10 số hạng (`team_strength.py`)

```
log λ(A tấn công B, A sân nhà) =
      log(nền giải)          ← khớp từ trận đã đá khi ≥40 trận, không thì 1.42
    + sức tấn công của A      ← prior ⊕ dữ liệu trong mùa
    + độ hở hàng thủ của B
    + lợi thế sân nhà         ← khớp từ trận đã đá; MỘT hệ số, nhân/chia
    + sức mạnh đội hình       ← (A sẵn sàng − B sẵn sàng)
    + chênh lệch ngày nghỉ
    + mật độ thi đấu
    + điều chỉnh chuyển nhượng
    + điều chỉnh HLV / hệ thống
    + điều chỉnh đội mới lên hạng
```

**`λ_against` không có công thức riêng.** Nó là chính hàm trên gọi ngược:
`λ_against(A) = λ(B tấn công A, B sân khách)`. Bản trước có hai nhánh song song cho
hai chiều — hai nhánh song song là hai chỗ để lệch nhau, và đúng một lần lệch dấu
lợi thế sân nhà là đủ hỏng cả bảng độ khó. Có test khoá đẳng thức này.

**Lợi thế sân nhà là một hệ số**, nhân cho đội nhà và chia cho đội khách. Bản trước
dùng hai hằng số rời (1.12 và 0.90) nên tích của chúng bằng 1.008 ≠ 1, và tổng số
bàn của giải trôi theo tỷ lệ nhà/khách của lịch.

Ba số hạng có dữ liệu thật, và ba số hạng thường bằng 0 — nói thẳng cái nào là cái
nào, vì payload có ghi lý do cho từng số hạng bằng 0:

| Số hạng | Nguồn | Độ lớn thực tế |
|---------|-------|----------------|
| đội hình | `status` + `chance_of_playing`, trọng số theo giá 11 người đắt nhất | tới ±10% λ |
| ngày nghỉ | `kickoff_time`, chặn ở ±4 ngày, hệ số 0.010/ngày | đo được ±2.2% λ |
| mật độ | số trận trong 14 ngày trước, quá 2 trận mới tính | **0 trong hầu hết trường hợp** |
| chuyển nhượng | rút ngắn thời gian dữ liệu trong mùa lấn át prior | 0 trước vòng 1 |
| HLV | như trên | 0 trước vòng 1 |
| lên hạng | trần bằng trung bình giải khi chưa có phút Ngoại hạng | chỉ với đội mới lên |

- **Mật độ gần như luôn bằng 0, và đó là giới hạn dữ liệu chứ không phải lựa chọn
  mô hình.** FPL API **chỉ có lịch Ngoại hạng**. Cúp châu Âu, cúp Liên đoàn, FA Cup
  — đúng những giải tạo ra mật độ thật — không nằm ở đâu trong dữ liệu này. Số hạng
  chỉ kích hoạt được ở các vòng đá giữa tuần của chính Ngoại hạng.
- **Hệ số ngày nghỉ và mật độ không khớp được từ dữ liệu trong DB** (trước vòng 1
  không có trận nào; trong mùa thì 380 trận vẫn quá ít để tách hiệu ứng ngày nghỉ
  khỏi chất lượng đội). Chúng được đặt cố ý nhỏ và chặn hai đầu, ở mức mà kể cả sai
  hoàn toàn cũng không đảo được thứ tự độ khó. Đặt về 0 để tắt hẳn.
- **Chuyển nhượng và HLV không dịch λ trực tiếp**, mà rút ngắn `K` trong
  `w = trận/(trận+K)`: với CLB đổi HLV hay thay máu đội hình, mô tả cũ hết hạn sớm
  hơn nên dữ liệu trong mùa lấn át prior nhanh hơn. Chúng được báo cáo dưới dạng
  **hiệu số log λ** mà cơ chế đó gây ra, nên phân rã vẫn cộng đúng bằng `log λ`.
- **Trần đội mới lên hạng**: CLB chưa có phút Ngoại hạng nào không được chấm trên
  trung bình giải, bất kể các nguồn khác nói gì. Trần tự biến mất khi đội đó tích
  đủ phút thật.

### BƯỚC 3 — hiệu chuẩn theo thị trường

```
λ_cuối = λ_thị_trường^w · λ_cấu_trúc^(1−w)
w      = trọng_số_gốc · min(1, số_nhà_cái/8) · độ_trưởng_thành
```

**Trung bình hình học, không phải số học.** Cả mô hình lẫn thị trường sống trong
thang log — trộn số học ở đây là trộn ở một thang khác với thang đã dựng ra hai số
đó. Hình học luôn ≤ số học, và có test khoá điều đó (nếu kết quả vượt trung bình
cộng thì đã trộn nhầm thang).

**Độ trưởng thành** là phần mới: giá của trận đá tuần này và giá của trận sau sáu
tuần không đáng tin như nhau — trận xa có ít nhà cái treo bảng hơn, biên rộng hơn,
và mọi tin đội hình sẽ làm giá chạy trước khi bóng lăn. Trong 10 ngày = trọng số
đầy đủ, giảm tuyến tính xuống 0.45 ở mốc 45 ngày.

**Và một bước mà "blend từng trận" không làm được: hiệu chuẩn toàn cục.** Độ lệch
hệ thống giữa mô hình và thị trường được đo trên những trận **có** giá rồi áp cho
**mọi** trận. Nếu mô hình nội bộ nóng hơn thị trường 7% ở các trận đã có bảng kèo,
thì nó cũng đang nóng hơn 7% ở GW12 — dù GW12 chưa ai ra giá. Đây là chỗ thông tin
thị trường lan sang phần lịch mà thị trường chưa chạm tới. Cần tối thiểu 6 trận có
giá, và bị chặn ở ±18%: lệch hơn thế nghĩa là mô hình và thị trường bất đồng về bản
chất, và ép khớp sẽ giấu bất đồng đó đi thay vì phơi nó ra. Đo trên dữ liệu hiện
tại: hệ số **0.927** (mô hình nóng hơn thị trường 7.8%), phủ 20/160 ô.

### BƯỚC 4 — percentile, không phải phép co tuyến tính

```
Attack Ease  = percentile(λ_for)
Defence Ease = percentile( 4·P(sạch lưới) − trừ điểm thủng lưới )
Role Ease    = percentile( điểm kỳ vọng của cầu thủ THAM CHIẾU ở vai trò đó )
```

Bản trước ánh xạ `λ ∈ [0.6, 2.6]` xuống thang 1–5 bằng một đoạn thẳng có hai đầu
mút ghi cứng. Hai hệ quả: mùa nhiều bàn thì cả giải trôi về phía "dễ" vì hai đầu
mút không đi theo; và khoảng giữa phân bố — nơi có phần lớn các trận — bị nén vào
chưa tới một bậc. Percentile lấy chính phân bố của cửa sổ đang xem làm thước, nên
"20% dễ nhất" luôn đúng nghĩa 20% dễ nhất.

**Cả hai vế của Defence Ease đọc từ `RULES`**, không ghi cứng số 4: FPL đã từng đổi
điểm sạch lưới. Khoản trừ tính theo **mốc trọn** (−1 mỗi 2 bàn), không theo `λ/2` —
thủng đúng một bàn không mất điểm nào.

**Role Ease giữ nguyên cầu thủ và chỉ đổi trận đấu.** Cầu thủ tham chiếu là trung
vị của giải ở vai trò đó (trung vị chứ không phải trung bình: phân bố sản lượng
lệch mạnh, một Haaland kéo trung bình tiền đạo lên khỏi mọi tiền đạo thật), đá
chính chắc suất, chạy qua đúng `expected_points()` đang dùng cho cầu thủ thật.

Ngưỡng "đá đều" để vào nhóm lấy trung vị là **tương đối** — 60% số phút của cầu thủ
đá nhiều nhất giải, trần 900 phút. Một mốc 900 phút tuyệt đối nghĩa là suốt GW1–GW9
không một ai đủ điều kiện và cả bốn vai trò rơi về prior, vì FPL reset thống kê mỗi
mùa; đó đúng là lỗi mà mục 2a-bis đã sửa một lần cho ngưỡng "đội mới lên hạng".
Ngưỡng tương đối lại cần một cái sàn của riêng nó: khi chưa ai đá phút nào thì
`0.6 × 0` = 0, mọi cầu thủ "đủ điều kiện" với 0 phút, và cầu thủ tham chiếu được
dựng từ toàn số 0 — nên chừng nào người đá nhiều nhất giải chưa qua 2 trận trọn vẹn
thì vẫn dùng prior theo vị trí.
`team_avg_gf` được đặt bằng **nền của giải** chứ không phải trung bình của đội —
lấy trung bình của đội thì một trận λ 1.8 sẽ ra "dễ" cho đội yếu và "khó" cho đội
mạnh, tức đo lại chính đội bóng, đúng thứ bước này cố ý loại ra.

Vì sao cần Role Ease khi đã có hai cái kia: một trận `λ_for 1.9 / λ_against 1.6` là
trận tốt cho tiền đạo và trận tệ cho hậu vệ. Xếp một hạng duy nhất cho cả bốn vị
trí — thứ FDR chính thức của FPL làm — là trộn hai câu hỏi khác nhau vào một câu
trả lời. Đo được trên dữ liệu hiện tại: Leeds là **FDR 2 cho tiền vệ nhưng FDR 4
cho hậu vệ**, Bournemouth là 4 cho tiền vệ và 5 cho hậu vệ.

### BƯỚC 5 — Schedule Ease

```
Schedule Ease = Σ w_k · RoleEase(vòng k) / Σ w_k  −  phạt bất định
w_k           = 0.5^(k / 4)        # nửa chu kỳ 4 vòng
```

**Gộp ở cấp vòng đấu, không phải cấp trận.** Đó là chỗ duy nhất xử lý đúng được
vòng trắng và vòng đôi: vòng trắng là **0 điểm** (không đá thì không có điểm — một
sự thật, không phải dữ liệu thiếu), vòng đôi là tổng của hai trận. Trung bình theo
*trận* làm vòng trắng biến mất khỏi phép tính và làm vòng đôi trông y hệt vòng đơn
— đúng hai lỗi khiến các bảng FDR thông thường vô dụng ở giai đoạn tái đấu.

Phạt bất định gộp ba thứ **đã biết là chưa biết** (trần 12 điểm percentile, tức hơn
nửa bậc FDR):

| Nguồn | Tỷ trọng | Ý nghĩa |
|-------|----------|---------|
| `share_no_market` | 50% | phần lịch chưa nhà cái nào ra giá |
| `1 − evidence_weight` | 35% | BƯỚC 1 gộp được mấy trong 5 nguồn cho đội này |
| `share_no_kickoff` | 15% | trận chưa có giờ chính thức, còn có thể bị dời |

Trước vòng 1 khoản phạt gần như đồng đều (đo được: 6.3–6.5 cho cả 20 đội) nên nó
**không** đảo thứ tự — và đó là hành vi đúng: bất định nên hạ mức tin cậy của cả
bảng, không nên xáo lại bảng. Nó chỉ cắn thật khi hai đội chênh nhau về lượng bằng
chứng, ví dụ một đội có kèo cho 6/8 vòng còn đội kia chỉ có 1/8.

### BƯỚC 6 — FDR theo ngũ phân vị

Xếp hạng theo Schedule Ease rồi chia đúng năm nhóm bằng nhau: 20 đội → **đúng 4 đội
mỗi bậc**. Chia theo *thứ hạng* chứ không theo ngưỡng giá trị, vì trước vòng 1 cả
giải chụm lại quanh nhau và một ngưỡng cố định sẽ dồn hết vào FDR 3. Từng ô lịch
cũng có FDR riêng, lấy từ percentile của ô (số ô mỗi vòng không chia hết cho 5 nên
ở cấp ô dùng percentile hợp hơn thứ hạng).

### Độ khó lịch KHÔNG phải đầu vào cuối cùng để chọn cầu thủ

Nó là **một thừa số** trong dự báo cầu thủ, và `expected_points()` ghép đúng như vậy:

```
xP = FixtureOpportunity × ExpectedMinutes × PlayerRole × PlayerShare
     + Bonus + DefensiveContribution − (thẻ + bàn thua)
```

| Thừa số | Nằm ở đâu trong code |
|---------|----------------------|
| FixtureOpportunity | `fixture_adj = λ_for(trận) / nền_giải`, và `λ_conceded` cho sạch lưới |
| ExpectedMinutes | `minutes_frac = xMins/90`, `p_60_plus` cho điểm ra sân & sạch lưới |
| PlayerRole | per-90 đã co giãn (`xg90`, `xa90`, `dc90`, `bps90`) + `pen_bump` cho người đá phạt đền |
| PlayerShare | `team_goal_scale` — chuẩn hoá để tổng bàn kỳ vọng của cả đội bằng đúng λ |
| Bonus | chia quỹ 6 điểm trong nội bộ trận, cần cả 22 người |
| DefensiveContribution | `2 · P(số hành động ≥ ngưỡng)` |

**Đo được lịch nặng bao nhiêu.** Tính xP 8 vòng của 496 cầu thủ hai lần — một lần
với lịch thật, một lần với lịch trung tính (λ hai chiều = nền giải):

| Đại lượng | p10 | trung vị | p90 | biên độ |
|-----------|-----|----------|-----|---------|
| đòn bẩy của **lịch** (xP thật / xP trung tính) | 0.92 | 0.99 | 1.14 | **1.24×** |
| chất lượng **cầu thủ** (xP 8 vòng ở lịch trung tính) | 4.12 | 10.99 | 22.60 | **5.49×** |

Lịch quyết định **ít hơn khoảng bốn lần** so với chính cầu thủ. Hệ quả cụ thể trên
dữ liệu hiện tại: trong **top 40 xP** chỉ có 16 người thuộc nhóm lịch FDR 1, còn 13
người thuộc FDR 3–4. **Watkins** (Aston Villa, lịch FDR 4) đứng **thứ 5 toàn giải**
và hơn **118/122** cầu thủ có lịch FDR 1, vì 74 xMins và đá phạt đền. Ngược lại
Saka (FDR 1) chỉ 57 xMins nên vẫn bị hai cầu thủ lịch FDR 3–5 vượt qua.

**Một chỗ lệch có chủ ý so với công thức trên: `− Risk` không nằm trong xP.** Rủi ro
là một trục riêng (`engine/risk.py` → `minutes_risk` / `performance_risk`), vào bài
toán tối ưu dưới dạng **hệ số phạt khi chọn** (`risk_weight` trong
`optimizer/transfer.py`) chứ không trừ vào xP. Lý do: xP phải giữ được nghĩa "kỳ
vọng không thiên lệch". Trừ rủi ro vào đó là trộn kỳ vọng với khẩu vị rủi ro, và
sau đó không còn tách lại được — cùng một con số vừa dùng để dự báo vừa dùng để
xếp hạng an toàn thì hỏng cả hai việc.

## 3. Monte Carlo — phân bổ bàn thắng cho cầu thủ

Mô phỏng ở cấp **trận đấu của đội**: mỗi vòng lặp rút một lần tổng bàn thắng và
tổng bàn thua, rút xem ai có mặt trên sân, rồi mới **chia** số bàn đó cho những
người thực sự ra sân.

```
team_goals    ~ Poisson(λ_for)       # dùng chung cho mọi cầu thủ cùng đội
team_conceded ~ Poisson(λ_against)   # clean sheet dùng chung GK + hậu vệ
→ chia team_goals theo Multinomial (rút bằng chuỗi Binomial có điều kiện)
→ chia tiếp kiến tạo, mỗi người bị chặn trên bởi số bàn NGƯỜI KHÁC ghi
```

### Share được tính thế nào

```
share_goal   = xG cầu thủ / Σ xG cả đội      (số liệu cả mùa)
share_assist = xA cầu thủ / Σ xG cả đội
```

Là **share theo xG, ĐÃ GỒM penalty** — không phải npxG, cũng không phải shot
share. Mẫu số của share_assist là tổng **xG** (không phải xA) và điều đó đúng về
thứ nguyên: phép rút diễn ra trên `team_goals` mà kỳ vọng ≈ xG của đội, nên cầu
thủ có xA 8 trong đội xG 60 với λ 1.5 tái tạo 7.6 kiến tạo cả mùa. Đổi sang mẫu
số xA sẽ thổi lên ~10.

### Sáu câu hỏi, sáu câu trả lời thẳng

**1. npxG share hay shot share?** → **xG share, đã gồm penalty.** FPL không công
bố npxG trong `bootstrap-static`.

**2. Penalty tách riêng thế nào?** → **Chưa tách.** FPL cho `penalties_missed`
nhưng **không** cho `penalties_scored` và **không** cho npxG, nên không có cách
nào tách phần xG từ chấm 11m ra khỏi tổng xG mà không áp một giả định trung bình
giải cho mọi người đá 11m — làm thế sẽ hoặc đếm hai lần, hoặc trừ nhầm của người
mà đội hiếm khi được hưởng phạt đền. Hệ quả còn lại: upside riêng của người đá
11m vẫn hoà trong share bóng sống. Muốn tách đúng thì cần một nguồn npxG bên
ngoài.

**3. Assist phân phối thế nào?** → Cũng chia theo Multinomial trên `team_goals`,
nhưng **mỗi người bị chặn trên bởi số bàn do người khác ghi** trong chính vòng
lặp đó, nên **không ai kiến tạo cho bàn của chính mình**. Tổng kiến tạo cũng
không vượt tổng bàn.

**4. Cầu thủ không đá thì share chuyển cho ai?** → **Chuyển cho những người
thực sự ra sân, theo tỷ lệ.** Share của cả đội được chuẩn hoá lại trên tập người
có mặt trong mỗi vòng lặp (hệ số trần 3.0, mỗi cá nhân trần 0.95). Hệ số điển
hình là **1.16**, p90 = 1.68, và chỉ **0.69%** số vòng lặp chạm trần. Kết quả đo:
điểm trung bình của người thay thế tăng **4.41 → 6.80** khi trụ cột rơi từ
P(start) 95% xuống 5%.

**5. Hai cầu thủ cùng đội tương quan ra sao?** → Tuỳ theo họ *chia sẻ* hay *cạnh
tranh*:

- **GK ↔ hậu vệ: +0.61.** Cùng ăn một sự kiện clean sheet — đây chính là lý do
  phải mô phỏng ở cấp đội thay vì từng người.
- **Hai tiền đạo: −0.04 đến −0.06.** Khi tổng bàn là Poisson và được chia
  Multinomial thì các phần **độc lập** (định lý thinning). Phần âm nhẹ chồng lên
  là cơ chế chuyển share: người này vắng thì người kia được nhiều hơn.

**6. Double Gameweek có mô phỏng rotation không?** → Mỗi trận rút `p_start`
**độc lập**; tương quan điểm giữa hai trận đo được **+0.0003**. Nên có mô phỏng
*biến động* xoay tua, nhưng **không** biểu diễn "nghỉ trận 1 nên nhiều khả năng
đá trận 2", cũng không có yếu tố mệt mỏi. **Đây vẫn là giới hạn chưa xử lý.**

### Đầu ra

Phân phối điểm mỗi cầu thủ → mean, median, P25/P75/P90, ceiling (P95),
P(blank ≤2), P(≥5), P(≥10), P(≥15), phương sai.

**xP KHÔNG đến từ Monte Carlo.** xP được tính giải tích ở `xpoints.py`; Monte
Carlo chỉ sinh ra phần phân phối. Vì vậy mọi thay đổi ở đây dịch chuyển ceiling /
floor / P(haul) / phương sai chứ **không** đổi xP một chút nào — đo được Δ xP =
0.0000 trên toàn bộ 567 cầu thủ.

### Ba lỗi đã sửa ở phần này

| Lỗi | Trước | Sau |
|---|---|---|
| Binomial độc lập, tổng bàn chia ra không bảo toàn | 20.9% số trận chia dư (tệ nhất: đội ghi 4, chia 14) | **0.00%** |
| Share của người vắng mặt bị vứt | điểm đồng đội y hệt nhau (4.5102) dù trụ cột P(start) 95% hay 10% | **4.41 → 6.80** |
| Kiến tạo rút độc lập với bàn thắng | tự kiến tạo cho bàn của mình | **0.00%** vi phạm |
| Thủ môn dự bị ăn điểm cứu thua | 0.064 điểm/trận cho thủ môn không ra sân | **0** |

Ảnh hưởng đo được, tách riêng khỏi mọi thay đổi khác, trên GW1:

```
Δ xP      = +0.0000  (xP là giải tích, không đi qua Monte Carlo)
Δ ceiling = +0.41 trung bình  (+0.81 với nhóm đá chính chắc)
P(haul)   Haaland 18% → 27% · B.Fernandes 21% → 27% · Rice 9% → 15%
```

**Chưa kiểm định.** Hướng của thay đổi là đúng về mặt toán (bảo toàn tổng bàn,
không tự kiến tạo, share được chuyển), nhưng **độ lớn của phần đuôi thì chưa có
gì đối chiếu** — mùa giải chưa đá trận nào. Phần backtest ở §6 mới là thứ trả lời
được con số nào gần sự thật hơn. Cho tới lúc đó, hãy đọc P(haul) và ceiling như
số **đã đổi thang**, và mọi ngưỡng từng chỉnh theo bộ số cũ cần xem lại.

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

### 5b. Cây quyết định (`services/decision_tree.py`)

Một danh sách "GW4: C + D → E + F" chưa phải kế hoạch dùng được: nó nói làm gì
nhưng không nói **vì sao chờ lại hơn làm ngay**, và ngầm giả định từ giờ tới GW4
không có gì thay đổi. Kết quả tối ưu vì thế được dịch thành cây:

**Nhánh chính** — mỗi bước, đặc biệt mỗi bước Roll, kèm lý do có số. Con số cốt
lõi là **chi phí của việc hành động ngay**: giả sử kéo nước đi kế tiếp lên vòng
hiện tại thì được gì và mất gì.

```
lợi  = Σ (xP cầu thủ vào − xP cầu thủ ra) trên các vòng từ t đến m−1
phí  = 4 × (số hit phát sinh về sau, tính lại bằng CHÍNH luật FT của bài toán)
ròng = lợi − phí
```

Vì bài toán tối ưu đã chọn lịch tốt nhất, `ròng` thường âm — và chính con số âm
đó, tách làm hai vế, là lời giải thích. Khi `ròng` không âm thì nghĩa là việc
tích FT **không** phải điều đang chi phối; lúc đó cây nói thẳng như vậy thay vì
bịa ra một lý do. Nước đi rơi vào vòng cuối tầm nhìn được gắn cờ `horizon_edge`
vì lợi ích của nó chỉ tính được đúng một vòng.

**Nhánh điều kiện** — chỉ sinh ra khi dữ liệu thật sự có tín hiệu:

| Nhánh | Kích hoạt bởi | Đưa ra |
|---|---|---|
| `injury` | `status ≠ a`, `chance_of_playing < 100`, hoặc có `injury_reports` | 2 phương án thay thế cùng vị trí, đủ tiền trong suất của cầu thủ bán, kèm chênh lệch xP so với nhánh chính |
| `price_rise` | Chuyển nhượng ròng vào ≥ 25k trong vòng | Khuyến nghị làm sớm, kèm thời điểm đổi giá và cái giá phải trả (mất phần FT đang tích) |
| `price_fall` | Chuyển nhượng ròng ra ≥ 25k | Bán sớm giữ giá trị đội, kèm phần xP sẽ mất |

Ngưỡng đổi giá thật của FPL **không công khai** và phụ thuộc tỷ lệ sở hữu, nên
nhánh giá luôn gắn nhãn tin cậy Low/Medium và một câu cảnh báo rõ đây là **chỉ
báo động lượng, không phải dự báo**.

Toàn bộ phần này là số học trên kế hoạch đã giải — không giải MILP lần hai, nên
không tốn thêm thời gian xử lý.

## 5c. Đội trưởng — bốn danh sách, không phải một (`services/captains.py`)

Xếp hạng theo xP chỉ trả lời được một trong bốn câu hỏi mà người chơi thật sự
có, và bốn câu đó cho bốn đáp án khác nhau. Mỗi danh sách được chấm và xếp hạng
**độc lập trên toàn bộ nhóm ứng viên**:

| Danh sách | Chấm theo | Trả lời |
|---|---|---|
| EV cao nhất | `2 × xP` | Nhiều điểm nhất tính trung bình |
| An toàn nhất | `2 × P25 × P(start) × (1 − ½·rủi ro thay ra) − 4 × P(blank)` | Giữ thứ hạng đang có |
| Ceiling cao nhất | `P95` | Điểm đủ thắng cả vòng |
| Đuổi hạng tốt nhất | `P95 × (2 − EO/100)` | Điểm **hơn được đám đông** |

Bản cũ xếp theo EV, cắt lấy top 20, rồi mới dán nhãn lên đúng lát cắt đó — nên
một lựa chọn ceiling hay differential đứng thứ 25 về EV thì vĩnh viễn không bao
giờ hiện ra.

**Lợi thế bứt phá** là con số làm cho danh sách đuổi hạng trung thực được. Nếu
bạn bắt băng cho X trong khi EO của X là *EO*, bạn nhận `2·xP` còn người trung
bình nhận `(EO/100)·xP`, nên phần hơn là

```
edge = xP · (2 − EO/100)
```

Ở EO = 200% (ai cũng sở hữu và ai cũng bắt băng) phần hơn **đúng bằng 0** — bắt
băng cho lựa chọn của cả làng thì không thể lên hạng, xP có cao đến đâu.

### EO dự phóng

FPL công khai tỷ lệ sở hữu nhưng **không** công khai số lượt bắt băng đội trưởng
trước hạn chót, nên nửa sau phải mô hình hoá: chỉ bắt băng được cho người mình
sở hữu, và đám đông dồn vào lựa chọn EV tốt nhất.

```
share(p) ∝ ownership(p) · exp(k · (EV(p) − EV_tốt_nhất)),  k = 0.35
EO = ownership + share
```

`k` hiệu chỉnh theo hình dạng thực tế của một vòng bình thường: một premium áp
đảo lấy khoảng một nửa số băng đội trưởng, á quân khoảng một phần mười, phần còn
lại rải dài. Kết quả **luôn gắn nhãn là dự phóng** kèm độ tin cậy riêng, và tin
cậy thấp nhất đúng ở chỗ quan trọng nhất — các lựa chọn đông người.

### Rủi ro bị thay ra

```
sub_risk = P(bị rút trước phút 60 | có đá chính) = (P(start) − P(60+)) / P(start)
```

Trước đây `P(60+)` được đặt cứng bằng `P(start) × 0.92`, nên chỉ số này ra **đúng
8% cho mọi cầu thủ trong game** — một con số trông như thông tin nhưng không mang
thông tin nào. Giờ tỷ lệ đá trọn trận được ước lượng riêng từng người: ưu tiên
các trận gần nhất mà cầu thủ có đá chính, nếu chưa đủ mẫu thì dùng số phút trên
mỗi lần đá chính của cả mùa, và chỉ khi không có gì mới rơi về mức nền của giải
(có ghi rõ nguồn suy ra). Chỉ số này cũng nuôi lại phần clean sheet trong xP.

## 5d. Tin tức → hành động (`services/news_tiers.py`)

Bộ lọc mức độ (Critical/High/…) chỉ trả lời "nặng cỡ nào", không trả lời "nên
tin đến đâu" và "giờ phải làm gì". Hai thứ biến bảng tin thành quyết định:

**1. Nguồn gốc.** Sáu tầng theo mức trực tiếp của bằng chứng — xem
DATA_SOURCES. Thông cáo CLB và tin đồn không phải cùng một loại bằng chứng, xếp
ngang nhau là cách một trang tin đánh lừa người đọc.

**2. Nó làm thay đổi cái gì.** Con số quan trọng là thay đổi phút thi đấu kỳ
vọng, vì đó là thứ lan vào xP rồi thành quyết định giữ/bán.

```
xMins_trước = estimate_minutes(status='a',   chance=None,   …cùng đầu vào)
xMins_sau   = estimate_minutes(status=thật,  chance=thật,   …cùng đầu vào)
```

`xMins_trước` là **phản thực**, không phải giá trị nhớ lại: mô hình phút thi đấu
là hàm thuần của tình trạng sẵn sàng + phong độ, nên chạy hai lần trên cùng đầu
vào và chỉ đổi tình trạng thì chênh lệch **đúng bằng** phần do tin này gây ra.
Cách này chạy được ngay ở lần đồng bộ đầu tiên sau khi tin nổ, không phải chờ
lần chạy mô hình thứ hai để so.

Khuyến nghị đặt ngưỡng theo **mức giảm tương đối**, không theo mức tuyệt đối:
một cầu thủ dự bị mất 20′ là nhiễu, một trụ cột mất 20′ là chuyện lớn.

| Mức giảm | Khuyến nghị |
|---|---|
| Chắc chắn vắng / xMins < 15 | Giữ → **Bán** |
| ≥ 45% | Giữ → **Bán** |
| ≥ 25% | Giữ → **Cân nhắc bán** |
| ≥ 10% | Giữ → **Theo dõi** |
| < 10% | Giữ |

**Tầng suy luận mô hình cố tình KHÔNG có trước/sau.** Đó là đánh giá thường
trực chứ không phải sự kiện, nên không có mốc "trước" để so; bịa ra một con số
cho đủ cột chính là kiểu thiếu trung thực mà trang này sinh ra để tránh.

Một cầu thủ chỉ có **một thẻ tin** thể hiện trạng thái hiện tại; các bước trước
đó được giữ trong `history` của chính thẻ đó. Tin cũ lặp nguyên văn bị loại, còn
diễn biến thật ("75% ra sân" → "đã rời CLB") thì giữ lại.

## 6. Backtest & chống data leakage (spec §18)

- Dự báo GW *t* chỉ dùng dữ liệu có **trước deadline** GW *t* (`data_cutoff`).
- Đánh giá: MAE, RMSE, Spearman rank, Brier P(start), calibration P(10+), top-10
  precision — công bố trên trang **Model Performance** (`/performance`).
- So baseline: `form` chính thức của FPL và sức mạnh đội suy từ kèo.

**Điều kiện nền: phải đóng băng dự báo trước deadline.** `player_projections` bị
**xoá và ghi lại** mỗi lần chạy engine, nên dự báo đưa ra trước deadline không còn
tồn tại sau vòng đấu. Vì vậy mỗi lần đồng bộ đều ghi một bản vào
`projection_snapshots`, rồi **khoá** (`is_locked`) khi deadline qua. Không có bước
này thì mọi chỉ số ở trên **vĩnh viễn** không đo được — không phải "chưa đo".

Khoá cũng chính là cơ chế chống data leakage: một lần chạy muộn hơn, lúc đã biết
đội hình ra sân và ai chấn thương, không thể lặng lẽ sửa lại "dự báo" cho đẹp điểm.
Kết quả thật chỉ đổ vào khi vòng đã `finished` — điểm giữa vòng còn đổi vì bonus
chốt muộn và dữ liệu còn được điều chỉnh.

Ba trạng thái của mỗi ô, cố tình phân biệt:

| Trạng thái | Nghĩa |
|---|---|
| có số | kèm cỡ mẫu `n` và hướng nào là tốt hơn |
| chưa có dữ liệu | đo được, nhưng chưa đủ dữ liệu — kèm điều kiện cụ thể để có số |
| **không áp dụng** | **không định nghĩa được** cho cột đó |

Ví dụ của cột cuối: Brier P(start) cho baseline `form`. Chỉ số `form` của FPL là
một con số điểm, nó không phát ra xác suất đá chính nào để mà chấm. Gộp nó vào
"chưa có dữ liệu" sẽ khiến người đọc chờ một con số không bao giờ tới.

Hai điều kiện lọc mẫu: chỉ chấm cầu thủ có `xMins ≥ 20` (gộp cả những người mô
hình dự báo gần như không ra sân sẽ làm MAE trông rất đẹp mà không nói gì về chất
lượng — đoán 0 điểm cho hậu vệ dự bị hầu như luôn đúng), và chỉ công bố khi có từ
30 quan sát.

**Một bất đối xứng của FPL API dễ sập bẫy:** trước vòng 1, tổng cả-mùa
(`minutes`, `bps`, …) vẫn là số của **mùa trước**, nhưng `form` bị **đặt lại về 0**
cho mọi cầu thủ. Nên cột baseline form là hằng số trước vòng 1: Spearman không định
nghĩa được, còn MAE/RMSE vẫn ra số nhưng chỉ đang đo điểm trung bình của giải. Trang
để trống cột đó kèm đúng lý do, thay vì quy sang "chưa đủ mẫu".

### Baseline kèo

Nhà cái không ra giá cho điểm FPL của từng cầu thủ, nên mọi baseline kèo ở cấp cầu
thủ đều phải đi qua một mô hình phân bổ. Cách trung thực nhất là dùng **đúng engine
hiện tại** và chỉ thay một thứ: sức mạnh đội lấy hoàn toàn từ kèo
(`market_weight = 1.0`, không hạ trọng số theo độ mỏng thị trường). Nhờ vậy chênh
lệch đo được đúng là "sức mạnh đội đến từ đâu", không lẫn khác biệt về cách tính.
Cùng xMins, cùng cách chia quỹ bonus, cùng luật điểm.

Chỉ tính cho trận **có kèo**. Trận không có kèo thì engine rơi về mô hình nội bộ, và
một "baseline kèo" như vậy thật ra là chính mô hình đội lốt — so với nó là tự so với
mình. Đã kiểm chứng: dữ liệu hiện chỉ có kèo cho GW1, và baseline chỉ được tính cho
đúng GW1.

Baseline được đóng băng **cùng lượt** với dự báo chính (`build_projections` chạy thêm
một lượt ở chế độ `market_only=True, persist=False`, không ghi gì vào DB). Chụp lệch
thời điểm là so gian lận: một bên sẽ biết tin đội hình muộn hơn bên kia.

**Đây là baseline yếu về mặt phân biệt, và trang nói thẳng điều đó.** Mô hình chính
đã pha 70% kèo, nên chuyển sang 100% kèo chỉ dịch phần 30% còn lại: đo trên GW1,
chênh lệch xP trung bình **0.074** điểm, lớn nhất 0.675. Hai cột gần nhau **không**
có nghĩa "mô hình chỉ ngang nhà cái" — nghĩa là mô hình **đã chứa** nhà cái.

### Lưu khuyến nghị đội trưởng

Trang Đội trưởng tính bốn bảng xếp hạng tại chỗ mỗi lần mở, nên trước đây sau vòng
đấu không còn biết hệ thống đã khuyên ai. Bảng `captain_picks` lưu 3 lựa chọn đầu
của **cả bốn bảng** (EV / An toàn / Ceiling / Đuổi hạng), cùng cơ chế khoá sau
deadline như snapshot. Lưu cả bốn để trả lời được câu đáng hỏi — *chiến lược nào
thắng* — thay vì một con số gộp.

Định nghĩa "đúng": lựa chọn số 1 ghi điểm cao nhất trong **toàn bộ cầu thủ mô hình
dự báo sẽ ra sân** ở vòng đó (xMins ≥ 20). Đây là bar cố tình khó. Bản đầu định
nghĩa sai theo hướng dễ — "cao nhất trong 3 người ta tự lưu", tức chỉ kiểm #1 có hơn
#2 và #3, là tự chấm mình — nên có test khoá lại. Không dùng "cao nhất trong đội của
bạn" vì đội hình lúc deadline không được lưu. Vì bar khó, `top_n_hit_rate` (người
hay nhất có nằm trong nhóm đầu ta đưa ra) mới là số nên đọc để so giữa các bảng.

**Còn chưa nối:** ba trong sáu chỉ số quyết định cần khuyến nghị đã lưu mà chỉ sinh
ra khi người dùng bấm chạy (`next_gw`, `free_hit`, `wildcard` — đã lưu sẵn vào
`optimization_runs`), và `Bench order points gained` cần thêm dữ liệu autosub thật.
Trang nói rõ từng cái thiếu gì.

## 7. Giới hạn đã biết

- Selling price của manager không có trên API công khai → xấp xỉ bằng giá hiện tại.
- Free transfer banked ước lượng từ lịch sử (người dùng có thể chỉnh tay).
- Khi chưa cấu hình provider tỷ lệ, xác suất là **model estimate**, không phải giá thị trường.
- Expert signals mặc định là **mock có nhãn**; thay bằng RSS/API hợp lệ để dùng thật.
