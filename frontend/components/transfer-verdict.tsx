"use client";
import { Card, CardContent, CardHeader, CardTitle, Badge } from "@/components/ui";

/** Khuyến nghị chuyển nhượng theo một cấu trúc cố định.
 *
 * Cùng một khung cho mọi khuyến nghị, để hai lựa chọn luôn so được với nhau: lợi
 * ích ở ba mốc horizon, các điều chỉnh có căn cứ, lợi ích ròng, kết luận, độ tin
 * cậy, và những tin CÓ THỂ làm đổi quyết định — kèm việc nói rõ tin nào hệ thống
 * không theo dõi được.
 */
export function TransferVerdict({ v }: { v: any }) {
  if (!v) return null;
  const roll = v.recommendation === "ROLL TRANSFER";

  return (
    <Card className={roll ? "border-caution/40" : "border-positive/40"}>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle className="text-base">
          KHUYẾN NGHỊ:{" "}
          <span className={roll ? "text-caution" : "text-positive"}>
            {v.recommendation}
          </span>
        </CardTitle>
        <div className="flex items-center gap-1.5">
          <Badge className="bg-muted text-muted-foreground">GW{v.gameweek}</Badge>
          <Badge className="bg-muted text-muted-foreground">
            {v.free_transfers} FT
          </Badge>
        </div>
      </CardHeader>

      <CardContent className="space-y-4 text-sm">
        {v.best_move ? (
          <>
            <Section title="Phương án tốt nhất nếu transfer">
              <div className="font-medium text-foreground">
                Bán {v.best_move.out.name}{" "}
                <span className="text-xs text-muted-foreground">
                  ({v.best_move.out.team} £{v.best_move.out.price}m)
                </span>{" "}
                → Mua {v.best_move.in.name}{" "}
                <span className="text-xs text-muted-foreground">
                  ({v.best_move.in.team} £{v.best_move.in.price}m)
                </span>
              </div>
            </Section>

            <Section title="Chênh lệch xP">
              <div className="flex flex-wrap gap-x-6 gap-y-1">
                {["1gw", "3gw", "5gw"].map((k) => (
                  <span key={k} className="text-muted-foreground">
                    {k.replace("gw", " GW")}:{" "}
                    <b className="tabular-nums text-foreground">
                      {fmtSigned(v.xp_delta?.[k])}
                    </b>
                  </span>
                ))}
              </div>
            </Section>

            <Section title="Điều chỉnh">
              <div className="space-y-1.5">
                {(v.adjustments ?? []).map((a: any) => (
                  <div key={a.label}>
                    <div className="flex items-baseline justify-between gap-4">
                      <span className="text-muted-foreground">{a.label}</span>
                      <b className="tabular-nums text-foreground">
                        {fmtSigned(a.value)}
                      </b>
                    </div>
                    <div className="text-xs text-muted-foreground">{a.basis}</div>
                  </div>
                ))}
                <div className="flex items-baseline justify-between gap-4 border-t pt-1.5">
                  <span className="font-medium text-foreground">Lợi ích ròng</span>
                  <b
                    className={`tabular-nums ${
                      (v.net_gain ?? 0) > 0 ? "text-positive" : "text-caution"
                    }`}
                  >
                    {fmtSigned(v.net_gain)}
                  </b>
                </div>
                {v.net_basis && (
                  <div className="text-xs text-muted-foreground">{v.net_basis}</div>
                )}
              </div>
            </Section>
          </>
        ) : null}

        <Section title="Kết luận">
          <p className="text-foreground">{v.conclusion}</p>
        </Section>

        <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1 border-t pt-3 text-xs">
          <span className="text-muted-foreground">
            Confidence:{" "}
            <b className="tabular-nums text-foreground">
              {v.confidence?.value === undefined
                ? "—"
                : `${Math.round(v.confidence.value * 100)}%`}
            </b>
          </span>
          {v.bank !== undefined && (
            <span className="text-muted-foreground">
              Bank: <b className="text-foreground">£{v.bank}m</b>
            </span>
          )}
        </div>
        {v.confidence?.basis && (
          <p className="text-xs text-muted-foreground">{v.confidence.basis}</p>
        )}

        <Section title="Tin có thể làm thay đổi quyết định">
          <ul className="space-y-2">
            {(v.news_watch ?? []).map((n: any) => (
              <li key={n.kind}>
                <div className="flex items-center gap-2">
                  <span className="text-foreground">{n.kind}</span>
                  {/* Phân biệt rõ "đã kiểm và không có gì" với "hệ thống không
                      theo dõi được" — gộp hai thứ là gợi ý sai rằng đã kiểm. */}
                  <Badge
                    className={
                      n.known
                        ? "bg-caution/15 text-caution"
                        : "bg-muted text-muted-foreground"
                    }
                  >
                    {n.known ? "có tín hiệu" : "không theo dõi được"}
                  </Badge>
                </div>
                <div className="text-xs text-muted-foreground">{n.detail}</div>
                {n.caveat && (
                  <div className="text-xs text-muted-foreground/80">{n.caveat}</div>
                )}
              </li>
            ))}
          </ul>
        </Section>
      </CardContent>
    </Card>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
      </div>
      {children}
    </div>
  );
}

function fmtSigned(x: number | null | undefined) {
  if (x === null || x === undefined) return "—";
  return `${x > 0 ? "+" : ""}${x.toFixed(1).replace(".", ",")}`;
}
