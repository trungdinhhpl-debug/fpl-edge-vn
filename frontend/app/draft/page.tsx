"use client";
import { useMemo, useState } from "react";
import { Hammer, Lock, Ban, X } from "lucide-react";
import { postJSON, useApi } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle, Button, Spinner, ErrorBox, Badge, Stat, Input } from "@/components/ui";
import { Pitch } from "@/components/pitch";
import { PosTag } from "@/components/fpl";
import { FieldTilt } from "@/components/field-tilt";
import { fmt } from "@/lib/format";

const MODES = [
  { key: "max_ep", label: "Tối đa điểm", desc: "Tổng xP thuần trên cả horizon." },
  { key: "balanced", label: "Cân bằng", desc: "Trừ rủi ro phút thi đấu mỗi vòng — đội ít vỡ hơn." },
  { key: "aggressive", label: "Đuổi hạng", desc: "Cộng thêm ceiling: nhiều tuần bùng nổ, kém đều." },
];

type P = { id: number; name: string; team: string; position: string; price: number; xp_next5?: number };

export default function DraftPage() {
  const [mode, setMode] = useState("balanced");
  const [budget, setBudget] = useState(1000);
  const [horizon, setHorizon] = useState(8);
  const [locked, setLocked] = useState<number[]>([]);
  const [excluded, setExcluded] = useState<number[]>([]);
  const [eoWeight, setEoWeight] = useState(0);
  const [leagueId, setLeagueId] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { data: playerData } = useApi<any>("/api/players?limit=1000");
  const players: P[] = playerData?.players ?? [];
  const byId = useMemo(() => new Map(players.map((p) => [p.id, p])), [players]);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (q.length < 2) return [];
    return players
      .filter((p) => p.name.toLowerCase().includes(q) || p.team.toLowerCase().includes(q))
      .slice(0, 8);
  }, [query, players]);

  function toggle(list: number[], set: (v: number[]) => void, id: number, other: number[], setOther: (v: number[]) => void) {
    setOther(other.filter((x) => x !== id));           // khoá và loại là hai ý ngược nhau
    set(list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);
    setQuery("");
  }

  async function run() {
    setLoading(true);
    setError(null);
    try {
      setResult(await postJSON("/api/optimizer/wildcard", {
        mode, budget, horizon, locked, excluded,
        eo_weight: eoWeight, league_id: leagueId,
      }));
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setLoading(false);
    }
  }

  const ignored: number[] = result?.locked_ignored ?? [];

  return (
    <div className="space-y-4">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold">
          <Hammer className="h-6 w-6 text-primary" /> Draft đội hình
        </h1>
        <p className="text-sm text-muted-foreground">
          Dựng 15 người từ con số 0 cho nhiều vòng liền — đội đầu mùa, hoặc một Wildcard. Khác Free Hit
          Lab: Free Hit tối ưu đúng một vòng, trang này tối ưu cả horizon với đội hình đứng yên.
        </p>
      </div>

      {/* mode */}
      <div className="grid gap-2 sm:grid-cols-3">
        {MODES.map((m) => (
          <button
            key={m.key}
            onClick={() => setMode(m.key)}
            className={`rounded-lg border p-3 text-left transition hover:border-primary ${mode === m.key ? "border-primary bg-primary/5" : ""}`}
          >
            <div className="font-semibold">{m.label}</div>
            <div className="text-xs text-muted-foreground">{m.desc}</div>
          </button>
        ))}
      </div>

      <FieldTilt
        weight={eoWeight}
        source={result?.field}
        onChange={(w, lid) => { setEoWeight(w); setLeagueId(lid); }}
      />

      {/* controls */}
      <Card>
        <CardContent className="space-y-3 pt-4">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <div className="flex items-center gap-2">
              <label className="text-sm text-muted-foreground">Ngân sách:</label>
              <input
                type="range" min={800} max={1050} step={5}
                value={budget} onChange={(e) => setBudget(Number(e.target.value))}
                className="w-40 accent-emerald-600"
              />
              <span className="font-semibold tabular-nums">£{(budget / 10).toFixed(1)}m</span>
            </div>
            <div className="flex items-center gap-2">
              <label className="text-sm text-muted-foreground">Số vòng tính:</label>
              <div className="flex gap-1">
                {[3, 5, 6, 8, 10].map((h) => (
                  <button
                    key={h}
                    onClick={() => setHorizon(h)}
                    className={`rounded-md border px-2 py-1 text-xs font-medium ${horizon === h ? "border-primary bg-primary/10 text-primary" : "hover:bg-muted"}`}
                  >
                    {h}
                  </button>
                ))}
              </div>
            </div>
            <Button size="sm" onClick={run}>Dựng đội</Button>
          </div>

          {/* lock / exclude */}
          <div className="space-y-2">
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Tìm cầu thủ để khoá (bắt buộc có) hoặc loại (không bao giờ chọn)…"
            />
            {matches.length > 0 && (
              <div className="rounded-md border">
                {matches.map((p) => (
                  <div key={p.id} className="flex items-center justify-between gap-2 border-b px-2 py-1.5 text-sm last:border-0">
                    <span className="flex items-center gap-2">
                      <PosTag pos={p.position} /> {p.name}
                      <span className="text-xs text-muted-foreground">{p.team} · £{fmt(p.price)}</span>
                    </span>
                    <span className="flex gap-1">
                      <button
                        onClick={() => toggle(locked, setLocked, p.id, excluded, setExcluded)}
                        className="flex items-center gap-1 rounded border px-2 py-0.5 text-xs hover:bg-primary/10"
                      >
                        <Lock className="h-3 w-3" /> Khoá
                      </button>
                      <button
                        onClick={() => toggle(excluded, setExcluded, p.id, locked, setLocked)}
                        className="flex items-center gap-1 rounded border px-2 py-0.5 text-xs hover:bg-danger/10"
                      >
                        <Ban className="h-3 w-3" /> Loại
                      </button>
                    </span>
                  </div>
                ))}
              </div>
            )}
            <div className="flex flex-wrap gap-1">
              {locked.map((id) => (
                <Badge key={id} className="bg-primary/15 text-primary">
                  <Lock className="mr-1 inline h-3 w-3" />
                  {byId.get(id)?.name ?? id}
                  <button onClick={() => setLocked(locked.filter((x) => x !== id))} className="ml-1 align-middle">
                    <X className="inline h-3 w-3" />
                  </button>
                </Badge>
              ))}
              {excluded.map((id) => (
                <Badge key={id} className="bg-danger/15 text-danger">
                  <Ban className="mr-1 inline h-3 w-3" />
                  {byId.get(id)?.name ?? id}
                  <button onClick={() => setExcluded(excluded.filter((x) => x !== id))} className="ml-1 align-middle">
                    <X className="inline h-3 w-3" />
                  </button>
                </Badge>
              ))}
            </div>
          </div>
        </CardContent>
      </Card>

      {loading && <Spinner label="Đang giải bài toán tối ưu (MILP)…" />}
      {error && <ErrorBox error={error} />}

      {result && !loading && (
        <>
          {ignored.length > 0 && (
            <div className="rounded-md border border-caution/40 bg-caution/10 p-3 text-sm">
              Không dùng được {ignored.length} khoá ({ignored.join(", ")}) — không có cầu thủ nào mang id đó
              trong dữ liệu. Đội hình dưới đây được dựng mà KHÔNG có họ.
            </div>
          )}

          <div className="grid gap-6 lg:grid-cols-3">
            <div className="lg:col-span-2">
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center justify-between">
                    <span>Đội hình đề xuất · GW{result.gameweeks?.[0]}–GW{result.gameweeks?.slice(-1)[0]}</span>
                    <Badge className="bg-primary/15 text-primary">{result.formation}</Badge>
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <Pitch starting={result.starting} bench={result.bench} />
                </CardContent>
              </Card>
            </div>

            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-2">
                <Stat label={`xP ${result.horizon} vòng (XI)`} value={fmt(result.xi_horizon_xp, 1)} />
                <Stat label="xP vòng tới (có C)" value={fmt(result.xi_xp)} />
                <Stat label="Chi phí" value={`£${fmt(result.total_cost)}`} />
                <Stat label="Còn dư" value={`£${fmt(budget / 10 - result.total_cost)}`} />
              </div>

              <Card>
                <CardHeader><CardTitle className="text-sm">Đọc con số này thế nào</CardTitle></CardHeader>
                <CardContent className="space-y-2 text-sm text-muted-foreground">
                  <p>{result.note}</p>
                  <p>
                    Thực tế bạn sẽ chuyển nhượng mỗi vòng, nên tổng xP horizon là mức SÀN của một đội đứng
                    yên, không phải dự báo điểm cuối cùng.
                  </p>
                </CardContent>
              </Card>

              <Card>
                <CardHeader><CardTitle className="text-sm">Cả 15 người</CardTitle></CardHeader>
                <CardContent className="space-y-1">
                  {[...result.starting, ...result.bench].map((p: any, i: number) => (
                    <div key={p.id} className="flex items-center justify-between gap-2 text-sm">
                      <span className="flex min-w-0 items-center gap-1 truncate">
                        {i >= 11 && <span className="text-[10px] text-muted-foreground">DP</span>}
                        {p.is_captain && <span className="font-bold text-primary">(C)</span>}
                        {p.is_locked && <Lock className="h-3 w-3 shrink-0 text-primary" />}
                        <span className="truncate">{p.name}</span>
                        <span className="shrink-0 text-xs text-muted-foreground">{p.team}</span>
                      </span>
                      <span className="shrink-0 tabular-nums text-muted-foreground">
                        £{fmt(p.price)} · {fmt(p.xp_horizon, 1)}đ
                        {p.field_eo !== undefined && (
                          <span className="ml-1 text-xs"> · EO {fmt(p.field_eo, 0)}%</span>
                        )}
                      </span>
                    </div>
                  ))}
                </CardContent>
              </Card>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
