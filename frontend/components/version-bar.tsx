"use client";
import { useApi } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { timeAgo } from "@/lib/format";

/** Nguồn gốc & phiên bản của dữ liệu đang hiển thị (spec §5: luôn nói rõ độ mới). */
export function VersionBar({ compact = false }: { compact?: boolean }) {
  const { lang } = useT();
  const { data } = useApi<any>("/api/meta/version");
  if (!data) return null;

  const vnTime = (iso?: string | null) =>
    iso
      ? new Date(iso).toLocaleString("vi-VN", {
          timeZone: "Asia/Ho_Chi_Minh",
          dateStyle: "short",
          timeStyle: "short",
        })
      : "—";

  const vnDate = (iso?: string | null) =>
    iso
      ? new Date(iso).toLocaleDateString("vi-VN", {
          timeZone: "Asia/Ho_Chi_Minh",
          day: "2-digit",
          month: "2-digit",
          year: "numeric",
        })
      : null;

  const bpsDate = vnDate(data.bps_rules_effective_from);

  const items: { label: string; value: string; title?: string }[] = [
    { label: "Mùa giải", value: data.season ?? "—", title: `Nguồn: ${data.season_source}` },
    {
      label: "Luật tính điểm",
      // nhãn dễ đọc; băm chính xác của game_config nằm trong tooltip
      value: data.rules_label ?? data.rules_version ?? "—",
      title:
        `Vân tay game_config: ${data.rules_version ?? "—"} · ` +
        (data.rules_updated_at ? `đồng bộ ${vnTime(data.rules_updated_at)} · ` : "") +
        `Nguồn: ${data.rules_source}`,
    },
    {
      // BPS tách riêng vì FPL không phát trọng số BPS qua API — phiên bản do
      // chúng ta khai, nên phải nói rõ nó có từ bao giờ.
      label: "BPS",
      value: bpsDate
        ? `cập nhật ${bpsDate}`
        : `${data.bps_rules_version ?? "—"} (chưa rõ ngày công bố)`,
      title:
        `Phiên bản ${data.bps_rules_version ?? "—"} · ` +
        `Trọng số BPS không có trong FPL API nên được khai trong app/bps_rules.py · ` +
        `Nguồn: ${data.bps_rules_source_url ?? "—"}`,
    },
    { label: "Model", value: data.projection_version ?? "—" },
    {
      label: "Dữ liệu chạy lần cuối",
      value: timeAgo(data.last_data_update, lang),
      title: vnTime(data.last_data_update),
    },
    {
      label: "Chạy mô hình",
      value: timeAgo(data.last_model_run, lang),
      title: vnTime(data.last_model_run),
    },
  ];

  const stale =
    data.last_data_update &&
    Date.now() - new Date(data.last_data_update).getTime() > 12 * 3600 * 1000;

  return (
    <div
      className={`flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground ${
        compact ? "" : "rounded-md border bg-muted/30 px-3 py-2"
      }`}
    >
      {items.map((it) => (
        <span key={it.label} title={it.title} className="whitespace-nowrap">
          {it.label}:{" "}
          <b className="font-semibold text-foreground/80 tabular-nums">{it.value}</b>
        </span>
      ))}
      {data.rules_source?.includes("fallback") && (
        <span className="rounded bg-caution/15 px-1.5 py-0.5 font-medium text-caution">
          luật dự phòng — chưa đồng bộ được từ FPL
        </span>
      )}
      {data.bps_rules_known === false && (
        <span className="rounded bg-caution/15 px-1.5 py-0.5 font-medium text-caution">
          chưa khai luật BPS cho mùa này — đang dùng bộ của mùa gần nhất
        </span>
      )}
      {stale && (
        <span className="rounded bg-caution/15 px-1.5 py-0.5 font-medium text-caution">
          dữ liệu đã cũ hơn 12 giờ
        </span>
      )}
    </div>
  );
}
