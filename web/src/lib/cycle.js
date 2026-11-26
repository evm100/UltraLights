// Cycle outputs: lights with their own controller, stepped by a servo pressing its button
// (off -> modes[0] -> ... -> off). The device counts presses: state.mode is the selected
// mode (kept while off), state.at is where the lights are now. Pure: test/cycle.test.js.

/** The output's modes in button order, without "off" (from the device's info). */
export const modesOf = (o) => o?.meta?.modes ?? ["on"];

/** "alternating" -> "Alternating", "slow_fade" -> "Slow fade". */
export const modeLabel = (m) => (m ? m[0].toUpperCase() + m.slice(1).replace(/_/g, " ") : "");

/** Where the lights should be, where they are, and whether the servo is still pressing. */
export function cycleView(o, s) {
  const modes = modesOf(o);
  const mode = modes.includes(s?.mode) ? s.mode : modes[0];
  const target = s?.on ? mode : "off";
  const at = s?.at ?? target;
  return { modes, mode, target, at, busy: at !== target, presses: presses(modes, at, target) };
}

/** Presses from one position to another, always forward through off. */
export function presses(modes, from, to) {
  const pos = ["off", ...modes];
  const a = pos.indexOf(from), b = pos.indexOf(to);
  return a < 0 || b < 0 ? 0 : (b - a + pos.length) % pos.length;
}

/** One line for a tile: "Off", "On · White", or "Changing to Off…". */
export function cycleStatus(o, s) {
  const v = cycleView(o, s);
  if (v.busy) return `Changing to ${v.target === "off" ? "Off" : modeLabel(v.target)}…`;
  if (!s?.on) return "Off";
  return v.modes.length > 1 ? `On · ${modeLabel(v.mode)}` : "On";
}

/** What a mode button sends: off is on: false (the mode stays remembered). */
export const choose = (m) => (m === "off" ? { on: false } : { on: true, mode: m });
