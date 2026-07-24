"use client";
import { useEffect, useState } from "react";
import { countdown } from "@/lib/format";
import { useT } from "@/lib/i18n";

export function DeadlineCountdown({ iso }: { iso?: string | null }) {
  const { t } = useT();
  const [, tick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);
  const c = countdown(iso);
  if (!c) return null;
  const parts = c.past
    ? [t("deadline") + " đã qua"]
    : [
        `${c.d}d`,
        `${String(c.h).padStart(2, "0")}h`,
        `${String(c.m).padStart(2, "0")}m`,
        `${String(c.s).padStart(2, "0")}s`,
      ];
  return (
    <div className="flex items-center gap-1 tabular-nums">
      {parts.map((p, i) => (
        <span
          key={i}
          className="rounded bg-primary/10 px-1.5 py-0.5 text-xs font-bold text-primary"
        >
          {p}
        </span>
      ))}
    </div>
  );
}
