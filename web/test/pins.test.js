import { describe, expect, it, vi } from "vitest";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { checkBoard, describe as describeCode, pinOptions } from "../src/lib/pins.js";
import { liveSender } from "../src/lib/throttle.js";

const EX = join(__dirname, "../../contracts/examples");
const load = (p) => JSON.parse(readFileSync(p, "utf8"));

describe("pin rules match contracts/tools/pins.py", () => {
  for (const f of readdirSync(join(EX, "board-rules")).filter((f) => f.endsWith(".json"))) {
    it(f, () => {
      const doc = load(join(EX, "board-rules", f));
      const expected = doc._expect;
      delete doc._expect;
      expect(checkBoard(doc)).toEqual(expected);
    });
  }
  for (const f of readdirSync(join(EX, "board")).filter((f) => f.endsWith(".json"))) {
    it(`example board ${f} has no errors`, () => {
      expect(checkBoard(load(join(EX, "board", f))).errors).toEqual([]);
    });
  }
  it("boot pin is fine for the reset button but flagged for outputs", () => {
    const b = { v: 1, chip: "esp32c3", board: "x", channels: [{ id: "a", type: "pwm", gpio: 4 }], reset_button: { gpio: 9 } };
    expect(checkBoard(b)).toEqual({ errors: [], warnings: [] });
    b.channels.push({ id: "b", type: "switch", gpio: 9 });
    expect(checkBoard(b).errors).toEqual(["pin_conflict:9"]);
  });
  it("describes codes with the chip's notes", () => {
    expect(describeCode("unusable_pin:12", "esp32c3")).toContain("SPI flash");
    expect(pinOptions("esp32c3").some((p) => p.gpio === 12)).toBe(false);
    expect(pinOptions("esp32c6").some((p) => p.gpio === 14)).toBe(false);
  });
});

describe("liveSender", () => {
  it("sends the first update now, throttles the rest and keeps the latest", () => {
    vi.useFakeTimers();
    let t = 0;
    const live = [];
    const sets = [];
    const s = liveSender((p) => live.push(p), (p) => sets.push(p), 50, () => t);
    s.update({ level: 0.1 });
    expect(live).toEqual([{ level: 0.1 }]);
    t = 10; s.update({ level: 0.2 });
    t = 20; s.update({ level: 0.3 });
    expect(live.length).toBe(1);
    t = 50; vi.advanceTimersByTime(40);
    expect(live).toEqual([{ level: 0.1 }, { level: 0.3 }]);
    t = 60; s.update({ level: 0.4 });
    s.release({ level: 0.45 });
    vi.advanceTimersByTime(100);
    expect(live.length).toBe(2); // pending live dropped, the final set wins
    expect(sets).toEqual([{ level: 0.45 }]);
    vi.useRealTimers();
  });
});
