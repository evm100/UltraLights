// Relays and valves. Pure: tested in test/switches.test.js.
import { stateOf } from "./parts.js";

export const SWITCH_KINDS = ["relay", "solenoid"];
const PRESET_MIN = [5, 15, 30, 60, 120];

/** Run lengths (ms) to offer for a valve: the presets within its limit, else the limit itself. */
export function presets(limitMs) {
  if (!limitMs) return PRESET_MIN.slice(0, 3).map((m) => m * 60000);
  const p = PRESET_MIN.map((m) => m * 60000).filter((ms) => ms <= limitMs);
  return p.length ? p.slice(0, 4) : [limitMs];
}

/** Time left in a valve's run, from the report's pulse_ms_left and when it arrived (state_at). */
export function remaining(o, now = Date.now()) {
  const s = stateOf(o);
  if (!s.on || s.pulse_ms_left == null) return 0;
  const at = o.state_at ?? now;
  return Math.max(0, s.pulse_ms_left - (now - at));
}

/** 5 min, 1 h 30 min, 45 s. */
export function duration(ms) {
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s} s`;
  const m = Math.round(s / 60), h = Math.floor(m / 60);
  return h ? (m % 60 ? `${h} h ${m % 60} min` : `${h} h`) : `${m} min`;
}

/** 14:05 or 1:02:09 for a countdown. */
export function clock(ms) {
  const t = Math.ceil(ms / 1000), h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}
