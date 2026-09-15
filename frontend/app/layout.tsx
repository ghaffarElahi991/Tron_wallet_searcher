import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "TronForge — Vanity wallet studio",
  description: "Generate and fund a fresh TRON vanity wallet securely.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
