// What the signed-in person may do to an output, from the server's per-output `can` list
// (contracts/schemas/ws-server: view, control, color). Controls come from capabilities the
// output has AND abilities the viewer holds, so a guest without color never sees the tabs.
const CAP_ABILITY = { onoff: "control", level: "control", pulse: "control", mode: "control", color: "color", cct: "color", white: "color", effect: "color" };

export function usableCaps(o) {
  if (!o) return [];
  const can = o.can ?? [];
  return (o.caps ?? []).filter((c) => !(c in CAP_ABILITY) || can.includes(CAP_ABILITY[c]));
}

export const canControl = (o) => !!o?.can?.includes("control");
