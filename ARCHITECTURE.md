# ARCHITECTURE.md — current state, codebase map, and how to brief work

Orientation for anyone (especially Claude) scoping or executing work in this repo. It captures
**what the app is, how the code is structured, and how it's deployed today**, so briefs match
reality. For the scoring math, key formulas, and the full HARD-CONSTRAINT list, see `CLAUDE.md`;
this file is the "current state" companion — keep it current when the architecture changes.

## What it is

A personal book-tracking + enjoyment-prediction app. The owner rates every book on **14
fine-grained components**; the engine predicts how much he'll enjoy any *unread* book before he
reads it, with a calibrated interval and a predicted rank. **Fiction and nonfiction** are tracked
separately (separate tables + engines). It began as a localhost single-user instrument and is now
**also a hosted, multi-tenant web app** — the same codebase runs in three modes.

## ONE codebase, THREE run-modes (the key architectural fact)

All three are the **same code on `main`**, switched purely by environment variables:

| Mode | Where | Env | Data | Auth | R/W |
|---|---|---|---|---|---|
| **Local dev** | `bash start.sh` on localhost | none of the flags | SQLite `books.db` | off → `DEFAULT_USER_ID` (Michael) | full R/W |
| **Public showcase** | Vercel `book-ranking-app` → `book-ranking-app-eight.vercel.app` | `STATIC_DATA=1` + `READONLY=1`, **NO Supabase vars** | bundled JSON in `frontend/public/data/` | none (no backend) | read-only |
| **Hosted app** | Vercel `the-reading-ledger` → `www.thereadingledger.com` + Railway backend | Supabase + `API_URL` (no static/readonly) | Postgres (Supabase) | **on** (`AUTH_ENABLED=1`, Supabase JWT) | per-user R/W |

Consequences worth memorizing:

