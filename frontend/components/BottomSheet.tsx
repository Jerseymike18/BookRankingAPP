"use client";

/**
 * BOTTOM SHEET — a native-style modal that slides up from the bottom of the
 * screen. Used by intercepted routes (app/@modal/…), so the URL reflects the
 * sheet: Back closes it, and a reload lands on the full page instead.
 *
 * Closes on: the ✕ button, a tap on the dimmed backdrop, Escape, or dragging
 * the grab handle down. Closing plays the slide-down, then `router.back()`
 * and a `router.refresh()` so the page underneath shows anything just saved.
 *
 * On wide screens it is a bottom-anchored panel capped at max-w-3xl. Fable
 * tokens only.
 */

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

const EXIT_MS = 220;

export default function BottomSheet({
  children,
  label,
}: {
  children: React.ReactNode;
  /** Accessible name for the dialog. */
  label: string;
}) {
  const router = useRouter();
  const [closing, setClosing] = useState(false);
  const [dragY, setDragY] = useState(0);
  const [dragging, setDragging] = useState(false);
  const dragStart = useRef<number | null>(null);
  const closingRef = useRef(false);

  const close = useCallback(() => {
    if (closingRef.current) return;
    closingRef.current = true;
    setClosing(true);
    window.setTimeout(() => {
      router.back();
      // The page underneath may have changed (a book was just added).
      window.setTimeout(() => router.refresh(), 0);
    }, EXIT_MS);
  }, [router]);

  // Escape closes; lock the page behind the sheet from scrolling.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [close]);

  // Drag-to-dismiss, on the handle only (so it never fights the form's scroll).
  const onPointerDown = (e: React.PointerEvent) => {
    dragStart.current = e.clientY;
    setDragging(true);
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (dragStart.current == null) return;
    setDragY(Math.max(0, e.clientY - dragStart.current));
  };
  const onPointerUp = () => {
    if (dragStart.current == null) return;
    dragStart.current = null;
    setDragging(false);
    if (dragY > 110) close();
    else setDragY(0);
  };

  return (
    <div className="fixed inset-0 z-[70]" role="dialog" aria-modal="true" aria-label={label}>
      <button
        type="button"
        aria-label="Close"
        onClick={close}
        className={`sheet-backdrop absolute inset-0 w-full h-full cursor-default ${closing ? "is-closing" : ""}`}
        style={{ background: "rgba(26, 23, 20, 0.4)" }}
      />
      <div
        className={`sheet-panel absolute inset-x-0 bottom-0 mx-auto w-full max-w-3xl rounded-t-2xl flex flex-col ${
          closing ? "is-closing" : ""
        }`}
        style={{
          background: "var(--color-ground)",
          boxShadow: "0 -8px 30px rgba(26, 23, 20, 0.15)",
          maxHeight: "calc(100dvh - max(1.5rem, env(safe-area-inset-top)))",
          transform: dragY ? `translateY(${dragY}px)` : undefined,
          transition: !dragging && !closing ? "transform 180ms ease" : undefined,
        }}
      >
        {/* Header: grab handle (drag down to dismiss) + close button */}
        <div
          className="app-chrome relative shrink-0 pt-2 pb-1 touch-none cursor-grab"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
        >
          <div className="mx-auto h-1.5 w-10 rounded-full" style={{ background: "var(--color-rule)" }} />
          <button
            type="button"
            onClick={close}
            onPointerDown={(e) => e.stopPropagation()}
            aria-label="Close"
            className="absolute right-3 top-2 w-8 h-8 rounded-full flex items-center justify-center"
            style={{ background: "var(--color-surface-2)", color: "var(--color-muted)" }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
            </svg>
          </button>
        </div>
        <div
          className="flex-1 overflow-y-auto overscroll-contain px-4 sm:px-6 pt-2"
          style={{ paddingBottom: "calc(1.5rem + env(safe-area-inset-bottom))" }}
        >
          {children}
        </div>
      </div>
    </div>
  );
}
