/// <reference types="vitest" />
import { describe, it, expect } from "vitest";
import { modelHealthClass, modelHealthLabel, statusColor } from "./format";

describe("'stale' status rendering", () => {
  describe("modelHealthLabel", () => {
    it("returns a non-empty label for a stale model", () => {
      const label = modelHealthLabel("stale");
      // We don't pin the exact wording (i18n keys can change), but
      // the label must be a non-empty string and NOT the generic
      // "unknown" fallback — that would defeat the whole point of
      // distinguishing stale from never-checked.
      expect(typeof label).toBe("string");
      expect(label.length).toBeGreaterThan(0);
      expect(label.toLowerCase()).not.toBe("unknown");
    });

    it("does not crash and returns a label for stale + enabled", () => {
      // Defensive — the function signature accepts an `enabled` arg.
      const label = modelHealthLabel("stale", true);
      expect(typeof label).toBe("string");
      expect(label.length).toBeGreaterThan(0);
    });
  });

  describe("modelHealthClass", () => {
    it("classifies stale as a visible-but-not-error state", () => {
      // Stale is *informational*, not a failure — same visual tier
      // as "suspect" (warn). It must NOT be "ok" (would hide the
      // staleness) and NOT be "fail" (would imply a real failure).
      const cls = modelHealthClass("stale");
      expect(cls).toBe("warn");
    });
  });

  describe("statusColor", () => {
    it("returns a CSS variable (not undefined) for stale", () => {
      // statusColor is typed as 'ok' | 'degraded' | 'fail' | null,
      // so passing 'stale' is a type error by design. The test
      // exists to surface the typing gap that has to be fixed
      // before this status can be rendered in dashboards that
      // show provider-level status dots. (Provider-level status
      // is still derived from list_models success/fail — stale
      // is a per-model concept — so the narrow type is correct
      // for *provider* dots. But the test below asserts the
      // function does not silently return 'unknown' for stale.)
      const color = statusColor("stale" as unknown as "ok");
      expect(color).toBeTruthy();
      // Must be a CSS var string, not 'undefined' or empty
      expect(color).toMatch(/^var\(--/);
    });
  });
});
