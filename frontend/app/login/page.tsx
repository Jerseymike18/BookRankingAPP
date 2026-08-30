"use client";

import { useState } from "react";
import { createSupabaseBrowserClient } from "@/lib/supabase/client";
import { signUp } from "@/lib/api";
import { ProgressBar } from "@/components/ProgressBar";

const inputStyle: React.CSSProperties = {
  background: "var(--color-surface)",
  border: "1px solid var(--color-rule)",
  color: "var(--color-ink)",
  fontFamily: "var(--font-body)",
};

/** Where to land after signing in: the path the proxy attached as ?next=, or the
 *  home page. Resolved against this origin and rejected unless it stays here — a
 *  startsWith("/") test is not enough, since a browser reads both "//evil.com" and
 *  "/\\evil.com" as protocol-relative and would follow them off-site.
 *
 *  Read at navigation time rather than mirrored into state on mount: it is never
 *  rendered, so it was never state's job, and the effect that copied it in was
 *  a cascading render for a value only one event handler reads. */
function nextDestination(): string {
  const raw = new URLSearchParams(window.location.search).get("next");
  if (!raw) return "/";
  try {
    const url = new URL(raw, window.location.origin);
    if (url.origin !== window.location.origin) return "/";
    return url.pathname + url.search + url.hash;
  } catch {
    return "/";
  }
}

const configured =
  !!process.env.NEXT_PUBLIC_SUPABASE_URL &&
  !!process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

type Mode = "signin" | "signup";

export default function LoginPage() {
  const [mode, setMode] = useState<Mode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function signInAndGo(): Promise<void> {
    const supabase = createSupabaseBrowserClient();
    const { error: signInError } = await supabase.auth.signInWithPassword({
      email: email.trim(),
      password,
    });
    if (signInError) {
      setError(signInError.message);
      setBusy(false);
      return;
    }
    // Hard navigation (not router.push): forces the proxy to re-run and the SSR
    // render of the destination to read the freshly-set session cookie.
    window.location.assign(nextDestination());
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "signup") {
        // Invite-gated account creation happens server-side; then sign in. Email
        // confirmation is off, so the new account is usable immediately.
        await signUp(email.trim(), password, inviteCode.trim());
      }
      await signInAndGo();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
      setBusy(false);
    }
  }

  function switchMode(m: Mode) {
    setMode(m);
    setError(null);
  }

  const isSignup = mode === "signup";

  return (
    <div className="max-w-sm mx-auto w-full">
      <div
        className="rounded-xl p-6 mt-8"
        style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
      >
        <h1 className="font-display font-semibold text-lg mb-1" style={{ color: "var(--color-ink)" }}>
          The Reading Ledger
        </h1>
        <p className="text-xs mb-5" style={{ color: "var(--color-muted)" }}>
          {isSignup ? "Create your ledger." : "Sign in to your ledger."}
        </p>

        {!configured ? (
          <p className="text-sm" style={{ color: "var(--color-muted)" }}>
            Authentication is not configured for this deployment.
          </p>
        ) : (
          <>
            <form onSubmit={handleSubmit} className="flex flex-col gap-3">
              <label className="text-xs font-medium" style={{ color: "var(--color-muted)" }}>
                Email
                <input
                  type="email"
                  autoComplete="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full mt-1 px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2"
                  style={inputStyle}
                />
              </label>
              <label className="text-xs font-medium" style={{ color: "var(--color-muted)" }}>
                Password
                <input
                  type="password"
                  autoComplete={isSignup ? "new-password" : "current-password"}
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full mt-1 px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2"
                  style={inputStyle}
                />
              </label>

              {isSignup && (
                <label className="text-xs font-medium" style={{ color: "var(--color-muted)" }}>
                  Invite code
                  <input
                    type="text"
                    autoComplete="off"
                    required
                    value={inviteCode}
                    onChange={(e) => setInviteCode(e.target.value)}
                    className="w-full mt-1 px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2"
                    style={inputStyle}
                  />
                </label>
              )}

              {error && (
                <p className="text-sm" style={{ color: "var(--color-spine-f)" }}>
                  {error}
                </p>
              )}

              <button
                type="submit"
                disabled={busy || !email.trim() || !password || (isSignup && !inviteCode.trim())}
                className="mt-1 px-6 py-3 rounded-xl font-semibold text-sm disabled:opacity-40 transition-colors"
                style={{ background: "var(--color-sage)", color: "#fff" }}
              >
                {busy
                  ? isSignup
                    ? "Creating account…"
                    : "Signing in…"
                  : isSignup
                    ? "Create account"
                    : "Sign in"}
              </button>
              {busy && (
                // Sign-up validates the invite, creates the account and then
                // signs in — a multi-round-trip wait behind one button.
                <ProgressBar
                  label={isSignup ? "Creating your account…" : "Signing you in…"}
                />
              )}
            </form>

            <p className="text-xs mt-4" style={{ color: "var(--color-muted)" }}>
              {isSignup ? "Already have an account?" : "Have an invite code?"}{" "}
              <button
                type="button"
                onClick={() => switchMode(isSignup ? "signin" : "signup")}
                className="font-medium underline"
                style={{ color: "var(--color-sage)" }}
              >
                {isSignup ? "Sign in" : "Create an account"}
              </button>
            </p>
          </>
        )}
      </div>
    </div>
  );
}
