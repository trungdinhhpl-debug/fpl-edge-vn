"use client";
import Link from "next/link";
import { Users, Copy, AlertTriangle, ShieldQuestion } from "lucide-react";
import { useApi } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Card, CardContent, CardHeader, CardTitle, Spinner, ErrorBox, Badge } from "@/components/ui";
import { MockTag, PosTag } from "@/components/fpl";

/** The whole point: raw post count vs what the evidence actually supports. */
function ConsensusBar({ p }: { p: any }) {
  const real = p.consensus_pct;
  const naive = p.naive_consensus_pct;
  const inflated = real != null && naive != null && naive > real + 5;
  // Một nguồn duy nhất thì KHÔNG có đồng thuận nào để đo. "100% đồng thuận" khi chỉ
  // có một tiếng nói là đúng về số học nhưng đọc thành "ai cũng đồng ý" — sai hẳn ý
  // nghĩa. Đây chính là lỗi mà cả trang này được viết ra để chống, nên nó không được
  // phép tồn tại ở chính đây.
  const single = (p.independent_sources ?? 0) <= 1;
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm">
        {single ? (
          <>
            <b className="text-lg">1 nguồn</b>
            <span className="text-muted-foreground">
              — chưa đủ để nói về đồng thuận
            </span>
          </>
        ) : (
          <>
            <b className="text-lg tabular-nums">{real == null ? "–" : `${real}%`}</b>
            <span className="text-muted-foreground">đồng thuận thực</span>
          </>
        )}
        {!single && naive != null && (
          <span className={`text-xs ${inflated ? "text-caution" : "text-muted-foreground"}`}>
            (đếm thô: {naive}%)
          </span>
        )}
      </div>
      {!single && (
        <div className="h-2 overflow-hidden rounded-full bg-muted">
          <div className="h-full rounded-full bg-primary" style={{ width: `${real ?? 0}%` }} />
        </div>
      )}
      <div className="flex flex-wrap gap-x-3 text-xs text-muted-foreground">
        <span>{p.posts} bài đăng</span>
        <span className="font-medium text-foreground">
          {p.independent_sources} nguồn độc lập
        </span>
        {p.echo_accounts > 0 && (
          <span className="flex items-center gap-1 text-caution">
            <Copy className="h-3 w-3" /> {p.echo_accounts} tài khoản lặp lại
          </span>
        )}
      </div>
    </div>
  );
}

