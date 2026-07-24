import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function fdrClass(score: number | null | undefined): string {
  if (score == null) return "fdr-3";
  const r = Math.round(score);
  return `fdr-${Math.min(5, Math.max(1, r))}`;
}

/** Semantic colour class for a risk level (spec §16). */
export function riskColor(level: string | null | undefined): string {
  switch (level) {
    case "Low":
      return "text-positive";
    case "Medium":
      return "text-caution";
    case "High":
    case "Very High":
      return "text-danger";
    default:
      return "text-neutralq";
  }
}

export function riskBg(level: string | null | undefined): string {
  switch (level) {
    case "Low":
      return "bg-positive/15 text-positive";
    case "Medium":
      return "bg-caution/15 text-caution";
    case "High":
    case "Very High":
      return "bg-danger/15 text-danger";
    default:
      return "bg-muted text-muted-foreground";
  }
}

export function playerPhoto(code?: string | null): string | null {
  if (!code) return null;
  return `https://resources.premierleague.com/premierleague/photos/players/110x140/p${code}.png`;
}
