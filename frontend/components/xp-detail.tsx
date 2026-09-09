"use client";
import { useState } from "react";
import { ChevronDown, ChevronUp, Info } from "lucide-react";
import { useApi } from "@/lib/api";
import { Badge, Card, CardContent, CardHeader, CardTitle, Spinner } from "@/components/ui";
import { fmt, pct, timeAgo } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Chi tiết con số xP(1): thành phần ghi bàn, từng trận một.
 *
 * Luật trình bày ở đây: **thiếu dữ liệu thì viết "Chưa đủ dữ liệu", không bao giờ
 * hiện 0%**. Một ô 0% nói rằng ta đã đo và kết quả là không; một ô "Chưa đủ dữ
 * liệu" nói rằng ta chưa đo. Hai câu đó dẫn tới hai quyết định khác nhau, nên API
 * trả `null` và mọi hàm ở dưới đọc `null` chứ không `|| 0`.
 */

const NO_DATA = "Chưa đủ dữ liệu";

function Val({
  label,
  children,
  hint,
}: {
  label: string;
  children: React.ReactNode;
  hint?: string;
}) {
  return (
    <div className="rounded-md bg-muted/40 p-2">
      <div className="flex items-center gap-1 text-[11px] uppercase tracking-wide text-muted-foreground">
        {label}
        {hint && <span title={hint}><Info className="h-3 w-3 opacity-60" /></span>}
      </div>
      <div className="mt-0.5 text-sm font-semibold tabular-nums">{children}</div>
    </div>
  );
}

function Missing({ note }: { note?: string | null }) {
  return (
    <span className="font-normal text-muted-foreground" title={note ?? undefined}>
      {NO_DATA}
    </span>
  );
}

function deltaClass(d: number | null | undefined) {
  if (d == null || Math.abs(d) < 0.005) return "text-muted-foreground";
  return d > 0 ? "text-positive" : "text-danger";
}

function signed(d: number | null | undefined, digits = 2) {
  if (d == null) return "–";
  const s = d > 0 ? "+" : d < 0 ? "−" : "";
  return `${s}${Math.abs(d).toFixed(digits)}`;
}

/** Chênh lệch tính trên hai con số ĐÃ LÀM TRÒN như đang hiện trên màn hình.
 *
 * Lấy thẳng `xp_delta` thô sẽ cho những dòng kiểu "3.48 → 3.73 (+0.26)" — người
 * đọc cộng nhẩm ra 3.74 và kết luận là trang tính sai. Phép trừ trên chính hai số
 * đang hiện thì luôn khớp. Khi phần điều chỉnh nhỏ hơn nửa xu nhưng khác 0, ta vẫn
 * nói là "< 0.01" thay vì "+0.00" — bằng 0 và gần bằng 0 không phải một chuyện.
 */
function shownDelta(before?: number | null, after?: number | null, raw?: number | null) {
  if (before == null || after == null) return signed(raw);
  const d = Number(after.toFixed(2)) - Number(before.toFixed(2));
  if (Math.abs(d) < 0.005 && raw != null && Math.abs(raw) >= 1e-6) {
    return raw > 0 ? "< +0.01" : "> −0.01";
  }
  return signed(d);
}

