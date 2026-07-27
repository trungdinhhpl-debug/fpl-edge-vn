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
            <p>Tỷ lệ per-90 (xG, xA, def. contribution) được <b>Bayesian shrinkage</b> về mức nền theo vị trí để tránh đánh giá quá cao mẫu nhỏ, rồi nhân với xMins và độ khó trận đấu (mô hình Poisson theo sức mạnh đội).</p>
            <p>Luật tính điểm đọc từ cấu hình mùa hiện tại (2025/26, gồm <b>Defensive Contribution</b>) — không hard-code luật mùa cũ.</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Kèo nhà cái (Tier-2)</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm text-muted-foreground">
            <p>
              Với những vòng đã có kèo, hệ thống lấy đồng thuận của ~20 nhà cái (The Odds API)
              và <b>giải ngược ra số bàn kỳ vọng</b> mỗi đội:
            </p>
            <code className="block rounded bg-muted p-2 text-xs text-foreground">
              Tài/xỉu → tổng bàn T · Kèo 1X2 → chênh lệch S · λ_nhà=(T+S)/2, λ_khách=(T−S)/2
            </code>
            <p>
              Giá đã khử biên lợi nhuận (de-vig) và lấy trung bình nhiều nhà cái, sau đó
              pha với mô hình nội bộ theo trọng số {data?.market_odds?.market_weight ?? 0.7} cho thị trường.
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
          <CardHeader><CardTitle>Monte Carlo & rủi ro</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm text-muted-foreground">
            <p>Mô phỏng ở cấp <b>trận đấu của đội</b> để giữ tương quan: sạch lưới dùng chung cho GK + hậu vệ; bàn thắng của cầu thủ rút từ tổng bàn của đội (không giả định độc lập).</p>
            <p>Xuất: trung vị, P25/P75/P90, ceiling (P95), P(blank), P(≥5), P(≥10), phương sai.</p>
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
