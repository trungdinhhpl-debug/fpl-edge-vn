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
  | "clean_sheet_prob";

const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: "xp_next5", label: "xP 5 vòng" },
  { key: "xp_next", label: "xP vòng tới" },
  { key: "xmins", label: "xMins" },
  { key: "value_next5", label: "Giá trị / triệu" },
  { key: "price", label: "Giá" },
  { key: "selected_by_percent", label: "Ownership" },
  { key: "clean_sheet_prob", label: "Xác suất sạch lưới" },
];

export default function PlayersPage() {
  const { t } = useT();
  const router = useRouter();
  const [pos, setPos] = useState("");
  const [q, setQ] = useState("");
  const [maxPrice, setMaxPrice] = useState("");
  const [sort, setSort] = useState<SortKey>("xp_next5");
  const [dir, setDir] = useState<SortDir>("desc");

  const query = `/api/players?limit=1000${pos ? `&position=${pos}` : ""}${maxPrice ? `&max_price=${maxPrice}` : ""}`;
  const { data, loading, error } = useApi<any>(query);

  const rows = useMemo(() => {
    let r = (data?.players ?? []) as any[];
    if (q) r = r.filter((p) => p.name.toLowerCase().includes(q.toLowerCase()));
    const sign = dir === "asc" ? 1 : -1;
    r = [...r].sort((a, b) => ((a[sort] ?? 0) - (b[sort] ?? 0)) * sign);
    return r.slice(0, 120);
  }, [data, q, sort, dir]);

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
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        <Input placeholder="Tìm cầu thủ…" value={q} onChange={(e) => setQ(e.target.value)} className="max-w-[200px]" />
        <Select value={pos} onChange={(e) => setPos(e.target.value)}>
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
                  </tr>
                </thead>
                <tbody>
                  {rows.map((p) => (
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
