import { api, setCsrf, setUnauthorizedHandler } from "./api.js";
import { connect, disconnect } from "./live.svelte.js";

export const session = $state({ user: null, ready: false });

export async function restore() {
  try {
    const me = await api("GET", "/api/auth/me");
    signedIn(me);
  } catch {
    session.user = null;
  }
  session.ready = true;
}

function signedIn(me) {
  setCsrf(me.csrf);
  session.user = me;
  connect();
}

export async function login(username, password) {
  signedIn(await api("POST", "/api/auth/login", { username, password }));
}

export async function logout() {
  try {
    await api("POST", "/api/auth/logout");
  } finally {
    disconnect();
    setCsrf(null);
    session.user = null;
  }
}

setUnauthorizedHandler(() => {
  disconnect();
  session.user = null;
});
