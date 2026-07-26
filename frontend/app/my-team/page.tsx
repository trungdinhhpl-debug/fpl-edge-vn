"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { Shield, Download, Wand2, ArrowRight } from "lucide-react";
import { postJSON, getJSON } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Card, CardContent, CardHeader, CardTitle, Button, Input, Spinner, ErrorBox, Badge, Stat } from "@/components/ui";
import { PosTag, StatusDot } from "@/components/fpl";
import { fmt } from "@/lib/format";
import { riskBg } from "@/lib/utils";

export default function MyTeamPage() {
  const { t } = useT();
  const [teamId, setTeamId] = useState("");
  const [imported, setImported] = useState<any>(null);
  const [pmap, setPmap] = useState<Record<number, any>>({});
  const [analysis, setAnalysis] = useState<any>(null);
  const [nextGw, setNextGw] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const saved = localStorage.getItem("teamId");
    if (saved) setTeamId(saved);
  }, []);

  async function loadPlayers() {
    if (Object.keys(pmap).length) return pmap;
    const res = await getJSON<any>("/api/players?limit=1000");
    const map: Record<number, any> = {};
    res.players.forEach((p: any) => (map[p.id] = p));
    setPmap(map);
    return map;
  }

  async function doImport() {
    setLoading(true); setError(null); setAnalysis(null); setNextGw(null);
    try {
      localStorage.setItem("teamId", teamId);
      const [res] = await Promise.all([postJSON("/api/team/import", { team_id: Number(teamId) }), loadPlayers()]);
      setImported(res);
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setLoading(false);
    }
  }

  const squadIds: number[] = imported?.picks?.map((p: any) => p.element) ?? [];

  async function analyze() {
    setBusy("analyze"); setError(null);
    try {
      setAnalysis(await postJSON("/api/team/analyze", { squad_ids: squadIds, bank: imported.bank }));
    } catch (e: any) { setError(String(e.message ?? e)); } finally { setBusy(null); }
  }

  async function optimizeNext() {
    setBusy("next"); setError(null);
    try {
      setNextGw(await postJSON("/api/optimizer/next-gameweek", {
        squad_ids: squadIds, bank: imported.bank, free_transfers: imported.free_transfers ?? 1, max_transfers: 2,
      }));
    } catch (e: any) { setError(String(e.message ?? e)); } finally { setBusy(null); }
  }

  const byPos = (t: number) => imported?.picks?.filter((p: any) => pmap[p.element]?.element_type === t) ?? [];

  return (
    <div className="space-y-5">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold"><Shield className="h-6 w-6 text-primary" /> {t("myTeam")}</h1>
        <p className="text-sm text-muted-foreground">Nhập FPL Team ID để tải đội hình, phân tích và tối ưu.</p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Input placeholder="Team ID (vd: 1234567)" value={teamId} onChange={(e) => setTeamId(e.target.value)} className="max-w-[220px]" />
        <Button onClick={doImport} disabled={!teamId || loading}>
          <Download className="h-4 w-4" /> {loading ? "…" : t("importTeam")}
        </Button>
        <span className="text-xs text-muted-foreground">Tìm ID trong URL trang “Points” của bạn trên fantasy.premierleague.com</span>
      </div>

      {error && <ErrorBox error={error} />}
      {loading && <Spinner label="Đang tải đội hình…" />}

      {imported && (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
            <Stat label="Manager" value={<span className="text-sm">{imported.player_name || "—"}</span>} sub={imported.team_name} />
            <Stat label="Overall Rank" value={imported.overall_rank ? imported.overall_rank.toLocaleString() : "—"} />
            <Stat label="Giá trị đội" value={`£${fmt(imported.team_value / 10)}`} />
            <Stat label="Ngân hàng" value={`£${fmt(imported.bank / 10)}`} />
            <Stat label="Free Transfers" value={imported.free_transfers ?? 1} />
          </div>

          {imported.note && (
            <p className="text-xs text-muted-foreground">ℹ {imported.note}</p>
          )}

          {squadIds.length !== 15 && (
            <div className="rounded-md border border-caution/40 bg-caution/10 p-3 text-sm text-caution">
              Đội này chưa có đội hình 15 cầu thủ cho vòng hiện tại (đang thấy {squadIds.length}).
              Thường gặp ở giai đoạn tiền mùa giải — hãy chọn đội trên trang FPL chính thức trước.
              Trong lúc đó bạn vẫn dùng được <b>Free Hit Lab</b> để xem đội hình tối ưu, và
              <b> Cầu thủ / Đội trưởng / Lịch thi đấu</b> để nghiên cứu.
            </div>
          )}

          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              onClick={analyze}
              disabled={busy === "analyze" || squadIds.length !== 15}
              title={squadIds.length !== 15 ? "Cần đủ 15 cầu thủ" : undefined}
            >
              <Wand2 className="h-4 w-4" /> {busy === "analyze" ? "…" : "Phân tích đội hình"}
            </Button>
            <Button
              variant="outline"
              onClick={optimizeNext}
              disabled={busy === "next" || squadIds.length !== 15}
              title={squadIds.length !== 15 ? "Cần đủ 15 cầu thủ" : undefined}
            >
              {busy === "next" ? "…" : "Tối ưu vòng tới"} <ArrowRight className="h-4 w-4" />
            </Button>
            <Link href="/planner"><Button variant="outline">Kế hoạch 3–8 vòng</Button></Link>
            <Link href="/free-hit"><Button variant="outline">Build Free Hit</Button></Link>
          </div>

          {/* Squad */}
          <Card>
            <CardHeader><CardTitle>Đội hình hiện tại</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              {[1, 2, 3, 4].map((tpos) => (
                <div key={tpos}>
                  <div className="mb-1 text-xs font-medium uppercase text-muted-foreground">
                    {["", "Thủ môn", "Hậu vệ", "Tiền vệ", "Tiền đạo"][tpos]}
                  </div>
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
                    {byPos(tpos).map((pick: any) => {
                      const pl = pmap[pick.element];
                      if (!pl) return null;
                      return (
                        <Link key={pick.element} href={`/players/${pick.element}`}
                          className="flex items-center justify-between rounded-md border p-2 text-sm hover:border-primary/50">
                          <div className="min-w-0">
                            <div className="flex items-center gap-1 truncate font-medium">
                              {pl.name}
                              {pick.is_captain && <Badge className="bg-primary/15 text-primary">C</Badge>}
                              {pick.is_vice_captain && <Badge className="bg-muted">V</Badge>}
                              <StatusDot status={pl.status} />
                            </div>
                            <div className="text-xs text-muted-foreground">{pl.team} · £{fmt(pl.price)}</div>
                          </div>
                          <div className="text-right">
                            <div className="font-bold tabular-nums text-primary">{fmt(pl.xp_next)}</div>
                          </div>
                        </Link>
                      );
                    })}
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>

          {/* Analysis */}
          {analysis && (
            <div className="grid gap-4 lg:grid-cols-3">
              <Card>
                <CardHeader><CardTitle className="text-sm text-positive">Điểm mạnh</CardTitle></CardHeader>
                <CardContent className="space-y-1 text-sm">
                  {analysis.strengths?.length ? analysis.strengths.map((s: string, i: number) => <p key={i}>• {s}</p>) : <p className="text-muted-foreground">—</p>}
                  <p className="pt-2 text-xs text-muted-foreground">Tổng xP 5 vòng: <b>{fmt(analysis.squad_xp_next5)}</b></p>
                </CardContent>
              </Card>
              <Card>
                <CardHeader><CardTitle className="text-sm text-caution">Điểm yếu</CardTitle></CardHeader>
                <CardContent className="space-y-1 text-sm">
                  {analysis.weaknesses?.length ? analysis.weaknesses.map((s: string, i: number) => <p key={i}>• {s}</p>) : <p className="text-muted-foreground">Không có điểm yếu rõ rệt.</p>}
                </CardContent>
              </Card>
              <Card>
                <CardHeader><CardTitle className="text-sm text-danger">Ưu tiên bán</CardTitle></CardHeader>
                <CardContent className="space-y-1 text-sm">
                  {analysis.sell_candidates?.length ? analysis.sell_candidates.map((s: any) => (
                    <div key={s.id} className="flex items-center justify-between">
                      <span>{s.name}</span>
                      <span className="text-xs text-muted-foreground">{s.reason}</span>
                    </div>
                  )) : <p className="text-muted-foreground">Không có.</p>}
                </CardContent>
              </Card>
            </div>
          )}

          {/* Next GW transfers */}
          {nextGw && (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center justify-between">
                  <span>Chuyển nhượng đề xuất — GW{nextGw.gameweek}</span>
                  <Badge className={nextGw.hits > 0 ? "bg-danger/15 text-danger" : "bg-positive/15 text-positive"}>
                    {nextGw.n_transfers} chuyển nhượng · {nextGw.hits > 0 ? `-${nextGw.hit_cost}đ` : "không hit"}
                  </Badge>
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {nextGw.explanations?.length ? nextGw.explanations.map((ex: any, i: number) => (
                  <div key={i} className="rounded-md border p-3">
                    <div className="flex items-center gap-2 text-sm font-medium">
                      <Badge className="bg-danger/15 text-danger">OUT</Badge> {ex.out.name} ({ex.out.team})
                      <ArrowRight className="h-4 w-4" />
                      <Badge className="bg-positive/15 text-positive">IN</Badge> {ex.in.name} ({ex.in.team})
                      <span className="ml-auto tabular-nums text-primary">{ex.xp_gain_horizon >= 0 ? "+" : ""}{fmt(ex.xp_gain_horizon)} xP</span>
                    </div>
                    <ul className="mt-1 list-inside list-disc text-xs text-muted-foreground">
                      {ex.reasons.map((r: string, j: number) => <li key={j}>{r}</li>)}
                    </ul>
                  </div>
                )) : <p className="text-sm text-muted-foreground">Không có chuyển nhượng nào cải thiện xP đáng kể — nên giữ (roll) free transfer.</p>}
                {nextGw.compare && (
                  <div className="rounded-md bg-muted/50 p-2 text-sm">
                    <b>So sánh:</b> Hành động ngay → XI xP {fmt(nextGw.compare.act_now_xi_xp)} ·
                    Giữ nguyên → {fmt(nextGw.compare.roll_xi_xp)}. <i>{nextGw.compare.recommendation}</i>
                  </div>
                )}
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
