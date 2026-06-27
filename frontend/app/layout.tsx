import type { Metadata } from "next";
import { Lora, Inter } from "next/font/google";
import "./globals.css";
import Nav from "@/components/Nav";

const lora = Lora({
  variable: "--font-lora",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  style: ["normal", "italic"],
  display: "swap",
});

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  weight: ["300", "400", "500", "600"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "The Reading Ledger",
  description: "A working record of books read, rated, and predicted.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={`${lora.variable} ${inter.variable}`}>
      <body className="min-h-screen flex flex-col">
        <Nav />
        <main className="flex-1 max-w-5xl mx-auto w-full px-4 py-8">
          {children}
        </main>
        <footer className="text-center py-6 text-xs" style={{ color: "var(--color-faint)" }}>
          The Reading Ledger — {new Date().getFullYear()}
        </footer>
      </body>
    </html>
  );
}
