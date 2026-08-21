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
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
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

  // Saved-book re-predictions from the read-queue are their own jobs, on their
  // own page — they belong in the same pill because from the reader's side there
  // is only one question ("is anything still working?").
  const repredicting = Object.values(jobs.queueRepredicts).filter(
    (j) => j.status === "running",
  );
  if (busy.length === 0 && repredicting.length === 0) return null;

  // Where the pill points has to follow what is actually running, or it drops
  // the reader on a page with nothing happening on it.
  const onlyRepredicts = busy.length === 0;
  const href = onlyRepredicts
    ? repredicting[0].kind === "nonfiction"
      ? "/read-queue?type=nonfiction"
      : "/read-queue"
    : "/predict";

  // The pill has room for one line, and several jobs at once is normal — so name
  // the count rather than truncating one of them.
  const jobCount = busy.length + repredicting.length;
  let text: string;
  if (jobCount > 1) {
    text = `${jobCount} predictions running…`;
  } else if (onlyRepredicts) {
    text = `Re-predicting ${repredicting[0].title}…`;
  } else {
    text = `${busy[0].label}…`;
  }

  // Stop is offered here ONLY when exactly one job is running. The pill is one
  // line and the whole point of this feature is that the reader is somewhere
  // else, so making them navigate back just to stop a run is the wrong answer —
  // but with two jobs live there is no way to say WHICH this would stop, and
  // cancelling the wrong one costs real money. Two jobs, and it stays a link to
  // the page where each has its own labelled Stop.
  const stopOne =
    jobCount !== 1
      ? null
      : onlyRepredicts
        ? () =>
            jobs.cancelQueueRepredict(repredicting[0].kind, repredicting[0].title)
        : () => jobs.cancelRun(busy[0].kind);

  return (
    <span
      className="inline-flex items-center gap-1 pr-1 rounded-md whitespace-nowrap"
      style={{
        background: "var(--color-sage-light)",
        border: "1px solid var(--color-sage)",
      }}
    >
      <Link
        href={href}
        onClick={() => {
          if (!onlyRepredicts) jobs.setActiveKind(busy[0].kind);
        }}
        title="A prediction is running in the background — click to watch it"
        className="inline-flex items-center gap-2 px-2.5 py-1.5 rounded-md text-xs font-medium no-underline"
        style={{ color: "var(--color-sage)" }}
      >
        <span
          className="inline-block w-2 h-2 rounded-full animate-pulse flex-shrink-0"
          style={{ background: "var(--color-sage)" }}
        />
        <span className="hidden sm:inline">{text}</span>
        <span className="sr-only sm:hidden">{text}</span>
      </Link>
      {stopOne && (
        <button
          onClick={stopOne}
          aria-label="Stop this prediction"
          title="Stop making further calls. Anything already done is kept; a call already in flight may still finish on the server."
          className="px-1.5 py-0.5 text-xs leading-none rounded"
          style={{ color: "var(--color-sage)" }}
        >
          ✕
        </button>
      )}
    </span>
  );
}

/** The one place a finished run is put into words. The banner and the OS
 *  notification both render from this, so the two can never disagree about what
 *  happened — they are the same announcement on two surfaces. */
function noticeSummary(notice: PredictNotice): {
  title: string;
  body: string;
  href: string;
} {
  const kindLabel = notice.kind === "nonfiction" ? "Nonfiction" : "Fiction";

  if (notice.type === "repredict") {
    const r = notice.report;
    // The read-queue is one route with a type toggle, so a nonfiction result has
    // to name its scope or the link would land the reader on the fiction list.
    const href =
      notice.kind === "nonfiction" ? "/read-queue?type=nonfiction" : "/read-queue";
    if (!r.changed) {
      return {
        title: `Re-predicted ${notice.title}`,
        // "No change" is a real outcome, not a failure — the prediction was
        // re-run and landed on the same answer. Say which answer.
        body: `No change — still WA ${r.new_wa.toFixed(2)}.`,
        href,
      };
    }
    const delta =
      r.d_wa != null ? ` (${r.d_wa >= 0 ? "+" : ""}${r.d_wa.toFixed(2)})` : "";
    const rank =
      r.old_rank != null
        ? ` · rank #${r.old_rank} → #${r.new_rank} of ${r.total}`
        : "";
    return {
      title: `Re-predicted ${notice.title}`,
      body: `WA ${r.old_wa?.toFixed(2) ?? "—"} → ${r.new_wa.toFixed(2)}${delta}${rank}.`,
      href,
    };
  }

  const parts: string[] = [
    `${notice.scored} book${notice.scored === 1 ? "" : "s"} scored`,
  ];
  if (notice.groundable > 0) {
    parts.push(`${notice.grounded} of ${notice.groundable} grounded with reviews`);
  }
  if (notice.failed > 0) {
    parts.push(`${notice.failed} could not be scored`);
  }
  return {
    title: `${kindLabel} prediction finished`,
    body: `${parts.join(" · ")}.`,
    href: "/predict",
  };
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
    <span className="inline-flex items-center gap-2">
      <button
        onClick={() => setNotifyMuted(!muted)}
        className={linkish}
        style={{ color: muted ? "var(--color-faint)" : "var(--color-sage)" }}
        title={
          muted
            ? "Turn notifications back on"
            : "Notifications fire when you are away from this page — another tab, another app, or a minimised window"
        }
      >
        {muted ? "Notifications off" : "Notifications on"}
      </button>
      {!muted && <NotifyTest />}
    </span>
  );
}

