"use client";
import { useMemo, useState } from "react";
import { useApi } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Card, CardContent, Spinner, ErrorBox, Button } from "@/components/ui";
import { SortControl, type SortDir } from "@/components/sort-control";
import { fdrClass } from "@/lib/utils";
import { fmt } from "@/lib/format";

/** Chế độ xem một ô lịch. `role` là bốn vai trò của BƯỚC 4. */
type View = "MID" | "DEF" | "FWD" | "GK" | "attack" | "defence";
type SortKey = "fdr" | "goals" | "cs" | "team";

const ROLE_VIEWS: { key: View; label: string; hint: string }[] = [
  { key: "GK", label: "Thủ môn", hint: "điểm kỳ vọng của một thủ môn tham chiếu" },
  { key: "DEF", label: "Hậu vệ", hint: "điểm kỳ vọng của một hậu vệ tham chiếu" },
  { key: "MID", label: "Tiền vệ", hint: "điểm kỳ vọng của một tiền vệ tham chiếu" },
  { key: "FWD", label: "Tiền đạo", hint: "điểm kỳ vọng của một tiền đạo tham chiếu" },
];
const RAW_VIEWS: { key: View; label: string; hint: string }[] = [
  { key: "attack", label: "Ghi bàn", hint: "percentile của λ bàn thắng kỳ vọng" },
  { key: "defence", label: "Sạch lưới", hint: "percentile của 4·P(sạch lưới) − trừ điểm thủng lưới" },
];

const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: "fdr", label: "Điểm lịch (Schedule Ease)" },
  { key: "goals", label: "Σ bàn kỳ vọng" },
  { key: "cs", label: "Σ sạch lưới kỳ vọng" },
  { key: "team", label: "Tên đội" },
];

const isRole = (v: View) => v !== "attack" && v !== "defence";

/** FDR của một ô, theo chế độ đang xem. */
function cellFdr(cell: any, view: View): number | null {
  if (view === "attack") return cell.attack_difficulty ?? null;
  if (view === "defence") return cell.defence_difficulty ?? null;
  return cell.role_fdr?.[view] ?? null;
}

/** Độ dễ (percentile 0–100) của một ô, theo chế độ đang xem. */
function cellEase(cell: any, view: View): number | null {
  if (view === "attack") return cell.attack_ease ?? null;
  if (view === "defence") return cell.defence_ease ?? null;
  return cell.role_ease?.[view] ?? null;
}

