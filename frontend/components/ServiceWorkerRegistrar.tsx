"use client";

import { useEffect } from "react";

/**
 * Registers the online-only service worker (public/sw.js) that makes the app
 * installable. Renders nothing.
 *
 * Disabled in development: a worker caching /_next/static/* across a dev server
 * that rebuilds constantly is a debugging trap. It also actively UNREGISTERS
 * any worker left over from a previous production build served on the same
 * localhost origin — otherwise a stale worker keeps shadowing `next dev`.
 */
export default function ServiceWorkerRegistrar() {
  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;

    if (process.env.NODE_ENV !== "production") {
      navigator.serviceWorker
        .getRegistrations()
        .then((registrations) =>
          registrations.forEach((registration) => registration.unregister()),
        )
        .catch(() => {
          /* nothing we can do; dev-only cleanup */
        });
      return;
    }

    // Register after load so the worker never competes with hydration or the
    // first data fetch for bandwidth.
    const register = () => {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {
        /* installability is an enhancement — never break the page over it */
      });
    };

    if (document.readyState === "complete") {
      register();
    } else {
      window.addEventListener("load", register, { once: true });
      return () => window.removeEventListener("load", register);
    }
  }, []);

  return null;
}
