import { describe, expect, it } from "vitest";
import { swatch } from "../src/lib/scenes.js";

describe("swatch", () => {
  it("is dark when off", () => expect(swatch({ on: false, color: [255, 0, 0] })).toBe("rgba(255,255,255,0.04)"));
  it("uses the color, then the palette", () => {
    expect(swatch({ on: true, level: 1, color: [255, 0, 0] })).toContain("255");
    expect(swatch({ on: true, effect: { name: "flow", palette: [[1, 2, 3]] }, color: [9, 9, 9] })).toMatch(/^rgba\(1,\s*2,\s*3/);
  });
});
