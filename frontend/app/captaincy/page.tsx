"use client";
import { useState } from "react";
import Link from "next/link";
import { Crown, Shield, Rocket, TrendingUp, ArrowLeftRight, Minus } from "lucide-react";
import { useApi } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Card, CardContent, CardHeader, CardTitle, Spinner, ErrorBox, Badge, Select } from "@/components/ui";
import { PosTag } from "@/components/fpl";
import { fmt, pct } from "@/lib/format";

const LISTS = [
  { key: "ev", icon: Crown, cls: "bg-primary/15 text-primary", ring: "border-primary/40 bg-primary/5" },
  { key: "safe", icon: Shield, cls: "bg-positive/15 text-positive", ring: "border-positive/40 bg-positive/5" },
  { key: "ceiling", icon: Rocket, cls: "bg-caution/15 text-caution", ring: "border-caution/40 bg-caution/5" },
  { key: "chase", icon: TrendingUp, cls: "bg-danger/15 text-danger", ring: "border-danger/40 bg-danger/5" },
];

function RiskTag({ label }: { label: string }) {
  const cls =
    label === "Cao" ? "bg-danger/15 text-danger"
    : label === "Trung bình" ? "bg-caution/15 text-caution"
    : label === "Thấp" ? "bg-positive/15 text-positive"
    : "bg-muted text-muted-foreground";
  return <Badge className={cls}>{label}</Badge>;
}

function ConfTag({ label }: { label: string }) {
  const cls =
    label === "Cao" ? "bg-positive/15 text-positive"
    : label === "Thấp" ? "bg-danger/15 text-danger"
    : "bg-muted text-muted-foreground";
  return <Badge className={cls}>{label}</Badge>;
}

