import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { usableCaps } from "../src/lib/access.js";

const snap = JSON.parse(readFileSync(join(__dirname, "../../contracts/examples/ws-server/snapshot.json"), "utf8"));

describe("usableCaps", () => {
  const o = { caps: ["onoff", "level", "color", "cct", "motion"] };
  it("keeps everything with full abilities", () => {
    expect(usableCaps({ ...o, can: ["view", "control", "color"] })).toEqual(o.caps);
  });
  it("drops color controls without the color ability", () => {
    expect(usableCaps({ ...o, can: ["view", "control"] })).toEqual(["onoff", "level", "motion"]);
  });
  it("leaves a view-only output with nothing to drive", () => {
    expect(usableCaps({ ...o, can: ["view"] })).toEqual(["motion"]);
  });
  it("reads the contract example", () => {
    const lamp = snap.devices[0].outputs[0];
    expect(usableCaps(lamp)).toEqual(lamp.caps);
  });
});
