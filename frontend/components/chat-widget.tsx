"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Send, X } from "lucide-react";
import { postJSON } from "@/lib/api";
import { cn } from "@/lib/utils";

type Msg = {
  role: "user" | "bot";
  text: string;
  players?: any[];
  suggestions?: string[];
};

/** Gợi ý xoay vòng để người dùng thấy được nhiều dạng câu hỏi khác nhau. */
const SUGGESTION_POOL = [
  "Ai nên làm đội trưởng?",
  "Đội hình tối ưu là gì?",
  "Cầu thủ nào đáng tiền nhất?",
  "Ai có ceiling cao nhất?",
  "Lựa chọn an toàn là ai?",
  "Ai là differential tốt?",
  "Ai đá penalty?",
  "Hậu vệ nào giữ sạch lưới tốt?",
  "Đội nào có lịch dễ?",
  "Cầu thủ Arsenal nào tốt nhất?",
  "Lịch Man City thế nào?",
  "Ai có nguy cơ bị xoay tua?",
  "Ai đang được mua nhiều nhất?",
  "Cầu thủ nào đang chấn thương?",
  "Có Double Gameweek nào không?",
  "Defensive Contribution là gì?",
  "Tiền đạo nào tốt nhất dưới 7 triệu?",
  "Cách dùng web này?",
];

function pickSuggestions(n = 3): string[] {
  const pool = [...SUGGESTION_POOL];
  const out: string[] = [];
  while (out.length < n && pool.length) {
    out.push(pool.splice(Math.floor(Math.random() * pool.length), 1)[0]);
  }
  return out;
}

const GREETING: Msg = {
  role: "bot",
  text:
    "Chào bạn 👋 Mình trả lời dựa trên **đúng số liệu** của web (xP, xMins, lịch thi đấu, kèo nhà cái) — không phán bừa.\n\n" +
    "Hỏi được về: đội trưởng · so sánh cầu thủ · đội hình tối ưu · giá trị · ceiling · differential · " +
    "penalty · sạch lưới · lịch từng đội · chấn thương · luật tính điểm.\n\nThử vài câu bên dưới nhé:",
  suggestions: [],
};

/** Quả bóng đá vẽ bằng SVG (nét sắc ở mọi kích thước, không phụ thuộc emoji hệ điều hành). */
function Football({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 64" className={className} role="presentation" aria-hidden>
      <defs>
        <radialGradient id="ballShade" cx="35%" cy="30%" r="75%">
          <stop offset="0%" stopColor="#ffffff" />
          <stop offset="70%" stopColor="#f1f5f9" />
          <stop offset="100%" stopColor="#cbd5e1" />
        </radialGradient>
      </defs>
      <circle cx="32" cy="32" r="30" fill="url(#ballShade)" stroke="#0f172a" strokeWidth="2" />
      {/* ngũ giác trung tâm */}
      <polygon points="32,18 42,25 38,37 26,37 22,25" fill="#0f172a" />
      {/* các mảng quanh rìa */}
      <polygon points="32,4 40,10 32,15 24,10" fill="#0f172a" opacity="0.92" />
      <polygon points="60,26 56,37 47,32 50,21" fill="#0f172a" opacity="0.92" />
      <polygon points="4,26 14,21 17,32 8,37" fill="#0f172a" opacity="0.92" />
      <polygon points="20,58 24,47 34,47 38,58" fill="#0f172a" opacity="0.92" />
      {/* đường nối */}
      <g stroke="#0f172a" strokeWidth="1.6" fill="none" opacity="0.75">
        <path d="M32 15 L32 18" />
        <path d="M47 32 L42 25" />
        <path d="M17 32 L22 25" />
        <path d="M26 37 L24 47" />
        <path d="M38 37 L34 47" />
      </g>
    </svg>
  );
}

/** Markdown tối giản: **đậm**, *nghiêng*, `code` — đủ cho câu trả lời của bot. */
function renderText(text: string) {
  return text.split("\n").map((line, i) => {
    if (!line.trim()) return <div key={i} className="h-2" />;
    const parts: React.ReactNode[] = [];
    const re = /(\*\*[^*]+\*\*|_[^_]+_|`[^`]+`)/g;
    let last = 0;
    let m: RegExpExecArray | null;
    while ((m = re.exec(line)) !== null) {
      if (m.index > last) parts.push(line.slice(last, m.index));
      const tok = m[0];
      if (tok.startsWith("**")) parts.push(<b key={`${i}-${m.index}`}>{tok.slice(2, -2)}</b>);
      else if (tok.startsWith("`"))
        parts.push(
          <code key={`${i}-${m.index}`} className="rounded bg-muted px-1 text-[11px]">
            {tok.slice(1, -1)}
          </code>,
        );
      else
        parts.push(
          <i key={`${i}-${m.index}`} className="text-muted-foreground">
            {tok.slice(1, -1)}
          </i>,
        );
      last = m.index + tok.length;
    }
    if (last < line.length) parts.push(line.slice(last));
    return (
      <p key={i} className="leading-relaxed">
        {parts}
      </p>
    );
  });
}

