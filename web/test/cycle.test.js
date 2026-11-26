import { describe, expect, it } from "vitest";
import { choose, cycleStatus, cycleView, modeLabel, modesOf, presses } from "../src/lib/cycle.js";

const xmas = { kind: "cycle", meta: { modes: ["white", "color", "alternating"] } };

describe("cycle outputs (servo-pressed appliance)", () => {
  it("counts presses forward through off", () => {
    const m = modesOf(xmas);
    expect(presses(m, "off", "white")).toBe(1);
    expect(presses(m, "white", "off")).toBe(3);
    expect(presses(m, "alternating", "color")).toBe(3);
    expect(presses(m, "color", "color")).toBe(0);
    expect(presses(m, "nope", "color")).toBe(0);
  });
  it("is busy while the lights aren't where they should be", () => {
    expect(cycleView(xmas, { on: false, mode: "white", at: "color" })).toMatchObject({ target: "off", busy: true, presses: 2 });
    expect(cycleView(xmas, { on: true, mode: "white", at: "white" })).toMatchObject({ target: "white", busy: false });
    expect(cycleView(xmas, { on: true })).toMatchObject({ mode: "white", target: "white", busy: false }); // no report yet
  });
  it("tile text", () => {
    expect(cycleStatus(xmas, { on: true, mode: "color", at: "color" })).toBe("On · Color");
    expect(cycleStatus(xmas, { on: false, mode: "color", at: "off" })).toBe("Off");
    expect(cycleStatus(xmas, { on: false, mode: "color", at: "alternating" })).toBe("Changing to Off…");
    expect(cycleStatus({ meta: {} }, { on: true, mode: "on", at: "on" })).toBe("On"); // one mode: a toggle
  });
  it("labels and choices", () => {
    expect(modeLabel("slow_fade")).toBe("Slow fade");
    expect(choose("off")).toEqual({ on: false });
    expect(choose("color")).toEqual({ on: true, mode: "color" });
  });
});
