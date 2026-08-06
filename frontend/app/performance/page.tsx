"use client";
import { Gauge } from "lucide-react";
import { useApi } from "@/lib/api";
import {
  Card, CardContent, CardHeader, CardTitle, Spinner, ErrorBox, Badge,
} from "@/components/ui";

/** Model Performance — chất lượng dự báo đo bằng kết quả thật.
 *
 * Nguyên tắc trình bày: một ô trống phải nói được VÌ SAO trống, và phải phân biệt
 * "chưa có dữ liệu" với "không định nghĩa được cho cột này". Gộp hai thứ lại sẽ
 * khiến người đọc chờ một con số không bao giờ tới.
 */
export default function PerformancePage() {
  const { data, error } = useApi<any>("/api/model/performance");
  const st = data?.state ?? {};

  // Tiêu đề luôn hiển thị, kể cả khi đang tải hoặc lỗi. Bản đầu return sớm nên
  // trang chỉ ra một spinner trần không tiêu đề — và khi backend chưa kịp deploy
  // endpoint thì người dùng chỉ thấy một khung lỗi không rõ của trang nào.
  const header = (
    <div>
      <h1 className="flex items-center gap-2 text-2xl font-bold">
        <Gauge className="h-6 w-6 text-primary" /> Model Performance
      </h1>
      <p className="text-sm text-muted-foreground">
        Mô hình dự báo tốt đến đâu, đo bằng kết quả đã xảy ra — không phải bằng lời.
        So với hai baseline: chỉ số <code>form</code> của chính FPL và sức mạnh đội
        suy từ kèo.
      </p>
    </div>
  );

  if (error) {
    return (
      <div className="space-y-4">
        {header}
        <ErrorBox error={String(error)} />
        <p className="text-xs text-muted-foreground">
          Nếu backend vừa được cập nhật, endpoint <code>/api/model/performance</code> có
          thể chưa sống. Thử lại sau vài phút.
        </p>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="space-y-4">
        {header}
        <Spinner label="Đang tính chỉ số…" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {header}

      <div className="flex flex-wrap gap-4 rounded-md border bg-muted/30 px-3 py-2 text-xs">
        <span>
          Mùa: <b className="text-foreground">{data.season}</b>
        </span>
        <span>
          Model: <b className="text-foreground">{data.model_version}</b>
        </span>
        <span>
          Dự báo đã đóng băng: <b className="text-foreground">{st.snapshots ?? 0}</b>
        </span>
        <span>
          Đã có kết quả để chấm: <b className="text-foreground">{st.snapshots_scored ?? 0}</b>
        </span>
        <span>
          Vòng đã kết thúc: <b className="text-foreground">{st.gameweeks_finished ?? 0}</b>
        </span>
        {st.archiving_active ? (
          <Badge className="bg-positive/15 text-positive">đang lưu trữ dự báo</Badge>
        ) : (
          <Badge className="bg-negative/15 text-negative">CHƯA lưu trữ dự báo</Badge>
        )}
      </div>

      {st.gameweeks_finished === 0 && (
        <Card className="border-caution/50">
          <CardContent className="pt-4 text-sm text-muted-foreground">
            <b className="text-foreground">Chưa vòng nào kết thúc</b> nên chưa ô nào có
            số — đó là câu trả lời đúng, không phải lỗi. Mọi chỉ số ở đây cần kết quả
            thật để so. Dự báo đang được đóng băng trước mỗi deadline, nên ngay sau vòng
            1 các cột sẽ bắt đầu có số.
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Dự báo cầu thủ</CardTitle>
        </CardHeader>
        <CardContent className="px-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-muted-foreground">
                  <th className="px-4 py-2 font-medium">Chỉ số</th>
                  <th className="px-4 py-2 font-medium">Model</th>
                  <th className="px-4 py-2 font-medium">Baseline FPL form</th>
                  <th className="px-4 py-2 font-medium">Baseline bookmaker</th>
                </tr>
              </thead>
              <tbody>
                {(data.player_forecasting?.rows ?? []).map((row: any) => (
                  <tr key={row.metric} className="border-b align-top last:border-0">
                    <td className="px-4 py-3">
                      <div className="font-medium text-foreground">{row.metric}</div>
                      <div className="mt-0.5 max-w-md text-xs text-muted-foreground">
                        {row.explain}
                      </div>
                    </td>
                    <Cell c={row.model} />
                    <Cell c={row.baseline_form} />
                    <Cell c={row.baseline_market} />
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data.player_forecasting?.sample?.note && (
            <p className="px-4 pt-3 text-xs text-muted-foreground">
              {data.player_forecasting.sample.note}
            </p>
          )}
        </CardContent>
      </Card>

      {(data.player_forecasting?.calibration_bins ?? []).length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Calibration P(10+) theo khoảng</CardTitle>
          </CardHeader>
          <CardContent className="px-0">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-xs text-muted-foreground">
                    <th className="px-4 py-2 font-medium">Khoảng xác suất</th>
                    <th className="px-4 py-2 font-medium">Số quan sát</th>
                    <th className="px-4 py-2 font-medium">Mô hình nói</th>
                    <th className="px-4 py-2 font-medium">Thực tế xảy ra</th>
                  </tr>
                </thead>
                <tbody>
                  {data.player_forecasting.calibration_bins.map((b: any) => (
                    <tr key={b.bin} className="border-b last:border-0">
                      <td className="px-4 py-2">{b.bin}</td>
                      <td className="px-4 py-2 tabular-nums">{b.n}</td>
                      <td className="px-4 py-2 tabular-nums">{pct(b.predicted)}</td>
                      <td className="px-4 py-2 tabular-nums">{pct(b.observed)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="px-4 pt-3 text-xs text-muted-foreground">
              Một ECE gộp có thể che mất việc mô hình quá tự tin ở đầu trên và quá dè
              dặt ở đầu dưới, nên bảng này tách từng khoảng.
            </p>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Quyết định FPL</CardTitle>
        </CardHeader>
        <CardContent className="px-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-muted-foreground">
                  <th className="px-4 py-2 font-medium">Chỉ số</th>
                  <th className="px-4 py-2 font-medium">Kết quả</th>
                </tr>
              </thead>
              <tbody>
                {(data.decisions?.rows ?? []).map((row: any) => (
                  <tr key={row.metric} className="border-b align-top last:border-0">
                    <td className="px-4 py-3">
                      <div className="font-medium text-foreground">{row.metric}</div>
                      <div className="mt-0.5 max-w-lg text-xs text-muted-foreground">
                        {row.explain}
                      </div>
                      {row.note && (
                        <div className="mt-1 max-w-lg text-xs text-muted-foreground">
                          {row.note}
                        </div>
                      )}
                      {/* Hit-rate của cả bốn bảng: câu đáng hỏi là chiến lược nào
                          thắng, không phải một con số gộp. */}
                      {row.by_list && Object.keys(row.by_list).length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
                          {Object.entries(row.by_list).map(([kind, v]: any) => (
                            <span key={kind} className="text-muted-foreground">
                              {LIST_LABEL[kind] ?? kind}:{" "}
                              <b className="tabular-nums text-foreground">
                                {v.hit_rate === null ? "—" : pct(v.hit_rate)}
                              </b>
                              {v.top_n_hit_rate !== null && (
                                <> · nhóm đầu {pct(v.top_n_hit_rate)}</>
                              )}
                            </span>
                          ))}
                        </div>
                      )}
                    </td>
                    <Cell c={row.result} />
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Trang này hoạt động thế nào</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm text-muted-foreground">
          {(data.how_it_works ?? []).map((s: string, i: number) => (
            <p key={i}>• {s}</p>
          ))}
          {data.player_forecasting?.baseline_market && (
            <>
              <p>
                • <b>Baseline bookmaker</b> —{" "}
                {data.player_forecasting.baseline_market.wired
                  ? data.player_forecasting.baseline_market.definition
                  : `chưa nối. ${data.player_forecasting.baseline_market.why_not_yet}`}
              </p>
              {data.player_forecasting.baseline_market.caveat && (
                <p className="rounded-md bg-caution/10 px-2.5 py-2 text-caution">
                  ⚠ {data.player_forecasting.baseline_market.caveat}
                </p>
              )}
            </>
          )}
          {data.decisions?.captain_pool && (
            <p>
              • <b>Mẫu so sánh đội trưởng</b> — {data.decisions.captain_pool}
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

/** Tên bốn bảng xếp hạng đội trưởng, khớp `list_kind` ở backend. */
const LIST_LABEL: Record<string, string> = {
  ev: "EV",
  safe: "An toàn",
  ceiling: "Ceiling",
  chase: "Đuổi hạng",
};

function pct(x: number | null) {
  return x === null || x === undefined ? "—" : `${(x * 100).toFixed(1)}%`;
}

/** Một ô: số + cỡ mẫu, hoặc lý do trống. Hai loại trống được phân biệt bằng màu. */
function Cell({ c }: { c: any }) {
  if (!c) return <td className="px-4 py-3 text-muted-foreground">—</td>;

  if (c.status === "ok") {
    return (
      <td className="px-4 py-3">
        <div className="font-semibold tabular-nums text-foreground">{c.value}</div>
        <div className="text-xs text-muted-foreground">
          n = {c.n} · {c.better === "low" ? "thấp hơn là tốt" : "cao hơn là tốt"}
        </div>
      </td>
    );
  }

  const na = c.status === "not_applicable";
  return (
    <td className="px-4 py-3">
      <div
        className={`text-xs font-medium ${na ? "text-muted-foreground" : "text-caution"}`}
      >
        {na ? "không áp dụng" : "chưa có dữ liệu"}
      </div>
      <div className="mt-0.5 max-w-xs text-xs text-muted-foreground">{c.unlock}</div>
    </td>
  );
}
