"use client";
import { useParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import {
  Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from "recharts";
import { useApi } from "@/lib/api";
import { PlayerScorecard } from "@/components/player-scorecard";
import { Card, CardContent, CardHeader, CardTitle, Spinner, ErrorBox, Badge, Stat } from "@/components/ui";
import { PlayerAvatar, PosTag, RiskBadge, MockTag } from "@/components/fpl";
import { fmt, pct } from "@/lib/format";

export default function PlayerDetail() {
  const params = useParams();
  const id = params?.id as string;
  const { data, loading, error } = useApi<any>(id ? `/api/players/${id}` : null);

  if (loading) return <Spinner label="Đang tải cầu thủ…" />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;

  const p = data.player;
  const horizon = data.horizon ?? [];
  const u = data.underlying ?? {};
  const verdict = data.verdict ?? {};
  const next = horizon[0];

  const chartData = horizon.map((h: any) => ({
    gw: `GW${h.gameweek}`,
    xP: h.xp,
    xMins: h.xmins,
    ceiling: h.ceiling,
  }));

  const breakdown = next
    ? Object.entries(next.breakdown).map(([k, v]) => ({ name: k, value: Number(v) }))
    : [];

  const verdictColor =
    verdict.label?.includes("Mua") ? "bg-positive/15 text-positive"
    : verdict.label?.includes("Bán") ? "bg-danger/15 text-danger"
    : "bg-caution/15 text-caution";

  return (
    <div className="space-y-6">
      <Link href="/players" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" /> Player Explorer
      </Link>

      {/* Header */}
      <div className="flex flex-wrap items-center gap-4 rounded-xl border bg-card p-4">
        <PlayerAvatar player={p} size={64} />
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-bold">{p.full_name || p.name}</h1>
            <PosTag pos={p.position} />
            {p.penalties_order === 1 && <Badge className="bg-primary/15 text-primary">Penalty #1</Badge>}
          </div>
          <p className="text-sm text-muted-foreground">
            {p.team_name} · £{fmt(p.price)} · {fmt(p.selected_by_percent)}% sở hữu
          </p>
          {p.news && <p className="mt-1 text-sm text-caution">⚠ {p.news}</p>}
        </div>
        <Badge className={`${verdictColor} text-sm`}>{verdict.label}</Badge>
      </div>

      {/* Stat row */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
        <Stat label="xP vòng tới" value={fmt(next?.xp)} />
        <Stat label="xP 5 vòng" value={fmt(verdict.xp_next5)} />
        <Stat label="xMins" value={next ? `${fmt(next.xmins, 0)}'` : "–"} />
        <Stat label="Ceiling" value={fmt(next?.ceiling)} />
        <Stat label="CS%" value={next ? pct(next.clean_sheet_prob) : "–"} />
        <Stat label="P(haul)" value={next ? pct(next.p_haul) : "–"} />
      </div>

      {/* Bộ chỉ số đầy đủ: phân phối, phút, VORP, bốn loại rủi ro, độ mới dữ liệu */}
      <PlayerScorecard playerId={id as string} />

      <div className="grid gap-6 lg:grid-cols-2">
        {/* xP horizon chart */}
        <Card>
          <CardHeader><CardTitle>xP & xMins theo vòng</CardTitle></CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={240}>
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                <XAxis dataKey="gw" tick={{ fontSize: 12 }} stroke="hsl(var(--muted-foreground))" />
                <YAxis tick={{ fontSize: 12 }} stroke="hsl(var(--muted-foreground))" />
                <Tooltip contentStyle={{ background: "hsl(var(--card))", border: "1px solid hsl(var(--border))", borderRadius: 8, fontSize: 12 }} />
                <Line type="monotone" dataKey="xP" stroke="hsl(var(--primary))" strokeWidth={2} dot={{ r: 3 }} />
                <Line type="monotone" dataKey="ceiling" stroke="hsl(var(--caution))" strokeWidth={1} strokeDasharray="4 4" dot={false} />
              </LineChart>
            </ResponsiveContainer>
            <p className="text-xs text-muted-foreground">Đường nét đứt = ceiling (P95 Monte Carlo).</p>
          </CardContent>
        </Card>

        {/* xP breakdown */}
        <Card>
          <CardHeader><CardTitle>Phân rã xP vòng tới</CardTitle></CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={breakdown} layout="vertical" margin={{ left: 20 }}>
                <XAxis type="number" tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" />
                <YAxis type="category" dataKey="name" tick={{ fontSize: 11 }} width={80} stroke="hsl(var(--muted-foreground))" />
                <Tooltip contentStyle={{ background: "hsl(var(--card))", border: "1px solid hsl(var(--border))", borderRadius: 8, fontSize: 12 }} />
                <Bar dataKey="value" radius={[0, 4, 4, 0]}>
                  {breakdown.map((b, i) => (
                    <Cell key={i} fill={b.value >= 0 ? "hsl(var(--positive))" : "hsl(var(--danger))"} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Underlying */}
        <Card>
          <CardHeader><CardTitle>Dữ liệu nền tảng (mùa này)</CardTitle></CardHeader>
          <CardContent className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-3">
            <UStat label="xG" value={fmt(u.expected_goals, 2)} />
            <UStat label="xA" value={fmt(u.expected_assists, 2)} />
            <UStat label="xGI" value={fmt(u.expected_goal_involvements, 2)} />
            <UStat label="Bàn thắng" value={u.goals_scored} />
            <UStat label="Kiến tạo" value={u.assists} />
            <UStat label="Def. Contribution" value={fmt(u.defensive_contribution, 0)} />
            <UStat label="Vượt xG" value={fmt(u.xg_overperformance, 1)} warn={u.xg_overperformance > 1.5} />
            <UStat label="BPS" value={u.bps} />
            <UStat label="Số trận đá chính" value={u.starts} />
          </CardContent>
        </Card>

        {/* Verdict + experts */}
        <Card>
          <CardHeader><CardTitle>Kết luận & nhận định</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <div className={`rounded-md p-3 ${verdictColor}`}>
              <div className="font-semibold">{verdict.label} · xP5 {fmt(verdict.xp_next5)}</div>
              <ul className="mt-1 list-inside list-disc space-y-0.5 text-sm">
                {verdict.reasons?.map((r: string, i: number) => <li key={i}>{r}</li>)}
              </ul>
            </div>
            {data.expert_signals?.length > 0 && (
              <div className="space-y-2">
                <div className="text-xs font-medium text-muted-foreground">Tín hiệu chuyên gia</div>
                {data.expert_signals.map((s: any, i: number) => (
                  <div key={i} className="rounded-md border p-2 text-sm">
                    <div className="flex items-center justify-between">
                      <Badge className="bg-primary/10 text-primary">{s.type}</Badge>
                      <span className="flex items-center gap-1 text-xs text-muted-foreground">
                        {s.source} {s.is_mock && <MockTag />}
                      </span>
                    </div>
                    <p className="mt-1 text-muted-foreground">{s.summary}</p>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function UStat({ label, value, warn }: { label: string; value: any; warn?: boolean }) {
  return (
    <div className="rounded-md bg-muted/40 p-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`font-bold tabular-nums ${warn ? "text-caution" : ""}`}>{value}</div>
    </div>
  );
}
