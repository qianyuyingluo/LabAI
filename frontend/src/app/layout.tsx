import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "LabAI 工作区 Agent",
  description: "Local workspace agent for project files, commands, and data analysis",
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
