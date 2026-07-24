"use client";
import { createContext, useContext, useEffect, useState } from "react";

type Lang = "vi" | "en";

const dict: Record<string, { vi: string; en: string }> = {
  appName: { vi: "FPL Edge VN", en: "FPL Edge VN" },
  tagline: {
    vi: "Quyết định FPL dựa trên dữ liệu",
    en: "Data-driven FPL decisions",
  },
  // nav
  dashboard: { vi: "Tổng quan", en: "Dashboard" },
  myTeam: { vi: "Đội của tôi", en: "My Team" },
  planner: { vi: "Kế hoạch dài hạn", en: "Long-term Planner" },
  freeHit: { vi: "Free Hit Lab", en: "Free Hit Lab" },
  captaincy: { vi: "Đội trưởng", en: "Captaincy" },
  players: { vi: "Cầu thủ", en: "Player Explorer" },
  fixtures: { vi: "Lịch thi đấu", en: "Fixture Ticker" },
  news: { vi: "Tin tức & chấn thương", en: "News & Injuries" },
  experts: { vi: "Chuyên gia", en: "Expert Consensus" },
  methodology: { vi: "Phương pháp", en: "Methodology" },
  // common
  deadline: { vi: "Hạn chót", en: "Deadline" },
  gameweek: { vi: "Vòng đấu", en: "Gameweek" },
  position: { vi: "Vị trí", en: "Position" },
  price: { vi: "Giá", en: "Price" },
  team: { vi: "Đội", en: "Team" },
  form: { vi: "Phong độ", en: "Form" },
  owned: { vi: "Sở hữu", en: "Owned" },
  confidence: { vi: "Độ tin cậy", en: "Confidence" },
  risk: { vi: "Rủi ro", en: "Risk" },
  ceiling: { vi: "Ceiling", en: "Ceiling" },
  captain: { vi: "Đội trưởng", en: "Captain" },
  vice: { vi: "Đội phó", en: "Vice" },
  bench: { vi: "Dự bị", en: "Bench" },
  formation: { vi: "Sơ đồ", en: "Formation" },
  loading: { vi: "Đang tải…", en: "Loading…" },
  noData: { vi: "Chưa có dữ liệu", en: "No data yet" },
  updated: { vi: "Cập nhật", en: "Updated" },
  source: { vi: "Nguồn", en: "Source" },
  topPredicted: { vi: "Điểm kỳ vọng cao nhất", en: "Top predicted" },
  topTransfers: { vi: "Được mua nhiều nhất", en: "Most transferred in" },
  injuryAlerts: { vi: "Cảnh báo chấn thương", en: "Injury alerts" },
  blankDouble: { vi: "Blank & Double GW", en: "Blank & Double GW" },
  importTeam: { vi: "Nhập Team ID", en: "Import Team ID" },
  analyze: { vi: "Phân tích", en: "Analyze" },
  optimize: { vi: "Tối ưu", en: "Optimize" },
  buildPlan: { vi: "Lập kế hoạch", en: "Build plan" },
  why: { vi: "Vì sao?", en: "Why?" },
  mockLabel: { vi: "dữ liệu mẫu", en: "mock data" },
  disclaimer: {
    vi: "Sản phẩm độc lập của người hâm mộ, không liên kết với Premier League/FPL. Mọi dự báo kèm mức tin cậy — không đảm bảo chắc chắn.",
    en: "Independent fan project, not affiliated with the Premier League/FPL. All forecasts carry a confidence level — never a guarantee.",
  },
};

type I18nCtx = { lang: Lang; setLang: (l: Lang) => void; t: (k: string) => string };
const Ctx = createContext<I18nCtx>({ lang: "vi", setLang: () => {}, t: (k) => k });

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLangState] = useState<Lang>("vi");
  useEffect(() => {
    const saved = localStorage.getItem("lang") as Lang | null;
    if (saved) setLangState(saved);
  }, []);
  const setLang = (l: Lang) => {
    setLangState(l);
    localStorage.setItem("lang", l);
  };
  const t = (k: string) => dict[k]?.[lang] ?? k;
  return <Ctx.Provider value={{ lang, setLang, t }}>{children}</Ctx.Provider>;
}

export const useT = () => useContext(Ctx);
