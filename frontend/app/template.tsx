// A template (unlike a layout) re-mounts on every navigation, so wrapping the
// page here gives each screen a short fade-in — the app-style page change.
// Opacity only; see .page-enter in globals.css.
export default function Template({ children }: { children: React.ReactNode }) {
  return <div className="page-enter">{children}</div>;
}
