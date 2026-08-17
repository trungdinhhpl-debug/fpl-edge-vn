"use client";
import { useEffect, useState } from "react";
import { Trophy, ShieldAlert, Sparkles } from "lucide-react";
import { postJSON } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle, Button, Input, Spinner, ErrorBox, Badge, Stat } from "@/components/ui";
import { PosTag } from "@/components/fpl";
import { fmt } from "@/lib/format";

/** Mini-league — EO ĐẾM ĐƯỢC từ đội hình thật của đối thủ.
 *
 * Khác trang Đội trưởng ở hai điểm và cả hai đều quan trọng: đám đông ở đây là
 * đúng nhóm bạn đang đua (không phải 11 triệu người), và phần băng đội trưởng là
 * số đếm chứ không phải mô hình. Cái giá là độ trễ một vòng — trang phải nói ra.
 */
export default function LeaguePage() {
  const [leagueId, setLeagueId] = useState("");
  const [teamId, setTeamId] = useState("");
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLeagueId(localStorage.getItem("leagueId") ?? "");
    setTeamId(localStorage.getItem("teamId") ?? "");
  }, []);

  async function run() {
    if (!leagueId) return;
    setLoading(true);
    setError(null);
    try {
      localStorage.setItem("leagueId", leagueId);
      if (teamId) localStorage.setItem("teamId", teamId);
      setData(await postJSON("/api/league/analyze", {
        league_id: Number(leagueId),
        entry_id: teamId ? Number(teamId) : null,
        top_n: 30,
      }));
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold">
          <Trophy className="h-6 w-6 text-primary" /> Mini-league
        </h1>
        <p className="text-sm text-muted-foreground">
          Bạn không đua với bảng xP, bạn đua với những người trong giải này. Trang này đếm đội hình
          thật của họ: ai cả giải đang có mà bạn không có, và người lạ nào của bạn thật sự là lợi thế.
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-2">
        <div>
          <label className="text-xs text-muted-foreground">Mã mini-league</label>
          <Input value={leagueId} onChange={(e) => setLeagueId(e.target.value)} placeholder="vd 314159" className="w-40" />
        </div>
        <div>
          <label className="text-xs text-muted-foreground">FPL Team ID của bạn (tuỳ chọn)</label>
          <Input value={teamId} onChange={(e) => setTeamId(e.target.value)} placeholder="vd 1234567" className="w-40" />
        </div>
        <Button onClick={run} disabled={loading || !leagueId}>Phân tích</Button>
      </div>
      <p className="text-xs text-muted-foreground">
        Mã giải nằm trong URL trang giải trên fantasy.premierleague.com (…/leagues/<b>314159</b>/standings/c).
        Hiện chỉ hỗ trợ giải classic, chưa hỗ trợ head-to-head.
      </p>

      {loading && <Spinner label="Đang đọc đội hình của từng đối thủ…" />}
      {error && <ErrorBox error={error} />}

      {data && !loading && data.available === false && (
        <Card>
          <CardHeader><CardTitle className="text-base">Chưa đo được</CardTitle></CardHeader>
          <CardContent className="text-sm text-muted-foreground">{data.message}</CardContent>
        </Card>
      )}

      {data?.available && (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Stat label="Giải" value={data.league_name ?? "—"} />
            <Stat label="Đối thủ đã đọc" value={data.n_rivals} />
            <Stat label="Hở sườn (điểm)" value={fmt(data.exposure_xp)} />
            <Stat label="Lợi thế riêng (điểm)" value={fmt(data.upside_xp)} />
          </div>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Đọc trang này thế nào</CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="list-inside list-disc space-y-1 text-sm text-muted-foreground">
                {(data.notes ?? []).map((n: string, i: number) => <li key={i}>{n}</li>)}
              </ul>
            </CardContent>
          </Card>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                  <ShieldAlert className="h-4 w-4 text-danger" /> Cả giải có, bạn không có
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-1">
                {data.missing_template?.length ? data.missing_template.map((p: any) => (
                  <div key={p.id} className="flex items-center justify-between gap-2 text-sm">
                    <span className="flex min-w-0 items-center gap-1.5 truncate">
                      <PosTag pos={p.position} />
                      <span className="truncate">{p.name}</span>
                      <span className="shrink-0 text-xs text-muted-foreground">{p.team}</span>
                    </span>
                    <span className="shrink-0 tabular-nums">
                      <span className="text-muted-foreground">EO {fmt(p.league_eo, 0)}% · </span>
                      <span className="font-semibold text-danger">{fmt(p.rank_edge)}đ</span>
                    </span>
                  </div>
                )) : <p className="text-sm text-muted-foreground">Không thiếu ai trong nhóm sở hữu cao.</p>}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                  <Sparkles className="h-4 w-4 text-positive" /> Bạn có, cả giải không có
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-1">
                {data.my_differentials?.length ? data.my_differentials.map((p: any) => (
                  <div key={p.id} className="flex items-center justify-between gap-2 text-sm">
                    <span className="flex min-w-0 items-center gap-1.5 truncate">
                      <PosTag pos={p.position} />
                      <span className="truncate">{p.name}</span>
                      <span className="shrink-0 text-xs text-muted-foreground">{p.team}</span>
                    </span>
                    <span className="shrink-0 tabular-nums">
                      <span className="text-muted-foreground">EO {fmt(p.league_eo, 0)}% · </span>
                      <span className="font-semibold text-positive">+{fmt(p.rank_edge)}đ</span>
                    </span>
                  </div>
                )) : <p className="text-sm text-muted-foreground">
                  {data.has_my_squad ? "Đội của bạn đang trùng template của giải." : "Nhập Team ID để so sánh."}
                </p>}
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">
                Toàn bộ cầu thủ trong giải · đo ở GW{data.measured_gameweek}, xP của GW{data.projection_gameweek}
              </CardTitle>
            </CardHeader>
            <CardContent className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-xs uppercase text-muted-foreground">
                  <tr>
                    <th className="pb-2">Cầu thủ</th>
                    <th className="pb-2 text-right">EO giải</th>
                    <th className="pb-2 text-right">Sở hữu</th>
                    <th className="pb-2 text-right">Bắt băng</th>
                    <th className="pb-2 text-right">Của tôi</th>
                    <th className="pb-2 text-right">xP</th>
                    <th className="pb-2 text-right">Hơn/kém giải</th>
                  </tr>
                </thead>
                <tbody>
                  {(data.players ?? []).map((p: any) => (
                    <tr key={p.id} className="border-t">
                      <td className="py-1.5">
                        <span className="flex items-center gap-1.5">
                          <PosTag pos={p.position} />
                          {p.name}
                          <span className="text-xs text-muted-foreground">{p.team}</span>
                          {p.i_own && <Badge className="bg-primary/15 text-primary">của tôi</Badge>}
                        </span>
                      </td>
                      <td className="py-1.5 text-right font-semibold tabular-nums">{fmt(p.league_eo, 0)}%</td>
                      <td className="py-1.5 text-right tabular-nums text-muted-foreground">{fmt(p.league_owned_pct, 0)}%</td>
                      <td className="py-1.5 text-right tabular-nums text-muted-foreground">{fmt(p.league_captain_pct, 0)}%</td>
                      <td className="py-1.5 text-right tabular-nums text-muted-foreground">{p.my_multiplier > 0 ? `×${p.my_multiplier}` : "–"}</td>
                      <td className="py-1.5 text-right tabular-nums">{fmt(p.xp_next)}</td>
                      <td className={`py-1.5 text-right font-semibold tabular-nums ${p.rank_edge >= 0 ? "text-positive" : "text-danger"}`}>
                        {p.rank_edge >= 0 ? "+" : ""}{fmt(p.rank_edge)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </CardContent>
          </Card>

          {data.failed?.length > 0 && (
            <p className="text-xs text-muted-foreground">
              {data.failed.length} đối thủ không đọc được đội hình và đã bị loại khỏi mẫu — EO ở trên tính
              trên {data.n_rivals} người còn lại.
            </p>
          )}
        </>
      )}
    </div>
  );
}
