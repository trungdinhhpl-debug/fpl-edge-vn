"use client";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ChevronDown, ChevronUp, ChevronsUpDown } from "lucide-react";
import { useApi } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Card, CardContent, Select, Input, Spinner, ErrorBox, Badge } from "@/components/ui";
import { PosTag, RiskBadge, StatusDot } from "@/components/fpl";
import { SortControl, type SortDir } from "@/components/sort-control";
import { fmt } from "@/lib/format";
import { cn } from "@/lib/utils";

type SortKey =
  | "xp_next"
  | "xp_next5"
  | "xmins"
  | "price"
  | "value_next5"
  | "selected_by_percent"
  | "clean_sheet_prob"
  | "net_transfers";

const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: "xp_next5", label: "xP 5 vòng" },
  { key: "xp_next", label: "xP vòng tới" },
  { key: "xmins", label: "xMins" },
  { key: "value_next5", label: "Giá trị / triệu" },
  { key: "price", label: "Giá" },
  { key: "selected_by_percent", label: "Ownership" },
  { key: "clean_sheet_prob", label: "Xác suất sạch lưới" },
  { key: "net_transfers", label: "Chuyển nhượng ròng" },
];

// Số dòng hiện lúc đầu. Trước đây đây là một trần CỨNG ở 120 dòng: xếp mặc định
// theo xP 5 vòng thì toàn bộ nhóm £4.0–4.5 — đám enabler bắt buộc phải có để
// nuôi nổi hai premium — không bao giờ lọt vào màn hình, và người dùng không có
// cách nào biết là mình đang bị cắt.
const PAGE_SIZE = 150;

