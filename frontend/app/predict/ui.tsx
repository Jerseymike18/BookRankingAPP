"use client";

/* Shared presentational primitives for the two Predict pages.
 *
 * These lived inside PredictClient until Predict became a heading over two
 * pages (book prediction / genre prediction). Both need the same card, button,
 * banner and input treatments, and the alternative to a shared module is two
 * drifting copies of the same styles — which HARD CONSTRAINT 5 exists to
 * prevent. Nothing here is new: every value is an existing globals.css token.
 */

import React from "react";

export const inputStyle: React.CSSProperties = {
  background: "var(--color-surface)",
  border: "1px solid var(--color-rule)",
  color: "var(--color-ink)",
  fontFamily: "var(--font-body)",
};

/* ── Grounding signal (the PRIMARY reliability indicator) ────────────────── */

export function Card({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div
      className={`rounded-xl p-5 ${className ?? ""}`}
      style={{ background: "var(--color-surface)", border: "1px solid var(--color-rule)" }}
    >
      {children}
    </div>
  );
}

/* ── Stop control ────────────────────────────────────────────────────────────
   Quiet on purpose: stopping should be findable but never look like the primary
   action next to a bar that is making progress. Reuses the existing rule/muted
   tokens rather than introducing a destructive colour — nothing is destroyed by
   pressing it, since everything already scored is kept. */
export function StopButton({
  onClick,
  label = "Stop",
  className = "",
}: {
  onClick: () => void;
  label?: string;
  className?: string;
}) {
  return (
    <button
      onClick={onClick}
      className={`text-xs px-2.5 py-1 rounded-md transition-colors ${className}`}
      style={{
        background: "var(--color-surface-2)",
        color: "var(--color-muted)",
        border: "1px solid var(--color-rule)",
      }}
      title="Stop making further calls. Anything already done is kept; a call already in flight may still finish on the server."
    >
      {label}
    </button>
  );
}

/* ── Sage button ─────────────────────────────────────────────────────────── */
export function SageButton({
  onClick,
  disabled,
  children,
  variant = "primary",
}: {
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
  variant?: "primary" | "secondary";
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-40 transition-colors"
      style={
        variant === "primary"
          ? { background: "var(--color-sage)", color: "#fff" }
          : {
              background: "var(--color-surface)",
              color: "var(--color-muted)",
              border: "1px solid var(--color-rule)",
            }
      }
    >
      {children}
    </button>
  );
}

export function ErrorBox({
  message,
  onDismiss,
}: {
  message: string;
  /** When given, the reader can clear the banner themselves — needed for a
   *  failure whose cause is external and may already have been fixed. */
  onDismiss?: () => void;
}) {
  return (
    <div
      className="rounded-lg px-4 py-3 text-sm flex items-start gap-3"
      style={{ background: "#FEF2F2", color: "#B91C1C", border: "1px solid #FCA5A5" }}
    >
      <span className="flex-1">{message}</span>
      {onDismiss && (
        <button onClick={onDismiss} aria-label="Dismiss" className="flex-shrink-0 leading-none">
          ✕
        </button>
      )}
    </div>
  );
}

export function InfoBox({ message }: { message: string }) {
  return (
    <div
      className="rounded-lg px-4 py-3 text-sm"
      style={{
        background: "var(--color-sage-light)",
        color: "var(--color-sage)",
        border: "1px solid var(--color-sage)",
      }}
    >
      {message}
    </div>
  );
}