export default function FixturesPage() {
  const { t } = useT();
  const [view, setView] = useState<View>("MID");
  const [sort, setSort] = useState<SortKey>("fdr");
  // Điểm lịch: CAO = lịch dễ, nên mặc định xếp giảm dần (dễ nhất lên đầu).
  const [dir, setDir] = useState<SortDir>("desc");
  const { data, loading, error } = useApi<any>("/api/fixtures/ticker?n_gws=8");

  // hooks phải chạy trước mọi return sớm
  const rows = useMemo(() => {
    const list = [...((data?.rows ?? []) as any[])];
    const sign = dir === "asc" ? 1 : -1;
    const scheduleOf = (r: any) =>
      r.schedule?.[isRole(view) ? view : data?.default_role ?? "MID"];
    const get = (r: any) => {
      switch (sort) {
        case "goals":
          return r.sum_proj_goals ?? 0;
        case "cs":
          return r.sum_clean_sheet_prob ?? 0;
        case "team":
          return r.team_name ?? r.team ?? "";
        default:
          return scheduleOf(r)?.ease ?? 0;
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
  }, [data, sort, dir, view]);

  if (loading) return <Spinner label={t("loading")} />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;

  const gws = data.gameweeks as number[];
  const model = data.model ?? {};
  const coverage = model.market_coverage ?? {};
  const activeHint =
    [...ROLE_VIEWS, ...RAW_VIEWS].find((v) => v.key === view)?.hint ?? "";
  const scheduleKey = isRole(view) ? view : data.default_role ?? "MID";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">{t("fixtures")}</h1>
          <p className="text-sm text-muted-foreground">
            FDR riêng của hệ thống, chia theo <b>ngũ phân vị</b>: 20% ô dễ nhất là 1,
            20% khó nhất là 5. Đang xem: {activeHint}.
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
          <div className="flex flex-wrap gap-1 rounded-md border p-1">
            {ROLE_VIEWS.map((v) => (
              <Button
                key={v.key}
                size="sm"
                variant={view === v.key ? "default" : "ghost"}
                onClick={() => setView(v.key)}
              >
                {v.label}
              </Button>
            ))}
            <span className="mx-1 self-center text-muted-foreground">|</span>
            {RAW_VIEWS.map((v) => (
              <Button
                key={v.key}
                size="sm"
                variant={view === v.key ? "default" : "ghost"}
                onClick={() => setView(v.key)}
              >
                {v.label}
              </Button>
            ))}
          </div>
        </div>
      </div>

      <Card>
        <CardContent className="p-0">
          <div className="scroll-thin overflow-x-auto">
            <table className="w-full min-w-[900px] border-collapse text-sm">
              <thead>
                <tr className="border-b bg-muted/40 text-xs uppercase text-muted-foreground">
                  <th className="p-2 text-left">Đội</th>
                  {gws.map((g) => (
                    <th key={g} className="p-2 text-center">GW{g}</th>
                  ))}
                  <th className="p-2 text-center" title="BƯỚC 5: trung bình Role Ease suy giảm theo thời gian, trừ phạt bất định">
                    Điểm lịch
                  </th>
                  <th className="p-2 text-center" title="BƯỚC 6: ngũ phân vị của điểm lịch">
                    FDR
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row: any) => {
                  const sched = row.schedule?.[scheduleKey];
                  return (
                    <tr key={row.team_id} className="border-b">
                      <td className="whitespace-nowrap p-2 font-semibold">{row.team}</td>
                      {gws.map((g) => {
                        const cells = row.cells[String(g)] ?? [];
                        return (
                          <td key={g} className="p-1 text-center">
                            <div className="flex flex-col gap-0.5">
                              {cells.length === 0 ? (
                                <span
                                  className="text-xs text-muted-foreground"
                                  title="Vòng trắng — không đá, tính 0 điểm chứ không phải thiếu dữ liệu"
                                >
                                  ⊘
                                </span>
                              ) : (
                                cells.map((c: any, i: number) => (
                                  <div
                                    key={i}
                                    className={`relative rounded px-1 py-0.5 text-[11px] font-medium ${fdrClass(
                                      cellFdr(c, view),
                                    )}`}
                                    title={
                                      `${c.opponent} (${c.is_home ? "sân nhà" : "sân khách"})` +
                                      ` · ghi ${fmt(c.proj_goals_for)} / thủng ${fmt(c.proj_goals_against)}` +
                                      ` · sạch lưới ${Math.round((c.clean_sheet_prob ?? 0) * 100)}%` +
                                      (isRole(view)
                                        ? ` · ${view} tham chiếu ${fmt(c.role_points?.[view])} điểm`
                                        : "") +
                                      ` · độ dễ ${fmt(cellEase(c, view))}/100` +
                                      (c.has_market
                                        ? ` · kèo nhà cái, trọng số ${Math.round((c.market_weight ?? 0) * 100)}%`
                                        : " · chưa có kèo, hoàn toàn từ mô hình")
                                    }
                                  >
                                    {c.opponent}
                                    <span className="opacity-70">
                                      {c.is_home ? " (H)" : " (A)"}
                                    </span>
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
                      <td
                        className="p-2 text-center font-semibold tabular-nums"
                        title={
                          sched
                            ? `Trước phạt: ${fmt(sched.raw_ease)} — phạt bất định: −${fmt(
                                sched.uncertainty_penalty,
                              )}`
                            : ""
                        }
                      >
                        {fmt(sched?.ease)}
                      </td>
                      <td className="p-2 text-center">
                        <span
                          className={`inline-block min-w-6 rounded px-1.5 py-0.5 text-xs font-bold ${fdrClass(
                            sched?.fdr,
                          )}`}
                        >
                          {sched?.fdr ?? "–"}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-4 sm:grid-cols-2">
        <Card>
          <CardContent className="pt-4">
            <h3 className="mb-2 font-semibold">Lịch ghi bàn tốt nhất (8 vòng)</h3>
            {data.best_attack.map((r: any, i: number) => (
              <div
                key={r.team_id}
                className="flex items-center justify-between border-b py-1.5 text-sm last:border-0"
              >
                <span>{i + 1}. {r.team_name}</span>
                <span className="font-semibold tabular-nums text-positive">
                  {fmt(r.sum_proj_goals)} bàn kỳ vọng
                </span>
              </div>
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <h3 className="mb-2 font-semibold">Lịch giữ sạch lưới tốt nhất (8 vòng)</h3>
            {data.best_defence.map((r: any, i: number) => (
              <div
                key={r.team_id}
                className="flex items-center justify-between border-b py-1.5 text-sm last:border-0"
              >
                <span>{i + 1}. {r.team_name}</span>
                <span className="font-semibold tabular-nums text-primary">
                  {fmt(r.sum_clean_sheet_prob)} CS kỳ vọng
                </span>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>

      <Card className="border-caution/40">
        <CardContent className="space-y-1.5 pt-4 text-sm">
          <p className="font-semibold">Đừng chọn cầu thủ từ bảng này.</p>
          <p className="text-muted-foreground">
            Độ khó lịch là <b>một thành phần</b> của dự báo cầu thủ, không phải đầu vào
            cuối cùng. Đo trên chính dữ liệu hiện tại: đổi lịch từ dễ nhất sang khó nhất
            làm xP của một cầu thủ thay đổi <b>1.24 lần</b>, còn đổi từ cầu thủ kém sang
            cầu thủ giỏi (cùng một lịch) thay đổi <b>5.49 lần</b> — lịch quyết định ít
            hơn khoảng bốn lần so với chính cầu thủ đó.
          </p>
          <p className="text-muted-foreground">
            Ví dụ đang có thật: <b>Watkins</b> (Aston Villa) chỉ có lịch <b>FDR 4</b>{" "}
            nhưng đứng thứ 5 toàn giải về xP 8 vòng — hơn <b>118/122</b> cầu thủ thuộc
            nhóm lịch FDR 1, vì đá gần trọn trận và nhận đá phạt đền. Ngược lại một cầu
            thủ lịch FDR 1 mà chỉ 57 phút kỳ vọng vẫn bị người có lịch FDR 3–5 vượt qua.
          </p>
          <p>
            <a href="/players" className="font-medium text-primary underline">
              Xem xếp hạng theo xP →
            </a>{" "}
            <span className="text-muted-foreground">
              (đã gộp lịch × phút thi đấu × vai trò × tỷ trọng tấn công + bonus + defcon)
            </span>
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="space-y-2 pt-4 text-xs text-muted-foreground">
          <div className="flex flex-wrap items-center gap-3">
            <span className="font-semibold text-foreground">Thang FDR:</span>
            {[1, 2, 3, 4, 5].map((n) => (
              <span key={n} className={`rounded px-2 py-0.5 ${fdrClass(n)}`}>
                {n === 1 ? "1 · rất dễ" : n === 5 ? "5 · rất khó" : n}
              </span>
            ))}
            <span className="flex items-center gap-1">
              <span className="rounded bg-muted px-1.5 py-0.5 font-semibold">•</span>
              = trận đã có kèo nhà cái
            </span>
            <span className="flex items-center gap-1">
              <span className="rounded bg-muted px-1.5 py-0.5 font-semibold">⊘</span>
              = vòng trắng
            </span>
          </div>
          <p>
            Kèo nhà cái phủ <b>{coverage.fixtures_with_odds ?? 0}/{coverage.fixtures_total ?? 0}</b>{" "}
            ô trong cửa sổ này ({Math.round((coverage.share ?? 0) * 100)}%). Những ô còn
            lại hoàn toàn từ mô hình nội bộ
            {model.calibration?.applied ? (
              <>
                , đã nhân hệ số hiệu chuẩn <b>{fmt(model.calibration.multiplier, 3)}</b> đo
                trên chính các trận đã có giá
              </>
            ) : null}
            . Nền bàn thắng của giải: {fmt(model.baseline_goals)}/đội/trận.
          </p>
          {model.steps ? (
            <ul className="list-inside list-disc space-y-0.5">
              {model.steps.map((s: string) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          ) : null}
          {model.reference_players ? (
            <p>
              Cầu thủ tham chiếu:{" "}
              {Object.entries(model.reference_players)
                .map(([k, v]) => `${k} — ${v}`)
                .join(" · ")}
              .
            </p>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
