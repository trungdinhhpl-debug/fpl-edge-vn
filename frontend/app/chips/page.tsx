"use client";
import { useEffect, useState } from "react";
import { CalendarCheck2 } from "lucide-react";
import { postJSON } from "@/lib/api";
import {
  Card, CardContent, CardHeader, CardTitle, Button, Spinner, ErrorBox, Badge, Input,
} from "@/components/ui";

/** Chip Calendar — cả 8 chip trên cùng một bảng.
 *
 * Nguyên tắc trình bày: vòng nào chưa có dự báo thì để TRỐNG, không tô màu, không
 * điền số. Một ô trống nói đúng sự thật ("chưa biết"); một con số nội suy thì nói
 * sai mà lại trông đáng tin hơn.
 */
export default function ChipsPage() {
  const [teamId, setTeamId] = useState("");
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const saved = localStorage.getItem("teamId");
    if (saved) setTeamId(saved);
    // Không có đội vẫn tải được: bảng hiện cửa sổ chip + giới hạn, thiếu phần điểm.
    run(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function run(id: string | null) {
    setLoading(true);
    setError(null);
    try {
      let body: any = { squad_ids: [], bank: 0, free_transfers: 1, chips_used: [] };
      if (id) {
        localStorage.setItem("teamId", id);
        const imported = await postJSON("/api/team/import", { team_id: Number(id) });
        body = {
          squad_ids: (imported.picks ?? []).map((p: any) => p.element),
          bank: imported.bank ?? 0,
          free_transfers: imported.free_transfers ?? 1,
          chips_used: imported.chips_used ?? [],
        };
      }
      setData(await postJSON("/api/chips/calendar", body));
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setLoading(false);
    }
  }

  const firstHalf = (data?.chips ?? []).filter((c: any) => c.set_index === 0);
  const secondHalf = (data?.chips ?? []).filter((c: any) => c.set_index === 1);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold">
          <CalendarCheck2 className="h-6 w-6 text-primary" /> Chip Calendar
        </h1>
        <p className="text-sm text-muted-foreground">
          Cả 8 chip của mùa trên một bảng: lợi từng vòng, giá trị của việc giữ chip, rủi ro
          hết hạn và xung đột giữa các chip. Cửa sổ dùng đọc trực tiếp từ FPL.
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-2">
        <div>
          <label className="text-xs text-muted-foreground">FPL Team ID</label>
          <Input
            value={teamId}
            onChange={(e) => setTeamId(e.target.value)}
            placeholder="vd 1234567"
            className="w-40"
          />
        </div>
        <Button onClick={() => run(teamId || null)} disabled={loading}>
          {teamId ? "Tính theo đội của tôi" : "Xem cửa sổ chip"}
        </Button>
        {data?.squad?.provided === false && (
          <span className="text-xs text-caution">
            Chưa có đội hình — phần điểm để trống. Nhập Team ID để tính.
          </span>
        )}
      </div>

      {loading && <Spinner label="Đang tính lịch chip…" />}
      {error && <ErrorBox error={error} />}

      {data && (
        <>
          <div className="flex flex-wrap gap-4 rounded-md border bg-muted/30 px-3 py-2 text-xs">
            <span>
              Vòng hiện tại: <b className="text-foreground">GW{data.current_gameweek}</b>
            </span>
            <span>
              Tầm dự báo:{" "}
              <b className="text-foreground">
                {data.projection_range
                  ? `GW${data.projection_range.from}–${data.projection_range.to}`
                  : "chưa chạy"}
              </b>
            </span>
            {data.squad?.provided && (
              <>
                <span>
                  Bank: <b className="text-foreground">£{data.squad.bank}m</b>
                </span>
                <span>
                  Free transfer: <b className="text-foreground">{data.squad.free_transfers}</b>
                </span>
              </>
            )}
          </div>

          {data.squad?.note && (
            <p className="text-xs text-muted-foreground">{data.squad.note}</p>
          )}

          {data.conflicts?.length > 0 && (
            <Card className="border-caution/50">
              <CardHeader>
                <CardTitle className="text-base">Xung đột chip</CardTitle>
              </CardHeader>
              <CardContent className="space-y-1 text-sm text-muted-foreground">
                {data.conflicts.map((c: any) => (
                  <p key={c.gameweek}>{c.message}</p>
                ))}
              </CardContent>
            </Card>
          )}

          <ChipSet title="Bộ nửa đầu mùa (hết hạn sau GW19)" chips={firstHalf} />
          <ChipSet title="Bộ nửa sau mùa (GW20–38)" chips={secondHalf} />

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Bảng này không trả lời được gì</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm text-muted-foreground">
              {(data.limits ?? []).map((l: string, i: number) => (
                <p key={i}>• {l}</p>
              ))}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}

function ChipSet({ title, chips }: { title: string; chips: any[] }) {
  if (!chips.length) return null;
  return (
    <div className="space-y-3">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
      </h2>
      <div className="grid gap-3 lg:grid-cols-2">
        {chips.map((c) => (
          <ChipCard key={`${c.chip}-${c.set_index}`} c={c} />
        ))}
      </div>
    </div>
  );
}

const RISK_CLASS: Record<string, string> = {
  Thấp: "bg-positive/15 text-positive",
  "Trung bình": "bg-caution/15 text-caution",
  Cao: "bg-negative/15 text-negative",
  "Đã hết hạn": "bg-muted text-muted-foreground",
};

function ChipCard({ c }: { c: any }) {
  const scored = (c.options ?? []).filter((o: any) => o.gain !== null);
  const max = scored.reduce((m: number, o: any) => Math.max(m, o.gain), 0);

  return (
    <Card className={c.used ? "opacity-60" : ""}>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle className="text-base">{c.label}</CardTitle>
        <div className="flex items-center gap-1.5">
          {c.used && <Badge className="bg-muted text-muted-foreground">đã dùng</Badge>}
          <Badge className="bg-muted text-muted-foreground">
            GW{c.window.start}–{c.window.stop}
          </Badge>
          <Badge className={RISK_CLASS[c.expiry_risk.level] ?? "bg-muted"} title={c.expiry_risk.rule}>
            hết hạn: {c.expiry_risk.level}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div className="flex flex-wrap gap-x-6 gap-y-1">
          <span className="text-muted-foreground">
            Vòng tốt nhất:{" "}
            {c.best ? (
              <b className="text-foreground">
                GW{c.best.gameweek} · +{c.best.gain}
              </b>
            ) : (
              <b className="text-foreground">—</b>
            )}
          </span>
          <span className="text-muted-foreground">
            Dùng vòng này:{" "}
            <b className="text-foreground">
              {c.this_gw?.gain !== null && c.this_gw?.gain !== undefined
                ? `+${c.this_gw.gain}`
                : "—"}
            </b>
          </span>
          <span className="text-muted-foreground">
            Giá trị giữ chip:{" "}
            <b className="text-foreground">
              {c.hold_value === null ? "—" : `${c.hold_value > 0 ? "+" : ""}${c.hold_value}`}
            </b>
          </span>
        </div>

        {/* Thanh theo vòng. Vòng chưa có dự báo là ô trống có viền đứt — nhìn là
            biết "chưa biết", không lẫn với "bằng 0". */}
        <div className="flex items-end gap-0.5 overflow-x-auto pb-1">
          {(c.options ?? []).map((o: any) => {
            const known = o.gain !== null;
            const h = known && max > 0 ? Math.max(3, (o.gain / max) * 40) : 0;
            const bd = o.blank_double ?? {};
            const tag = bd.is_double ? "DGW" : bd.is_blank ? "BGW" : "";
            return (
              <div
                key={o.gameweek}
                className="flex w-6 shrink-0 flex-col items-center gap-0.5"
                title={
                  known
                    ? `GW${o.gameweek}: +${o.gain}. ${o.detail}`
                    : `GW${o.gameweek}: ${o.detail}`
                }
              >
                {known ? (
                  <div
                    className={`w-full rounded-t ${
                      o.gameweek === c.best?.gameweek ? "bg-primary" : "bg-primary/35"
                    }`}
                    style={{ height: `${h}px` }}
                  />
                ) : (
                  <div className="h-[10px] w-full rounded-t border border-dashed border-muted-foreground/40" />
                )}
                <span className="text-[9px] leading-none text-muted-foreground">
                  {o.gameweek}
                </span>
                {tag && (
                  <span className="text-[8px] font-semibold leading-none text-caution">
                    {tag}
                  </span>
                )}
              </div>
            );
          })}
        </div>

        <div className="rounded-md bg-muted/40 px-2.5 py-2">
          <div className="font-semibold">{c.recommendation.action}</div>
          <div className="text-xs text-muted-foreground">{c.recommendation.reason}</div>
        </div>

        {/* Chỉ hiện khi nó nói thêm điều gì — khi bị chặn thì hold_note trùng luôn
            với lý do ở trên, in hai lần chỉ làm loãng. */}
        {c.hold_note && c.hold_note !== c.recommendation.reason && (
          <p className="text-xs text-muted-foreground">{c.hold_note}</p>
        )}
      </CardContent>
    </Card>
  );
}
