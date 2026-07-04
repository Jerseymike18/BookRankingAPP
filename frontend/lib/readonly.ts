/** Read-only public deployment flag.
 *
 * When set (Vercel build env `NEXT_PUBLIC_READONLY=1`), all write/mutation UI
 * is hidden and the write endpoints in lib/api.ts throw instead of fetching.
 * Local dev leaves this unset, so every edit/predict/queue flow works as before.
 */
export const READONLY = process.env.NEXT_PUBLIC_READONLY === "1";
