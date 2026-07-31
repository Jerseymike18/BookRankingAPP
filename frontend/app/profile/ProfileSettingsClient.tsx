"use client";

import { useState } from "react";
import Link from "next/link";
import type { Profile } from "@/lib/types";
import { saveMyProfile } from "@/lib/api";

export default function ProfileSettingsClient({ initial }: { initial: Profile | null }) {
  const [handle, setHandle] = useState(initial?.handle ?? "");
  const [displayName, setDisplayName] = useState(initial?.display_name ?? "");
  const [isPublic, setIsPublic] = useState(initial?.is_public ?? false);
  const [saved, setSaved] = useState<Profile | null>(initial);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSave() {
    setSaving(true);
    setError(null);
    try {
      const p = await saveMyProfile({
        handle: handle.trim(),
        display_name: displayName.trim() || null,
        is_public: isPublic,
      });
      setSaved(p);
      setHandle(p.handle);
      setDisplayName(p.display_name ?? "");
      setIsPublic(p.is_public);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save.");
    } finally {
      setSaving(false);
    }
  }

  const labelStyle = { color: "var(--color-muted)" };
  const inputClass = "w-full rounded-lg px-3 py-2 text-sm";
  const inputStyle = {
    background: "var(--color-surface)",
    border: "1px solid var(--color-rule)",
    color: "var(--color-ink)",
  };

  return (
    <div className="max-w-lg">
      <div className="mb-6">
        <h1
          className="font-display text-3xl font-bold leading-tight"
          style={{ color: "var(--color-ink)" }}
        >
          My Profile
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--color-muted)" }}>
          Claim a handle and choose whether other readers can browse your rankings and
          to-read queue. Your profile is <strong>private</strong> until you make it public.
        </p>
      </div>

      <div className="space-y-4">
        <label className="block">
          <span className="block text-xs mb-1 font-semibold uppercase tracking-widest" style={labelStyle}>
            Handle
          </span>
          <input
            type="text"
            value={handle}
            onChange={(e) => setHandle(e.target.value)}
            placeholder="e.g. bookworm_42"
            className={inputClass}
            style={inputStyle}
          />
          <span className="block text-xs mt-1" style={{ color: "var(--color-faint)" }}>
            3–30 characters: lowercase letters, numbers, underscores.
          </span>
        </label>

        <label className="block">
          <span className="block text-xs mb-1 font-semibold uppercase tracking-widest" style={labelStyle}>
            Display name <span style={{ textTransform: "none" }}>(optional)</span>
          </span>
          <input
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="Shown instead of your handle"
            className={inputClass}
            style={inputStyle}
          />
        </label>

        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={isPublic}
            onChange={(e) => setIsPublic(e.target.checked)}
          />
          <span className="text-sm" style={{ color: "var(--color-ink)" }}>
            Make my profile public (listed in the directory)
          </span>
        </label>

        <div className="flex items-center gap-3 pt-2">
          <button
            onClick={onSave}
            disabled={saving || !handle.trim()}
            className="px-4 py-2 rounded-lg text-sm font-medium transition-colors"
            style={{
              background: saving || !handle.trim() ? "var(--color-surface-2)" : "var(--color-sage)",
              color: saving || !handle.trim() ? "var(--color-muted)" : "#fff",
            }}
          >
            {saving ? "Saving…" : "Save"}
          </button>
          {saved?.is_public && (
            <Link
              href={`/u/${encodeURIComponent(saved.handle)}`}
              className="text-sm underline"
              style={{ color: "var(--color-sage)" }}
            >
              View public profile →
            </Link>
          )}
        </div>

        {error && (
          <p className="text-sm" style={{ color: "#B45309" }}>
            {error}
          </p>
        )}
        {saved && !error && !saving && (
          <p className="text-xs" style={{ color: "var(--color-muted)" }}>
            Saved. {saved.is_public
              ? "Your profile is public."
              : "Your profile is private — only you can see it."}
          </p>
        )}
      </div>
    </div>
  );
}