export default function ExpertsPage() {
  const { t } = useT();
  const { data, loading, error } = useApi<any>("/api/expert-consensus");

  if (loading) return <Spinner label={t("loading")} />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold">
          <Users className="h-6 w-6 text-primary" /> {t("experts")}
        </h1>
        <p className="text-sm text-muted-foreground">
          Đếm bài đăng là cách dễ sai nhất: nhiều tài khoản dẫn lại một phát biểu là
          MỘT bằng chứng được lan truyền, không phải nhiều người cùng đồng ý.
        </p>
      </div>

      {/* ---------------------------- consensus per player ------------------ */}
      <div className="space-y-3">
        {data.players?.length ? data.players.map((p: any) => (
          <Card key={p.id}>
            <CardContent className="space-y-3 pt-4">
              <div className="flex flex-wrap items-center gap-2">
                <Link href={`/players/${p.id}`}
                      className="font-semibold hover:text-primary">{p.name}</Link>
                <PosTag pos={p.position} />
                <span className="text-sm text-muted-foreground">{p.team}</span>
                {p.has_dissent && (
                  <Badge className="bg-caution/15 text-caution">
                    <AlertTriangle className="mr-1 inline h-3 w-3" />có ý kiến trái chiều
                  </Badge>
                )}
              </div>

              <ConsensusBar p={p} />

              {/* one row per PRIMARY source, however many accounts carried it */}
              <div className="space-y-1">
                {p.votes.map((v: any, i: number) => (
                  <div key={i}
                       className="flex flex-wrap items-center gap-2 rounded-md border px-2 py-1.5 text-sm">
                    <Badge className={
                      v.direction === "for" ? "bg-positive/15 text-positive"
                      : v.direction === "against" ? "bg-danger/15 text-danger"
                      : "bg-muted text-muted-foreground"
                    }>
                      {v.direction === "for" ? "Ủng hộ"
                        : v.direction === "against" ? "Trái chiều" : "Trung lập"}
                    </Badge>
                    <span className="text-muted-foreground">{v.sources.join(", ")}</span>
                    {v.n_posts > 1 && (
                      <Badge className="bg-caution/15 text-caution">
                        <Copy className="mr-1 inline h-3 w-3" />
                        {v.n_posts} bài, cùng 1 nguồn gốc
                      </Badge>
                    )}
                    <span className="ml-auto text-xs tabular-nums text-muted-foreground">
                      trọng số {v.weight}
                    </span>
                  </div>
                ))}
              </div>

              {p.echoed_origins?.length > 0 && (
                <p className="text-xs text-muted-foreground">
                  Nguồn gốc bị dẫn lại: <code>{p.echoed_origins.join(", ")}</code>
                </p>
              )}

              <details>
                <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
                  Xem từng tín hiệu ({p.signals.length})
                </summary>
                <ul className="mt-1 space-y-1 border-l pl-3">
                  {p.signals.map((s: any, i: number) => (
                    <li key={i} className="text-xs text-muted-foreground">
                      <b className="text-foreground">{s.source}</b>{" "}
                      <Badge className="bg-muted text-muted-foreground">{s.signal_type}</Badge>{" "}
                      <span className="opacity-70">[{s.domain_label}]</span>{" "}
                      {s.is_mock && <MockTag />} — {s.summary}
                    </li>
                  ))}
                </ul>
              </details>
            </CardContent>
          </Card>
        )) : <p className="text-sm text-muted-foreground">{t("noData")}</p>}
      </div>

      {/* --------------------------- trạng thái từng nguồn ------------------ */}
      {data.source_status && (
        <Card>
          <CardHeader><CardTitle>Nguồn nào đang chạy</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p className="text-muted-foreground">
              Mọi tín hiệu ở trang này đến từ <b>API công khai của FPL</b> — không cào
              nội dung trả phí, và không gắn phát biểu cho người thật. Nguồn chưa có dữ
              liệu thì nói rõ vì sao, thay vì để trống cho người đọc tự suy.
            </p>
            <ul className="space-y-1.5">
              {data.source_status.map((s: any) => (
                <li key={s.id} className="flex flex-wrap items-baseline gap-x-2">
                  <Badge
                    className={
                      s.state === "đang chạy"
                        ? "bg-positive/15 text-positive"
                        : s.state === "chưa có dữ liệu"
                          ? "bg-caution/15 text-caution"
                          : "bg-muted text-muted-foreground"
                    }
                  >
                    {s.state}
                  </Badge>
                  <b>{s.name}</b>
                  {s.signals > 0 && (
                    <span className="tabular-nums text-muted-foreground">
                      · {s.signals} tín hiệu
                    </span>
                  )}
                  <span className="w-full text-xs text-muted-foreground">{s.why}</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      {/* ------------------------------- source registry -------------------- */}
      <Card>
        <CardHeader><CardTitle>Danh bạ nguồn</CardTitle></CardHeader>
        <CardContent className="p-0">
          <div className="scroll-thin overflow-x-auto">
            <table className="w-full min-w-[820px] text-sm">
              <thead className="border-b bg-muted/40 text-xs uppercase text-muted-foreground">
                <tr>
                  <th className="p-3 text-left">Nguồn</th>
                  <th className="p-2 text-left">Loại</th>
                  <th className="p-2 text-right">Tin cậy nền</th>
                  <th className="p-2 text-right">Độc lập</th>
                  <th className="p-2 text-left">Chuyên môn &amp; lịch sử chính xác</th>
                  <th className="p-2 text-left">Thứ hạng FPL</th>
                </tr>
              </thead>
              <tbody>
                {data.sources.map((s: any) => (
                  <tr key={s.id} className="border-b align-top hover:bg-muted/40">
                    <td className="p-3">
                      {s.url ? (
                        <a href={s.url} target="_blank" rel="noreferrer"
                           className="font-medium underline hover:text-primary">{s.name}</a>
                      ) : <span className="font-medium">{s.name}</span>}
                    </td>
                    <td className="p-2 text-muted-foreground">{s.type}</td>
                    <td className="p-2 text-right tabular-nums">
                      {Math.round(s.reliability * 100)}%
                    </td>
                    <td className="p-2 text-right tabular-nums">
                      {Math.round(s.independence * 100)}%
                    </td>
                    <td className="p-2">
                      {s.expertise.length === 0 ? (
                        <span className="text-xs text-muted-foreground">—</span>
                      ) : (
                        <ul className="space-y-0.5">
                          {s.expertise.map((e: any) => (
                            <li key={e.domain} className="text-xs">
                              <span className="text-muted-foreground">{e.label}:</span>{" "}
                              {e.accuracy == null ? (
                                <span className="italic text-muted-foreground">{e.status}</span>
                              ) : (
                                <b>{Math.round(e.accuracy * 100)}% ({e.resolved} dự đoán)</b>
                              )}
                            </li>
                          ))}
                        </ul>
                      )}
                    </td>
                    <td className="p-2">
                      <span className="flex items-start gap-1 text-xs text-muted-foreground">
                        <ShieldQuestion className="mt-0.5 h-3 w-3 shrink-0" />
                        chưa xác minh
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="space-y-1 border-t p-3 text-xs text-muted-foreground">
            <p>
              <b>Tin cậy nền</b> là tiên nghiệm theo <i>loại</i> nguồn (toà soạn có quy
              trình đính chính so với diễn đàn ẩn danh), không phải đánh giá hiệu suất
              của cá nhân nào. Số người theo dõi không được tính.
            </p>
            <p>
              <b>Lịch sử chính xác</b> chỉ hiện sau khi có đủ {data.min_scored_sample} dự
              đoán đã được chấm điểm. Đây là những người và tổ chức có thật, nên trang
              này không gán sẵn con số chưa từng đo.
            </p>
            <p>
              <b>Thứ hạng FPL</b> để trống: FPL không có API xác thực thứ hạng của một
              tài khoản bên thứ ba, nên chép lại con số tự khai là không kiểm chứng được.
            </p>
          </div>
        </CardContent>
      </Card>

      <p className="text-xs text-muted-foreground">{data.disclaimer}</p>
    </div>
  );
}