/**
 * The contextual opt-in, shown WHILE something is running.
 *
 * The standing toggle lives in the Predict page's request row, which is a fine
 * home for a setting and a poor one for a discovery: it is a few words of small
 * print, and a reader who never notices it gets a feature that does nothing and
 * looks broken rather than switched off. This asks at the only moment the answer
 * is obviously useful — a job is in flight and they are deciding whether to sit
 * and watch it.
 *
 * Renders nothing once the question has been answered either way (granted or
 * denied), so it can never become nagging, and the click is a real user gesture,
 * which is what browsers require of requestPermission().
 */
export function PredictNotifyPrompt({ className = "" }: { className?: string }) {
  const state = useNotifyState();
  if (state !== "default") return null;
  return (
    <button
      onClick={() => void requestNotifyPermission()}
      className={`text-xs underline decoration-dotted underline-offset-2 ${className}`}
      style={{ color: "var(--color-sage)" }}
      title="Your browser will tell you when this finishes, even if you're in another tab or app"
    >
      Notify me when this finishes
    </button>
  );
}

/**
 * Fire one notification on demand.
 *
 * This exists because the feature is invisible when it is misconfigured: a
 * missing permission, a browser that refuses, and "the run simply hasn't
 * finished yet" all look exactly alike — nothing happens. One button turns that
 * into an answer, and its result names which case it was.
 *
 * It forces past the away-check, since pressing it means the reader is looking
 * right at the page.
 */
function NotifyTest() {
  const [result, setResult] = useState<string | null>(null);

  async function send() {
    const shown = await showPredictNotification(
      "Notifications are working",
      "This is what you'll get when a prediction finishes.",
      true,
    );
    setResult(
      shown
        ? // Genuinely possible: the call succeeded and the OS still showed
          // nothing — Do Not Disturb / Focus, or notifications switched off for
          // the browser itself. Neither is visible from in here.
          "Sent. If nothing appeared, your OS is suppressing it (Do Not Disturb, or notifications turned off for this browser)."
        : "This browser wouldn't show it. The in-page banner still works.",
    );
  }

  return (
    <>
      <button
        onClick={() => void send()}
        className="text-xs underline decoration-dotted underline-offset-2"
        style={{ color: "var(--color-faint)" }}
        title="Send a test notification now"
      >
        Test
      </button>
      {result && (
        <span className="text-xs" style={{ color: "var(--color-faint)" }}>
          {result}
        </span>
      )}
    </>
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

  // A notice is redundant only on the page that already shows its result, with
  // the tab actually in front — there the banner would just cover the thing it is
  // announcing. That page differs per job type: a re-predict belongs to the
  // read-queue, so being on /predict must NOT silence it. Anywhere else, or in a
  // background tab, the banner is the whole point and stays until dismissed.
  const noticeHome = notice?.type === "repredict" ? "/read-queue" : "/predict";
  const onNoticeHome = path === noticeHome;
  useEffect(() => {
    if (!notice || !dismissNotice || !onNoticeHome) return;
    if (typeof document !== "undefined" && document.visibilityState === "hidden") {
      const onVisible = () => {
        if (document.visibilityState === "visible") dismissNotice();
      };
      document.addEventListener("visibilitychange", onVisible);
      return () => document.removeEventListener("visibilitychange", onVisible);
    }
    dismissNotice();
  }, [notice, dismissNotice, onNoticeHome]);

  if (!jobs || !notice) return null;
  if (onNoticeHome && typeof document !== "undefined" && document.visibilityState === "visible") {
    return null;
  }

  const { title, body, href } = noticeSummary(notice);

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
            href={href}
            onClick={() => {
              // Only the Predict page has a toggle this can pre-set; the
              // read-queue carries its scope in the href instead.
              if (notice.type === "run") jobs.setActiveKind(notice.kind);
              jobs.dismissNotice();
            }}
            className="inline-block mt-2 text-xs font-semibold underline"
            style={{ color: "var(--color-sage)" }}
          >
            {notice.type === "repredict" ? "View in your queue" : "View results"}
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
