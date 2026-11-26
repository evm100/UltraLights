<script>
  // A valve: fixed run lengths within its limit, a live countdown while open, and Close.
  // Valves only ever open for a set time; the device closes them itself when it ends.
  import { onDestroy } from "svelte";
  import { post, errText } from "../lib/api.js";
  import { canControl } from "../lib/access.js";
  import { stateOf } from "../lib/parts.js";
  import { presets, remaining, duration, clock } from "../lib/switches.js";

  let { dev, o, online = true, large = false } = $props();
  let limit = $derived(o.meta?.max_on_ms ?? null);
  let now = $state(Date.now());
  let left = $derived(remaining(o, now));
  // Open while the countdown runs; a report without pulse_ms_left trusts its on flag.
  let open = $derived(stateOf(o).on && (left > 0 || stateOf(o).pulse_ms_left == null));
  let busy = $state(false);
  let error = $state("");
  const timer = setInterval(() => (now = Date.now()), 1000);
  onDestroy(() => clearInterval(timer));

  async function act(path, body) {
    busy = true; error = "";
    try {
      const r = await post(`/api/devices/${dev}/outputs/${o.key}/${path}`, body);
      if (r.result && !r.result.ok) error = `Device said: ${r.result.error}`;
      else if (!r.result) error = "Sent; the device hasn't answered yet.";
    } catch (e) { error = errText(e); }
    finally { busy = false; }
  }
</script>

<div class="valve stack" class:large>
  <div class="row status">
    <span class="state" class:open>{open ? "Open" : "Closed"}</span>
    {#if open}<span class="left">{clock(left)} left</span>{/if}
  </div>
  {#if canControl(o)}
    <div class="row">
      {#if open}
        <button class="btn solid sm" disabled={busy || !online} onclick={() => act("close")}>Close now</button>
      {:else}
        {#each presets(limit) as ms (ms)}
          <button class="btn sm" disabled={busy || !online} onclick={() => act("pulse", { ms })}>{duration(ms).toUpperCase()}</button>
        {/each}
      {/if}
    </div>
  {/if}
  <span class="small faint">{limit ? `Limit ${duration(limit)}. ` : ""}Closes on its own, and on power loss.</span>
  {#if error}<span class="small err" role="alert">{error}</span>{/if}
</div>

<style>
  .valve { gap: 10px; }
  .status { gap: 10px; align-items: baseline; }
  .state { font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; font-size: 0.85rem; color: var(--muted); }
  .state.open { color: var(--info); }
  .left { font-variant-numeric: tabular-nums; font-weight: 600; }
  .large .state { font-size: 1.1rem; }
  .large .left { font-size: 2.4rem; }
  .err { color: var(--err); }
</style>
