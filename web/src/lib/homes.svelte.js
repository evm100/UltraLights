// The homes (and their rooms) the signed-in person can see, from /api/homes, shared by every
// screen. Call loadHomes() after changing rooms so all screens pick the change up.
import { get } from "./api.js";

export const homesState = $state({ list: [], loaded: false });

export async function loadHomes() {
  try {
    homesState.list = await get("/api/homes");
  } catch {
    homesState.list = [];
  }
  homesState.loaded = true;
}

/** Homes where the person may change something (rooms, devices or people). */
export const managed = (homes) => homes.filter((h) => h.can.includes("manage") || h.can.includes("people"));
