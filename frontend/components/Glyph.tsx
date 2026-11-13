/** Small stroke icons for icon-only buttons (close, +/−, sort direction).
 *
 *  Text glyphs like ✕ − + ↑ ↓ sit wherever the font puts them — on the
 *  baseline, with side bearings, and on iOS ✕ even falls back to another font —
 *  so they never land dead-centre in a button. These are drawn on a 24×24 grid
 *  around its exact middle, so inside an `inline-flex items-center
 *  justify-center` button they are centred at every size. Same stroke style as
 *  the BottomSheet close button and the nav icons. */

export type GlyphName = "close" | "plus" | "minus" | "arrow-up" | "arrow-down";

const PATHS: Record<GlyphName, string> = {
  close: "M6 6l12 12M18 6L6 18",
  plus: "M12 5v14M5 12h14",
  minus: "M5 12h14",
  "arrow-up": "M12 19V5M6 11l6-6 6 6",
  "arrow-down": "M12 5v14M6 13l6 6 6-6",
};

export default function Glyph({
  name,
  size = 14,
  strokeWidth = 2.5,
}: {
  name: GlyphName;
  size?: number;
  strokeWidth?: number;
}) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true" className="shrink-0">
      <path d={PATHS[name]} stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
