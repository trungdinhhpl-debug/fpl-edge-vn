"use client";
import { Badge } from "@/components/ui";
import { cn, playerPhoto, riskBg } from "@/lib/utils";
import { fmt } from "@/lib/format";
import type { Player } from "@/lib/api";

export function RiskBadge({ level }: { level?: string | null }) {
  if (!level) return <Badge className="bg-muted text-muted-foreground">–</Badge>;
  return <Badge className={riskBg(level)}>{level}</Badge>;
}

export function ConfidenceBar({ value }: { value?: number | null }) {
  const v = value ?? 0;
  const color = v >= 0.7 ? "bg-positive" : v >= 0.45 ? "bg-caution" : "bg-danger";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-muted">
        <div className={cn("h-full", color)} style={{ width: `${Math.round(v * 100)}%` }} />
      </div>
      <span className="text-xs tabular-nums text-muted-foreground">{Math.round(v * 100)}%</span>
    </div>
  );
}

const POS_COLOR: Record<string, string> = {
  GK: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  DEF: "bg-sky-500/15 text-sky-600 dark:text-sky-400",
  MID: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  FWD: "bg-rose-500/15 text-rose-600 dark:text-rose-400",
};

export function PosTag({ pos }: { pos: string }) {
  return <Badge className={POS_COLOR[pos] ?? "bg-muted"}>{pos}</Badge>;
}

export function PlayerAvatar({ player, size = 40 }: { player: Player; size?: number }) {
  const src = playerPhoto(player.photo_code);
  const initials = player.name?.slice(0, 2).toUpperCase() ?? "?";
  return (
    <div
      className="relative flex shrink-0 items-center justify-center overflow-hidden rounded-full bg-muted text-xs font-semibold text-muted-foreground"
      style={{ width: size, height: size }}
    >
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={src}
          alt={player.name}
          className="h-full w-full object-cover"
          onError={(e) => ((e.target as HTMLImageElement).style.display = "none")}
        />
      ) : (
        initials
      )}
    </div>
  );
}

export function StatusDot({ status }: { status?: string }) {
  if (!status || status === "a") return null;
  const color =
    status === "i" || status === "s" || status === "u"
      ? "bg-danger"
      : status === "d"
        ? "bg-caution"
        : "bg-neutralq";
  return <span className={cn("inline-block h-2 w-2 rounded-full", color)} title={status} />;
}

export function PlayerCard({ p, onClick }: { p: Player; onClick?: () => void }) {
  return (
    <button
      onClick={onClick}
      className="flex w-full items-center gap-3 rounded-lg border bg-card p-2.5 text-left transition hover:border-primary/50 hover:shadow-sm"
    >
      <PlayerAvatar player={p} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="truncate font-medium">{p.name}</span>
          <StatusDot status={p.status} />
          {p.penalties_order === 1 && (
            <Badge className="bg-primary/15 text-primary">PEN</Badge>
          )}
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <PosTag pos={p.position} />
          <span>{p.team}</span>
          <span>£{fmt(p.price)}</span>
        </div>
      </div>
      <div className="text-right">
        <div className="text-sm font-bold tabular-nums">{fmt(p.xp_next ?? p.xp)}</div>
        <div className="text-[10px] uppercase text-muted-foreground">xP</div>
      </div>
      <div className="hidden text-right sm:block">
        <div className="text-sm font-semibold tabular-nums text-muted-foreground">
          {fmt(p.xmins, 0)}'
        </div>
        <div className="text-[10px] uppercase text-muted-foreground">xMins</div>
      </div>
    </button>
  );
}

export function MockTag() {
  return (
    <Badge className="bg-caution/15 text-caution" title="Dữ liệu mẫu, không dùng cho khuyến nghị thật">
      mock
    </Badge>
  );
}
