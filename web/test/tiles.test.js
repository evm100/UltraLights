import { describe, expect, it } from "vitest";
import { roomCards, tileItems } from "../src/lib/tiles.js";

const out = (key, kind, room_id, on, can = ["view", "control", "color"]) => ({
  key, kind, name: key, caps: kind === "motion" ? ["motion"] : ["onoff", "level"], room_id, meta: {},
  state: { on }, desired: null, can });
const dev = (id, outputs, online = true) => ({ id, name: id, online, combined: false, outputs });

describe("room cards", () => {
  const devices = [
    dev("ul-000001", [out("a", "white", 1, true), out("b", "cct", 1, false), out("pump", "relay", 1, true), out("pir", "motion", 1, false)]),
    dev("ul-000002", [out("c", "rgb", 2, true, ["view"])]),
    dev("ul-000003", [out("d", "white", 2, true)], false),
  ];
  const homes = [{ id: 7, name: "Home", rooms: [{ id: 1, name: "Kitchen" }, { id: 2, name: "Den" }, { id: 3, name: "Empty" }] }];
  const [card] = roomCards(homes, tileItems(devices));

  it("counts lights only, never relays or sensors", () => {
    const k = card.rooms[0];
    expect(k.lights.map((x) => x.o.key)).toEqual(["a", "b"]);
    expect(k.on).toBe(1);
    expect(k.anyOn).toBe(true);
    expect(k.control).toBe(true);
  });
  it("ignores offline lights for on and control", () => {
    const d = card.rooms[1];
    expect(d.lights.length).toBe(2);
    expect(d.on).toBe(1);
    expect(d.control).toBe(false); // c is view-only, d is offline
  });
  it("keeps empty rooms", () => {
    expect(card.rooms[2]).toMatchObject({ name: "Empty", lights: [], on: 0, control: false });
  });
});
