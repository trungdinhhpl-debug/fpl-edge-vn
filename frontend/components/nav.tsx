"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import {
  LayoutDashboard, Users, CalendarRange, CalendarCheck2, Crown, Search, Zap,
  Newspaper, MessageSquareQuote, BookOpen, Shield, Menu, X, Globe,
} from "lucide-react";
import { ThemeToggle } from "@/components/theme-toggle";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const items = [
  { href: "/", key: "dashboard", icon: LayoutDashboard },
  { href: "/my-team", key: "myTeam", icon: Shield },
  { href: "/planner", key: "planner", icon: CalendarRange },
  { href: "/chips", key: "chips", icon: CalendarCheck2 },
  { href: "/free-hit", key: "freeHit", icon: Zap },
  { href: "/captaincy", key: "captaincy", icon: Crown },
  { href: "/players", key: "players", icon: Search },
  { href: "/fixtures", key: "fixtures", icon: Users },
  { href: "/news", key: "news", icon: Newspaper },
  { href: "/experts", key: "experts", icon: MessageSquareQuote },
  { href: "/methodology", key: "methodology", icon: BookOpen },
];

export function Nav() {
  const path = usePathname();
  const { t, lang, setLang } = useT();
  const [open, setOpen] = useState(false);

  return (
    <header className="sticky top-0 z-40 border-b bg-background/90 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-7xl items-center gap-3 px-4">
        <Link href="/" className="flex items-center gap-2 font-bold">
          <span className="flex h-7 w-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
            ⚡
          </span>
          <span className="hidden sm:inline">FPL Edge VN</span>
        </Link>

        <nav className="ml-2 hidden flex-1 items-center gap-0.5 lg:flex">
          {items.map((it) => {
            const active = path === it.href;
            return (
              <Link
                key={it.href}
                href={it.href}
                className={cn(
                  "rounded-md px-2.5 py-1.5 text-sm font-medium transition",
                  active
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
              >
                {t(it.key)}
              </Link>
            );
          })}
        </nav>

        <div className="ml-auto flex items-center gap-1.5">
          <button
            onClick={() => setLang(lang === "vi" ? "en" : "vi")}
            className="flex h-9 items-center gap-1 rounded-md border px-2 text-xs font-semibold hover:bg-muted"
          >
            <Globe className="h-3.5 w-3.5" />
            {lang.toUpperCase()}
          </button>
          <ThemeToggle />
          <button
            className="flex h-9 w-9 items-center justify-center rounded-md border lg:hidden"
            onClick={() => setOpen((o) => !o)}
          >
            {open ? <X className="h-4 w-4" /> : <Menu className="h-4 w-4" />}
          </button>
        </div>
      </div>

      {open && (
        <nav className="grid grid-cols-2 gap-1 border-t p-3 lg:hidden">
          {items.map((it) => {
            const active = path === it.href;
            const Icon = it.icon;
            return (
              <Link
                key={it.href}
                href={it.href}
                onClick={() => setOpen(false)}
                className={cn(
                  "flex items-center gap-2 rounded-md px-3 py-2 text-sm",
                  active ? "bg-primary/10 text-primary" : "hover:bg-muted",
                )}
              >
                <Icon className="h-4 w-4" />
                {t(it.key)}
              </Link>
            );
          })}
        </nav>
      )}
    </header>
  );
}
