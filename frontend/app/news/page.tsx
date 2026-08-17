"use client";
import { useState } from "react";
import { Newspaper, ArrowRight, Clock, Download, Users, History } from "lucide-react";
import { useApi } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Card, CardContent, Spinner, ErrorBox, Badge, Button } from "@/components/ui";
import { riskBg } from "@/lib/utils";
import { timeAgo, fmt, parseApiDate } from "@/lib/format";

const IMPACTS = ["", "Critical", "High", "Medium", "Low"];

const ACTION_CLS: Record<string, string> = {
  "Bán": "bg-danger/15 text-danger",
  "Cân nhắc bán": "bg-caution/15 text-caution",
  "Theo dõi": "bg-muted text-muted-foreground",
  "Giữ": "bg-positive/15 text-positive",
};

function dt(iso: string | null) {
  if (!iso) return "—";
  return parseApiDate(iso).toLocaleString("vi-VN", {
    timeZone: "Asia/Ho_Chi_Minh", dateStyle: "short", timeStyle: "short",
  });
}

/** xMins 78 → 42, with the drop called out. Null "before" means no event. */
function XminsShift({ before, after, delta }: {
  before: number | null; after: number | null; delta: number | null;
}) {
  if (before == null || delta == null) {
    return (
      <div className="text-sm">
        <span className="text-muted-foreground">xMins </span>
        <b className="tabular-nums">{fmt(after, 0)}′</b>{" "}
        <span className="text-xs text-muted-foreground">
          (không có sự kiện để so trước/sau)
        </span>
      </div>
    );
  }
  const worse = delta < 0;
  return (
    <div className="flex flex-wrap items-baseline gap-1.5 text-sm">
      <span className="text-muted-foreground">Trước tin</span>
      <b className="tabular-nums">{fmt(before, 0)}′</b>
      <ArrowRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      <span className="text-muted-foreground">Sau tin</span>
      <b className={`tabular-nums ${worse ? "text-danger" : "text-positive"}`}>
        {fmt(after, 0)}′
      </b>
      <span className={`text-xs tabular-nums ${worse ? "text-danger" : "text-positive"}`}>
        ({delta > 0 ? "+" : ""}{fmt(delta, 0)}′)
      </span>
    </div>
  );
}

