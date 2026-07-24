"use client";
import Link from "next/link";
import { AlertTriangle, TrendingUp, Crown, Activity, RefreshCw } from "lucide-react";
import { useApi, postJSON } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Card, CardContent, CardHeader, CardTitle, Spinner, ErrorBox, Badge, Button, Stat } from "@/components/ui";
import { PlayerCard } from "@/components/fpl";
import { DeadlineCountdown } from "@/components/deadline-countdown";
import { fmt, timeAgo } from "@/lib/format";
import { riskBg } from "@/lib/utils";
import { useState } from "react";

export default function Dashboard() {
  const { t, lang } = useT();
  const { data, loading, error, reload } = useApi<any>("/api/dashboard");
  const [refreshing, setRefreshing] = useState(false);

  async function refresh() {
    setRefreshing(true);
    try {
      await postJSON("/api/admin/refresh", {});
      setTimeout(reload, 4000);
    } finally {
      setTimeout(() => setRefreshing(false), 4000);
    }
  }

  if (loading) return <Spinner label={t("loading")} />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;

  const gw = data.gameweek;
  const next = gw?.next;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border bg-gradient-to-r from-primary/10 to-transparent p-4">
        <div>
          <h1 className="text-2xl font-bold">{t("tagline")}</h1>
          <p className="text-sm text-muted-foreground">
            {next ? `${t("gameweek")} ${next.id} · ${next.name}` : "—"}
          </p>
        </div>
        <div className="flex items-center gap-4">
          <div className="text-right">
            <div className="text-xs text-muted-foreground">{t("deadline")}</div>
            <DeadlineCountdown iso={gw?.deadline} />
          </div>
          <Button variant="outline" size="sm" onClick={refresh} disabled={refreshing}>
            <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
            {refreshing ? "…" : "Sync"}
          </Button>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Top predicted */}
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <TrendingUp className="h-4 w-4 text-primary" /> {t("topPredicted")}
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-2 sm:grid-cols-2">
            {data.top_predicted?.slice(0, 10).map((p: any) => (
              <Link key={p.id} href={`/players/${p.id}`}>
                <PlayerCard p={p} />
              </Link>
            ))}
          </CardContent>
        </Card>

        {/* Captain top */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Crown className="h-4 w-4 text-primary" /> {t("captaincy")}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {data.captain_top?.map((c: any, i: number) => (
              <div key={c.id} className="flex items-center justify-between rounded-md border p-2">
                <div className="flex items-center gap-2">
                  <span className="w-4 text-sm font-bold text-muted-foreground">{i + 1}</span>
                  <div>
                    <div className="text-sm font-medium">{c.name}</div>
                    <div className="text-xs text-muted-foreground">{c.team} · {c.position}</div>
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-sm font-bold tabular-nums text-primary">{fmt(c.captain_xp)}</div>
                  <div className="text-[10px] text-muted-foreground">Cap xP</div>
                </div>
              </div>
            ))}
            <Link href="/captaincy" className="block pt-1 text-center text-xs text-primary hover:underline">
              Xem tất cả →
            </Link>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Injury alerts */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-danger" /> {t("injuryAlerts")}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {data.injury_alerts?.length ? (
              data.injury_alerts.map((n: any) => (
                <div key={n.player_id} className="rounded-md border p-2 text-sm">
                  <div className="flex items-center justify-between">
                    <span className="font-medium">{n.name} <span className="text-muted-foreground">· {n.team}</span></span>
                    <Badge className={riskBg(n.impact === "Critical" ? "Very High" : n.impact === "High" ? "High" : "Medium")}>
                      {n.impact}
                    </Badge>
                  </div>
                  {n.news && <p className="mt-1 text-xs text-muted-foreground">{n.news}</p>}
                </div>
              ))
            ) : (
              <p className="text-sm text-muted-foreground">{t("noData")}</p>
            )}
          </CardContent>
        </Card>

        {/* Top transfers in */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Activity className="h-4 w-4 text-primary" /> {t("topTransfers")}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-1.5">
            {data.top_transfers_in?.slice(0, 6).map((p: any) => (
              <div key={p.id} className="flex items-center justify-between text-sm">
                <span className="font-medium">{p.name} <span className="text-muted-foreground">· {p.team}</span></span>
                <span className="tabular-nums text-muted-foreground">
                  +{(p.transfers_in_event / 1000).toFixed(0)}k · xP {fmt(p.xp_next)}
                </span>
              </div>
            ))}
            <p className="pt-1 text-[11px] text-muted-foreground">
              * Lượt mua chỉ phản ánh đám đông, không phải bằng chứng cầu thủ tốt.
            </p>
          </CardContent>
        </Card>

        {/* Blank/Double + freshness */}
        <Card>
          <CardHeader>
            <CardTitle>{t("blankDouble")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {data.blank_double && Object.keys(data.blank_double).length ? (
              Object.entries(data.blank_double).map(([g, v]: any) => (
                <div key={g} className="flex items-center gap-2">
                  <Badge className="bg-primary/10 text-primary">GW{g}</Badge>
                  {v.double?.length > 0 && <span className="text-positive">DGW: {v.double.length} đội</span>}
                  {v.blank?.length > 0 && <span className="text-danger">Blank: {v.blank.length} đội</span>}
                </div>
              ))
            ) : (
              <p className="text-muted-foreground">Không có blank/double sắp tới.</p>
            )}
            <div className="border-t pt-2">
              <div className="mb-1 text-xs font-medium text-muted-foreground">{t("updated")}</div>
              {data.last_updated?.slice(0, 3).map((s: any, i: number) => (
                <div key={i} className="flex items-center justify-between text-xs">
                  <span className="flex items-center gap-1">
                    <span className={`h-1.5 w-1.5 rounded-full ${s.status === "ok" ? "bg-positive" : "bg-danger"}`} />
                    {s.source}
                  </span>
                  <span className="text-muted-foreground">{timeAgo(s.fetched_at, lang)}</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
