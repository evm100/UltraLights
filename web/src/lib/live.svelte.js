// Live device state over the WebSocket (contracts/schemas/ws-server, ws-client).
export const live = $state({
  devices: {}, // id -> device view
  connected: false,
  broker: false,
  events: [], // admin: recent evt messages, newest first
  health: {}, // admin: id -> health
  lastError: null,
});

let ws = null;
let retry = 1000;
let stopped = false;
let pingTimer = null;

function apply(msg) {
  switch (msg.t) {
    case "snapshot": {
      const d = {};
      for (const dev of msg.devices) d[dev.id] = dev;
      live.devices = d;
      live.broker = !!msg.broker;
      break;
    }
    case "device":
      live.devices[msg.device.id] = msg.device;
      break;
    case "state": {
      const o = live.devices[msg.dev]?.outputs.find((x) => x.key === msg.out);
      if (o) o.state = msg.state;
      break;
    }
    case "health":
      live.health[msg.dev] = msg.health;
      if (live.devices[msg.dev]?.admin) live.devices[msg.dev].admin.health = msg.health;
      break;
    case "evt":
      live.events = [{ at: new Date(), dev: msg.dev, evt: msg.evt }, ...live.events].slice(0, 100);
      break;
    case "error":
      live.lastError = { at: new Date(), ...msg };
      break;
  }
}

export function connect() {
  stopped = false;
  if (ws && ws.readyState <= 1) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/api/ws`);
  ws.onopen = () => {
    live.connected = true;
    retry = 1000;
    clearInterval(pingTimer);
    pingTimer = setInterval(() => send({ t: "ping" }), 25000);
  };
  ws.onmessage = (e) => apply(JSON.parse(e.data));
  ws.onclose = () => {
    live.connected = false;
    clearInterval(pingTimer);
    if (!stopped) setTimeout(connect, retry);
    retry = Math.min(retry * 2, 15000);
  };
}

export function disconnect() {
  stopped = true;
  ws?.close();
  ws = null;
}

function send(msg) {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify(msg));
}

export const sendLive = (dev, out, state) => send({ t: "live", dev, out, state });
export const sendSet = (dev, out, state) => send({ t: "set", dev, out, state });

/** What the output is doing: the device's report, else what we asked for. */
export function current(o) {
  return o?.state ?? o?.desired ?? { on: false };
}
