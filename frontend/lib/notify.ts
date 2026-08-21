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

/** True when a finished run should raise an OS notification right now. */
export function shouldNotify(): boolean {
  if (notifyState() !== "granted" || notifyMuted()) return false;
  // Visible tab → the banner has it covered; a second announcement is noise.
  return typeof document !== "undefined" && document.visibilityState === "hidden";
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
): Promise<void> {
  if (!shouldNotify()) return;
  const options: NotificationOptions = {
    body,
    icon: "/icons/icon-192.png",
    badge: "/icons/icon-192.png",
    // One tag for the feature: a second finished run REPLACES the first rather
    // than stacking, so stepping away for a while can't bury the reader.
    tag: "trl-predict-run",
    data: { url: "/predict" },
  };
  try {
    const reg =
      "serviceWorker" in navigator
        ? await navigator.serviceWorker.getRegistration()
        : undefined;
    if (reg) {
      await reg.showNotification(title, options);
      return;
    }
    new Notification(title, options);
  } catch {
    /* blocked, unsupported, or the constructor threw — the banner still stands */
  }
}
