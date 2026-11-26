// Scene helpers. Pure: tested in test/scenes.test.js.
import { kelvinToRgb, css } from "./color.js";

const WARM = [242, 196, 109];

/** A swatch for one scene item: its color, its warmth, or warm white; dark when off. */
export function swatch(state) {
  if (!state?.on) return "rgba(255,255,255,0.04)";
  const rgb = state.effect?.palette?.[0] ?? state.color ?? (state.cct ? kelvinToRgb(state.cct) : WARM);
  return css(rgb, 0.35 + 0.65 * (state.level ?? 1));
}