export default function CaptaincyPage() {
  const { t } = useT();
  const { data, loading, error, reload } = useApi<any>("/api/captains?limit=12");
  const [active, setActive] = useState("ev");
  const [aId, setAId] = useState<string>("");
  const [bId, setBId] = useState<string>("");

  const cmpUrl =
    aId && bId && aId !== bId ? `/api/captains/compare?a=${aId}&b=${bId}` : null;
  const { data: cmp, loading: cmpLoading } = useApi<any>(cmpUrl);

  if (loading) return <Spinner label={t("loading")} />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!data) return null;

  const list = data.lists[active];
  const meta = LISTS.find((l) => l.key === active)!;
  const MetaIcon = meta.icon;
  const top = list?.players?.[0];

  // union of all four lists — the comparison pickers should not be limited to
  // whichever list happens to be open
  const pool: any[] = [];
  for (const l of LISTS) {
    for (const p of data.lists[l.key]?.players ?? []) {
      if (!pool.some((x) => x.id === p.id)) pool.push(p);
    }
  }
  pool.sort((a, b) => b.captain_xp - a.captain_xp);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold">{t("captaincy")} · GW{data.gameweek}</h1>
        <p className="text-sm text-muted-foreground">
          Bốn câu hỏi khác nhau thì bốn câu trả lời khác nhau — mỗi mục tiêu được xếp
          hạng riêng trên cùng {data.n_candidates} ứng viên, không phải một bảng xP duy nhất.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {LISTS.map((l) => {
          const lst = data.lists[l.key];
          const best = lst?.players?.[0];
          const Icon = l.icon;
          return (
            <button
              key={l.key}
              onClick={() => setActive(l.key)}
              className={`rounded-lg border p-3 text-left transition ${
                active === l.key ? "border-primary bg-primary/5" : "hover:border-primary/40"
              }`}
            >
              <Badge className={l.cls}>
                <Icon className="mr-1 inline h-3 w-3" />
                {lst.title}
              </Badge>
              {best && (
                <div className="mt-2">
                  <div className="font-semibold">{best.name}</div>
                  <div className="text-xs text-muted-foreground">
                    {best.team} · {best.position}
                  </div>
                </div>
              )}
            </button>
          );
        })}
      </div>

      {top && (
        <Card className={meta.ring}>
          <CardContent className="flex flex-wrap items-center gap-4 pt-4">
            <MetaIcon className="h-8 w-8 text-primary" />
            <div className="min-w-[220px] flex-1">
              <div className="text-lg font-bold">
                {top.name}{" "}
                <span className="text-sm font-normal text-muted-foreground">
                  · {top.team} · {top.position}
                </span>
              </div>
              <div className="text-sm text-muted-foreground">{list.desc}</div>
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
            <table className="w-full min-w-[1040px] text-sm">
              <thead className="border-b bg-muted/40 text-xs uppercase text-muted-foreground">
                <tr>
                  <th className="p-3 text-left">#</th>
                  <th className="p-2 text-left">Cầu thủ</th>
                  <th className="p-2 text-right">xP</th>
                  <th className="p-2 text-right">xMins</th>
                  <th className="p-2 text-right">P(start)</th>
                  <th className="p-2 text-right">P(blank)</th>
                  <th className="p-2 text-right">P(10+)</th>
                  <th className="p-2 text-right">P(15+)</th>
                  <th className="p-2 text-center">Pen</th>
                  <th className="p-2 text-right">EO dự phóng</th>
                  <th className="p-2 text-center">Thay ra</th>
                  <th className="p-2 text-center">Tin cậy</th>
                </tr>
              </thead>
              <tbody>
                {list.players.map((c: any) => (
                  <tr key={c.id} className="border-b hover:bg-muted/40">
                    <td className="p-3 font-bold text-muted-foreground">{c.rank}</td>
                    <td className="p-2">
                      <Link href={`/players/${c.id}`} className="flex items-center gap-1.5 font-medium hover:text-primary">
                        {c.name} <PosTag pos={c.position} />
                      </Link>
                      <span className="text-xs text-muted-foreground">{c.team}</span>
                    </td>
                    <td className="p-2 text-right text-base font-bold tabular-nums text-primary">{fmt(c.xp)}</td>
                    <td className="p-2 text-right tabular-nums text-muted-foreground">{fmt(c.xmins, 0)}′</td>
                    <td className="p-2 text-right tabular-nums">{pct(c.p_start)}</td>
                    <td className="p-2 text-right tabular-nums text-muted-foreground">{pct(c.p_blank)}</td>
                    <td className="p-2 text-right tabular-nums">{pct(c.p_10_plus)}</td>
                    <td className="p-2 text-right tabular-nums">{pct(c.p_15_plus)}</td>
                    <td className="p-2 text-center">
                      {c.penalty_order === 1 ? (
                        <Badge className="bg-primary/15 text-primary">Số 1</Badge>
                      ) : (
                        <span className="text-xs text-muted-foreground">{c.penalty_duty}</span>
                      )}
                    </td>
                    <td className="p-2 text-right tabular-nums">
                      {fmt(c.projected_eo, 0)}%
                      <span className="ml-1 text-xs text-muted-foreground">({c.eo_confidence})</span>
                    </td>
                    <td className="p-2 text-center"><RiskTag label={c.substitution_risk_label} /></td>
                    <td className="p-2 text-center"><ConfTag label={c.confidence_label} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <p className="text-xs text-muted-foreground">{data.eo_note}</p>

      {/* -------------------------- head to head --------------------------- */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ArrowLeftRight className="h-5 w-5 text-primary" /> So sánh trực tiếp
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <Select value={aId} onChange={(e) => setAId(e.target.value)} className="max-w-[210px]">
              <option value="">Đội trưởng A…</option>
              {pool.map((p) => (
                <option key={p.id} value={p.id}>{p.name} ({p.team})</option>
              ))}
            </Select>
            <span className="text-sm text-muted-foreground">vs</span>
            <Select value={bId} onChange={(e) => setBId(e.target.value)} className="max-w-[210px]">
              <option value="">Đội trưởng B…</option>
              {pool.map((p) => (
                <option key={p.id} value={p.id}>{p.name} ({p.team})</option>
              ))}
            </Select>
          </div>

          {cmpLoading && <Spinner label="Đang so sánh…" />}
          {cmp?.error && <ErrorBox error={cmp.error} />}

          {cmp && !cmp.error && (
            <>
              <div className="grid gap-3 md:grid-cols-2">
                <Side title={`${cmp.a.name} hơn về`} rows={cmp.a_better} mineKey="a" otherKey="b" />
                <Side title={`${cmp.b.name} hơn về`} rows={cmp.b_better} mineKey="b" otherKey="a" />
              </div>

              {cmp.even.length > 0 && (
                <p className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                  <Minus className="h-3 w-3 shrink-0" /> Ngang nhau:{" "}
                  {cmp.even.map((r: any) => r.dimension).join(", ")}
                </p>
              )}

              <div className="rounded-md border border-primary/40 bg-primary/5 p-3">
                <div className="font-semibold">
                  Kết luận: {cmp.verdict.pick_name}{" "}
                  <span className="text-sm font-normal text-muted-foreground">
                    (chênh {fmt(cmp.verdict.margin_xp)}đ EV)
                  </span>
                </div>
                <p className="mt-1 text-sm text-muted-foreground">{cmp.verdict.reason}</p>
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Side({ title, rows, mineKey, otherKey }: {
  title: string; rows: any[]; mineKey: string; otherKey: string;
}) {
  return (
    <div className="rounded-md border p-3">
      <div className="mb-2 font-semibold">{title}</div>
      {rows.length === 0 ? (
        <p className="text-xs text-muted-foreground">Không hơn ở hạng mục nào.</p>
      ) : (
        <ul className="space-y-1">
          {rows.map((r: any, i: number) => (
            <li key={i} className="flex items-baseline justify-between gap-2 text-sm">
              <span className="text-muted-foreground">{r.dimension}</span>
              <span className="whitespace-nowrap tabular-nums">
                <b>{r[`${mineKey}_display`]}</b>
                <span className="text-xs text-muted-foreground"> vs {r[`${otherKey}_display`]}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
