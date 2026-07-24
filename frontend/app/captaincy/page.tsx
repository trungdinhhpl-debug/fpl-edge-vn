"use client";
import Link from "next/link";
import { Crown } from "lucide-react";
import { useApi } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Card, CardContent, Spinner, ErrorBox, Badge } from "@/components/ui";
import { PosTag, RiskBadge } from "@/components/fpl";
import { fmt, pct } from "@/lib/format";

export default function CaptaincyPage() {
  const { t } = useT();
  const { data, loading, error } = useApi<any>("/api/captains?limit=20");

  if (loading) return <Spinner label={t("loading")} />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;

  const top = data.candidates?.[0];

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold">{t("captaincy")} · GW{data.gameweek}</h1>
        <p className="text-sm text-muted-foreground">
          Xếp hạng theo captain EV (2×xP) sau khi xét xMins, ceiling và độ bất định — không theo phong độ 1 trận hay danh tiếng.
        </p>
      </div>

      {top && (
        <Card className="border-primary/40 bg-primary/5">
          <CardContent className="flex flex-wrap items-center gap-4 pt-4">
            <Crown className="h-8 w-8 text-primary" />
            <div className="flex-1">
              <div className="text-lg font-bold">{top.name} <span className="text-sm font-normal text-muted-foreground">· {top.team} · {top.position}</span></div>
              <div className="text-sm text-muted-foreground">
                Captain EV cao nhất vòng này. P(≥20đ) = {pct(top.p_haul)} · Ceiling {fmt(top.ceiling)}
              </div>
            </div>
            <div className="text-right">
              <div className="text-3xl font-bold tabular-nums text-primary">{fmt(top.captain_xp)}</div>
              <div className="text-xs text-muted-foreground">Captain xP</div>
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardContent className="p-0">
          <div className="scroll-thin overflow-x-auto">
            <table className="w-full min-w-[860px] text-sm">
              <thead className="border-b bg-muted/40 text-xs uppercase text-muted-foreground">
                <tr>
                  <th className="p-3 text-left">#</th>
                  <th className="p-2 text-left">Cầu thủ</th>
                  <th className="p-2 text-right">Cap xP</th>
                  <th className="p-2 text-right">Floor</th>
                  <th className="p-2 text-right">Ceiling</th>
                  <th className="p-2 text-right">P(≥20)</th>
                  <th className="p-2 text-right">P(blank)</th>
                  <th className="p-2 text-right">EO</th>
                  <th className="p-2 text-center">Rủi ro</th>
                  <th className="p-2 text-left">Phân loại</th>
                </tr>
              </thead>
              <tbody>
                {data.candidates.map((c: any, i: number) => (
                  <tr key={c.id} className="border-b hover:bg-muted/40">
                    <td className="p-3 font-bold text-muted-foreground">{i + 1}</td>
                    <td className="p-2">
                      <Link href={`/players/${c.id}`} className="flex items-center gap-1.5 font-medium hover:text-primary">
                        {c.name} <PosTag pos={c.position} />
                        {c.penalty_taker && <Badge className="bg-primary/15 text-primary">PEN</Badge>}
                      </Link>
                      <span className="text-xs text-muted-foreground">{c.team}</span>
                    </td>
                    <td className="p-2 text-right text-base font-bold tabular-nums text-primary">{fmt(c.captain_xp)}</td>
                    <td className="p-2 text-right tabular-nums text-muted-foreground">{fmt(c.floor)}</td>
                    <td className="p-2 text-right tabular-nums">{fmt(c.ceiling)}</td>
                    <td className="p-2 text-right tabular-nums">{pct(c.p_haul)}</td>
                    <td className="p-2 text-right tabular-nums text-muted-foreground">{pct(c.p_blank)}</td>
                    <td className="p-2 text-right tabular-nums text-muted-foreground">{fmt(c.effective_ownership)}%</td>
                    <td className="p-2 text-center"><RiskBadge level={c.overall_risk} /></td>
                    <td className="p-2">
                      <div className="flex flex-wrap gap-1">
                        {c.tags?.map((tg: string) => (
                          <Badge key={tg} className={
                            tg === "Rủi ro" ? "bg-danger/15 text-danger"
                            : tg === "An toàn" ? "bg-positive/15 text-positive"
                            : "bg-muted text-muted-foreground"
                          }>{tg}</Badge>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
