"use client";
import { useState } from "react";
import { Zap } from "lucide-react";
import { postJSON } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Card, CardContent, CardHeader, CardTitle, Button, Spinner, ErrorBox, Badge, Stat } from "@/components/ui";
import { Pitch } from "@/components/pitch";
import { fmt } from "@/lib/format";

const MODES = [
  { key: "max_ep", label: "Max Expected Points", desc: "Tối đa điểm kỳ vọng tuyệt đối" },
  { key: "balanced", label: "Balanced Rank Gain", desc: "Cân bằng EV & rủi ro phút" },
  { key: "aggressive", label: "Aggressive Rank Chase", desc: "Ceiling cao, đuổi hạng" },
];

export default function FreeHitPage() {
  const { t } = useT();
  const [mode, setMode] = useState("max_ep");
  const [budget, setBudget] = useState(1000);
  const [result, setResult] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(m: string) {
    setMode(m);
    setLoading(true);
    setError(null);
    try {
      const res = await postJSON("/api/optimizer/free-hit", { mode: m, budget });
      setResult(res);
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold"><Zap className="h-6 w-6 text-primary" /> Free Hit Lab</h1>
        <p className="text-sm text-muted-foreground">
          Tối ưu đúng 1 vòng, không bị ràng buộc đội hình hiện tại. Tự nhận diện Blank/Double GW.
        </p>
      </div>

      <div className="grid gap-2 sm:grid-cols-3">
        {MODES.map((m) => (
          <button
            key={m.key}
            onClick={() => run(m.key)}
            className={`rounded-lg border p-3 text-left transition hover:border-primary ${mode === m.key ? "border-primary bg-primary/5" : ""}`}
          >
            <div className="font-semibold">{m.label}</div>
            <div className="text-xs text-muted-foreground">{m.desc}</div>
          </button>
        ))}
      </div>

      <div className="flex items-center gap-2">
        <label className="text-sm text-muted-foreground">Ngân sách:</label>
        <input
          type="range" min={800} max={1100} step={5}
          value={budget} onChange={(e) => setBudget(Number(e.target.value))}
          className="w-48 accent-emerald-600"
        />
        <span className="font-semibold tabular-nums">£{(budget / 10).toFixed(1)}m</span>
        <Button size="sm" onClick={() => run(mode)}>Chạy tối ưu</Button>
      </div>

      {loading && <Spinner label="Đang giải bài toán tối ưu (MILP)…" />}
      {error && <ErrorBox error={error} />}

      {result && !loading && (
        <div className="grid gap-6 lg:grid-cols-3">
          <div className="lg:col-span-2">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center justify-between">
                  <span>Đội hình đề xuất</span>
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
              <Stat label="Tổng xP (XI)" value={fmt(result.xi_xp)} />
              <Stat label="Chi phí" value={`£${fmt(result.total_cost)}`} />
            </div>

            <Card>
              <CardHeader><CardTitle className="text-sm">Vì sao?</CardTitle></CardHeader>
              <CardContent className="space-y-2 text-sm">
                <p className="text-muted-foreground">{result.explanation?.mode_desc}</p>
                <div className="rounded-md bg-muted/50 p-2">
                  <div className="text-xs font-medium">Đội trưởng</div>
                  <p className="text-muted-foreground">{result.explanation?.captain_reason}</p>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader><CardTitle className="text-sm">Đội hình xuất phát</CardTitle></CardHeader>
              <CardContent className="space-y-1">
                {result.starting.map((p: any) => (
                  <div key={p.id} className="flex items-center justify-between text-sm">
                    <span className="flex items-center gap-1">
                      {p.is_captain && <span className="text-primary">(C)</span>}
                      {p.name}
                    </span>
                    <span className="tabular-nums text-muted-foreground">£{fmt(p.price)} · xP {fmt(p.xp)}</span>
                  </div>
                ))}
              </CardContent>
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}
