// Schedule helpers: describe a schedule document and build one from the form.
// Pure: tested in test/schedules.test.js. Days are 0 = Monday (contracts/schemas/schedule).
export const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export function whenText(w) {
  if (!w) return "";
  if (w.time) return w.time;
  const name = w.sun === "sunrise" ? "Sunrise" : "Sunset";
  const off = w.offset_min ?? 0;
  return off ? `${name} ${off > 0 ? "+" : "−"}${Math.abs(off)} min` : name;
}

export function daysText(days) {
  if (!days || days.length === 7) return "Every day";
  const s = [...days].sort((a, b) => a - b);
  if (s.join() === "0,1,2,3,4") return "Weekdays";
  if (s.join() === "5,6") return "Weekends";
  return s.map((d) => DAY_NAMES[d]).join(", ");
}

/** "Sunset to 23:00 · Weekdays" */
export function scheduleText(doc) {
  const span = doc.until ? `${whenText(doc.at)} to ${whenText(doc.until)}` : whenText(doc.at);
  return `${span} · ${daysText(doc.days)}`;
}

/** What it does, given names: { scenes: {id: name}, rooms: {id: name}, outputs: {"dev/out": name} }. */
export function actionText(doc, names) {
  const d = doc.do;
  if (d.scene != null) return `Scene ${names.scenes?.[d.scene] ?? `#${d.scene}`}`;
  if (d.room != null) return `${names.rooms?.[d.room] ?? "Room"} lights ${d.on ? "on" : "off"}`;
  const n = names.outputs?.[`${d.dev}/${d.out}`] ?? d.out;
  if (d.run_ms) return `${n}: run ${Math.round(d.run_ms / 60000)} min`;
  return `${n} ${d.on ? "on" : "off"}`;
}

/**
 * The form's fields to a schedule document.
 * f: { name, days: [..], atKind: "time"|"sunrise"|"sunset", atTime, atOffset, untilTime ("" = none),
 *      action: "scene"|"room"|"output"|"valve", target, on, runMin }
 */
export function buildSchedule(f) {
  const at = f.atKind === "time" ? { time: f.atTime } : { sun: f.atKind, ...(Number(f.atOffset) ? { offset_min: Number(f.atOffset) } : {}) };
  const doc = { v: 1, name: f.name.trim(), at };
  if (f.days.length && f.days.length < 7) doc.days = [...f.days].sort((a, b) => a - b);
  if (f.action === "scene") doc.do = { scene: Number(f.target) };
  else if (f.action === "room") doc.do = { room: Number(f.target), on: !!f.on };
  else {
    const [dev, out] = f.target.split("/");
    doc.do = f.action === "valve" ? { dev, out, run_ms: Math.round(Number(f.runMin) * 60000) } : { dev, out, on: !!f.on };
  }
  if (f.untilTime && f.action !== "valve") doc.until = { time: f.untilTime };
  return doc;
}
