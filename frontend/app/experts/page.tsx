"use client";
import { MessageSquareQuote } from "lucide-react";
import { useApi } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Card, CardContent, CardHeader, CardTitle, Spinner, ErrorBox, Badge } from "@/components/ui";
import { MockTag } from "@/components/fpl";

export default function ExpertsPage() {
  const { t } = useT();
  const { data, loading, error } = useApi<any>("/api/expert-consensus");

  if (loading) return <Spinner label={t("loading")} />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold"><MessageSquareQuote className="h-6 w-6 text-primary" /> {t("experts")}</h1>
        <p className="text-sm text-muted-foreground">{data.disclaimer}</p>
      </div>

      {/* Sources */}
      <Card>
        <CardHeader><CardTitle>Nguồn chuyên gia & độ tin cậy</CardTitle></CardHeader>
        <CardContent className="p-0">
          <div className="scroll-thin overflow-x-auto">
            <table className="w-full min-w-[640px] text-sm">
              <thead className="border-b bg-muted/40 text-xs uppercase text-muted-foreground">
                <tr>
                  <th className="p-3 text-left">Nguồn</th>
                  <th className="p-2 text-left">Loại</th>
                  <th className="p-2 text-left">Chuyên môn</th>
                  <th className="p-2 text-right">Uy tín</th>
                  <th className="p-2 text-right">Độ chính xác</th>
                  <th className="p-2 text-right">Độc lập</th>
                </tr>
              </thead>
              <tbody>
                {data.sources.map((s: any) => (
                  <tr key={s.name} className="border-b">
                    <td className="p-3 font-medium">
                      {s.url ? <a href={s.url} target="_blank" rel="noreferrer" className="hover:text-primary hover:underline">{s.name}</a> : s.name}
                      {s.verified_track_record && <Badge className="ml-1 bg-positive/15 text-positive">verified</Badge>}
                    </td>
                    <td className="p-2 text-muted-foreground">{s.type}</td>
                    <td className="p-2 text-muted-foreground">{s.expertise}</td>
                    <td className="p-2 text-right tabular-nums">{Math.round(s.reliability * 100)}%</td>
                    <td className="p-2 text-right tabular-nums">{Math.round(s.historical_accuracy * 100)}%</td>
                    <td className="p-2 text-right tabular-nums">{Math.round(s.independence * 100)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      {/* Consensus per player */}
      <Card>
        <CardHeader>
          <CardTitle>Đồng thuận theo cầu thủ</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {data.players?.length ? data.players.map((p: any) => (
            <div key={p.id} className="rounded-md border p-3">
              <div className="flex items-center justify-between">
                <span className="font-semibold">{p.name} <span className="text-sm font-normal text-muted-foreground">· {p.team} · {p.position}</span></span>
                <Badge className="bg-primary/15 text-primary">Điểm đồng thuận {p.consensus_score.toFixed(2)}</Badge>
              </div>
              <div className="mt-2 space-y-1">
                {p.signals.map((s: any, i: number) => (
                  <div key={i} className="flex items-center gap-2 text-sm">
                    <Badge className="bg-muted">{s.type}</Badge>
                    <span className="text-muted-foreground">{s.summary}</span>
                    <span className="ml-auto flex items-center gap-1 text-xs text-muted-foreground">
                      {s.source} {s.is_mock && <MockTag />}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )) : (
            <p className="text-sm text-muted-foreground">
              Chưa có tín hiệu khớp cầu thủ (dữ liệu mẫu khớp theo tên — sẽ đầy đủ khi đồng bộ dữ liệu thật).
            </p>
          )}
          <p className="pt-2 text-xs text-muted-foreground">
            Điểm đồng thuận đã tính hệ số độc lập để tránh “echo chamber” — 20 tài khoản chép lại 1 nguồn không được tính là 20 nguồn.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
