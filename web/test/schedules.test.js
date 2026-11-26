import { describe, expect, it } from "vitest";
import { actionText, buildSchedule, daysText, scheduleText } from "../src/lib/schedules.js";

const form = { name: " Path ", days: [], atKind: "sunset", atTime: "", atOffset: "-15", untilTime: "23:00",
  action: "output", target: "ul-3fa2c1/path", on: true, runMin: 15 };

describe("schedules", () => {
  it("builds a document from the form", () => {
    expect(buildSchedule(form)).toEqual({ v: 1, name: "Path", at: { sun: "sunset", offset_min: -15 }, until: { time: "23:00" },
      do: { dev: "ul-3fa2c1", out: "path", on: true } });
    const valve = buildSchedule({ ...form, action: "valve", target: "ul-3fa2c1/valve", days: [4, 0, 2], atKind: "time", atTime: "06:30" });
    expect(valve).toEqual({ v: 1, name: "Path", at: { time: "06:30" }, days: [0, 2, 4], do: { dev: "ul-3fa2c1", out: "valve", run_ms: 900000 } });
  });
  it("describes it", () => {
    const doc = buildSchedule(form);
    expect(scheduleText(doc)).toBe("Sunset −15 min to 23:00 · Every day");
    expect(daysText([0, 1, 2, 3, 4])).toBe("Weekdays");
    expect(daysText([6, 5])).toBe("Weekends");
    expect(actionText(doc, { outputs: { "ul-3fa2c1/path": "Path lights" } })).toBe("Path lights on");
    expect(actionText({ do: { scene: 3 } }, { scenes: { 3: "Evening" } })).toBe("Scene Evening");
    expect(actionText({ do: { room: 2, on: false } }, { rooms: { 2: "Den" } })).toBe("Den lights off");
  });
});
