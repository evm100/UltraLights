import { describe, expect, it } from "vitest";
import { clock, duration, presets, remaining } from "../src/lib/switches.js";

const MIN = 60000;

describe("valves", () => {
  it("offers presets within the limit", () => {
    expect(presets(45 * MIN)).toEqual([5 * MIN, 15 * MIN, 30 * MIN]);
    expect(presets(120 * MIN)).toEqual([5 * MIN, 15 * MIN, 30 * MIN, 60 * MIN]);
    expect(presets(2 * MIN)).toEqual([2 * MIN]);
  });
  it("counts down from when the report arrived", () => {
    const o = { state: { on: true, pulse_ms_left: 10 * MIN }, state_at: 1000 };
    expect(remaining(o, 1000 + 4 * MIN)).toBe(6 * MIN);
    expect(remaining(o, 1000 + 20 * MIN)).toBe(0);
    expect(remaining({ state: { on: false, pulse_ms_left: 5 } }, 0)).toBe(0);
  });
  it("formats", () => {
    expect(duration(45 * MIN)).toBe("45 min");
    expect(duration(90 * MIN)).toBe("1 h 30 min");
    expect(duration(120 * MIN)).toBe("2 h");
    expect(clock(14 * MIN + 5000)).toBe("14:05");
    expect(clock(62 * MIN + 9000)).toBe("1:02:09");
  });
});
