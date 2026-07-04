This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](https://nextjs.org/docs/app/api-reference/cli/create-next-app).

## Getting Started

First, run the development server:

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

You can start editing the page by modifying `app/page.tsx`. The page auto-updates as you edit the file.

This project uses [`next/font`](https://nextjs.org/docs/app/building-your-application/optimizing/fonts) to automatically optimize and load [Geist](https://vercel.com/font), a new font family for Vercel.

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Public deployment (read-only, no hosted backend)

The site can be published to Vercel as a **read-only public snapshot** with **no
hosted FastAPI**. All data is baked into static JSON under `public/data/` from a
local `books.db`, so nothing on the public site can mutate state or spend
Anthropic tokens. Local dev (`next dev` + uvicorn) is completely unchanged and
still has every write/predict/queue flow.

### The update loop

1. Add / rate / re-order books locally as usual (the normal app against uvicorn).
2. Regenerate the snapshot:
   ```bash
   cd frontend
   npm run export-data        # runs scripts/export_static_data.py in-process
   ```
   This deletes and recreates `public/data/` from the current `books.db`. It runs
   the FastAPI app via `TestClient` (no uvicorn needed) and only touches
   read-only GET endpoints. Requires `python3` with the backend's deps installed
   (`fastapi`, and `httpx` for the TestClient — both already pulled in by
   FastAPI; no extra install for this repo).
3. Commit the regenerated data and push:
   ```bash
   git add public/data && git commit -m "Refresh public data snapshot" && git push
   ```
4. Vercel auto-rebuilds and redeploys.

### Vercel project settings

- **Root directory:** `frontend`
- **Build:** default Next.js (do **not** set `output: "export"` — the
  `redirects()` in `next.config.ts` needs the normal Next.js runtime, which is
  fine on the free tier).
- **Environment variables** (build-time — see `.env.production.example`):
  - `NEXT_PUBLIC_STATIC_DATA=1` — read from `public/data/` instead of the backend.
  - `NEXT_PUBLIC_READONLY=1` — hide all write UI; `/predict`, `/add-book`,
    `/edit-ratings` show a read-only notice.

### Local smoke test

Verify the static build with the backend **off**:

```bash
cd frontend
NEXT_PUBLIC_STATIC_DATA=1 NEXT_PUBLIC_READONLY=1 npm run build && npm run start
```

Every read-only page (Rankings, Tier List, Series, Timeline, Reading, Stats,
Taste Lab, Calibration, Delta Log, Read Queue) should render from the snapshot
with no requests to `localhost:8000`.

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/app/building-your-application/deploying) for more details.
