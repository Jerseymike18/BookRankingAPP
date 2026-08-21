"use client";

/**
 * PREDICT JOB CHROME — the two pieces of app-wide UI that make a background
 * prediction run visible from anywhere in the site.
 *
 *   <PredictJobPill />    lives in the nav. Shows what a run is doing right now
 *                         and links back to /predict. Renders nothing when idle.
 *   <PredictJobBanner />  lives in the root layout. Announces a finished run.
 *
 * Both are chrome on every route, so both read the provider through the
 * non-throwing hook and no-op if it is ever absent.
 *
 * No new visual language: the pill and banner are built from the existing Fable
 * tokens (sage / sage-light / surface / rule / muted) and reuse ProgressBar, the
 * same primitives the Predict page itself uses.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useSyncExternalStore } from "react";
import {
  useOptionalPredictJobs,
  isRunBusy,
  type PredictRunState,
  type PredictNotice,
} from "@/lib/predict-jobs";
import {
  subscribeNotify,
  notifyState,
  notifyMuted,
  requestNotifyPermission,
  setNotifyMuted,
  showPredictNotification,
  type NotifyState,
} from "@/lib/notify";
import type { BookKind } from "@/lib/types";

/** What a busy run is doing, phrased for a one-line pill. Ordered by which phase
 *  the reader most needs to see: an explicit count beats a vague "working". */
function busyLabel(r: PredictRunState): string | null {
  if (r.scoringIdx !== null && r.candidates) {
    return `Scoring ${r.scoringIdx + 1}/${r.candidates.length}`;
  }
  if (r.genLoading) return "Finding candidates";
  if (r.saving && r.saveProgress.total > 0) {
    return `Saving ${r.saveProgress.done}/${r.saveProgress.total}`;
  }
  if (r.refining.length > 0) return `Grounding ${r.refining.length}`;
  if (r.repredicting.length > 0) return `Re-predicting ${r.repredicting.length}`;
  if (isRunBusy(r)) return "Working";
  return null;
}

export function PredictJobPill() {
  const jobs = useOptionalPredictJobs();
  if (!jobs) return null;

  const busy = (["fiction", "nonfiction"] as BookKind[])
    .map((kind) => ({ kind, label: busyLabel(jobs.runs[kind]) }))
    .filter((x): x is { kind: BookKind; label: string } => x.label !== null);
  if (busy.length === 0) return null;

  // Two runs at once is possible (each kind has its own state), and the pill has
  // room for one line — so name the count rather than truncating one of them.
  const text =
    busy.length === 1 ? `${busy[0].label}…` : `${busy.length} predictions running…`;

  return (
    <Link
      href="/predict"
      onClick={() => jobs.setActiveKind(busy[0].kind)}
      title="A prediction is running in the background — click to watch it"
      className="inline-flex items-center gap-2 px-2.5 py-1.5 rounded-md text-xs font-medium no-underline whitespace-nowrap"
      style={{
        background: "var(--color-sage-light)",
        color: "var(--color-sage)",
        border: "1px solid var(--color-sage)",
      }}
    >
      <span
        className="inline-block w-2 h-2 rounded-full animate-pulse flex-shrink-0"
        style={{ background: "var(--color-sage)" }}
      />
      <span className="hidden sm:inline">{text}</span>
      <span className="sr-only sm:hidden">{text}</span>
    </Link>
  );
}

/** The one place a finished run is put into words. The banner and the OS
 *  notification both render from this, so the two can never disagree about what
 *  happened — they are the same announcement on two surfaces. */
function noticeSummary(notice: PredictNotice): { title: string; body: string } {
  const kindLabel = notice.kind === "nonfiction" ? "Nonfiction" : "Fiction";
  const parts: string[] = [
    `${notice.scored} book${notice.scored === 1 ? "" : "s"} scored`,
  ];
  if (notice.groundable > 0) {
    parts.push(`${notice.grounded} of ${notice.groundable} grounded with reviews`);
  }
  if (notice.failed > 0) {
    parts.push(`${notice.failed} could not be scored`);
  }
  return { title: `${kindLabel} prediction finished`, body: `${parts.join(" · ")}.` };
}

/* ── The reader-facing permission control ────────────────────────────────── */

function useNotifyState(): NotifyState {
  return useSyncExternalStore(
    subscribeNotify,
    notifyState,
    () => "unsupported" as NotifyState,
  );
}

function useNotifyMuted(): boolean {
  return useSyncExternalStore(subscribeNotify, notifyMuted, () => false);
}

