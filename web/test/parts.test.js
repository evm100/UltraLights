import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { lead, partsOf, roomOf, summary, tiles } from "../src/lib/parts.js";

const EX = join(__dirname, "../../contracts/examples/ws-server");
const load = (f) => JSON.parse(readFileSync(join(EX, f), "utf8"));
const part = (key, state, extra = {}) => ({ key, kind: "rgb", name: key, caps: ["onoff", "level", "color"], room_id: null,
  meta: { reachable: true, ...extra }, state, desired: null });

describe("combined devices", () => {
  it("are one tile, in the room of their first placed part", () => {
    const snap = [...load("snapshot.json").devices, ...load("snapshot-bridge.json").devices];
    const t = tiles(snap);
    expect(t.filter((x) => x.combined)).toHaveLength(1);
    expect(t.find((x) => x.combined).room_id).toBe(2);
    expect(t.filter((x) => !x.combined).map((x) => x.o.key)).toEqual(["main", "fan"]);
  });

  it("summarise their parts: any on, highest lit level, lead color, mixed colors", () => {
    const parts = [
      part("gpu", { on: false, level: 1, color: [0, 0, 255], seq: 1 }),
      part("ram", { on: true, level: 0.3, color: [255, 0, 0], seq: 1 }),
      part("fans", { on: true, level: 0.8, color: [0, 255, 0], seq: 1 }),
    ];
    expect(summary(parts)).toEqual({ on: true, level: 0.8, color: [255, 0, 0], mixed: true });
    expect(lead(parts).key).toBe("ram");
    parts[2].state.color = [255, 0, 0];
    expect(summary(parts).mixed).toBe(false);
  });

  it("skip unreachable parts when choosing the lead, and report off when nothing is lit", () => {
    const parts = [
      part("gpu", { on: true, level: 1, color: [0, 0, 255], seq: 1 }, { reachable: false }),
      part("ram", { on: false, level: 0.5, color: [255, 0, 0], seq: 1 }),
    ];
    expect(lead(parts).key).toBe("ram");
    expect(summary([parts[1]])).toEqual({ on: false, level: 0.5, color: [255, 0, 0], mixed: false });
  });

  it("list only switchable outputs as parts", () => {
    const d = { outputs: [part("gpu", null), { key: "pir", kind: "motion", caps: ["motion"], room_id: 4 }] };
    expect(partsOf(d).map((p) => p.key)).toEqual(["gpu"]);
    expect(roomOf(d)).toBe(null);
  });
});
