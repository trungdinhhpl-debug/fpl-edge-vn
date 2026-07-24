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