export function ChatWidget() {
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([GREETING]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [msgs, open]);

  // chọn gợi ý sau khi mount (tránh lệch nội dung giữa máy chủ và trình duyệt)
  useEffect(() => {
    setMsgs((m) =>
      m.length === 1 && m[0].role === "bot" && !m[0].suggestions?.length
        ? [{ ...m[0], suggestions: pickSuggestions(4) }]
        : m,
    );
  }, []);

  async function ask(question: string) {
    const q = question.trim();
    if (!q || busy) return;
    setMsgs((m) => [...m, { role: "user", text: q }]);
    setInput("");
    setBusy(true);
    try {
      const res = await postJSON<any>("/api/chat", { question: q });
      setMsgs((m) => [
        ...m,
        {
          role: "bot",
          text: res.answer,
          players: res.player_cards ?? [],
          suggestions: res.suggestions ?? [],
        },
      ]);
    } catch (e: any) {
      setMsgs((m) => [
        ...m,
        {
          role: "bot",
          text:
            "Xin lỗi, mình không lấy được dữ liệu lúc này. Máy chủ có thể đang khởi động lại — bạn thử lại sau ít giây nhé.",
        },
      ]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {!open && (
        <button
          onClick={() => setOpen(true)}
          aria-label="Mở trợ lý hỏi đáp AI"
          className="group fixed bottom-5 right-5 z-50 flex flex-col items-center gap-1.5 focus:outline-none"
        >
          {/* nhãn phía trên quả bóng */}
          <span className="chat-label relative rounded-full bg-primary px-3 py-1 text-[11px] font-bold uppercase tracking-wide text-primary-foreground shadow-lg">
            AI Chatbot
            <span className="absolute -bottom-1 left-1/2 h-2 w-2 -translate-x-1/2 rotate-45 bg-primary" />
          </span>

          {/* quả bóng nảy + xoay, có vòng sáng lan toả */}
          <span className="relative flex h-14 w-14 items-center justify-center">
            <span className="chat-ring absolute inset-0 rounded-full bg-primary/40" />
            <span className="absolute inset-0 rounded-full bg-primary/15 blur-md" />
            <span className="ball-bounce relative block h-14 w-14 drop-shadow-lg transition-transform group-hover:scale-110 group-focus-visible:scale-110">
              <Football className="ball-spin h-14 w-14" />
            </span>
          </span>
        </button>
      )}

      {open && (
        <div className="fixed inset-x-0 bottom-0 z-50 flex h-[85vh] flex-col border-t bg-card shadow-2xl sm:inset-x-auto sm:bottom-5 sm:right-5 sm:h-[600px] sm:w-[420px] sm:rounded-xl sm:border">
          {/* header */}
          <div className="flex items-center justify-between border-b px-4 py-3">
            <div className="flex items-center gap-2">
              <Football className="h-8 w-8" />
              <div>
                <div className="text-sm font-semibold">AI Chatbot · FPL Edge</div>
                <div className="text-[11px] text-muted-foreground">
                  Trả lời từ dữ liệu thật của web
                </div>
              </div>
            </div>
            <button
              onClick={() => setOpen(false)}
              aria-label="Đóng"
              className="flex h-8 w-8 items-center justify-center rounded-md hover:bg-muted"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          {/* messages */}
          <div className="scroll-thin flex-1 space-y-3 overflow-y-auto px-4 py-3">
            {msgs.map((m, i) => (
              <div key={i} className={cn("flex", m.role === "user" ? "justify-end" : "justify-start")}>
                <div
                  className={cn(
                    "max-w-[92%] rounded-lg px-3 py-2 text-sm",
                    m.role === "user"
                      ? "bg-primary text-primary-foreground"
                      : "bg-muted/60",
                  )}
                >
                  <div className="space-y-0.5">{renderText(m.text)}</div>

                  {m.players && m.players.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {m.players.slice(0, 5).map((p: any) => (
                        <Link
                          key={p.id}
                          href={`/players/${p.id}`}
                          onClick={() => setOpen(false)}
                          className="rounded-md border bg-background px-2 py-1 text-xs hover:border-primary"
                        >
                          {p.name} <span className="text-muted-foreground">· {p.team}</span>
                        </Link>
                      ))}
                    </div>
                  )}

                  {m.suggestions && m.suggestions.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {m.suggestions.map((s) => (
                        <button
                          key={s}
                          onClick={() => ask(s)}
                          className="rounded-full border border-primary/40 px-2.5 py-1 text-xs text-primary transition hover:bg-primary/10"
                        >
                          {s}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ))}
            {busy && (
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <span className="h-3 w-3 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                Đang tra dữ liệu…
              </div>
            )}
            <div ref={endRef} />
          </div>

          {/* input */}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              ask(input);
            }}
            className="flex items-center gap-2 border-t p-3"
          >
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Hỏi về cầu thủ, đội trưởng, lịch đấu…"
              className="h-10 flex-1 rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring/40"
            />
            <button
              type="submit"
              disabled={busy || !input.trim()}
              aria-label="Gửi"
              className="flex h-10 w-10 items-center justify-center rounded-md bg-primary text-primary-foreground disabled:opacity-40"
            >
              <Send className="h-4 w-4" />
            </button>
          </form>
        </div>
      )}
    </>
  );
}
