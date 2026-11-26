// Tile and room models for Home, Rooms and a room's page. Pure: tested in test/tiles.test.js.
import { tiles, partsOf, summary, stateOf } from "./parts.js";
import { canControl } from "./access.js";

export const LIGHT_KINDS = ["white", "cct", "rgb", "rgbw", "rgbcct", "argb"];

/** One tile per output, except a combined device (a bridged PC), which is one tile for all its parts. */
export function tileItems(devices) {
  return tiles(devices).map((x) => {
    if (!x.combined) {
      return { ...x, key: x.dev.id + "/" + x.o.key, name: x.o.name, caps: x.o.caps, light: LIGHT_KINDS.includes(x.o.kind),
               control: canControl(x.o), s: stateOf(x.o), href: `#/light/${x.dev.id}/${x.o.key}` };
    }
    const parts = partsOf(x.dev);
    return { ...x, key: x.dev.id, name: x.dev.name, caps: ["onoff", "level", "color"], light: true,
             control: parts.some(canControl), s: summary(parts), parts, href: `#/light/${x.dev.id}` };
  });
}

/**
 * Room cards: per home, each room with its lights (the room toggle switches lights only, as
 * the server does), how many are on, and whether the viewer can switch any of them.
 */
export function roomCards(homes, items) {
  return homes.map((h) => ({
    home: h,
    rooms: h.rooms.map((r) => {
      const lights = items.filter((x) => x.room_id === r.id && x.light);
      const on = lights.filter((x) => x.dev.online && x.s.on).length;
      return { ...r, home_id: h.id, lights, on, anyOn: on > 0, control: lights.some((x) => x.control && x.dev.online) };
    }),
  }));
}
