export function fmt(n: number | null | undefined, digits = 1): string {
  if (n == null || Number.isNaN(n)) return "–";
  return n.toFixed(digits);
}

export function pct(n: number | null | undefined, digits = 0): string {
  if (n == null) return "–";
  return `${(n * 100).toFixed(digits)}%`;
}

export function money(tenthsOrM: number, fromTenths = false): string {
  const m = fromTenths ? tenthsOrM / 10 : tenthsOrM;
  return `£${m.toFixed(1)}`;
}

export function timeAgo(iso?: string | null, lang: "vi" | "en" = "vi"): string {
  if (!iso) return "–";
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diff / 60000);
  if (mins < 1) return lang === "vi" ? "vừa xong" : "just now";
  if (mins < 60) return lang === "vi" ? `${mins} phút trước` : `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return lang === "vi" ? `${hrs} giờ trước` : `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  return lang === "vi" ? `${days} ngày trước` : `${days}d ago`;
}

/** Countdown to a deadline, in Asia/Ho_Chi_Minh-friendly parts. */
export function countdown(iso?: string | null): { d: number; h: number; m: number; s: number; past: boolean } | null {
  if (!iso) return null;
  const diff = new Date(iso).getTime() - Date.now();
  const past = diff <= 0;
  const abs = Math.abs(diff);
  return {
    d: Math.floor(abs / 86400000),
    h: Math.floor((abs % 86400000) / 3600000),
    m: Math.floor((abs % 3600000) / 60000),
    s: Math.floor((abs % 60000) / 1000),
    past,
  };
}