- **`NEXT_PUBLIC_*` inline at BUILD time** — changing a Vercel env var does nothing until a rebuild.
- **`proxy.ts` gates login purely on the two Supabase vars being present** (independent of `STATIC_DATA`). The showcase project must carry **NO** Supabase vars, or it flips to a login wall.
- On the hosted app, page data reads happen **server-side (SSR)**: a page reads the token from the cookie and fetches Railway server-to-server, so backend calls **never appear in the browser Network tab** (an SSR fetch failure shows Next's generic "server error", not a `/login` bounce).
- A backend URL env var must include the **scheme** (`https://…`) or SSR `fetch()` throws `Failed to parse URL`.

## Deployment map

- **Branch:** everything is on **`main`** (consolidated 2026-07-11). The old `feat/multi-tenant-migration` branch is redundant.
- **Vercel `book-ranking-app`** (showcase): builds `main`, static+readonly, no Supabase → public read-only snapshot. **A data commit IS a publish** (git hooks regenerate + gate the snapshot).
- **Vercel `the-reading-ledger`** (app): builds `main`, Supabase+`API_URL`, **Root Directory = `frontend`** → live multi-tenant app on `www.thereadingledger.com` (apex `thereadingledger.com` 308→www; domain at Cloudflare, DNS-only/grey-cloud records).
- **Railway** (backend): FastAPI + Postgres (Supabase); env `AUTH_ENABLED=1`, `ALLOWED_ORIGIN=https://www.thereadingledger.com`, `SIGNUP_INVITE_CODE`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_URL`, `ANTHROPIC_API_KEY`, `DATABASE_URL`. Serves the app's SSR fetches and client mutations (CORS-locked to the app origin).
- **Supabase**: Auth (ES256 JWT via JWKS) + Postgres. Public sign-ups disabled; accounts minted only via the backend `POST /api/signup` (invite-gated, admin API, email pre-confirmed).

## Codebase map

**Backend / engine (Python, repo root + `backend/`):**
- `backend/main.py` — FastAPI app; all HTTP routes; per-tenant engine cache (`_get_engine(user_id)` → `_build_engine_for`); `Depends(auth.get_current_user_id)` on data routes.
- **Read-only engine — DO NOT reimplement/modify the math:** `predict_engine.py`, `db_loader.py`, `views.py` (+ `nonfiction_engine.py`). WA, shrinkage, tiers, series math live here.
- `db_write.py` — the ONLY write path (validated functions; every one takes `user_id`).
- `db_backend.py` — SQLite⇄Postgres switch (`DB_BACKEND`); `DEFAULT_USER_ID` (Michael) is the auth-off / local / export fallback.
- `auth.py` — Supabase JWT verify → `user_id`. `signup.py` — invite-gated account creation.
- Prediction support: `research_predict.py`, `reresearch_and_measure.py`, `intervals.py` (conformal band), `repredict_on_add.py` (auto re-predict), `research_layer.py` (LLM key).
- Validation/reporting: `validate_engine.py`, `walkforward.py`, `track_record.py`, `engine_parameters.py`; **`test_engine.py` is the health gate — must stay 29/29.**
- Nonfiction: `nonfiction_engine.py`, `nonfiction_research.py`.

**Frontend (`frontend/`, Next.js 16 App Router — read `frontend/AGENTS.md`; it is NOT vanilla Next):**
- `app/*` — pages (server components fetch via SSR). Top-level: `add-book`, `edit-ratings`, `predict`, `read-queue`, `stats`, `analytics`, `calibration`, `track-record`, `methodology`, `delta-log`, `login`, home. Route groups `fiction/*` + `nonfiction/*` (rankings, tier-list, series, reading, timeline, read-queue) share `components/views/*View.tsx` (kind-param).
- `lib/api.ts` — all backend calls; `STATIC` (bundle) vs live; `apiFetch(url, init, serverToken?)` attaches the Supabase token; `signUp()`.
- `lib/supabase/{client,server}.ts`; `proxy.ts` (Next-16 middleware — session refresh + `/login` gate, no-op when Supabase env absent); `lib/readonly.ts`; `lib/types.ts`; `lib/slug.ts`; `components/Nav.tsx`.
- Design tokens live in `app/globals.css` (the "Fable" system) — reuse existing primitives; **no new styles**.

**Data + publishing:**
- SQLite `books.db` (local + the showcase's export source) / Postgres (hosted). **7 per-user tables**: `books`, `recommendations`, `nonfiction_books`, `nonfiction_recommendations`, `read_queue`, `nonfiction_read_queue`, `delta_log`. Weight tables are **global**.
- `scripts/export_static_data.py` → deterministic snapshot in `frontend/public/data/`. Git hooks (`scripts/hooks/`) regenerate it on commit and gate it on push. **A display feature that shows new data must ALSO be added to this export**, or the static showcase has nothing to render.

## Prediction engine (one-liner; math in `CLAUDE.md`)

14 components in 5 categories → per-book **WA** (genre-weighted) is the ranking score; empirical-Bayes shrinkage toward author/genre/global means; unread-book predictions get a **conformal 80% interval** (density-bucketed, `intervals.py`) — never `resid_sd`. Honest walk-forward MAE baseline = **0.631**. Multi-tenant **cold-start v1**: a tenant with fewer than 15 books borrows the seed (Michael's) fitted model; smooth per-user personalization is future "Phase 4".

## Hard constraints (full list in `CLAUDE.md` — the load-bearing ones)

1. Never modify prediction/derived math (`predict_engine.py`, `db_loader.py`, `views.py`).
2. All writes go through `db_write.py`; DB schema is fixed (7 tenant tables + global weights).
3. `test_engine.py` must stay 29/29.
4. No new visual styles — extend `globals.css` tokens + existing primitives.
5. Served interval is the conformal 80% band, never a `resid_sd` CI.
6. The showcase project carries NO Supabase vars; `main` is the single source for both deployments.

## How to write a good brief for this repo

A strong brief here states:

- **Which run-mode(s)** the change targets (local dev / public showcase / hosted app) and whether it touches the **read (SSR)** or **write (mutation + CORS)** path.
- **Which files** — and explicitly that it stays OUT of the read-only engine. Rule of thumb: if it changes a *number* it's probably engine/math (off-limits); if it changes how an existing number is *displayed or sorted* it's presentation (fair game).
- **Deployment impact** — a change on `main` rebuilds BOTH sites; a *data* commit publishes the showcase; new displayed data needs an `export_static_data.py` update; `NEXT_PUBLIC_*` need a rebuild to take effect.
- **Verification** — `test_engine.py` 29/29, `python3 scripts/export_static_data.py --check`, and driving the actual affected flow.
- **Constraints touched** — auth/tenancy (`user_id` via `db_write` + `Depends`), the conformal interval, the design tokens, the showcase's no-Supabase-vars rule.
