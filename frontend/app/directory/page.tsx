import Link from "next/link";
import { getServerAccessToken } from "@/lib/supabase/server";
import { fetchProfileDirectory } from "@/lib/api";

export const dynamic = "force-dynamic";

// The public directory — every reader who has opted their profile public. Each
// card links to /u/<handle>. Signed-in only (the backend gates + rate-limits it).
export default async function DirectoryPage() {
  const token = await getServerAccessToken();
  const { profiles } = await fetchProfileDirectory(token);

  return (
    <div>
      <div className="mb-6">
        <h1
          className="font-display text-3xl font-bold leading-tight"
          style={{ color: "var(--color-ink)" }}
        >
          Directory
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--color-muted)" }}>
          {profiles.length} public {profiles.length === 1 ? "profile" : "profiles"} · browse
          another reader&apos;s rankings and to-read queue
        </p>
      </div>

      {profiles.length === 0 ? (
        <div
          className="text-center py-16 rounded-xl"
          style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
        >
          <p className="text-sm" style={{ color: "var(--color-muted)" }}>
            No public profiles yet. Make yours public from{" "}
            <Link href="/profile" className="underline" style={{ color: "var(--color-sage)" }}>
              My Profile
            </Link>
            .
          </p>
        </div>
      ) : (
        <div
          className="grid gap-3"
          style={{ gridTemplateColumns: "repeat(auto-fill, minmax(16rem, 1fr))" }}
        >
          {profiles.map((p) => (
            <Link
              key={p.handle}
              href={`/u/${encodeURIComponent(p.handle)}`}
              className="block rounded-xl p-4 no-underline transition-colors"
              style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
            >
              <p className="font-display text-lg font-semibold" style={{ color: "var(--color-ink)" }}>
                {p.display_name || p.handle}
              </p>
              <p className="text-xs mt-0.5" style={{ color: "var(--color-faint)" }}>
                @{p.handle}
              </p>
              <p className="text-sm mt-2" style={{ color: "var(--color-muted)" }}>
                {p.fiction_books} fiction · {p.nonfiction_books} nonfiction
              </p>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