export default function PlayersPage() {
  const { t } = useT();
  const router = useRouter();
  const [pos, setPos] = useState("");
  const [q, setQ] = useState("");
  const [maxPrice, setMaxPrice] = useState("");
  const [teamId, setTeamId] = useState("");
  const [sort, setSort] = useState<SortKey>("xp_next5");
  const [dir, setDir] = useState<SortDir>("desc");
  const [showAll, setShowAll] = useState(false);

  const query =
    `/api/players?limit=1000` +
    (pos ? `&position=${pos}` : "") +
    (maxPrice ? `&max_price=${maxPrice}` : "") +
    (teamId ? `&team_id=${teamId}` : "");
  const { data, loading, error } = useApi<any>(query);
  const { data: teamData } = useApi<any>("/api/teams");

  const rows = useMemo(() => {
    let r = (data?.players ?? []) as any[];
    if (q) {
      // tìm theo tên cầu thủ HOẶC tên đội (vd gõ "Arsenal", "MCI")
      const needle = q.toLowerCase().trim();
      r = r.filter(
        (p) =>
          p.name.toLowerCase().includes(needle) ||
          (p.team ?? "").toLowerCase().includes(needle) ||
          (p.team_name ?? "").toLowerCase().includes(needle),
      );
    }
    const sign = dir === "asc" ? 1 : -1;
    return [...r].sort((a, b) => ((a[sort] ?? 0) - (b[sort] ?? 0)) * sign);
  }, [data, q, sort, dir]);

  const shown = showAll ? rows : rows.slice(0, PAGE_SIZE);

  /** Bấm tiêu đề cột: chọn cột mới (mặc định cao→thấp) hoặc đảo chiều cột đang xếp. */
  function toggleSort(key: SortKey) {
    if (sort === key) setDir((d) => (d === "desc" ? "asc" : "desc"));
    else {
      setSort(key);
      setDir("desc");
    }
  }

  /** Tiêu đề cột; truyền key=null cho cột không sắp xếp được. */
  const th = (label: string, key: SortKey | null, className: string) => (
    <th key={label} className={className}>
      {key ? (
        <button
          type="button"
          onClick={() => toggleSort(key)}
          className={cn(
            "inline-flex items-center gap-1 rounded transition hover:text-foreground focus:outline-none focus:ring-2 focus:ring-ring/40",
            sort === key && "font-semibold text-primary",
          )}
        >
          {label}
          {sort === key ? (
            dir === "desc" ? <ChevronDown className="h-3 w-3" /> : <ChevronUp className="h-3 w-3" />
          ) : (
            <ChevronsUpDown className="h-3 w-3 opacity-40" />
          )}
        </button>
      ) : (
        label
      )}
    </th>
  );

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold">{t("players")}</h1>
        <p className="text-sm text-muted-foreground">
          Lọc theo xP, xMins, giá trị, rủi ro — mọi cột đều dựa trên mô hình dữ liệu nền tảng.
          Cột “CN ròng” là số người mua trừ số người bán trong vòng này: động lượng đám đông,
          không phải dự báo đổi giá (ngưỡng đổi giá của FPL không công khai).
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        <Input
          placeholder="Tìm cầu thủ hoặc đội…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          className="max-w-[220px]"
        />
        <Select value={teamId} onChange={(e) => setTeamId(e.target.value)} aria-label="Lọc theo đội">
          <option value="">Tất cả đội</option>
          {(teamData?.teams ?? []).map((tm: any) => (
            <option key={tm.id} value={tm.id}>
              {tm.name}
            </option>
          ))}
        </Select>
        <Select value={pos} onChange={(e) => setPos(e.target.value)} aria-label="Lọc theo vị trí">
          <option value="">Tất cả vị trí</option>
          <option value="GK">Thủ môn</option>
          <option value="DEF">Hậu vệ</option>
          <option value="MID">Tiền vệ</option>
          <option value="FWD">Tiền đạo</option>
        </Select>
        <Select value={maxPrice} onChange={(e) => setMaxPrice(e.target.value)}>
          <option value="">Giá tối đa</option>
          {[5, 6, 7, 8, 9, 10, 12, 15].map((p) => (
            <option key={p} value={p}>£{p.toFixed(1)}</option>
          ))}
        </Select>
        <SortControl
          value={sort}
          options={SORT_OPTIONS}
          dir={dir}
          onValue={setSort}
          onDir={setDir}
        />
      </div>

      <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
        <span>
          {loading ? "Đang lọc…" : `${rows.length} cầu thủ`}
          {!loading && shown.length < rows.length && ` · đang hiện ${shown.length}`}
        </span>
        {!loading && rows.length > PAGE_SIZE && (
          <button
            type="button"
            onClick={() => setShowAll((s) => !s)}
            className="rounded-md border px-2 py-0.5 transition hover:bg-muted"
          >
            {showAll ? `Chỉ hiện ${PAGE_SIZE} đầu` : `Hiện tất cả ${rows.length}`}
          </button>
        )}
        {(q || pos || maxPrice || teamId) && (
          <button
            type="button"
            onClick={() => {
              setQ("");
              setPos("");
              setMaxPrice("");
              setTeamId("");
            }}
            className="rounded-md border px-2 py-0.5 transition hover:bg-muted"
          >
            Xoá bộ lọc
          </button>
        )}
      </div>

      {loading ? (
        <Spinner label={t("loading")} />
      ) : error ? (
        <ErrorBox error={error} />
      ) : (
        <Card>
          <CardContent className="p-0">
            <div className="scroll-thin overflow-x-auto">
              <table className="w-full min-w-[820px] text-sm">
                <thead className="border-b bg-muted/40 text-xs uppercase text-muted-foreground">
                  <tr>
                    {th("Cầu thủ", null, "p-3 text-left")}
                    {th("Vị trí", null, "p-2 text-center")}
                    {th("Giá", "price", "p-2 text-right")}
                    {th("xP (1)", "xp_next", "p-2 text-right")}
                    {th("xP (5)", "xp_next5", "p-2 text-right")}
                    {th("xMins", "xmins", "p-2 text-right")}
                    {th("Val/£", "value_next5", "p-2 text-right")}
                    {th("CS%", "clean_sheet_prob", "p-2 text-right")}
                    {th("Rủi ro", null, "p-2 text-center")}
                    {th("Own%", "selected_by_percent", "p-2 text-right")}
                    {th("CN ròng", "net_transfers", "p-2 text-right")}
                  </tr>
                </thead>
                <tbody>
                  {shown.map((p) => (
                    <tr
                      key={p.id}
                      onClick={() => router.push(`/players/${p.id}`)}
                      className="cursor-pointer border-b transition hover:bg-muted/40"
                    >
                      <td className="p-3">
                        <div className="flex items-center gap-1.5 font-medium">
                          {p.name}
                          <StatusDot status={p.status} />
                          {p.penalties_order === 1 && <Badge className="bg-primary/15 text-primary">PEN</Badge>}
                        </div>
                        <div className="text-xs text-muted-foreground">{p.team}</div>
                      </td>
                      <td className="p-2 text-center"><PosTag pos={p.position} /></td>
                      <td className="p-2 text-right tabular-nums">£{fmt(p.price)}</td>
                      <td className="p-2 text-right font-semibold tabular-nums text-primary">{fmt(p.xp_next)}</td>
                      <td className="p-2 text-right font-semibold tabular-nums">{fmt(p.xp_next5)}</td>
                      <td className="p-2 text-right tabular-nums text-muted-foreground">{fmt(p.xmins, 0)}'</td>
                      <td className="p-2 text-right tabular-nums">{fmt(p.value_next5)}</td>
                      <td className="p-2 text-right tabular-nums text-muted-foreground">
                        {p.clean_sheet_prob != null ? `${Math.round(p.clean_sheet_prob * 100)}%` : "–"}
                      </td>
                      <td className="p-2 text-center"><RiskBadge level={p.overall_risk} /></td>
                      <td className="p-2 text-right tabular-nums text-muted-foreground">{fmt(p.selected_by_percent)}</td>
                      <td
                        className={cn(
                          "p-2 text-right tabular-nums",
                          (p.net_transfers ?? 0) > 0 ? "text-positive"
                            : (p.net_transfers ?? 0) < 0 ? "text-danger" : "text-muted-foreground",
                        )}
                      >
                        {p.net_transfers ? `${p.net_transfers > 0 ? "+" : "−"}${Math.abs(Math.round(p.net_transfers / 1000))}k` : "0"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
