import { describe, expect, test } from "vitest";
import { guestNote, blankGrantForm, buildGrant, grantForm, grantProblem, grantStatus, grantText, windowText } from "../src/lib/grants.js";

describe("grants", () => {
  test("form to document and back", () => {
    const f = { ...blankGrantForm(), label: " Pool party ", whole: false, rooms: [3], devices: ["ul-91c0e2"], color: false,
                kinds: ["light", "switch"], timed: true, days: [5], from: "16:00", to: "23:59", expires: "2026-11-30" };
    const doc = buildGrant(f);
    expect(doc).toEqual({ v: 1, label: "Pool party", scope: ["room:3", "device:ul-91c0e2"], can: ["control"], kinds: ["light", "switch"],
                          window: { from: "16:00", to: "23:59", days: [5] }, expires: "2026-11-30" });
    expect(buildGrant(grantForm(doc))).toEqual(doc);
  });

  test("defaults: whole home, lights, control and color, any time", () => {
    expect(buildGrant(blankGrantForm())).toEqual({ v: 1, scope: ["home"], can: ["control", "color"], kinds: ["light"] });
    const all = { ...blankGrantForm(), timed: true, days: [0, 1, 2, 3, 4, 5, 6] };
    expect(buildGrant(all).window).toEqual({ from: "16:00", to: "23:00" }); // every day = no days list
  });

  test("problems", () => {
    expect(grantProblem({ ...blankGrantForm(), whole: false })).toMatch(/rooms or devices/);
    expect(grantProblem({ ...blankGrantForm(), kinds: [] })).toMatch(/kinds/);
    expect(grantProblem(blankGrantForm())).toBe("");
  });

  test("descriptions", () => {
    const doc = { v: 1, scope: ["room:3", "device:ul-91c0e2"], can: [], kinds: ["light"], window: { days: [5], from: "16:00", to: "23:59" }, expires: "2026-11-30" };
    expect(grantText(doc, { rooms: { 3: "Patio" }, devices: { "ul-91c0e2": "Pool node" } }))
      .toBe("Patio, Pool node (lights) · look only · Sat 16:00–23:59, until 2026-11-30");
    expect(grantText(null)).toBe("No access yet");
    expect(windowText({ from: "20:00", to: "20:00" })).toBe("Every day, all day");
    expect(grantStatus({ active: false, expired: true }, doc).text).toBe("expired");
    expect(grantStatus({ active: false, expired: false }, doc).text).toBe("outside hours");
    expect(grantStatus({ active: true }, doc).tone).toBe("ok");
  });

  test("guest notes", () => {
    const h = { name: "Lakeview", role: "guest" };
    expect(guestNote({ ...h, grant: null })).toMatch(/anything in Lakeview yet/);
    expect(guestNote({ ...h, grant: { expired: true, active: false, expires: "2026-11-30" } })).toBe("Your access to Lakeview ended on 2026-11-30.");
    expect(guestNote({ ...h, grant: { active: false, expired: false, window: { days: [5], from: "16:00", to: "23:59" } } }))
      .toBe("Your access to Lakeview works Sat 16:00–23:59.");
    expect(guestNote({ ...h, grant: { active: true } })).toBe("");
    expect(guestNote({ name: "Mine", role: "owner" })).toBe("");
  });
});
