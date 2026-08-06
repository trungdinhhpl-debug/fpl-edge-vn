"use client";
import { useApi } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle, Badge, Spinner } from "@/components/ui";

/** Bộ chỉ số đầy đủ của một cầu thủ: phân phối điểm + bốn loại rủi ro + độ mới.
 *
 * Gộp vào một chỗ vì chúng phải đọc CÙNG NHAU: xP 5.0 với P10 = 0 và rotation risk
 * Cao không phải cùng một món hàng với xP 5.0 của người chắc suất.
 */
export function PlayerScorecard({ playerId }: { playerId: number | string }) {
  const { data, error } = useApi<any>(
    playerId ? `/api/players/${playerId}/scorecard` : null,
  );

  if (error) return null;
  if (!data) return <Spinner label="Đang tải chỉ số…" />;

  const d = data.distribution;
  const m = data.minutes ?? {};

  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Phân phối điểm (GW{data.gameweek})</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          {d ? (
            <>
              <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-3">
                <Num label="xP (giải tích)" v={d.xp_mean} />
                <Num label="MC mean" v={d.mc_mean} />
                <Num label="Median" v={d.median} />
                <Num label="P10" v={d.p10} />
                <Num label="P25" v={d.p25} />
                <Num label="P75" v={d.p75} />
                <Num label="P90" v={d.p90} />
                <Num label="P95 (ceiling)" v={d.p95_ceiling} />
                <Num label="P(haul ≥10)" v={d.p_haul} pct />
              </div>
              {/* Lệch giữa hai con số trung bình là dấu hiệu mô phỏng chưa khớp
                  phần giải tích — hiện ra chứ không chọn một cái rồi im lặng. */}
              {d.xp_mean !== null && d.mc_mean !== null &&
                Math.abs(d.xp_mean - d.mc_mean) > 0.5 && (
                  <p className="text-xs text-caution">
                    xP giải tích ({d.xp_mean}) lệch {(d.xp_mean - d.mc_mean).toFixed(2)}{" "}
                    so với trung bình mô phỏng ({d.mc_mean}). xP là con số dùng để xếp
                    hạng; phần trăm phân vị đến từ mô phỏng.
                  </p>
                )}
            </>
          ) : (
            <p className="text-xs text-muted-foreground">
              Chưa có dự báo cho vòng này.
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Phút thi đấu & giá trị</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-3">
            <Num label="xMins" v={m.xmins} />
            <Num label="P(đá chính)" v={m.p_start} pct />
            <Num label="P(không ra sân)" v={m.p_dnp} pct />
            <Num label="P(≥60 phút)" v={m.p_60_plus} pct />
            <Num label="VORP" v={data.vorp?.value} />
            <Num
              label="Model confidence"
              v={data.model_confidence?.value}
              pct
            />
          </div>
          {data.vorp?.basis && (
            <p className="text-xs text-muted-foreground">{data.vorp.basis}</p>
          )}
          {m.reason && (
            <p className="text-xs text-muted-foreground">Lý do xMins: {m.reason}</p>
          )}
        </CardContent>
      </Card>

      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle className="text-base">Rủi ro & độ mới dữ liệu</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2.5 text-sm">
          <RiskRow label="Rotation" r={data.rotation_risk} />
          <RiskRow label="Chấn thương / ra sân" r={data.injury_risk} />
          <RiskRow
            label="Biến động giá"
            r={data.price_risk}
            extra={data.price_risk?.direction}
          />
          <div>
            <div className="flex items-center gap-2">
              <span className="text-foreground">Độ mới dữ liệu</span>
              <Badge
                className={
                  data.source_freshness?.stale
                    ? "bg-caution/15 text-caution"
                    : "bg-positive/15 text-positive"
                }
              >
                {data.source_freshness?.stale ? "đã cũ" : "mới"}
              </Badge>
            </div>
            <div className="text-xs text-muted-foreground">
              {data.source_freshness?.basis}
            </div>
          </div>
          {data.price_risk?.caveat && (
            <p className="text-xs text-muted-foreground/80">
              {data.price_risk.caveat}
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

const LEVEL_CLASS: Record<string, string> = {
  "Thấp": "bg-positive/15 text-positive",
  "Trung bình": "bg-caution/15 text-caution",
  "Cao": "bg-negative/15 text-negative",
};

function RiskRow({ label, r, extra }: { label: string; r: any; extra?: string }) {
  if (!r) return null;
  return (
    <div>
      <div className="flex items-center gap-2">
        <span className="text-foreground">{label}</span>
        <Badge className={LEVEL_CLASS[r.level] ?? "bg-muted text-muted-foreground"}>
          {r.level}
          {extra ? ` · ${extra}` : ""}
        </Badge>
      </div>
      <div className="text-xs text-muted-foreground">{r.basis}</div>
    </div>
  );
}

function Num({ label, v, pct }: { label: string; v: any; pct?: boolean }) {
  const shown =
    v === null || v === undefined
      ? "—"
      : pct
        ? `${Math.round(v * 100)}%`
        : String(v).replace(".", ",");
  return (
    <div>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="font-semibold tabular-nums text-foreground">{shown}</div>
    </div>
  );
}