/**
 * Opt in to (or mute) the OS notification. Rendered on the Predict page.
 *
 * The permission prompt fires from this button and nowhere else: Safari refuses
 * `requestPermission()` outside a user gesture, and asking unprompted on page
 * load is the pattern browsers are actively penalising. Renders nothing at all
 * where the API does not exist — notably iOS Safari in a normal tab, where the
 * app must be installed to the home screen first.
 */
export function PredictNotifyToggle() {
  const state = useNotifyState();
  const muted = useNotifyMuted();

  if (state === "unsupported") return null;

  const linkish =
    "text-xs underline decoration-dotted underline-offset-2";

  if (state === "denied") {
    return (
      <span className={linkish.replace("underline", "")} style={{ color: "var(--color-faint)" }}>
        Notifications are blocked for this site — the in-page banner still works.
      </span>
    );
  }

  if (state === "default") {
    return (
      <button
        onClick={() => void requestNotifyPermission()}
        className={linkish}
        style={{ color: "var(--color-sage)" }}
        title="Get a desktop notification when a run finishes while this tab is in the background"
      >
        Notify me when a run finishes
      </button>
    );
  }

  return (
    <button
      onClick={() => setNotifyMuted(!muted)}
      className={linkish}
      style={{ color: muted ? "var(--color-faint)" : "var(--color-sage)" }}
      title={
        muted
          ? "Turn desktop notifications back on"
          : "Notifications fire only while this tab is in the background"
      }
    >
      {muted ? "Notifications off" : "Notifications on"}
    </button>
  );
}

export function PredictJobBanner() {
  const jobs = useOptionalPredictJobs();
  const path = usePathname();
  const notice = jobs?.notice ?? null;
  const dismissNotice = jobs?.dismissNotice;

  // Raise the OS notification for a run that landed while the tab was hidden.
  // Keyed on `notice.at` so a re-render (or the auto-dismiss effect below firing
  // first) can never announce the same run twice. shouldNotify() inside
  // showPredictNotification is what enforces the hidden-tab rule, so a reader
  // watching the app gets the banner and nothing else.
  const lastNotifiedAt = useRef<number | null>(null);
  useEffect(() => {
    if (!notice || lastNotifiedAt.current === notice.at) return;
    lastNotifiedAt.current = notice.at;
    const { title, body } = noticeSummary(notice);
    void showPredictNotification(title, body);
  }, [notice]);

  // On the Predict page with the tab actually in front, the results are already
  // on screen — the banner would just be noise covering them. Anywhere else (or
  // in a background tab) it is the whole point, so it stays until dismissed.
  const onPredictPage = path === "/predict";
  useEffect(() => {
    if (!notice || !dismissNotice || !onPredictPage) return;
    if (typeof document !== "undefined" && document.visibilityState === "hidden") {
      const onVisible = () => {
        if (document.visibilityState === "visible") dismissNotice();
      };
      document.addEventListener("visibilitychange", onVisible);
      return () => document.removeEventListener("visibilitychange", onVisible);
    }
    dismissNotice();
  }, [notice, dismissNotice, onPredictPage]);

  if (!jobs || !notice) return null;
  if (onPredictPage && typeof document !== "undefined" && document.visibilityState === "visible") {
    return null;
  }

  const { title, body } = noticeSummary(notice);

  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed z-[60] left-4 right-4 bottom-4 sm:left-auto sm:right-6 sm:bottom-6 sm:max-w-sm rounded-xl px-4 py-3 shadow-lg"
      style={{
        background: "var(--color-surface)",
        border: "1px solid var(--color-sage)",
      }}
    >
      <div className="flex items-start gap-3">
        <span
          className="mt-1 inline-block w-2 h-2 rounded-full flex-shrink-0"
          style={{ background: "var(--color-sage)" }}
        />
        <div className="flex-1 min-w-0">
          <p
            className="font-display font-semibold text-sm leading-tight"
            style={{ color: "var(--color-ink)" }}
          >
            {title}
          </p>
          <p className="text-xs mt-0.5" style={{ color: "var(--color-muted)" }}>
            {body}
          </p>
          <Link
            href="/predict"
            onClick={() => {
              jobs.setActiveKind(notice.kind);
              jobs.dismissNotice();
            }}
            className="inline-block mt-2 text-xs font-semibold underline"
            style={{ color: "var(--color-sage)" }}
          >
            View results
          </Link>
        </div>
        <button
          onClick={jobs.dismissNotice}
          aria-label="Dismiss"
          className="flex-shrink-0 -mt-1 -mr-1 p-1 text-sm leading-none"
          style={{ color: "var(--color-faint)" }}
        >
          ✕
        </button>
      </div>
    </div>
  );
}
