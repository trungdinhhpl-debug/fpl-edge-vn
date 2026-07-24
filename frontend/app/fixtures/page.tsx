"use client";
import { useMemo, useState } from "react";
import { useApi } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Card, CardContent, Spinner, ErrorBox, Button } from "@/components/ui";
import { SortControl, type SortDir } from "@/components/sort-control";
import { fdrClass } from "@/lib/utils";
import { fmt } from "@/lib/format";

type SortKey = "difficulty" | "goals" | "cs" | "team";

const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: "difficulty", label: "Độ khó trung bình" },
  { key: "goals", label: "Σ bàn kỳ vọng" },
  { key: "cs", label: "Σ sạch lưới kỳ vọng" },
  { key: "team", label: "Tên đội" },
];

export default function FixturesPage() {
  const { t } = useT();
  const [mode, setMode] = useState<"attack" | "defence">("attack");
  const [sort, setSort] = useState<SortKey>("difficulty");
  // độ khó: thấp = lịch dễ, nên mặc định xếp tăng dần (dễ nhất lên đầu)
  const [dir, setDir] = useState<SortDir>("asc");
  const { data, loading, error } = useApi<any>("/api/fixtures/ticker?n_gws=8");

  // hooks phải chạy trước mọi return sớm
  const rows = useMemo(() => {
    const list = [...((data?.rows ?? []) as any[])];
    const sign = dir === "asc" ? 1 : -1;
    const get = (r: any) => {
      switch (sort) {
        case "goals":
          return r.sum_proj_goals ?? 0;
        case "cs":
          return r.sum_clean_sheet_prob ?? 0;
        case "team":
          return r.team_name ?? r.team ?? "";
        default:
          return (
            (mode === "attack" ? r.avg_attack_difficulty : r.avg_defence_difficulty) ?? 9
          );
      }
    };
    list.sort((a, b) => {
      const va = get(a);
      const vb = get(b);
      if (typeof va === "string" || typeof vb === "string") {
        return String(va).localeCompare(String(vb)) * sign;
      }
      return ((va as number) - (vb as number)) * sign;
    });
    return list;
  }, [data, sort, dir, mode]);

  if (loading) return <Spinner label={t("loading")} />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;

  const gws = data.gameweeks as number[];
  const key = mode === "attack" ? "attack_difficulty" : "defence_difficulty";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">{t("fixtures")}</h1>
          <p className="text-sm text-muted-foreground">
            Độ khó riêng cho {mode === "attack" ? "tấn công (khả năng ghi bàn)" : "phòng ngự (khả năng giữ sạch lưới)"} — không dùng FDR chính thức.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <SortControl
            value={sort}
            options={SORT_OPTIONS}
            dir={dir}
            onValue={setSort}
            onDir={setDir}
          />
          <div className="flex gap-1 rounded-md border p-1">
            <Button size="sm" variant={mode === "attack" ? "default" : "ghost"} onClick={() => setMode("attack")}>
              Tấn công
            </Button>
            <Button size="sm" variant={mode === "defence" ? "default" : "ghost"} onClick={() => setMode("defence")}>
              Phòng ngự
            </Button>
          </div>
        </div>
      </div>

      <Card>
        <CardContent className="p-0">
          <div className="scroll-thin overflow-x-auto">
            <table className="w-full min-w-[800px] border-collapse text-sm">
              <thead>
                <tr className="border-b bg-muted/40 text-xs uppercase text-muted-foreground">
                  <th className="p-2 text-left">Đội</th>
                  {gws.map((g) => <th key={g} className="p-2 text-center">GW{g}</th>)}
                  <th className="p-2 text-center">{mode === "attack" ? "Σ Bàn" : "Σ CS%"}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row: any) => (
                  <tr key={row.team_id} className="border-b">
                    <td className="whitespace-nowrap p-2 font-semibold">{row.team}</td>
                    {gws.map((g) => {
                      const cells = row.cells[String(g)] ?? [];
                      return (
                        <td key={g} className="p-1 text-center">
                          <div className="flex flex-col gap-0.5">
                            {cells.length === 0 ? (
                              <span className="text-xs text-muted-foreground">–</span>
                            ) : (
                              cells.map((c: any, i: number) => (
                                <div
                                  key={i}
                                  className={`relative rounded px-1 py-0.5 text-[11px] font-medium ${fdrClass(c[key])}`}
                                  title={
                                    `${c.opponent} (${c.is_home ? "H" : "A"}) · ghi ${fmt(c.proj_goals_for)} / thủng ${fmt(c.proj_goals_against)}` +
                                    (c.has_market ? " · nguồn: kèo nhà cái" : " · nguồn: mô hình nội bộ")
                                  }
                                >
                                  {c.opponent}
                                  <span className="opacity-70">{c.is_home ? " (H)" : " (A)"}</span>
                                  {c.has_market && (
                                    <span
                                      aria-hidden
                                      className="absolute right-0.5 top-0 text-[9px] leading-none opacity-80"
                                    >
                                      •
                                    </span>
                                  )}
                                </div>
                              ))
                            )}
                          </div>
                        </td>
                      );
                    })}
                    <td className="p-2 text-center font-bold tabular-nums">
                      {mode === "attack" ? fmt(row.sum_proj_goals) : `${Math.round((row.sum_clean_sheet_prob / Math.max(row.n_fixtures, 1)) * 100)}%`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-4 sm:grid-cols-2">
        <Card>
          <CardContent className="pt-4">
            <h3 className="mb-2 font-semibold">Lịch tấn công tốt nhất (8 vòng)</h3>
            {data.best_attack.map((r: any, i: number) => (
              <div key={r.team_id} className="flex items-center justify-between border-b py-1.5 text-sm last:border-0">
                <span>{i + 1}. {r.team_name}</span>
                <span className="font-semibold tabular-nums text-positive">{fmt(r.sum_proj_goals)} bàn kỳ vọng</span>
              </div>
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <h3 className="mb-2 font-semibold">Lịch giữ sạch lưới tốt nhất (8 vòng)</h3>
            {data.best_defence.map((r: any, i: number) => (
              <div key={r.team_id} className="flex items-center justify-between border-b py-1.5 text-sm last:border-0">
                <span>{i + 1}. {r.team_name}</span>
                <span className="font-semibold tabular-nums text-primary">{fmt(r.sum_clean_sheet_prob)} CS kỳ vọng</span>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>

      <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
        <span>Thang độ khó:</span>
        {[1, 2, 3, 4, 5].map((n) => (
          <span key={n} className={`rounded px-2 py-0.5 ${fdrClass(n)}`}>{n === 1 ? "Rất dễ" : n === 5 ? "Rất khó" : n}</span>
        ))}
        <span className="ml-2 flex items-center gap-1">
          <span className="rounded bg-muted px-1.5 py-0.5 font-semibold">•</span>
          = có kèo nhà cái (chính xác hơn); ô không có dấu là ước lượng từ mô hình
        </span>
      </div>
    </div>
  );
}
