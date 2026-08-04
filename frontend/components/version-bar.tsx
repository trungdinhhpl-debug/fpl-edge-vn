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

  const items: { label: string; value: string; title?: string }[] = [
    { label: "Mùa giải", value: data.season ?? "—", title: `Nguồn: ${data.season_source}` },
    {
      label: "Phiên bản luật",
      value: data.rules_version ?? "—",
      title:
        (data.rules_updated_at ? `Luật cập nhật ${vnTime(data.rules_updated_at)} · ` : "") +
        `Nguồn: ${data.rules_source}`,
    },
    { label: "Phiên bản mô hình", value: data.projection_version ?? "—" },
    {
      label: "Dữ liệu cập nhật",
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
      {stale && (
        <span className="rounded bg-caution/15 px-1.5 py-0.5 font-medium text-caution">
          dữ liệu đã cũ hơn 12 giờ
        </span>
      )}
    </div>
  );
}
