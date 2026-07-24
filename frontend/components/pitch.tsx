"use client";
import { PlayerAvatar, StatusDot } from "@/components/fpl";
import { fmt } from "@/lib/format";
import { cn } from "@/lib/utils";

type PitchPlayer = {
  id: number;
  name: string;
  team: string;
  position: string;
  element_type: number;
  price: number;
  xp?: number;
  xmins?: number;
  photo_code?: string | null;
  status?: string;
  is_captain?: boolean;
  is_vice?: boolean;
  overall_risk?: string | null;
};

function Chip({ p }: { p: PitchPlayer }) {
  return (
    <div className="flex w-[76px] flex-col items-center gap-0.5">
      <div className="relative">
        <PlayerAvatar player={p as any} size={38} />
        {p.is_captain && (
          <span className="absolute -right-1 -top-1 flex h-4 w-4 items-center justify-center rounded-full bg-primary text-[9px] font-bold text-primary-foreground">
            C
          </span>
        )}
        {p.is_vice && (
          <span className="absolute -right-1 -top-1 flex h-4 w-4 items-center justify-center rounded-full bg-muted text-[9px] font-bold">
            V
          </span>
        )}
        <span className="absolute -bottom-1 left-1/2 -translate-x-1/2">
          <StatusDot status={p.status} />
        </span>
      </div>
      <div className="w-full truncate text-center text-[11px] font-medium leading-tight">
        {p.name}
      </div>
      <div className="rounded bg-background/80 px-1 text-[10px] font-bold tabular-nums text-primary">
        {fmt(p.xp)}
      </div>
    </div>
  );
}

export function Pitch({
  starting,
  bench,
}: {
  starting: PitchPlayer[];
  bench?: PitchPlayer[];
}) {
  const rows = [1, 2, 3, 4].map((t) => starting.filter((p) => p.element_type === t));
  return (
    <div className="space-y-3">
      <div
        className="rounded-xl border p-4"
        style={{
          background:
            "linear-gradient(180deg, hsl(152 45% 30%) 0%, hsl(152 45% 26%) 100%)",
        }}
      >
        <div className="space-y-4">
          {rows.map((row, i) => (
            <div key={i} className="flex justify-center gap-3">
              {row.map((p) => (
                <Chip key={p.id} p={p} />
              ))}
            </div>
          ))}
        </div>
      </div>
      {bench && bench.length > 0 && (
        <div>
          <div className="mb-1 text-xs font-medium text-muted-foreground">Băng ghế dự bị</div>
          <div className="flex gap-3 rounded-lg border bg-muted/40 p-3">
            {bench.map((p, i) => (
              <div key={p.id} className="flex flex-col items-center">
                <span className="mb-0.5 text-[10px] text-muted-foreground">
                  {p.element_type === 1 ? "GK" : i + 1}
                </span>
                <Chip p={p} />
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
