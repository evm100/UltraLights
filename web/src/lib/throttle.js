// Live control sender: at most one live update per `interval` ms (50 ms = 20 Hz), the
// latest value always wins, and release() sends the final value as a set. Mirrors the
// server coalescer so the browser never floods the WebSocket.
export function liveSender(sendLive, sendSet, interval = 50, now = () => performance.now()) {
  let last = -Infinity;
  let pending = null;
  let timer = null;

  const flush = () => {
    timer = null;
    if (pending) {
      last = now();
      const p = pending;
      pending = null;
      sendLive(p);
    }
  };

  return {
    update(partial) {
      pending = { ...(pending || {}), ...partial };
      const wait = last + interval - now();
      if (wait <= 0 && !timer) flush();
      else if (!timer) timer = setTimeout(flush, Math.max(0, wait));
    },
    release(final) {
      if (timer) clearTimeout(timer);
      timer = null;
      pending = null;
      sendSet(final);
    },
  };
}
