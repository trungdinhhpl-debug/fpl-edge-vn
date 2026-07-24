"use client";
import { useState } from "react";
import { Newspaper } from "lucide-react";
import { useApi } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Card, CardContent, Spinner, ErrorBox, Badge, Button } from "@/components/ui";
import { riskBg } from "@/lib/utils";
import { timeAgo } from "@/lib/format";

const IMPACTS = ["", "Critical", "High", "Medium", "Low"];

export default function NewsPage() {
  const { t, lang } = useT();
  const [impact, setImpact] = useState("");
  const { data, loading, error } = useApi<any>(`/api/news?limit=150${impact ? `&impact=${impact}` : ""}`);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold"><Newspaper className="h-6 w-6 text-primary" /> {t("news")}</h1>
        <p className="text-sm text-muted-foreground">
          Chấn thương, án treo giò, tin ra sân. Mỗi tin kèm mức ảnh hưởng, nguồn, thời gian và trạng thái xác nhận.
        </p>
      </div>

      <div className="flex gap-1 rounded-md border p-1">
        {IMPACTS.map((im) => (
          <Button key={im} size="sm" variant={impact === im ? "default" : "ghost"} onClick={() => setImpact(im)}>
            {im || "Tất cả"}
          </Button>
        ))}
      </div>

      {loading ? <Spinner label={t("loading")} /> : error ? <ErrorBox error={error} /> : (
        <div className="space-y-2">
          {data?.news?.length ? data.news.map((n: any, i: number) => (
            <Card key={i}>
              <CardContent className="flex items-start gap-3 py-3">
                <Badge className={riskBg(n.impact === "Critical" ? "Very High" : n.impact === "High" ? "High" : n.impact === "Medium" ? "Medium" : "Low")}>
                  {n.impact}
                </Badge>
                <div className="flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold">{n.name}</span>
                    <span className="text-sm text-muted-foreground">{n.team} · {n.position}</span>
                    {n.status !== "a" && <Badge className="bg-muted text-muted-foreground">status: {n.status}</Badge>}
                    {!n.confirmed && <Badge className="bg-caution/15 text-caution">chưa xác nhận</Badge>}
                    {n.chance_of_playing != null && <Badge className="bg-muted">{n.chance_of_playing}% ra sân</Badge>}
                  </div>
                  {n.news && <p className="mt-1 text-sm text-muted-foreground">{n.news}</p>}
                  <div className="mt-1 text-xs text-muted-foreground">
                    {t("source")}: {n.source_name} · {timeAgo(n.fetched_at, lang)}
                  </div>
                </div>
              </CardContent>
            </Card>
          )) : <p className="text-sm text-muted-foreground">{t("noData")}</p>}
        </div>
      )}
    </div>
  );
}
