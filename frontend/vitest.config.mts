import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

/* Unit tests for the frontend's PURE logic.
 *
 * Scope on purpose: `environment: "node"`, no jsdom, no React Testing Library.
 * What is worth guarding here is the code that computes something and can be
 * silently wrong — the session-snapshot codec, nav active-state resolution,
 * the analytics math — not the markup, which typecheck + review already cover
 * and which would cost a rendering stack to assert on. A component test needs
 * `// @vitest-environment jsdom` plus jsdom + @testing-library/react; add them
 * the day there is a component whose behaviour genuinely needs it.
 *
 * `include` is narrowed to tests/ so nothing under app/ or lib/ can be picked
 * up as a test by accident. */
export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL(".", import.meta.url)) },
  },
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts"],
  },
});
