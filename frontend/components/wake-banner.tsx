"use client";

import { useEffect, useState } from "react";
import { WAKE_NOTICE, subscribeWaking } from "@/lib/api";

/** Banner "máy chủ đang khởi động", gắn một lần ở layout.
 *
 * Máy chủ nằm trên gói free nên ngủ sau ~15 phút không ai dùng, và lần truy cập
 * kế tiếp phải chờ nó dậy. Không nói gì thì người dùng chỉ thấy trang trắng và
 * kết luận web hỏng, dù chỉ cần đợi thêm nửa phút.
 */
export function WakeBanner() {
  const [waking, setWaking] = useState(false);

  useEffect(() => subscribeWaking(setWaking), []);

  if (!waking) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      className="mx-auto mb-4 flex max-w-7xl items-center gap-3 rounded-md border border-caution/40 bg-caution/10 px-4 py-3 text-sm text-caution"
    >
      <span className="h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-current border-t-transparent" />
      <span>{WAKE_NOTICE}</span>
    </div>
  );
}
