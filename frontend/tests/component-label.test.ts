import { describe, it, expect } from "vitest";
import { componentLabel } from "../lib/format";

describe("componentLabel", () => {
  it("shows the fiction display names over the stored keys", () => {
    expect(componentLabel("Narration")).toBe("Voice");
    expect(componentLabel("Depth2")).toBe("World Depth");
    expect(componentLabel("Depth")).toBe("Depth");
  });
  it("keeps the nonfiction renames separate", () => {
    expect(componentLabel("Entertainment", "nonfiction")).toBe("Enjoyment");
    expect(componentLabel("Depth2", "nonfiction")).toBe("Depth2");
  });
});
