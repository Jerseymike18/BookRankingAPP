import type { BookKind } from "@/lib/types";

/** The in-page content-type scope. "all" = combined fiction + nonfiction.
 * Kept in a plain (non-"use client") module so server page components can parse
 * the `?type=` query param without pulling in the client TypeToggle bundle. */
export type TypeScope = "all" | BookKind;

/** Parse a `?type=` query value into a TypeScope, falling back when absent or
 * invalid. `includeAll=false` maps a stray "all" onto the fallback so the
 * two-way views (Tier List / Series / Timeline / Reading / Read Queue) never
 * land on an unsupported combined mode. */
export function parseTypeScope(
  raw: string | string[] | undefined,
  fallback: TypeScope,
  includeAll = true,
): TypeScope {
  const v = Array.isArray(raw) ? raw[0] : raw;
  if (v === "fiction" || v === "nonfiction") return v;
  if (v === "all" && includeAll) return "all";
  return fallback;
}
