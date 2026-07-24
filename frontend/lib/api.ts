"use client";
import { useCallback, useEffect, useState } from "react";

// Same-origin: next.config rewrites /api -> backend. Override with NEXT_PUBLIC_API_URL.
const BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

export async function getJSON<T = any>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export async function postJSON<T = any>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`${res.status}: ${detail || res.statusText}`);
  }
  return res.json();
}

export function useApi<T = any>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState<boolean>(!!path);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    if (!path) return;
    setLoading(true);
    setError(null);
    getJSON<T>(path)
      .then(setData)
      .catch((e) => setError(String(e.message ?? e)))
      .finally(() => setLoading(false));
  }, [path]);

  useEffect(() => {
    reload();
  }, [reload]);

  return { data, loading, error, reload };
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
