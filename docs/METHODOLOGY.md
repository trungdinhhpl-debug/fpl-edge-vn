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

rồi nhân với `xMins/90` và hệ số độ khó trận:

```
fixture_adj = clamp(λ_team_goals(fixture) / λ_team_goals(season_avg), 0.4, 1.8)
goal_EV     = xg90_shrunk · (xMins/90) · fixture_adj · pen_bump · goal_pts[pos]
assist_EV   = xa90_shrunk · (xMins/90) · fixture_adj · 3
appearance  = P(≥60')·2 + (P(appear) − P(≥60'))·1
clean_sheet = e^(−λ_conceded) · P(≥60') · cs_pts[pos]
conceded    = (λ_conceded / 2) · (−1) · P(≥60')          # GK/DEF
saves_EV    = (saves90 · xMins/90 · shot_adj) / 3         # GK
defcon_EV   = 2 · P(actions ≥ ngưỡng)                     # Poisson
bonus_EV    = chia quỹ 6 điểm trong nội bộ trận            # xem 2c
```

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
- Đánh giá: MAE, RMSE, Spearman rank, Brier (goal/CS), calibration, captain hit-rate,
  top-10 precision, tỷ lệ cầu thủ không ra sân.
- So baseline: tổng điểm mùa, form chính thức, FDR chính thức, ownership cao nhất, "không chuyển nhượng".

## 7. Giới hạn đã biết

- Selling price của manager không có trên API công khai → xấp xỉ bằng giá hiện tại.
- Free transfer banked ước lượng từ lịch sử (người dùng có thể chỉnh tay).
- Khi chưa cấu hình provider tỷ lệ, xác suất là **model estimate**, không phải giá thị trường.
- Expert signals mặc định là **mock có nhãn**; thay bằng RSS/API hợp lệ để dùng thật.
