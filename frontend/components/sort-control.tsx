"use client";
import { ArrowDownWideNarrow, ArrowUpNarrowWide } from "lucide-react";
import { Select } from "@/components/ui";

export type SortDir = "desc" | "asc";

/** Dropdown chọn tiêu chí + nút đảo chiều cao↔thấp. Dùng chung cho các bảng dữ liệu. */
export function SortControl<T extends string>({
  value,
  options,
  dir,
  onValue,
  onDir,
  label = "Sắp xếp",
}: {
  value: T;
  options: { key: T; label: string }[];
  dir: SortDir;
  onValue: (v: T) => void;
  onDir: (d: SortDir) => void;
  label?: string;
}) {
  const desc = dir === "desc";
  return (
    <div className="flex items-center gap-1">
      <Select
        aria-label={label}
        value={value}
        onChange={(e) => onValue(e.target.value as T)}
      >
        {options.map((o) => (
          <option key={o.key} value={o.key}>
            {label}: {o.label}
          </option>
        ))}
      </Select>
      <button
        type="button"
        onClick={() => onDir(desc ? "asc" : "desc")}
        aria-label={desc ? "Đang xếp cao đến thấp, bấm để đảo" : "Đang xếp thấp đến cao, bấm để đảo"}
        title={desc ? "Cao → thấp" : "Thấp → cao"}
        className="flex h-10 items-center gap-1.5 whitespace-nowrap rounded-md border border-input px-3 text-sm font-medium transition hover:bg-muted focus:outline-none focus:ring-2 focus:ring-ring/40"
      >
        {desc ? (
          <ArrowDownWideNarrow className="h-4 w-4 text-primary" />
        ) : (
          <ArrowUpNarrowWide className="h-4 w-4 text-primary" />
        )}
        <span className="hidden sm:inline">{desc ? "Cao → thấp" : "Thấp → cao"}</span>
      </button>
    </div>
  );
}
