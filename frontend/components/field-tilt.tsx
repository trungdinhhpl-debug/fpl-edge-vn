"use client";
import { useEffect, useState } from "react";
import { Users2 } from "lucide-react";

/** Núm chỉnh "so với đám đông" dùng chung cho mọi trang tối ưu.
 *
 * Ba nút, một nghĩa duy nhất, đặt ở một chỗ — bốn trang mà mỗi trang tự khai một
 * kiểu thì cùng chữ "đuổi hạng" sẽ ra bốn hành vi khác nhau.
 *
 * Điều quan trọng nhất trên giao diện này là câu cảnh báo bên dưới: ở kỳ vọng
 * thuần, nghiêng hay không nghiêng đều ra cùng một số điểm. Cái nó đổi là ĐỘ
 * BIẾN THIÊN của thứ hạng. Nói rõ ra, vì một núm không giải thích sẽ được đọc
 * thành "bấm vào đây để được nhiều điểm hơn".
 */
export const TILT_OPTIONS = [
  { key: "protect", weight: -0.5, label: "Giữ thứ hạng", desc: "Bám đội hình phổ biến — ít tụt, cũng ít vượt." },
  { key: "neutral", weight: 0, label: "Trung lập", desc: "Chỉ tối đa điểm, không nhìn đám đông." },
  { key: "chase", weight: 0.5, label: "Đuổi hạng", desc: "Ưu tiên người ít người có — biến động hai chiều." },
];

export function FieldTilt({
  weight,
  onChange,
  source,
}: {
  weight: number;
  onChange: (w: number, leagueId: number | null) => void;
  source?: { kind?: string; label?: string } | null;
}) {
  const [leagueId, setLeagueId] = useState<number | null>(null);

  // Mã giải do trang Mini-league lưu lại. Có thì EO là số ĐẾM ĐƯỢC từ đội hình
  // đối thủ thật, không thì rơi về EO toàn cầu — backend tự quyết và báo lại
  // trong `source`, giao diện không đoán thay.
  useEffect(() => {
    const saved = localStorage.getItem("leagueId");
    setLeagueId(saved ? Number(saved) : null);
  }, []);

  return (
    <div className="rounded-lg border p-3">
      <div className="mb-2 flex items-center gap-1.5 text-sm font-medium">
        <Users2 className="h-4 w-4 text-primary" /> So với đám đông
      </div>
      <div className="flex flex-wrap gap-1.5">
        {TILT_OPTIONS.map((o) => (
          <button
            key={o.key}
            onClick={() => onChange(o.weight, leagueId)}
            title={o.desc}
            className={`rounded-md border px-2.5 py-1.5 text-xs font-medium transition ${
              weight === o.weight ? "border-primary bg-primary/10 text-primary" : "hover:bg-muted"
            }`}
          >
            {o.label}
          </button>
        ))}
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        {TILT_OPTIONS.find((o) => o.weight === weight)?.desc}
      </p>
      {weight !== 0 && (
        <p className="mt-1 text-xs text-caution">
          Ở kỳ vọng thuần, ba lựa chọn này cho cùng một số điểm — thứ chúng đổi là độ biến thiên của
          thứ hạng, không phải điểm. Nghiêng đi luôn phải trả giá một chút xP tuyệt đối.
        </p>
      )}
      {source?.label && weight !== 0 && (
        <p className="mt-1 text-xs text-muted-foreground">
          Nguồn EO: {source.label}
          {source.kind === "modelled" && !leagueId && (
            <> Nhập mã giải ở trang Mini-league để dùng số đếm được từ chính đối thủ của bạn.</>
          )}
        </p>
      )}
    </div>
  );
}