export default function NewsPage() {
  const { t, lang } = useT();
  const [impact, setImpact] = useState("");
  const [tier, setTier] = useState("");
  const qs = [`limit=150`, impact && `impact=${impact}`, tier && `tier=${tier}`]
    .filter(Boolean).join("&");
  const { data, loading, error } = useApi<any>(`/api/news?${qs}`);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold">
          <Newspaper className="h-6 w-6 text-primary" /> {t("news")}
        </h1>
        <p className="text-sm text-muted-foreground">
          Mỗi tin kèm nguồn gốc, thời điểm, vòng bị ảnh hưởng và thay đổi xMins —
          để tin trở thành hành động, không chỉ là bảng tin.
        </p>
      </div>

      {/* provenance tiers, including the ones with no feed yet */}
      {data?.tiers && (
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {data.tiers.map((tr: any) => {
            const on = tier === tr.key;
            return (
              <button
                key={tr.key}
                onClick={() => setTier(on ? "" : tr.key)}
                disabled={!tr.configured}
                className={`rounded-md border p-2.5 text-left transition ${
                  on ? "border-primary bg-primary/5" : "hover:border-primary/40"
                } ${tr.configured ? "" : "cursor-not-allowed opacity-60"}`}
                title={tr.configured ? tr.description : tr.needs}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium">
                    <span className="mr-1 text-xs text-muted-foreground">{tr.rank}.</span>
                    {tr.label}
                  </span>
                  {tr.configured ? (
                    <Badge className="bg-primary/15 text-primary">{tr.count}</Badge>
                  ) : (
                    <Badge className="bg-muted text-muted-foreground">chưa có nguồn</Badge>
                  )}
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {tr.configured ? tr.description : tr.needs}
                </p>
                {tr.configured && (
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    Độ tin cậy tầng {tr.reliability} · nguồn: {tr.feed}
                  </p>
                )}
              </button>
            );
          })}
        </div>
      )}

      <div className="flex flex-wrap gap-1 rounded-md border p-1">
        {IMPACTS.map((im) => (
          <Button key={im} size="sm" variant={impact === im ? "default" : "ghost"}
                  onClick={() => setImpact(im)}>
            {im || "Tất cả"}
          </Button>
        ))}
        {tier && (
          <Button size="sm" variant="ghost" onClick={() => setTier("")}>
            Bỏ lọc tầng ✕
          </Button>
        )}
      </div>

      {loading ? <Spinner label={t("loading")} /> : error ? <ErrorBox error={error} /> : (
        <div className="space-y-2">
          {data?.items?.length ? data.items.map((n: any, i: number) => (
            <Card key={`${n.player_id}-${i}`}>
              <CardContent className="flex items-start gap-3 py-3">
                <Badge className={riskBg(
                  n.impact === "Critical" ? "Very High"
                  : n.impact === "High" ? "High"
                  : n.impact === "Medium" ? "Medium" : "Low"
                )}>
                  {n.impact}
                </Badge>

                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold">{n.name}</span>
                    <span className="text-sm text-muted-foreground">
                      {n.team} · {n.position}
                    </span>
                    <Badge className="bg-muted text-muted-foreground">{n.tier_label}</Badge>
                    {n.chance_of_playing != null && (
                      <Badge className="bg-muted">{n.chance_of_playing}% ra sân</Badge>
                    )}
                    <Badge className="ml-auto bg-primary/10 text-primary">
                      GW{n.affected_gameweek}
                    </Badge>
                  </div>

                  {n.news && <p className="mt-1 text-sm text-muted-foreground">{n.news}</p>}

                  <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1">
                    <XminsShift before={n.xmins_before} after={n.xmins_after}
                                delta={n.xmins_delta} />
                    {n.action && (
                      <Badge className={ACTION_CLS[n.action.to] ?? "bg-muted"}>
                        {n.action.label}
                      </Badge>
                    )}
                  </div>
                  {n.action?.why && (
                    <p className="mt-1 text-xs text-muted-foreground">{n.action.why}</p>
                  )}

                  <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                    <span>
                      {t("source")}:{" "}
                      {n.source_url ? (
                        <a href={n.source_url} target="_blank" rel="noreferrer"
                           className="underline hover:text-primary">{n.source_name}</a>
                      ) : n.source_name}
                    </span>
                    <span className="flex items-center gap-1">
                      <Clock className="h-3 w-3" /> Xuất bản: {dt(n.published_at)}
                    </span>
                    <span className="flex items-center gap-1">
                      <Download className="h-3 w-3" /> Lấy tin: {dt(n.fetched_at)}
                      <span className="opacity-70">({timeAgo(n.fetched_at, lang)})</span>
                    </span>
                    <span className="flex items-center gap-1">
                      <Users className="h-3 w-3" />
                      {n.independent_sources} nguồn độc lập
                      {n.independent_source_names?.length
                        ? ` (${n.independent_source_names.join(", ")})` : ""}
                    </span>
                  </div>

                  {n.history?.length > 0 && (
                    <details className="mt-2">
                      <summary className="flex cursor-pointer items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
                        <History className="h-3 w-3" /> Diễn biến trước đó ({n.history.length})
                      </summary>
                      <ul className="mt-1 space-y-0.5 border-l pl-3">
                        {n.history.map((h: any, k: number) => (
                          <li key={k} className="text-xs text-muted-foreground">
                            <span className="opacity-70">{dt(h.fetched_at)}:</span> {h.news}
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                </div>
              </CardContent>
            </Card>
          )) : <p className="text-sm text-muted-foreground">{t("noData")}</p>}
        </div>
      )}

      {data?.note && <p className="text-xs text-muted-foreground">{data.note}</p>}
    </div>
  );
}
