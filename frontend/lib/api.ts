"use client";
import { useCallback, useEffect, useRef, useState } from "react";

// Same-origin: next.config rewrites /api -> backend. Override with NEXT_PUBLIC_API_URL.
const BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

/** Máy chủ free tier ngủ sau ~15 phút; lần gọi đầu đánh thức nó mất ~50 giây. */
const WAKE_MESSAGE =
  "Máy chủ đang khởi động lại sau thời gian nghỉ. Việc này mất khoảng một phút — trang sẽ tự hiện dữ liệu, bạn không cần làm gì.";
const DOWN_MESSAGE =
  "Không kết nối được tới máy chủ dữ liệu. Có thể máy chủ đang bảo trì — thử lại sau ít phút.";

export class ApiError extends Error {
  /** "network" = chưa chạm được tới máy chủ (ngủ đông, mất mạng, CORS).
   *  "http" = máy chủ có trả lời, nhưng trả lời bằng một mã lỗi. */
  kind: "network" | "http";
  status?: number;

  constructor(message: string, kind: "network" | "http", status?: number) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
  }
}

/** Phân biệt "máy chủ đang ngủ" với "máy chủ trả lỗi".
 *
 * fetch chỉ ném TypeError khi request còn chưa rời được trình duyệt hoặc không
 * có phản hồi nào — tức là lỗi mạng/CORS/máy chủ chưa dậy. Mọi mã lỗi HTTP đều
 * là một phản hồi hợp lệ, nên không thuộc nhóm này và KHÔNG được thử lại mù.
 */
function asApiError(e: unknown): ApiError {
  if (e instanceof ApiError) return e;
  return new ApiError(String((e as Error)?.message ?? e), "network");
}

export async function getJSON<T = any>(path: string): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, { headers: { Accept: "application/json" } });
  } catch (e) {
    throw asApiError(e);
  }
  if (!res.ok) throw new ApiError(`${res.status} ${res.statusText}`, "http", res.status);
  return res.json();
}

export async function postJSON<T = any>(path: string, body: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (e) {
    throw asApiError(e);
  }
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new ApiError(`${res.status}: ${detail || res.statusText}`, "http", res.status);
  }
  return res.json();
}

// --- trạng thái "đang đánh thức máy chủ", dùng chung cho cả app ---
// Đếm số request đang trong chuỗi thử lại. Gom về một chỗ để banner chỉ cần
// gắn một lần ở layout, thay vì mỗi trang tự lo phần thông báo của mình.
let wakingCount = 0;
const wakeListeners = new Set<(waking: boolean) => void>();

function notifyWake() {
  const on = wakingCount > 0;
  wakeListeners.forEach((fn) => fn(on));
}

function markWaking(on: boolean) {
  wakingCount = Math.max(0, wakingCount + (on ? 1 : -1));
  notifyWake();
}

export function subscribeWaking(fn: (waking: boolean) => void): () => void {
  wakeListeners.add(fn);
  fn(wakingCount > 0);
  return () => {
    wakeListeners.delete(fn);
  };
}

export const WAKE_NOTICE = WAKE_MESSAGE;

// Đợi giữa các lần thử: 2s, 4s, 8s, 15s, 15s, 15s → bao trọn ~60 giây cold start
// của gói free mà không nện liên tục vào một máy chủ đang bận khởi động.
const RETRY_DELAYS_MS = [2000, 4000, 8000, 15000, 15000, 15000];

export function useApi<T = any>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState<boolean>(!!path);
  const [error, setError] = useState<string | null>(null);
  /** Đang trong chuỗi thử lại vì máy chủ chưa trả lời — khác hẳn "đang tải". */
  const [waking, setWaking] = useState(false);
  const runId = useRef(0);
  // Bộ đếm toàn cục phải khớp tuyệt đối số lần tăng/giảm, nên trạng thái thật
  // nằm ở ref chứ không đọc từ state React (state có thể chưa kịp cập nhật).
  const wakingRef = useRef(false);

  const setWake = useCallback((on: boolean) => {
    if (on === wakingRef.current) return;
    wakingRef.current = on;
    markWaking(on);
    setWaking(on);
  }, []);

  const reload = useCallback(() => {
    if (!path) return;
    // Mỗi lần gọi mang một id riêng: một chuỗi thử lại cũ đang chạy dở sẽ tự
    // bỏ kết quả của mình thay vì ghi đè lên lần tải mới hơn.
    const id = ++runId.current;
    setLoading(true);
    setError(null);

    const attempt = async (n: number): Promise<void> => {
      let json: T;
      try {
        json = await getJSON<T>(path);
      } catch (e) {
        if (id !== runId.current) return;
        const err = asApiError(e);

        // Lỗi HTTP là câu trả lời dứt khoát của máy chủ — thử lại cũng vậy thôi.
        if (err.kind === "http" || n >= RETRY_DELAYS_MS.length) {
          setWake(false);
          setError(err.kind === "network" ? DOWN_MESSAGE : err.message);
          setLoading(false);
          return;
        }

        setWake(true);
        setTimeout(() => {
          if (id === runId.current) void attempt(n + 1);
        }, RETRY_DELAYS_MS[n]);
        return;
      }

      if (id !== runId.current) return;
      setWake(false);
      setData(json);
      setLoading(false);
    };

    void attempt(0);
  }, [path, setWake]);

  useEffect(() => {
    reload();
    // Rời trang giữa chuỗi thử lại thì phải trả lại bộ đếm, không thì banner
    // "đang khởi động" kẹt lại vĩnh viễn trên những trang chẳng liên quan.
    return () => {
      runId.current++;
      setWake(false);
    };
  }, [reload, setWake]);

  return { data, loading, error, reload, waking, wakeMessage: WAKE_MESSAGE };
}

// ---- shared types (loose) ----
export type Player = {
  id: number;
  name: string;
  team: string;
  team_id: number;
  position: string;
  element_type: number;
  price: number;
  selected_by_percent: number;
  status: string;
  photo_code?: string | null;
  xp?: number;
  xp_next?: number;
  xp_next3?: number;
  xp_next5?: number;
  xmins?: number;
  ceiling?: number;
  overall_risk?: string;
  confidence?: number;
  clean_sheet_prob?: number;
  goal_prob?: number;
  value_next5?: number;
  penalties_order?: number | null;
};
