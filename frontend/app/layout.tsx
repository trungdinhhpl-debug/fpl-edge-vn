import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "./providers";
import { Nav } from "@/components/nav";

export const metadata: Metadata = {
  title: "FPL Edge VN — Quyết định FPL dựa trên dữ liệu",
  description:
    "Expected points, expected minutes, Monte Carlo và tối ưu đội hình cho Fantasy Premier League. Sản phẩm độc lập của người hâm mộ.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="vi" suppressHydrationWarning>
      <body>
        <Providers>
          <Nav />
          <main className="mx-auto max-w-7xl px-4 py-6">{children}</main>
          <footer className="mx-auto max-w-7xl px-4 py-8 text-xs text-muted-foreground">
            <p>
              FPL Edge VN · Sản phẩm độc lập của người hâm mộ, không liên kết với
              Premier League hoặc Fantasy Premier League. Dữ liệu từ FPL API công khai.
              Mọi dự báo đều kèm mức tin cậy — không phải lời khẳng định chắc chắn.
            </p>
          </footer>
        </Providers>
      </body>
    </html>
  );
}
