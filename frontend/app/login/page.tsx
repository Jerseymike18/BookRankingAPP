"use client";

import { useEffect, useState } from "react";
import { createSupabaseBrowserClient } from "@/lib/supabase/client";
import { signUp } from "@/lib/api";

const inputStyle: React.CSSProperties = {
  background: "var(--color-surface)",
  border: "1px solid var(--color-rule)",
  color: "var(--color-ink)",
  fontFamily: "var(--font-body)",
};

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
  const [next, setNext] = useState("/");

  // Read the post-login destination the proxy attached (?next=/some/path). Only
  // same-origin paths are honoured, so this can't be turned into an open redirect.
  useEffect(() => {
    const p = new URLSearchParams(window.location.search).get("next");
    if (p && p.startsWith("/") && !p.startsWith("//")) setNext(p);
  }, []);

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
    window.location.assign(next);
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
