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
          <CardHeader><CardTitle>Monte Carlo — phân bổ bàn thắng</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm text-muted-foreground">
            <p>Mô phỏng ở cấp <b>trận đấu của đội</b>: mỗi vòng lặp rút một lần tổng bàn thắng và tổng bàn thua, rồi mới chia cho cầu thủ — sạch lưới dùng chung cho GK + hậu vệ, bàn thắng rút từ chính tổng bàn đó.</p>
            <code className="block rounded bg-muted p-2 text-xs text-foreground">
              share_goal = xG cầu thủ / Σ xG đội · share_assist = xA cầu thủ / Σ xG đội
            </code>
            <p>
              <b>Là xG share, không phải npxG hay shot share</b> — FPL không tách npxG nên
              phần xG từ chấm 11m nằm luôn trong share.
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
            <details className="rounded-md border p-2">
              <summary className="cursor-pointer text-xs font-medium text-foreground">
                Còn lại 2 giới hạn chưa xử lý
              </summary>
              <ul className="mt-2 space-y-1.5 text-xs">
                <li>
                  <b>Penalty chưa tách riêng.</b> FPL cho <code>penalties_missed</code> nhưng
                  không cho <code>penalties_scored</code> và không cho npxG, nên không tách
                  được phần xG từ chấm 11m mà không áp một giả định trung bình giải cho mọi
                  người đá 11m. Upside riêng của họ vẫn hoà trong share bóng sống.
                </li>
                <li>
                  <b>Double Gameweek không mô phỏng rotation.</b> Hai trận rút độc lập (tương
                  quan +0,0003) → có biến động xoay tua, nhưng không biểu diễn &ldquo;nghỉ
                  trận 1 nên dễ đá trận 2&rdquo; và không có yếu tố mệt mỏi.
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
