"use client";
import { ArrowRight, GitBranch, Stethoscope, TrendingUp, TrendingDown } from "lucide-react";
import { Badge } from "@/components/ui";
import { fmt } from "@/lib/format";

/** Reason text carries **bold** for the numbers that matter — render it. */
function Rich({ text }: { text: string }) {
  return (
    <>
      {text.split(/(\*\*[^*]+\*\*)/g).map((chunk, i) =>
        chunk.startsWith("**") && chunk.endsWith("**") ? (
          <b key={i} className="text-foreground">{chunk.slice(2, -2)}</b>
        ) : (
          <span key={i}>{chunk}</span>
        ),
      )}
    </>
  );
}

const TRIGGER_META: Record<string, { label: string; icon: any; cls: string }> = {
  injury: { label: "Chấn thương", icon: Stethoscope, cls: "bg-danger/15 text-danger" },
  price_rise: { label: "Tăng giá", icon: TrendingUp, cls: "bg-positive/15 text-positive" },
  price_fall: { label: "Giảm giá", icon: TrendingDown, cls: "bg-caution/15 text-caution" },
};

export function DecisionTree({ plan, tree }: { plan: any; tree: any }) {
  if (!tree) return null;
  const branchesByGw: Record<number, any[]> = {};
  for (const b of tree.branches ?? []) {
    (branchesByGw[b.at_gameweek] ??= []).push(b);
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">{tree.note}</p>

      <div className="relative space-y-3 border-l-2 border-dashed border-border pl-5">
        {tree.main_line.map((s: any) => {
          const week = plan.weeks.find((w: any) => w.gameweek === s.gameweek);
          const rolling = s.action === "roll";
          return (
            <div key={s.gameweek} className="relative">
              {/* node on the spine */}
              <span
                className={`absolute -left-[27px] top-3 h-3 w-3 rounded-full ring-4 ring-background ${
                  rolling ? "bg-muted-foreground/50" : "bg-primary"
                }`}
              />
              <div className="rounded-md border p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge className="bg-primary/10 text-primary">GW{s.gameweek}</Badge>
                  <span className={`font-semibold ${rolling ? "text-muted-foreground" : ""}`}>
                    {s.label}
                  </span>
                  {s.hits > 0 && <Badge className="bg-danger/15 text-danger">−{s.hit_cost}đ hit</Badge>}
                  <span className="ml-auto text-xs text-muted-foreground">
                    FT {fmt(s.free_transfers)} · XI xP {fmt(s.xi_xp)}
                  </span>
                </div>

                {s.moves?.length > 0 && (
                  <div className="mt-2 space-y-1">
                    {s.moves.map((m: any, i: number) => (
                      <div key={i} className="flex items-center gap-2 text-sm">
                        <Badge className="bg-danger/15 text-danger">OUT</Badge> {m.out?.name}
                        <ArrowRight className="h-3.5 w-3.5" />
                        <Badge className="bg-positive/15 text-positive">IN</Badge> {m.in?.name ?? "?"}
                        {m.in && (
                          <span className="ml-auto text-xs text-muted-foreground">
                            {m.in.team} · {m.in.price}tr
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                )}

                {/* the point of the whole feature: why, with numbers */}
                {s.why?.length > 0 && (
                  <ul className="mt-2 space-y-1 border-t pt-2">
                    {s.why.map((w: any, i: number) => (
                      <li key={i} className="flex gap-2 text-xs text-muted-foreground">
                        <span className="text-muted-foreground/60">›</span>
                        <span><Rich text={w.text} /></span>
                      </li>
                    ))}
                  </ul>
                )}

                {week?.captain_detail?.[0] && (
                  <p className="mt-2 text-xs">
                    👑 Captain: <b>{week.captain_detail[0].name}</b> (xP {fmt(week.captain_detail[0].xp)})
                  </p>
                )}
              </div>

              {/* conditional branches hanging off this GW */}
              {(branchesByGw[s.gameweek] ?? []).map((b: any, i: number) => {
                const meta = TRIGGER_META[b.trigger] ?? {
                  label: b.trigger, icon: GitBranch, cls: "bg-muted text-muted-foreground",
                };
                const Icon = meta.icon;
                return (
                  <div key={i} className="relative ml-6 mt-2">
                    <span className="absolute -left-[19px] top-4 h-px w-4 bg-border" />
                    <div className="rounded-md border border-dashed p-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge className={meta.cls}>
                          <Icon className="mr-1 inline h-3 w-3" />{meta.label}
                        </Badge>
                        <span className="text-sm font-medium">{b.condition}</span>
                        <Badge className="ml-auto bg-muted text-muted-foreground">
                          Tin cậy {b.confidence}
                        </Badge>
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground">{b.evidence}</p>
                      <p className="mt-2 flex items-center gap-1.5 text-sm">
                        <ArrowRight className="h-3.5 w-3.5 shrink-0 text-primary" />
                        <span>{b.action.label}</span>
                        {b.action.cost_vs_main != null && (
                          <span className="ml-auto whitespace-nowrap text-xs text-muted-foreground">
                            {fmt(b.action.cost_vs_main)} xP so với kế hoạch chính
                          </span>
                        )}
                      </p>
                      {b.action.alternatives?.length > 1 && (
                        <p className="mt-1 text-xs text-muted-foreground">
                          Phương án 2: {b.action.alternatives[1].name} ({fmt(b.action.alternatives[1].xp_vs_target)} xP)
                        </p>
                      )}
                      {b.action.tradeoff && (
                        <p className="mt-1 text-xs text-muted-foreground">
                          <b>Đánh đổi:</b> {b.action.tradeoff}
                        </p>
                      )}
                      {b.caveat && (
                        <p className="mt-1 text-xs italic text-muted-foreground/80">{b.caveat}</p>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>
    </div>
  );
}
