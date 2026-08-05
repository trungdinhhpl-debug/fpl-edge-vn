"use client";
import { useState } from "react";
import { AlertTriangle, ChevronDown, ChevronUp } from "lucide-react";
import { useApi } from "@/lib/api";

/**
 * Site-wide phase label. A projection made before a ball is kicked and one made
 * in December are different kinds of claim, so the label travels with every page
 * rather than living in a methodology note nobody opens.
 */
export function SeasonBanner() {
  const { data } = useApi<any>("/api/meta/version");
  const [open, setOpen] = useState(false);
  const s = data?.season_state;
  if (!s?.label) return null;   // mid-season: nothing to warn about

  const dw = s.downweighting;
  return (
    <div className="mb-4 rounded-md border border-caution/40 bg-caution/10 px-3 py-2">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 text-left"
      >
        <span className="flex items-center gap-1.5 rounded bg-caution/20 px-2 py-0.5 text-xs font-bold uppercase tracking-wide text-caution">
          <AlertTriangle className="h-3.5 w-3.5" />
          {s.label}
        </span>
        <span className="text-sm">
          Dữ liệu PL mùa này: <b>{s.matches_played} trận</b>
        </span>
        <span className="text-sm">
          Phần lớn xMins dựa trên prior &amp; preseason:{" "}
          <b>{s.prior_based_share_pct}%</b>
        </span>
        <span className="text-sm">
          Confidence toàn hệ thống: <b>{s.system_confidence}</b>
        </span>
        <span className="ml-auto flex items-center gap-1 text-xs text-muted-foreground">
          {open ? "Thu gọn" : "Chi tiết"}
          {open ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
        </span>
      </button>

      {open && (
        <div className="mt-2 space-y-2 border-t border-caution/30 pt-2 text-xs text-muted-foreground">
          <p>{s.note}</p>
          <p>
            <b>Giảm trọng số dữ liệu mùa trước.</b>{" "}
            {dw.configured ? (
              <>
                Đang áp dụng cho{" "}
                {dw.new_manager_clubs.length > 0 && (
                  <>CLB đổi HLV: <b>{dw.new_manager_clubs.join(", ")}</b> (×{dw.weight_new_manager}){" "}</>
                )}
                {dw.new_signing_players.length > 0 && (
                  <><b>{dw.new_signing_players.length} tân binh</b> (×{dw.weight_new_signing})</>
                )}
                .
              </>
            ) : (
              <>
                <b className="text-caution">Chưa khai báo CLB đổi HLV hay tân binh nào</b> —
                nên hiện chưa cầu thủ nào được giảm trọng số. {dw.note}
              </>
            )}
          </p>
          {s.matches_until_established > 0 && s.phase !== "preseason" && (
            <p>
              Còn <b>{s.matches_until_established} trận</b> nữa mới đủ mẫu để dự báo
              chủ yếu dựa trên dữ liệu mùa này.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
