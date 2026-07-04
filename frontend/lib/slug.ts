/** URL/file slug for a book title.
 *
 * MUST stay byte-for-byte identical to slugify() in
 * scripts/export_static_data.py — the static export names per-book score files
 * by this slug, so any drift here silently 404s those files.
 *
 * Rule: lowercase, collapse every run of non-[a-z0-9] into "-", trim dashes.
 */
export function slugify(title: string): string {
  return title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}