/** Một trận: đối thủ, phút, kèo, vai trò/lợi thế vị trí, xP trước/sau. */
function FixtureBlock({ f }: { f: any }) {
  const m = f.minutes ?? {};
  const g = f.goals ?? {};
  const pos = f.position ?? {};
  const mk = f.market ?? {};

  return (
    <div className="space-y-2 rounded-lg border p-3">
      {/* Đối thủ + sân nhà/khách */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <span>
            {f.opponent ? `vs ${f.opponent}` : "Đối thủ chưa rõ"}{" "}
            <Badge className={cn(
              "ml-1",
              f.was_home ? "bg-primary/15 text-primary" : "bg-muted text-muted-foreground",
            )}>
              {f.was_home == null ? "?" : f.was_home ? "Sân nhà" : "Sân khách"}
            </Badge>
          </span>
        </div>
        <div className={cn("text-sm font-bold tabular-nums", deltaClass(f.xp_delta))}>
          xP {fmt(f.xp_before, 2)} → {fmt(f.xp_after, 2)} (
          {shownDelta(f.xp_before, f.xp_after, f.xp_delta)})
        </div>
      </div>

      {/* Phút dự kiến theo kịch bản */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Val label="Phút dự kiến">
          {m.xmins == null ? <Missing /> : `${fmt(m.xmins, 0)}'`}
        </Val>
        <Val label="Đá chính">
          {m.p_start == null ? <Missing /> : pct(m.p_start)}
        </Val>
        <Val
          label="Vào từ ghế"
          hint="Phút dự kiến trong từng kịch bản — kèo ghi bàn được khớp qua chính hai kịch bản này."
        >
          {m.p_sub == null ? <Missing /> : pct(m.p_sub)}
        </Val>
        <Val label="Phút chính / ghế">
          {m.minutes_if_start == null ? (
            <Missing />
          ) : (
            `${fmt(m.minutes_if_start, 0)}' / ${fmt(m.minutes_if_sub, 0)}'`
          )}
        </Val>
      </div>

      {/* Số bàn kỳ vọng: bóng sống / 11m / sau điều chỉnh / cuối cùng */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        <Val label="Bàn (mô hình)">{fmt(g.model, 3)}</Val>
        <Val label="Bóng sống">{fmt(g.open_play, 3)}</Val>
        <Val
          label="Chấm 11m"
          hint="Phần 11m KHÔNG bị nhân hệ số vị trí: một quả 11m đáng 0.79 bàn bất kể hàng phòng ngự đối thủ lệch về đâu."
        >
          {fmt(g.penalty, 3)}
        </Val>
        <Val
          label="Bóng chết khác"
          hint="FPL không phát số xG bóng chết tách riêng, nên đây là chỗ chưa đo được — không phải bằng 0."
        >
          {g.set_piece == null ? <Missing /> : fmt(g.set_piece, 3)}
        </Val>
        <Val label="Bàn (cuối)">
          <span className={deltaClass((g.final ?? 0) - (g.model ?? 0))}>
            {fmt(g.final, 3)}
          </span>
        </Val>
      </div>

      {/* Vai trò chiến thuật + lợi thế vị trí */}
      <div className="space-y-1 rounded-md border border-dashed p-2">
        <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
          Vai trò chiến thuật & lợi thế vị trí
        </div>
        {pos.role ? (
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Badge className="bg-primary/15 text-primary">{pos.role}</Badge>
            <span>{pos.role_label}</span>
            {pos.axis_label && (
              <span className="text-muted-foreground">đối đầu {pos.axis_label}</span>
            )}
            {pos.has_data ? (
              <>
                <span className="tabular-nums">
                  hệ số trục {fmt(pos.axis_factor, 3)} · áp cho ghi bàn{" "}
                  <b>{fmt(pos.position_factor, 3)}</b>
                </span>
                <Badge className="bg-muted text-muted-foreground">
                  {pos.reliability} · mẫu {fmt(pos.sample_90s, 0)} lượt-90
                </Badge>
              </>
            ) : (
              <Missing note={pos.note} />
            )}
          </div>
        ) : (
          <div className="text-sm">
            <Missing note={pos.note} />{" "}
            <span className="text-xs text-muted-foreground">
              — chưa có nguồn vai trò chiến thuật cho cầu thủ này, nên hệ số đúng bằng 1.0
            </span>
          </div>
        )}
        {pos.role && pos.finishing_share != null && (
          <p className="text-xs text-muted-foreground">
            Phần dứt điểm của vai trò: {pct(pos.finishing_share)} — phần còn lại là lợi
            thế <i>tạo cơ hội</i>, thuộc về kiến tạo và không được cộng vào khả năng ghi
            bàn. (Giả định chưa kiểm chứng.)
          </p>
        )}
        {pos.note && pos.has_data && (
          <p className="text-xs text-muted-foreground">{pos.note}</p>
        )}
        {pos.role_source && (
          <p className="text-[11px] text-muted-foreground">
            Nguồn vai trò: {pos.role_source}
            {pos.role_confidence != null && ` · độ tin cậy ${pct(pos.role_confidence)}`}
          </p>
        )}
      </div>

      {/* Kèo ghi bàn */}
      <div className="space-y-1 rounded-md border border-dashed p-2">
        <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
          Xác suất ghi bàn từ thị trường
        </div>
        {mk.has_data ? (
          <>
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-semibold tabular-nums">{pct(mk.prob, 1)}</span>
              <span className="text-muted-foreground">
                ghi ít nhất 1 bàn → {fmt(mk.expected_goals, 3)} bàn kỳ vọng
              </span>
              {mk.goals_per_90_on_pitch != null && (
                <span className="text-xs text-muted-foreground">
                  ({fmt(mk.goals_per_90_on_pitch, 3)} bàn/90' trên sân)
                </span>
              )}
              <Badge
                className={cn(
                  mk.usable_for_xp
                    ? "bg-positive/15 text-positive"
                    : "bg-caution/15 text-caution",
                )}
              >
                {mk.usable_for_xp
                  ? `Đã vào xP · trọng số ${pct(mk.weight_applied)}`
                  : "Chỉ tham khảo"}
              </Badge>
            </div>
            <p className="text-xs text-muted-foreground">
              Điều kiện áp dụng: {mk.condition_label ?? NO_DATA}
            </p>
            {mk.note && <p className="text-xs text-muted-foreground">{mk.note}</p>}
            <p className="text-[11px] text-muted-foreground">
              Nguồn: {mk.source ?? NO_DATA}
              {mk.n_bookmakers != null && ` · ${mk.n_bookmakers} nhà cái`}
              {mk.n_devigged != null && ` (${mk.n_devigged} có cả Yes/No để loại biên)`}
              {mk.bookmakers && ` · ${mk.bookmakers}`}
              {mk.market_updated_at && ` · nhà cái cập nhật ${timeAgo(mk.market_updated_at)}`}
              {mk.fetched_at && ` · lấy về ${timeAgo(mk.fetched_at)}`}
            </p>
          </>
        ) : (
          <div className="text-sm">
            <Missing note={mk.note} />{" "}
            <span className="text-xs text-muted-foreground">
              — nhà cái chưa ra kèo ghi bàn cho cầu thủ này, hoặc chưa cấu hình nguồn kèo
            </span>
          </div>
        )}
      </div>

      <p className="text-[11px] text-muted-foreground">
        Điểm mỗi bàn của vị trí này: {f.points_per_goal ?? "–"} · mô hình{" "}
        {f.model_version || "–"} · tính đến {timeAgo(f.data_cutoff)}
      </p>
    </div>
  );
}

/** Trạng thái ba nguồn — để người đọc biết ô trống là vì chưa cấu hình hay vì
 *  cầu thủ này không có dữ liệu. */
function SourceStatus({ s }: { s: any }) {
  if (!s) return null;
  const rows: { label: string; ok: boolean; detail?: string; needs?: string | null }[] = [
    {
      label: "Vai trò chiến thuật",
      ok: !!s.roles?.configured,
      detail: s.roles?.detail,
      needs: s.roles?.needs,
    },
    {
      label: "Điểm yếu đối thủ theo vị trí",
      ok: !!s.opponent_weakness?.configured,
      detail: s.opponent_weakness?.detail,
      needs: s.opponent_weakness?.needs,
    },
    {
      label: "Kèo ghi bàn (player_goal_scorer_anytime)",
      ok: !!s.market?.usable_for_xp,
      detail: s.market?.enabled
        ? `${s.market?.has_rows ? "đã có dữ liệu" : "chưa có dòng nào"} · ${
            s.market?.condition_label ?? ""
          }`
        : "chưa cấu hình ODDS_API_KEY hoặc đã tắt",
      needs: s.market?.needs,
    },
  ];
  return (
    <div className="space-y-1.5 rounded-md bg-muted/40 p-2 text-xs">
      <div className="font-medium text-muted-foreground">Nguồn dữ liệu của phần này</div>
      {rows.map((r) => (
        <div key={r.label} className="flex flex-wrap items-start gap-1.5">
          <Badge
            className={cn(
              r.ok ? "bg-positive/15 text-positive" : "bg-muted text-muted-foreground",
            )}
          >
            {r.ok ? "có" : "chưa có"}
          </Badge>
          <span className="font-medium">{r.label}</span>
          {r.detail && <span className="text-muted-foreground">— {r.detail}</span>}
          {!r.ok && r.needs && (
            <span className="basis-full pl-1 text-muted-foreground">Cần: {r.needs}</span>
          )}
        </div>
      ))}
      {s.market?.weight_is_assumption && s.market?.usable_for_xp && (
        <p className="text-muted-foreground">
          Trọng số thị trường {pct(s.market.weight)} là <b>giả định chưa kiểm chứng</b> —
          chưa có đủ vòng đã đóng để so sai số giữa mô hình và kèo.
        </p>
      )}
    </div>
  );
}

/** Bảng chi tiết xP(1) đầy đủ. Dùng trên trang cầu thủ và trong popover bảng. */
export function XpGoalDetail({
  playerId,
  gameweek,
  compact = false,
}: {
  playerId: number | string;
  gameweek?: number;
  compact?: boolean;
}) {
  const q = playerId
    ? `/api/players/${playerId}/xp-detail${gameweek ? `?gameweek=${gameweek}` : ""}`
    : null;
  const { data, error } = useApi<any>(q);

  if (error)
    return (
      <p className="text-xs text-muted-foreground">Không tải được chi tiết xP.</p>
    );
  if (!data) return <Spinner label="Đang tải chi tiết xP…" />;

  const body = (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <div className="tabular-nums">
          <span className="text-muted-foreground">xP(1) trước điều chỉnh </span>
          <b>{data.xp_before == null ? "–" : fmt(data.xp_before, 2)}</b>
          <span className="text-muted-foreground"> → sau </span>
          <b>{data.xp_after == null ? "–" : fmt(data.xp_after, 2)}</b>
        </div>
        <Badge className={cn("tabular-nums", deltaClass(data.xp_delta))}>
          {shownDelta(data.xp_before, data.xp_after, data.xp_delta)} điểm từ thành phần
          ghi bàn
        </Badge>
        {data.is_double && (
          <Badge className="bg-caution/15 text-caution">
            Vòng đôi — {data.n_fixtures} trận, cộng từng trận
          </Badge>
        )}
      </div>

      {data.is_blank ? (
        <p className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">
          Vòng trắng: đội của cầu thủ này không đá vòng {data.gameweek}, nên xP bằng 0 và
          không có gì để điều chỉnh.
        </p>
      ) : data.fixtures?.length ? (
        <div className="space-y-2">
          {data.fixtures.map((f: any) => (
            <FixtureBlock key={f.fixture_id} f={f} />
          ))}
        </div>
      ) : (
        <p className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">
          Chưa có bản ghi theo từng trận cho vòng {data.gameweek}. xP(1) vẫn là con số
          đầy đủ của mô hình — chỉ chưa có nguồn nào để thay thành phần ghi bàn.
        </p>
      )}

      <SourceStatus s={data.status} />

      <p className="text-[11px] text-muted-foreground">
        Cách tính: xP_mới = xP_cũ + (bàn kỳ vọng mới − bàn kỳ vọng cũ) × điểm mỗi bàn.
        Chỉ thành phần ghi bàn bị thay; kiến tạo, sạch lưới, bonus và defcon giữ nguyên.
      </p>
    </div>
  );

  if (compact) return body;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">
          Chi tiết xP(1) · thành phần ghi bàn (GW{data.gameweek})
        </CardTitle>
      </CardHeader>
      <CardContent>{body}</CardContent>
    </Card>
  );
}

/** Ô xP(1) trong bảng Cầu thủ: bấm để mở chi tiết ngay dưới dòng. */
export function XpOneCell({
  value,
  delta,
  open,
  onToggle,
}: {
  value: number | null | undefined;
  delta?: number | null;
  open: boolean;
  onToggle: () => void;
}) {
  const moved = delta != null && Math.abs(delta) >= 0.005;
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onToggle();
      }}
      title="Bấm để xem đối thủ, phút dự kiến, xác suất ghi bàn, vai trò và nguồn dữ liệu"
      className="inline-flex items-center gap-1 rounded px-1 font-semibold tabular-nums text-primary transition hover:bg-primary/10 focus:outline-none focus:ring-2 focus:ring-ring/40"
    >
      {fmt(value)}
      {moved && (
        <span className={cn("text-[10px] font-medium", deltaClass(delta))}>
          {signed(delta, 1)}
        </span>
      )}
      {open ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3 opacity-50" />}
    </button>
  );
}
