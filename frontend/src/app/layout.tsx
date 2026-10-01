import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI 实验数据处理平台",
  description: "AI-first laboratory data processing workbench",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
