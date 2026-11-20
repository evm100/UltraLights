// Port of contracts/tools/pins.py: per-chip pin rules for a board descriptor.
// Tested against contracts/examples/board-rules (test/pins.test.js), so the builder
// shows exactly what the server and ul_board will enforce.
import CHIPS from "../../../contracts/chips.json";

export { CHIPS };

export const DEFAULT_HZ = 19000;

/** @returns {{errors: string[], warnings: string[]}} codes "<code>:<detail>", sorted, unique */
export function checkBoard(board) {
  const chip = CHIPS[board.chip];
  const errors = new Set();
  const warnings = new Set();
  if (!chip) return { errors: [`unknown_chip:${board.chip}`], warnings: [] };
  const used = new Map();

  const claim = (gpio, who, isReset = false) => {
    const g = String(gpio);
    if (gpio > chip.gpio_max) errors.add(`no_such_pin:${gpio}`);
    else if (g in chip.unusable) errors.add(`unusable_pin:${gpio}`);
    else if (g in chip.caution && !(isReset && gpio === chip.boot_button)) warnings.add(`caution_pin:${gpio}`);
    if (used.has(gpio)) errors.add(`pin_conflict:${gpio}`);
    used.set(gpio, who);
  };

  const ids = new Set();
  let pwm = 0;
  let argb = 0;
  const freqs = new Set();
  for (const ch of board.channels || []) {
    if (ids.has(ch.id)) errors.add(`duplicate_id:${ch.id}`);
    ids.add(ch.id);
    claim(ch.gpio, ch.id);
    if (ch.type === "pwm") {
      pwm += 1;
      freqs.add(ch.hz ?? DEFAULT_HZ);
    } else if (ch.type === "argb") argb += 1;
  }
  if (board.reset_button) claim(board.reset_button.gpio, "reset_button", true);
  else warnings.add("no_reset_button:");
  if (board.status_led) claim(board.status_led.gpio, "status_led");

  const lim = chip.limits;
  if (pwm > lim.pwm) errors.add(`too_many_pwm:${pwm}`);
  if (freqs.size > lim.pwm_freqs) errors.add(`too_many_pwm_freqs:${freqs.size}`);
  if (argb > lim.argb) errors.add(`too_many_argb:${argb}`);
  return { errors: [...errors].sort(), warnings: [...warnings].sort() };
}

/** Human sentence for a check code, using the chip's own notes. */
export function describe(code, chipKey) {
  const chip = CHIPS[chipKey] || {};
  const [kind, detail] = code.split(":");
  const why = (map) => (map && map[detail]) || "";
  switch (kind) {
    case "no_such_pin": return `GPIO${detail} doesn't exist on the ${chip.name}.`;
    case "unusable_pin": return `GPIO${detail} can't be used: ${why(chip.unusable)}.`;
    case "caution_pin": return `GPIO${detail}: ${why(chip.caution)}.`;
    case "pin_conflict": return `GPIO${detail} is used twice.`;
    case "duplicate_id": return `Two channels are called "${detail}".`;
    case "no_reset_button": return `No reset button: Wi-Fi can only be reset over the network.`;
    case "too_many_pwm": return `${detail} PWM channels; the ${chip.name} has ${chip.limits?.pwm}.`;
    case "too_many_pwm_freqs": return `${detail} different PWM frequencies; at most ${chip.limits?.pwm_freqs}.`;
    case "too_many_argb": return `${detail} strips; at most ${chip.limits?.argb}.`;
    case "schema": return `Descriptor format: ${code.slice(7)}`;
    default: return code;
  }
}

/** Pins worth offering in a picker: everything that exists and isn't unusable. */
export function pinOptions(chipKey) {
  const chip = CHIPS[chipKey];
  const out = [];
  for (let g = 0; g <= chip.gpio_max; g++) {
    if (String(g) in chip.unusable) continue;
    out.push({ gpio: g, note: chip.caution[String(g)] || "" });
  }
  return out;
}
