// Guest grant helpers: the owner's form to a grant document (contracts/schemas/grant) and
// back, and plain-language descriptions. Pure: tested in test/grants.test.js.
import { DAY_NAMES, daysText } from "./schedules.js";

export const KIND_LABELS = { light: "Lights", switch: "Switches", solenoid: "Valves", sensor: "Sensors" };

export const blankGrantForm = () => ({
  label: "", whole: true, rooms: [], devices: [], control: true, color: true,
  kinds: ["light"], timed: false, days: [], from: "16:00", to: "23:00", expires: "",
});

/** Form → grant document. */
export function buildGrant(f) {
  const scope = f.whole ? ["home"] : [...f.rooms.map((r) => `room:${r}`), ...f.devices.map((d) => `device:${d}`)];
  const doc = { v: 1, scope, can: [f.control && "control", f.color && "color"].filter(Boolean), kinds: [...f.kinds] };
  if (f.label.trim()) doc.label = f.label.trim();
  if (f.timed) {
    doc.window = { from: f.from, to: f.to };
    if (f.days.length && f.days.length < 7) doc.window.days = [...f.days].sort((a, b) => a - b);
  }
  if (f.expires) doc.expires = f.expires;
  return doc;
}

/** Grant document → form (to edit a guest's grant). */
export function grantForm(doc) {
  const f = blankGrantForm();
  if (!doc) return f;
  f.label = doc.label ?? "";
  f.whole = doc.scope.includes("home");
  f.rooms = doc.scope.filter((s) => s.startsWith("room:")).map((s) => Number(s.slice(5)));
  f.devices = doc.scope.filter((s) => s.startsWith("device:")).map((s) => s.slice(7));
  f.control = doc.can.includes("control");
  f.color = doc.can.includes("color");
  f.kinds = [...doc.kinds];
  if (doc.window) { f.timed = true; f.days = doc.window.days ?? []; f.from = doc.window.from; f.to = doc.window.to; }
  f.expires = doc.expires ?? "";
  return f;
}

/** Why the form can't be saved yet, or "". */
export function grantProblem(f) {
  if (!f.whole && !f.rooms.length && !f.devices.length) return "Choose the rooms or devices the guest may use.";
  if (!f.kinds.length) return "Choose which kinds of things they may use.";
  return "";
}

/** "Control and color" / "Look only" */
export function canText(can) {
  if (can.includes("control") && can.includes("color")) return "Control and color";
  if (can.includes("control")) return "On, off and brightness";
  if (can.includes("color")) return "Color only";
  return "Look only";
}

/** "Sat 16:00–23:59" / "Every day 20:00–02:00" / "" (any time) */
export function windowText(w) {
  if (!w) return "";
  const days = w.days?.length === 1 ? DAY_NAMES[w.days[0]] : daysText(w.days);
  return w.from === w.to ? `${days}, all day` : `${days} ${w.from}–${w.to}`;
}

/** One line for the owner's people list. names: { rooms: {id: name}, devices: {id: name} } */
export function grantText(doc, names = {}) {
  if (!doc) return "No access yet";
  const where = doc.scope.includes("home") ? "Whole home"
    : doc.scope.map((s) => (s.startsWith("room:") ? names.rooms?.[Number(s.slice(5))] ?? "a room"
      : names.devices?.[s.slice(7)] ?? s.slice(7))).join(", ");
  const kinds = doc.kinds.map((k) => KIND_LABELS[k].toLowerCase()).join(", ");
  const when = [windowText(doc.window), doc.expires && `until ${doc.expires}`].filter(Boolean).join(", ");
  return [`${where} (${kinds})`, canText(doc.can).toLowerCase(), when].filter(Boolean).join(" · ");
}

/** For the guest: when their access works. "" when always. */
export function whenText(g) {
  return [windowText(g.window), g.expires && `until ${g.expires}`].filter(Boolean).join(", ");
}

/** Status tag for a grant: { text, tone } */
export function grantStatus(state, doc) {
  if (!doc) return { text: "no access", tone: "warn" };
  if (state?.expired) return { text: "expired", tone: "warn" };
  if (state && !state.active) return { text: "outside hours", tone: "" };
  return { text: "active", tone: "ok" };
}

/** What a guest is told about a home where their access isn't working now, or "". */
export function guestNote(home) {
  if (home.role !== "guest" || !("grant" in home)) return "";
  const g = home.grant;
  if (!g) return `You don't have access to anything in ${home.name} yet. The owner can share some lights with you.`;
  if (g.expired) return `Your access to ${home.name} ended on ${g.expires}.`;
  if (!g.active) return `Your access to ${home.name} works ${whenText(g)}.`;
  return "";
}
