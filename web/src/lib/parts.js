// Combined devices (a bridged PC): its outputs are parts of one light. Home shows one tile,
// the light screen shows one set of controls plus a part picker. Pure functions, tested in
// test/parts.test.js.

/** What an output is doing: the device's report, else what we asked for. */
export const stateOf = (o) => o?.state ?? o?.desired ?? { on: false };

/** Whether a part's machine is reachable (bridge info reachable; nodes are always). */
export const reachable = (o) => o?.meta?.reachable !== false;

/** Controllable outputs of a device, in its own order (the agent's config order). */
export function partsOf(device) {
  return (device?.outputs ?? []).filter((o) => o.kind !== "motion" && o.caps.includes("onoff"));
}

/** The part whose look stands for the whole device: the first lit reachable one. */
export function lead(parts) {
  return parts.find((p) => reachable(p) && stateOf(p).on) ?? parts.find(reachable) ?? parts[0] ?? null;
}

/**
 * One state for a set of parts: on if any part is on, the highest level among lit parts,
 * the lead part's color, and whether the parts disagree on color.
 */
export function summary(parts) {
  const lit = parts.filter((p) => stateOf(p).on);
  const l = lead(parts);
  const ls = stateOf(l);
  const colors = lit.filter((p) => stateOf(p).color).map((p) => stateOf(p).color.join(","));
  return {
    on: lit.length > 0,
    level: lit.length ? Math.max(...lit.map((p) => stateOf(p).level ?? 1)) : (ls.level ?? 1),
    color: ls.color,
    mixed: new Set(colors).size > 1,
  };
}

/** Room of a combined device: the first part that has one. */
export const roomOf = (device) => partsOf(device).find((o) => o.room_id != null)?.room_id ?? null;

/**
 * Home screen tiles: one per output, except a combined device which is one tile.
 * Returns [{ dev, o, combined, room_id }].
 */
export function tiles(devices) {
  const out = [];
  for (const d of devices) {
    if (d.combined) {
      if (partsOf(d).length) out.push({ dev: d, o: null, combined: true, room_id: roomOf(d) });
    } else {
      for (const o of d.outputs) if (o.kind !== "motion") out.push({ dev: d, o, combined: false, room_id: o.room_id });
    }
  }
  return out;
}

// Small line icons for part types (24x24, stroke).
export const PART_ICONS = {
  gpu: "M3 7h18v10H3zM7 17v3M11 17v3M6 10.5h3M15.5 12a2.5 2.5 0 1 0 0 .01",
  ram: "M3 8h18v8H3zM6 8v8M10 8v8M14 8v8M18 8v8M5 16v3M19 16v3",
  board: "M4 4h16v16H4zM8 8h5v5H8zM16 8v2M16 13v3M8 16h3",
  fans: "M12 12m-8 0a8 8 0 1 0 16 0a8 8 0 1 0-16 0M12 12c0-3 1-5 3.5-5.5M12 12c3 0 5 1 5.5 3.5M12 12c0 3-1 5-3.5 5.5M12 12c-3 0-5-1-5.5-3.5",
  cooler: "M12 12m-7 0a7 7 0 1 0 14 0a7 7 0 1 0-14 0M12 12m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0",
  strip: "M3 15c3-6 6 0 9-6s6 0 9-6M5 13.5v.01M9 11v.01M13 8.5v.01M17 6v.01",
  keyboard: "M2 7h20v10H2zM6 11h.01M10 11h.01M14 11h.01M18 11h.01M7 14h10",
  mouse: "M7 9a5 5 0 0 1 10 0v6a5 5 0 0 1-10 0zM12 4v5",
  headset: "M4 15v-3a8 8 0 0 1 16 0v3M4 15h3v5H4zM17 15h3v5h-3z",
  case: "M6 2h12v20H6zM9 6h6M9 9h6M12 17m-1.5 0a1.5 1.5 0 1 0 3 0a1.5 1.5 0 1 0-3 0",
  other: "M12 12m-6 0a6 6 0 1 0 12 0a6 6 0 1 0-12 0",
  all: "M3 4h18v12H3zM8 20h8M12 16v4",
};
export const partIcon = (o) => PART_ICONS[o?.meta?.part] ?? PART_ICONS.other;
