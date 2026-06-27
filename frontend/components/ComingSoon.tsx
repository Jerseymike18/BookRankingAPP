export default function ComingSoon({ title }: { title: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-32 gap-4">
      <h1
        className="font-display text-3xl font-bold"
        style={{ color: "var(--color-ink)" }}
      >
        {title}
      </h1>
      <p className="text-sm" style={{ color: "var(--color-muted)" }}>
        Coming in a later stage.
      </p>
    </div>
  );
}
