import TryClient from "./TryClient";

// A PUBLIC, no-login route (exempted in proxy.ts). It renders a self-contained
// client demo that calls only the read-only /api/demo/predict endpoint — no SSR
// data fetch, so it needs no auth token and works for an anonymous visitor.
export const metadata = {
  title: "Try it — The Reading Ledger",
  description:
    "Predict how much this reader would enjoy any book — before reading it. A live demo of The Reading Ledger's prediction engine.",
};

export default function TryPage() {
  return <TryClient />;
}
