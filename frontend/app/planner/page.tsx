"use client";
import { useEffect, useState } from "react";
import { CalendarRange } from "lucide-react";
import { postJSON } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Card, CardContent, CardHeader, CardTitle, Button, Input, Select, Spinner, ErrorBox, Badge } from "@/components/ui";
import { DecisionTree } from "@/components/decision-tree";
import { FieldTilt } from "@/components/field-tilt";
import { fmt, parseApiDate } from "@/lib/format";

const PROFILES = [
  { key: "safe", label: "An toàn", color: "bg-positive/15 text-positive" },
  { key: "balanced", label: "Cân bằng", color: "bg-primary/15 text-primary" },
  { key: "aggressive", label: "Mạo hiểm", color: "bg-danger/15 text-danger" },
];

export default function PlannerPage() {
  const { t } = useT();
  const [teamId, setTeamId] = useState("");
  const [horizon, setHorizon] = useState(5);
  const [eoWeight, setEoWeight] = useState(0);
  const [leagueId, setLeagueId] = useState<number | null>(null);
  const [result, setResult] = useState<any>(null);
  const [active, setActive] = useState("balanced");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const s = localStorage.getItem("teamId");
    if (s) setTeamId(s);
  }, []);

  async function build() {
    setLoading(true); setError(null);
    try {
      const imp = await postJSON<any>("/api/team/import", { team_id: Number(teamId) });
      const squad_ids = (imp.picks ?? []).map((p: any) => p.element);
      if (squad_ids.length !== 15) {
        const st = imp.squad_status;
        const when = st?.available_after
          ? ` Đội hình sẽ hiện sau ${parseApiDate(st.available_after).toLocaleString("vi-VN", {
              timeZone: "Asia/Ho_Chi_Minh",
              dateStyle: "short",
              timeStyle: "short",
            })} (giờ VN).`
          : "";
        setError(
          (st?.message ??
            `Đội "${imp.team_name ?? teamId}" chưa có đội hình 15 cầu thủ (hiện ${squad_ids.length}).`) +
            when +
            " Trong lúc chờ, bạn có thể dùng Free Hit Lab để dựng đội hình tối ưu.",
        );
        return;
      }
      const res = await postJSON("/api/optimizer/long-term", {
        squad_ids, bank: imp.bank, free_transfers: imp.free_transfers ?? 1, horizon, discount: 0.9,
        eo_weight: eoWeight, league_id: leagueId,
      });
      setResult(res);
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setLoading(false);
    }
  }

  const plan = result?.plans?.[active];

  return (
    <div className="space-y-4">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold"><CalendarRange className="h-6 w-6 text-primary" /> {t("planner")}</h1>
        <p className="text-sm text-muted-foreground">
          Kế hoạch chuyển nhượng {horizon} vòng với 3 chiến lược. Có tính giá trị giữ free transfer, điểm trừ và chiết khấu vòng xa.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Input placeholder="Team ID" value={teamId} onChange={(e) => setTeamId(e.target.value)} className="max-w-[180px]" />
        <Select value={horizon} onChange={(e) => setHorizon(Number(e.target.value))}>
          {[3, 4, 5, 6, 7, 8].map((h) => <option key={h} value={h}>{h} vòng</option>)}
        </Select>
        <Button onClick={build} disabled={!teamId || loading}>{loading ? "…" : t("buildPlan")}</Button>
      </div>

      <FieldTilt
        weight={eoWeight}
        source={result?.field}
        onChange={(w, lid) => { setEoWeight(w); setLeagueId(lid); }}
      />

      {error && <ErrorBox error={error} />}
      {loading && <Spinner label="Đang giải bài toán tối ưu đa vòng (MILP)…" />}

      {result && (
        <>
          {/* plan tabs + summary comparison */}
          <div className="grid gap-3 sm:grid-cols-3">
            {PROFILES.map((pf) => {
              const p = result.plans[pf.key];
              return (
                <button key={pf.key} onClick={() => setActive(pf.key)}
                  className={`rounded-lg border p-3 text-left transition ${active === pf.key ? "border-primary bg-primary/5" : "hover:border-primary/40"}`}>
                  <div className="flex items-center justify-between">
                    <Badge className={pf.color}>{pf.label}</Badge>
                    <span className="text-lg font-bold tabular-nums text-primary">{fmt(p.net_xp)}</span>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">{p.summary.desc}</p>
                  <div className="mt-2 flex gap-3 text-xs text-muted-foreground">
                    <span>{p.summary.total_transfers} CN</span>
                    <span>{p.total_hits} hit</span>
                  </div>
                </button>
              );
            })}
          </div>

          {plan && (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center justify-between">
                  <span>Kế hoạch {PROFILES.find((p) => p.key === active)?.label}</span>
                  <span className="text-sm font-normal text-muted-foreground">Net xP {fmt(plan.net_xp)} · {plan.status}</span>
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <p className="rounded-md bg-muted/50 p-2 text-sm">
                  <b>Rủi ro chính:</b> {plan.summary.main_risk}
                </p>
                <DecisionTree plan={plan} tree={plan.decision_tree} />
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
