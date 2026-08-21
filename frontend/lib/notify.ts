/**
 * BROWSER NOTIFICATIONS — the out-of-band half of "tell me when it's done".
 *
 * The in-app banner (PredictJobBanner) covers the case where the reader is still
 * somewhere in the site: they see it the moment a run lands. It cannot reach them
 * when the tab itself is in the background — another browser tab, another app, a
 * minimised PWA — which is exactly when a multi-minute run is most likely to
 * finish unwatched. That is what this is for.
 *
 * Division of labour, so the reader is never told twice:
 *   tab VISIBLE  → the in-app banner only.
 *   tab HIDDEN   → an OS notification (and the banner is still there when they
 *                  come back, until they dismiss it).
 *
 * Everything here degrades to a no-op rather than throwing: the API is absent in
 * some browsers entirely (iOS Safari outside an installed PWA), permission may be
 * denied, and a page served over plain http has no Notification at all. In every
 * one of those cases the banner is still the guarantee — this is strictly an
 * enhancement layered on top.
 */

export type NotifyState = "unsupported" | "default" | "granted" | "denied";

/* A minimal subscribable store so the UI can read permission + mute through
   useSyncExternalStore. That hook (rather than useState + a mount effect) is what
   keeps this SSR-safe without a setState inside an effect: the server snapshot is
   "unsupported", so the toggle renders nothing until the client knows better.
   Neither value can change without going through this module, so emitting from
   the two writers below is a complete subscription. */
const listeners = new Set<() => void>();

export function subscribeNotify(cb: () => void): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

function emit(): void {
  listeners.forEach((l) => l());
}

/** localStorage, not session: a notification preference should outlive the tab,
 *  unlike the run state it reports on. Nothing sensitive lives under this key. */
const MUTE_KEY = "trl:predict-notify-muted";

/** Whether this browser can show notifications at all. Must only be called
 *  client-side — every caller here is inside an effect or an event handler. */
export function notifyState(): NotifyState {
  if (typeof window === "undefined" || !("Notification" in window)) return "unsupported";
  return Notification.permission as NotifyState;
}

/** Ask the browser for permission. MUST be called from a user gesture — Safari
 *  rejects it outright otherwise, and Chrome will stop honouring it. That is why
 *  the toggle is a button the reader clicks rather than something fired on load. */
export async function requestNotifyPermission(): Promise<NotifyState> {
  if (typeof window === "undefined" || !("Notification" in window)) return "unsupported";
  try {
    return (await Notification.requestPermission()) as NotifyState;
  } catch {
    // Older Safari's callback-style signature rejects the promise form.
    return notifyState();
  } finally {
    emit();
  }
}

export function notifyMuted(): boolean {
  try {
    return window.localStorage.getItem(MUTE_KEY) === "1";
  } catch {
    return false;
  }
}

export function setNotifyMuted(muted: boolean): void {
  try {
    if (muted) window.localStorage.setItem(MUTE_KEY, "1");
    else window.localStorage.removeItem(MUTE_KEY);
  } catch {
    /* storage disabled — the toggle just won't persist */
  }
  emit();
}

/**
 * True when a finished run should raise an OS notification right now.
 *
 * "Away" is NOT just a hidden tab. `visibilityState` stays "visible" when the
 * browser window is merely behind another application, or sitting unfocused
 * beside it — so gating on hidden alone missed the most ordinary way of stepping
 * away from a long job: switching to a different app entirely. `hasFocus()` is
 * what catches that. Hidden covers a background tab and a minimised window;
 * unfocused covers everything else.
 *
 * The reader who is actually looking at the page still gets the banner alone —
 * a focused, visible document satisfies neither test.
 */
export function shouldNotify(): boolean {
  if (notifyState() !== "granted" || notifyMuted()) return false;
  if (typeof document === "undefined") return false;
  if (document.visibilityState === "hidden") return true;
  return typeof document.hasFocus === "function" && !document.hasFocus();
}

/**
 * Show one notification. Prefers the service worker's registration, because
 * `new Notification()` is not supported at all on Android Chrome (it throws an
 * illegal-constructor TypeError there) and the SW route is the only one that
 * works for an installed PWA. Falls back to the constructor when no worker is
 * registered — which is every local dev run, since ServiceWorkerRegistrar
 * deliberately unregisters there.
 *
 * `getRegistration()` rather than `navigator.serviceWorker.ready`: ready never
 * resolves when nothing is registered, which would hang this call forever.
 */
export async function showPredictNotification(
  title: string,
  body: string,
  /** Skip the away-check. Only the "send a test" control passes this: the reader
   *  is by definition looking at the page when they press it, so the normal gate
   *  would swallow the very thing they asked to see. */
  force = false,
): Promise<boolean> {
  if (!force && !shouldNotify()) return false;
  if (notifyState() !== "granted") return false;
  const options: NotificationOptions = {
    body,
    icon: "/icons/icon-192.png",
    badge: "/icons/icon-192.png",
    // One tag for the feature: a second finished run REPLACES the first rather
    // than stacking, so stepping away for a while can't bury the reader.
    tag: "trl-predict-run",
    data: { url: "/predict" },
    // The whole point is a job the reader walked away from. Desktop Chrome
    // auto-hides a notification after a few seconds, which for a run they left
    // ten minutes ago means it is gone before they look — indistinguishable from
    // never having fired. Keep it up until they deal with it.
    requireInteraction: true,
  };
  const viaWorker = async (): Promise<boolean> => {
    if (!("serviceWorker" in navigator)) return false;
    const reg = await navigator.serviceWorker.getRegistration();
    // A registration whose worker is not active yet cannot show anything.
    if (!reg || !reg.active) return false;
    await reg.showNotification(title, options);
    return true;
  };
  try {
    if (await viaWorker()) return true;
  } catch {
    /* fall through to the constructor — see below */
  }
  // Fallback, and it must be a real fallback rather than an else-branch: a
  // registration can exist and still refuse to show (worker not yet active, or
  // showNotification rejecting). Returning silently there left desktop Chrome —
  // where the plain constructor works fine — with no notification at all.
  try {
    new Notification(title, options);
    return true;
  } catch {
    // Android Chrome throws here by design; there the worker path is the only
    // one, and if it failed there is nothing further to try. The banner stands.
    return false;
  }
}
