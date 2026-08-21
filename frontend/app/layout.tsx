import type { Metadata, Viewport } from "next";
import { Lora, Inter } from "next/font/google";
import "./globals.css";
import Nav from "@/components/Nav";
import ServiceWorkerRegistrar from "@/components/ServiceWorkerRegistrar";
import { PredictJobsProvider } from "@/lib/predict-jobs";
import { PredictJobBanner } from "@/components/PredictJobStatus";

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
  // Installable-PWA wiring. The manifest is a static file in `public/` rather
  // than an `app/manifest.ts` route on purpose: the auth proxy's matcher already
  // excludes `*.json`, so /manifest.json is reachable signed-out, whereas the
  // generated route's /manifest.webmanifest would be gated (see proxy.ts).
  manifest: "/manifest.json",
  icons: {
    icon: [
      { url: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
      { url: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
    ],
    apple: [{ url: "/icons/icon-180.png", sizes: "180x180", type: "image/png" }],
  },
  appleWebApp: {
    capable: true,
    title: "Ledger",
    // Parchment ground behind the status bar, matching background_color.
    statusBarStyle: "default",
  },
};

// `themeColor` is a viewport export, not metadata, since Next 14 (it is
// deprecated on `metadata`). #FFFFFF is --color-surface — the sticky Nav's
// background — so the browser/OS chrome blends into the header.
export const viewport: Viewport = {
  themeColor: "#FFFFFF",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={`${lora.variable} ${inter.variable}`}>
      <body className="min-h-screen flex flex-col" suppressHydrationWarning>
        <ServiceWorkerRegistrar />
        {/* A prediction run is minutes of LLM calls. Its state lives in this
            provider — ABOVE {children} — so switching tabs unmounts the Predict
            page but not the work: the run keeps going, the nav shows a live
            pill, and the banner announces the finish from whatever page the
            reader has wandered to. Inert (and free) until a run is started. */}
        <PredictJobsProvider>
          <Nav />
          <main className="flex-1 max-w-5xl mx-auto w-full px-4 py-8">
            {children}
          </main>
          <footer className="text-center py-6 text-xs" style={{ color: "var(--color-faint)" }}>
            The Reading Ledger — {new Date().getFullYear()}
          </footer>
          <PredictJobBanner />
        </PredictJobsProvider>
      </body>
    </html>
  );
}
