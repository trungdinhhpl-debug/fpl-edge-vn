"use client";
import { BookOpen } from "lucide-react";
import { useApi } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle, Badge } from "@/components/ui";

export default function MethodologyPage() {
  const { data } = useApi<any>("/api/model/health");

  return (
    <div className="space-y-5">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold"><BookOpen className="h-6 w-6 text-primary" /> Phương pháp (Methodology)</h1>
        <p className="text-sm text-muted-foreground">Minh bạch mô hình: dùng gì, không dùng gì, và giới hạn.</p>
      </div>

      {data && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat label="Model version" value={data.model_version} />
          <Stat label="Cầu thủ" value={data.players} />
          <Stat label="Projections" value={data.projections} />
          <Stat label="Monte Carlo" value={`${(data.montecarlo_iterations / 1000).toFixed(0)}k lần`} />
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Expected Minutes (xMins)</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm text-muted-foreground">
            <p>Kết hợp: số trận đá chính gần đây, phút 3/6/10 trận, trạng thái ra sân (status FPL), % khả năng ra sân, cạnh tranh vị trí và congestion (double gameweek).</p>
            <code className="block rounded bg-muted p-2 text-xs text-foreground">
              xMins = P(start)·E[phút|start] + P(sub)·E[phút|sub]
            </code>
            <p>Đầu ra kèm P(đá chính), P(vào sân), P(không ra sân), khoảng tin cậy và lý do chính.</p>
            <p>
              <b>Chuyển sang dữ liệu thật:</b> tỷ lệ đá chính được làm mượt (Laplace) nên một
              trận duy nhất không tạo ra kết luận chắc nịch — đá chính vòng 1 cho ~63% chứ không
              phải 98%, ngồi ghế vòng 1 cho ~30% chứ không phải 0%. Độ tự tin chỉ tăng khi bằng
              chứng tích luỹ: sau 10 vòng đá chính đều đặn mới lên ~91%.
            </p>
            <p>
              <b>Cầu thủ chưa có phút Ngoại hạng</b> (đội mới lên hạng, tân binh từ giải khác):
              không thể tính tỷ lệ từ mẫu rỗng, nên vai trò được ước lượng theo{" "}
              <b>giá FPL trong từng vị trí của đội</b> — giá do FPL đặt phản ánh vai trò dự kiến.
              Nhóm này luôn bị gắn <b>độ tin cậy Thấp</b> và được thay bằng số phút thật chỉ sau
              vài vòng đấu. Cờ chấn thương/treo giò vẫn luôn được ưu tiên hơn.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Expected Points (xP)</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm text-muted-foreground">
            <code className="block rounded bg-muted p-2 text-xs text-foreground">
              xP = ra sân + bàn + kiến tạo + sạch lưới + cứu thua + bonus + def.contribution − thẻ − thủng lưới
            </code>
            <p>
              Tỷ lệ per-90 (xG, xA, def. contribution) được <b>Bayesian shrinkage</b> về mức
              nền theo vị trí để tránh đánh giá quá cao mẫu nhỏ, rồi nhân với xMins. Hệ số độ
              khó trận đấu <b>chỉ áp cho bàn thắng và kiến tạo</b> — xem mục Defensive
              Contribution bên dưới.
            </p>
            <p>
              <b>Kiến tạo FPL không phải xA.</b> FPL trao kiến tạo rộng hơn Opta (bóng bật ra,
              cú sút bị cản, kiếm 11m…): 4 mùa liền FPL có <b>0,90–0,95 kiến tạo mỗi bàn</b>,
              trong khi xA chỉ ~0,63 mỗi xG — tức kiến tạo thật ≈ <b>1,4 × xA</b>. Trước
              21/10/2026 engine chia kiến tạo theo xA thô nên mọi cầu thủ thiếu ~30% điểm kiến
              tạo, nặng nhất ở người sáng tạo. Nay xA được quy về thang kiến tạo FPL (tỷ lệ đo
              tại chỗ), trộn <b>20% số kiến tạo thật</b>, và tổng kiến tạo của đội ={" "}
              <code>λ × kiến tạo mỗi bàn của giải</code>. Con số 20% đến từ backtest 2 mùa
              (20 709 lượt cầu thủ-vòng, dự báo vòng N chỉ bằng dữ liệu trước N): giảm sai số
              2,3%, khoảng tin cậy hoàn toàn về phía cải thiện. Làm y hệt cho <b>bàn thắng</b>{" "}
              thì không cải thiện gì (−0,07%, khoảng tin cậy chứa 0) — nên bàn thắng vẫn chỉ
              theo xG: ghi vượt xG trong vài chục trận chủ yếu là may rủi.
            </p>
            <p>
              <b>Defensive Contribution là luật ngưỡng, không phải tỷ lệ.</b> Hậu vệ đạt{" "}
              <b>≥ 10</b> hành động (CBIT) được 2 điểm; tiền vệ và tiền đạo cần <b>≥ 12</b>{" "}
              (CBIRT); thủ môn không có. Mô hình tính <code>2 × P(số hành động ≥ ngưỡng)</code>{" "}
              với số hành động theo Poisson — trần 2 điểm mỗi trận được thoả theo cấu tạo vì
              xác suất không vượt 1. Rổ hành động theo vị trí do chính FPL cung cấp sẵn trong
              trường <code>defensive_contribution</code>, đã kiểm chứng trên dữ liệu mùa
              2025/26 (hậu vệ khớp CBI+tackles 69/71, tiền vệ khớp thêm recoveries 87/88).
            </p>
            <p className="rounded-md bg-caution/10 px-2.5 py-2 text-caution">
              ⚠ Giới hạn: hành động phòng ngự <b>chưa phản ứng với đối thủ</b>. Đo được Gabriel
              (Arsenal) có defcon = 0,293 giống hệt ở cả 8 vòng, gặp Coventry hay Chelsea không
              khác gì — trong khi thực tế hậu vệ bị vây hãm sẽ phá bóng và cản phá nhiều hơn.
              Sửa đúng phải dùng hệ số theo λ <i>bàn thua</i>, không phải hệ số độ khó hiện có
              (vốn xây trên λ bàn thắng của chính đội mình, dùng vào đây sẽ cho kết quả ngược).
            </p>
            <p>Luật tính điểm đọc từ <code>game_config</code> của FPL cho mùa đang chạy (gồm <b>Defensive Contribution</b>) — không hard-code tên mùa hay điểm từng hạng mục.</p>
            <p>
              Riêng <b>trọng số BPS thì FPL không phát qua API</b>, nên chúng được đánh phiên
              bản theo mùa trong code. Mùa <b>2026/27</b> hạ BPS từ clearances/blocks/interceptions
              (1 điểm mỗi 3 hành động thay vì mỗi 2), bỏ trừ điểm khi bị qua người và thêm thưởng
              cứu thua từ big chance. Vì trước vòng 1 FPL vẫn phát tổng BPS của <i>mùa trước</i>,
              tổng đó được <b>quy đổi về luật mùa này</b> trước khi vào mô hình bonus — nếu không,
              trung vệ sẽ bị định giá cao hơn thực tế.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Bonus — chia quỹ 6 điểm mỗi trận</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm text-muted-foreground">
            <p>
              Mỗi trận FPL phát đúng <b>6 điểm bonus</b> (3 + 2 + 1) cho ba người có BPS cao
              nhất; người thứ tư được 0 dù BPS bao nhiêu. Nên bonus <b>không phải thuộc tính
              của một cầu thủ</b> mà là kết quả tranh giành trong một trận cụ thể — không thể
              tính từ một người đứng riêng.
            </p>
            <code className="block rounded bg-muted p-2 text-xs text-foreground">
              trọng số = (BPS kỳ vọng trong trận)^1.99 · bonus = 6 × trọng số / Σ trọng số (cả hai đội)
            </code>
            <p>
              Số mũ <b>1.99 đo từ dữ liệu</b> (hồi quy log-log bonus/90 theo BPS/90 trên 252
              cầu thủ đá từ 900 phút mùa 2025/26), không phải hệ số chọn tay. Nó lớn hơn 1 vì
              cơ chế top-3: BPS gấp đôi cho bonus gấp khoảng bốn lần.
            </p>
            <p>
              Cách chia này <b>bảo toàn quỹ theo đúng luật</b>: tổng bonus mô hình phân bổ cho
              một vòng 10 trận là 60.0 điểm. Bản trước tính bonus như một công thức rời và chỉ
              phân bổ <b>2.47 điểm mỗi trận</b> — hụt khoảng 60% ở mọi vị trí.
            </p>
            <p>
              Phần còn hụt được nói rõ chứ không lấp: hậu vệ hiện ở khoảng <b>68%</b> mức bonus
              thực nhận mùa trước. Một phần là đúng (luật BPS 2026/27 hạ điểm CBI nên hậu vệ
              thật sự kiếm ít hơn, khoảng −13% sau khi tính số mũ), phần còn lại chưa giải
              thích được. Chúng tôi không nhân thêm hệ số theo vị trí để kéo về 100%, vì mốc so
              sánh vừa theo luật BPS mùa cũ, vừa được chia theo số phút cuối mùa — thông tin mà
              ở vòng 1 không ai có.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Kèo nhà cái (Tier-2)</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm text-muted-foreground">
            <p>
              Với những vòng đã có kèo, hệ thống lấy đồng thuận của ~20 nhà cái (The Odds API)
              và <b>giải ngược ra số bàn kỳ vọng</b> mỗi đội. Hai tham số λ được khớp{" "}
              <b>đồng thời với cả ba thị trường</b> trên cùng một ma trận tỷ số, thay vì
              giải lần lượt từng thị trường:
            </p>
            <code className="block rounded bg-muted p-2 text-xs text-foreground">
              min (λ_nhà, λ_khách): w₁·sai số(1X2) + w₂·sai số(tài/xỉu) + w₃·sai số(kèo châu Á)
            </code>
            <p>
              Ma trận tỷ số dùng hiệu chỉnh <b>Dixon–Coles</b> (ρ ={" "}
              {data?.market_odds?.inversion?.dixon_coles_rho ?? -0.13}): nâng xác suất các tỷ
              số thấp <b>0-0</b> và <b>1-1</b>, hạ <b>1-0</b> và <b>0-1</b> — đúng chỗ mà mô
              hình Poisson độc lập sai nhiều nhất, vì nó định giá hụt các trận hòa ít bàn.
              Hiệu chỉnh này chỉ đổi quan hệ giữa hai đội, không đổi kỳ vọng bàn thắng của
              từng đội.
            </p>
            <p>
              Giá được <b>khử biên lợi nhuận (de-vig) theo từng nhà cái trước</b>, rồi mới tổng
              hợp — biên của một nhà cái không lẫn sang giá của nhà cái khác. Tổng hợp bằng{" "}
              <b>trung vị</b> chứ không phải trung bình: trung bình cho mỗi nhà cái quyền dịch
              đồng thuận 1/n nên một nhà cái treo giá cũ là đủ kéo lệch, trung vị thì không.
              Với 1X2, trung vị lấy theo từng kết cục rồi chuẩn hoá lại.
            </p>
            <p>
              Sau đó pha với mô hình nội bộ theo trọng số{" "}
              {data?.market_odds?.market_weight ?? 0.7} cho thị trường — nhưng trọng số này{" "}
              <b>hạ theo độ mỏng của thị trường</b> (nhân với số nhà cái chia cho{" "}
              {data?.market_odds?.full_support_books ?? 8}, tối đa 1). Đồng thuận 20 nhà cái và
              giá lẻ của 2 nhà cái không phải cùng một loại bằng chứng.
            </p>
            <p>
              Hệ thống <b>không</b> gán trọng số cho nhà cái theo thanh khoản hay độ chính xác
              lịch sử: nguồn dữ liệu không công bố doanh số cũng không công bố kết quả đã quyết
              toán, nên một trọng số như vậy sẽ là số tự đặt. Trung vị đồng trọng số là đồng
              thuận mạnh nhất mà dữ liệu hiện có cho phép.
            </p>
            <p>
              Vòng <b>chưa có kèo</b> dùng mô hình nội bộ và được gắn nhãn “model estimate” —
              không bao giờ trình bày số của mô hình như giá thị trường thật.
              {data?.market_odds?.fixtures_covered ? (
                <> Hiện có kèo cho <b>{data.market_odds.fixtures_covered} trận</b>
                  {data.market_odds.gameweeks?.length ? ` (GW ${data.market_odds.gameweeks.join(", ")})` : ""}.</>
              ) : null}
            </p>
          </CardContent>
        </Card>

        {data?.championship_data?.teams_covered ? (
          <Card>
            <CardHeader><CardTitle>Đội mới lên hạng</CardTitle></CardHeader>
            <CardContent className="space-y-2 text-sm text-muted-foreground">
              <p>
                Ba đội vừa lên hạng không có dữ liệu Ngoại hạng. Thay vì chấm giống hệt nhau,
                hệ thống dùng kết quả <b>Championship {data.championship_data.season}</b>{" "}
                ({data.championship_data.teams_covered} đội, nguồn {data.championship_data.source})
                để <b>xếp hạng họ so với nhau</b>.
              </p>
              <p>
                Quan trọng: <b>không</b> quy đổi bàn thắng Championship thành bàn thắng Ngoại hạng.
                Chỉ số được neo vào mức nền dành cho đội mới lên hạng và{" "}
                <b>không bao giờ vượt mức trung bình giải</b> — vô địch Championship vẫn được coi
                là dưới trung bình Ngoại hạng.
              </p>
              <p>
                Mức nền này tự động bị thay thế khi có bằng chứng tốt hơn: kèo nhà cái →
                chỉ số sức mạnh của FPL → kết quả thật khi mùa giải diễn ra.
              </p>
            </CardContent>
          </Card>
        ) : null}

        <Card>
          <CardHeader><CardTitle>Độ khó lịch thi đấu — sáu bước</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm text-muted-foreground">
            <ol className="list-inside list-decimal space-y-1">
              <li>
                <b>Prior đội bóng</b> gộp 5 nguồn có trọng số: 45% xG/xGA đã hiệu chỉnh
                đối thủ, 25% thị trường/Elo, 15% chất lượng đội hình, 10% tính liên tục
                của HLV, 5% dữ liệu tiền mùa. Nguồn nào <b>không có dữ liệu thì bị loại
                khỏi mẫu số</b>, chứ không được mặc định bằng trung bình giải.
              </li>
              <li>
                <b>λ dạng log cộng 10 số hạng</b>: nền giải, tấn công, hở hàng thủ đối
                thủ, sân nhà, đội hình, ngày nghỉ, mật độ, chuyển nhượng, HLV, lên hạng.
                λ bàn thua <b>không có công thức riêng</b> — nó là chính công thức trên
                chạy ngược, nên hai chiều không thể lệch nhau.
              </li>
              <li>
                <b>Ghép với kèo nhà cái bằng trung bình hình học</b> (không phải trung
                bình cộng), trọng số theo số nhà cái và độ trưởng thành của thị trường.
                Độ lệch hệ thống đo trên các trận <i>có</i> giá được áp cho <i>mọi</i>{" "}
                trận — kể cả trận chưa ai ra giá.
              </li>
              <li>
                <b>Độ dễ tính bằng percentile</b>, không phải phép co tuyến tính: độ dễ
                ghi bàn, độ dễ sạch lưới, và độ dễ riêng cho <b>từng vai trò</b> — cùng
                một trận không cùng độ khó cho thủ môn và tiền đạo.
              </li>
              <li>
                <b>Điểm lịch cả cửa sổ</b>: trung bình có trọng số suy giảm theo thời
                gian (nửa chu kỳ 4 vòng), trừ phạt bất định. Vòng trắng tính <b>0 điểm</b>{" "}
                — không đá thì không có điểm, đó là sự thật chứ không phải dữ liệu thiếu.
              </li>
              <li>
                <b>FDR 1–5 theo ngũ phân vị</b>: 20% dễ nhất là 1, 20% khó nhất là 5.
              </li>
            </ol>
            <p className="rounded-md bg-caution/10 px-2.5 py-2 text-caution">
              ⚠ Giới hạn: số hạng <b>mật độ thi đấu</b> hầu như luôn bằng 0 vì FPL API chỉ
              công bố lịch Ngoại hạng — cúp châu Âu, cúp Liên đoàn và FA Cup, đúng những
              giải tạo ra mật độ thật, không có trong dữ liệu. Hệ số ngày nghỉ và mật độ
              cũng chưa khớp được từ dữ liệu, nên chúng cố ý nhỏ và bị chặn hai đầu.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Monte Carlo — phân bổ bàn thắng</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm text-muted-foreground">
            <p>Mô phỏng ở cấp <b>trận đấu của đội</b>: mỗi vòng lặp rút một lần tổng bàn thắng và tổng bàn thua, rồi mới chia cho cầu thủ — sạch lưới dùng chung cho GK + hậu vệ, bàn thắng rút từ chính tổng bàn đó.</p>
            <code className="block rounded bg-muted p-2 text-xs text-foreground">
              share = bàn (hoặc kiến tạo) kỳ vọng giải tích của trận / (P(ra sân) × λ) → chỉnh để trung bình MC khớp đúng kỳ vọng đó
            </code>
            <p>
              <b>Share lấy từ chính xP của trận, không từ tổng cả mùa.</b> Tổng cả mùa đã
              gồm sẵn số phút từng được đá, mà Monte Carlo lại tự rút ai ra sân — dùng nó
              là tính khả năng ra sân hai lần, và trụ cột ôm phần của người đá luân phiên
              (bàn thắng MC của nhóm xP &gt; 2,5 từng cao hơn xP 15–34%). Giờ kỳ vọng của
              trận chia cho xác suất ra sân thành share <i>khi có mặt</i>, rồi vài vòng
              chỉnh đưa trung bình MC về đúng xP (lệch ≤ 3% theo vị trí). Vì thế npxG,
              phần 11m theo <code>penalties_order</code> và điều chỉnh vai trò/kèo đi vào
              phân phối đúng như đi vào xP.
            </p>
            <p>
              Tổng bàn được chia theo <b>Multinomial</b>, nên phần chia ra không bao giờ vượt
              số bàn đội thực ghi. Share của cầu thủ <b>không ra sân được chuyển cho những
              người có mặt</b> theo tỷ lệ, và <b>không ai kiến tạo cho bàn của chính mình</b>.
            </p>
            <p>
              Tương quan đồng đội tuỳ theo họ <i>chia sẻ</i> hay <i>cạnh tranh</i>:
              GK ↔ hậu vệ <b>+0,61</b> (cùng một clean sheet), còn hai tiền đạo{" "}
              <b>−0,05</b> (chia nhau cùng số bàn, và người này vắng thì người kia được nhiều hơn).
            </p>
            <p>
              <b>xP không đến từ Monte Carlo</b> — xP tính giải tích; mô phỏng chỉ sinh ra
              ceiling, floor, P(haul), phương sai.
            </p>
            <p>
              <b>Penalty đã tách khỏi bóng sống — bằng số quả THẬT.</b> FPL chỉ phát xG
              tổng (gồm 11m) và không có số quả 11m đã đá, nên số đó lấy từ API công khai
              của Premier League (khớp cầu thủ bằng mã Opta). Engine trừ đúng{" "}
              <b>0,79 × số quả đã đá</b> khỏi xG của mùa này <i>và</i> của nền mùa trước,
              rồi cộng lại phần 11m kỳ vọng theo <code>penalties_order</code> (người số 2
              chỉ được phần khi người số 1 vắng — suy từ mô hình phút). Tỷ lệ 11m của giải
              cũng đếm thật: 103 quả trên 900 trận-đội (mùa này + mùa trước) → 0,090
              xG/trận-đội.
            </p>
            <p>
              Vì sao phải đếm thật: trước 20/10/2026 engine trừ một <i>tỷ lệ trung bình</i>.
              Le Fée (Sunderland) được FPL xếp đá 11m <b>số 2</b> nhưng đã đá{" "}
              <b>6 quả trong ~40 trận</b> — 1,58/2,09 xG mùa này và 3,16/5,22 mùa trước là
              11m. Trừ theo tỷ lệ chỉ bớt ~0,05/90, nên engine tưởng anh ta tạo ~0,2 xG
              bóng sống mỗi 90 phút trong khi thật là ~0,07, và xP 5 vòng bị thổi lên
              ~3,6 điểm. Khi nguồn Premier League không lấy được, engine quay về cách trừ
              theo tỷ lệ.
            </p>
            <p>
              <b>Đội tấn công yếu được ít 11m hơn — gần tỷ lệ thuận.</b> Đo trên 140
              đội-mùa (2019/20–2025/26, 708 quả): 1/3 đội yếu nhất được 0,109 quả/trận, 1/3
              mạnh nhất 0,175. Hồi quy Poisson cho độ co giãn 0,74 ± 0,27 theo số bàn không
              tính 11m; hiệu chỉnh nhiễu của thước đo đó ra <b>0,95</b> (theo số lần chạm
              bóng trong vòng cấm: 1,09). Nên phần 11m của mỗi trận nhân{" "}
              <code>(λ trận / nền giải)^0,95</code> — so với nền <i>của giải</i>, không so
              với trung bình của chính đội. Mỗi quả 11m còn có <b>19% hỏng (−2 điểm)</b>:
              tỷ lệ vào đo được 574/708 = 81%.
            </p>
            <p>
              Phần 11m chỉ chiếm khoảng <b>4–9% xP 5 vòng</b> của người đá 11m số 1 (1–2
              điểm). Tiền đạo đá 11m đứng cao chủ yếu vì xG bóng sống: Barry chưa đá quả nào
              nhưng chiếm 51% xG của Everton.
            </p>
            <p>
              <b>Double Gameweek đã mô phỏng xoay tua và mệt mỏi.</b> Hai lần đá chính
              giờ có tương quan âm, dựng bằng phân phối hai điểm{" "}
              <b>giữ nguyên biên duyên</b> — nên xP không đổi một chút nào, chỉ phương
              sai đổi (đo được −2,3% đến −5,5% độ lệch chuẩn). Giới hạn khả thi tự chặn
              theo mức chắc suất: trụ cột 95% gần như không xoay được, cầu thủ luân phiên
              60% thì xoay mạnh. Trận thứ hai còn bị trừ phút theo số ngày nghỉ.
            </p>
            <details className="rounded-md border p-2">
              <summary className="cursor-pointer text-xs font-medium text-foreground">
                Hệ số chưa khớp được từ dữ liệu
              </summary>
              <ul className="mt-2 space-y-1.5 text-xs">
                <li>
                  <b>Tương quan xoay tua (−0,25).</b> DB chưa có vòng đôi nào đã đá để khớp.
                  Đặt 0 là quay về hành vi cũ.
                </li>
                <li>
                  Hai hệ số 11m trước đây nằm ở đây — độ co giãn 0,5 và tỷ lệ vào 79% — nay
                  đều <b>đo được</b> (0,95 và 81%, xem trên). Riêng −6 BPS của quả 11m hỏng
                  chưa đưa vào phần bonus, và Monte Carlo chưa mô phỏng quả hỏng (lệch xP
                  ~0,04 điểm/trận với người đá 11m).
                </li>
              </ul>
            </details>
            <p>Xuất: trung vị, P25/P75/P90, ceiling (P95), P(blank), P(≥5), P(≥10), P(≥15), phương sai.</p>
            <p>3 chỉ số rủi ro riêng: Minutes / Performance / Structural.</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Tối ưu đội hình (MILP)</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm text-muted-foreground">
            <p>Dùng PuLP/CBC (mixed-integer programming) tuân thủ đầy đủ luật FPL: ngân sách, 2/5/5/3, tối đa 3 cầu thủ/CLB, sơ đồ hợp lệ, đội trưởng.</p>
            <p>Planner đa vòng có chiết khấu vòng xa, giá trị giữ free transfer, chi phí hit và ràng buộc rủi ro theo 3 chiến lược.</p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader><CardTitle>Nguyên tắc & giới hạn</CardTitle></CardHeader>
        <CardContent className="space-y-2 text-sm">
          <div className="flex flex-wrap gap-2">
            <Badge className="bg-positive/15 text-positive">Dùng: xG/xA, xMins, độ khó lịch, sân nhà/khách, set-piece, penalty</Badge>
            <Badge className="bg-danger/15 text-danger">Không dùng: tổng điểm đơn thuần, form 3-5 trận, ownership làm bằng chứng “tốt”</Badge>
          </div>
          <ul className="list-inside list-disc space-y-1 text-muted-foreground">
            <li>Không khẳng định chắc chắn cầu thủ sẽ ghi bàn / giữ sạch lưới / đá chính — mọi dự báo có mức tin cậy.</li>
            <li>Ownership chỉ dùng cho an toàn thứ hạng / Effective Ownership / mức khác biệt, không phải bằng chứng chất lượng.</li>
            <li>Tín hiệu chuyên gia không ghi đè dữ liệu chính thức hay tin ra sân đã xác nhận.</li>
            <li>Nếu chưa có API tỷ lệ cược, hệ thống dùng mô hình nội bộ và gắn nhãn “model estimate”.</li>
            <li>Backtest phải chống data leakage (không dùng dữ liệu sau deadline để dự báo vòng trước đó).</li>
          </ul>
          <p className="text-xs text-muted-foreground">Nguồn dữ liệu chính: FPL API công khai. Sản phẩm độc lập, không liên kết Premier League/FPL.</p>
        </CardContent>
      </Card>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: any }) {
  return (
    <div className="rounded-md bg-muted/50 p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-lg font-bold">{value}</div>
    </div>
  );
}
