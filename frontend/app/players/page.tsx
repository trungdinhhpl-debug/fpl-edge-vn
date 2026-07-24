"use client";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useApi } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Card, CardContent, Select, Input, Spinner, ErrorBox, Badge } from "@/components/ui";
import { PosTag, RiskBadge, StatusDot } from "@/components/fpl";
import { fmt } from "@/lib/format";

type SortKey = "xp_next" | "xp_next5" | "xmins" | "price" | "value_next5" | "selected_by_percent";

export default function PlayersPage() {
  const { t } = useT();
  const router = useRouter();
  const [pos, setPos] = useState("");
  const [q, setQ] = useState("");
  const [maxPrice, setMaxPrice] = useState("");
  const [sort, setSort] = useState<SortKey>("xp_next5");

  const query = `/api/players?limit=1000${pos ? `&position=${pos}` : ""}${maxPrice ? `&max_price=${maxPrice}` : ""}`;
  const { data, loading, error } = useApi<any>(query);

  const rows = useMemo(() => {
    let r = (data?.players ?? []) as any[];
    if (q) r = r.filter((p) => p.name.toLowerCase().includes(q.toLowerCase()));
    r = [...r].sort((a, b) => (b[sort] ?? 0) - (a[sort] ?? 0));
    return r.slice(0, 120);
  }, [data, q, sort]);

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
        <Select value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
          <option value="xp_next5">Sắp xếp: xP 5 vòng</option>
          <option value="xp_next">xP vòng tới</option>
          <option value="xmins">xMins</option>
          <option value="value_next5">Giá trị / triệu</option>
          <option value="price">Giá</option>
          <option value="selected_by_percent">Ownership</option>
        </Select>
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
                    <th className="p-3 text-left">Cầu thủ</th>
                    <th className="p-2 text-center">Vị trí</th>
                    <th className="p-2 text-right">Giá</th>
                    <th className="p-2 text-right">xP (1)</th>
                    <th className="p-2 text-right">xP (5)</th>
                    <th className="p-2 text-right">xMins</th>
                    <th className="p-2 text-right">Val/£</th>
                    <th className="p-2 text-right">CS%</th>
                    <th className="p-2 text-center">Rủi ro</th>
                    <th className="p-2 text-right">Own%</th>
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
