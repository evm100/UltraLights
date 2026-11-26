<script>
  // Lights with their own controller, stepped by a servo pressing its mode button. One
  // button per mode plus Off; the node presses forward until the lights get there (always a
  // full press, never left on the button), so the chosen button lights up first and the
  // "now" marker follows as each press lands. If someone used the lights' own button, "Out
  // of step?" tells the node what they're really showing, without pressing.
  import { post, errText } from "../lib/api.js";
  import { canControl } from "../lib/access.js";
  import { stateOf } from "../lib/parts.js";
  import { sendSet } from "../lib/live.svelte.js";
  import { cycleView, modeLabel, choose } from "../lib/cycle.js";

  let { dev, o, online = true } = $props();
  let v = $derived(cycleView(o, stateOf(o)));
  let can = $derived(canControl(o) && online);
  let fixing = $state(false);
  let error = $state("");
  const name = (m) => (m === "off" ? "Off" : modeLabel(m));

  async function sync(at) {
    error = "";
    try {
      const r = await post(`/api/devices/${dev}/outputs/${o.key}/sync`, { at });
      if (r.result && !r.result.ok) error = r.result.error === "busy" ? "Wait for the servo to finish, then try again." : `Device said: ${r.result.error}`;
      else if (!r.result) error = "Sent; the node hasn't answered yet.";
      else fixing = false;
    } catch (e) { error = errText(e); }
  }
</script>

<div class="cycle stack">
  <div class="modes" role="radiogroup" aria-label="Mode">
    {#each ["off", ...v.modes] as m (m)}
      <button role="radio" aria-checked={v.target === m} class:active={v.target === m} class:here={v.busy && v.at === m}
              disabled={!can} onclick={() => sendSet(dev, o.key, choose(m))}>
        {name(m)}
        {#if v.busy && v.at === m}<span class="now">now</span>{/if}
      </button>
    {/each}
  </div>
  <p class="small muted status" aria-live="polite">
    {#if v.busy}Pressing… {v.presses} more {v.presses === 1 ? "press" : "presses"} to {name(v.target)}.
    {:else if v.target === "off"}Off. On returns to {name(v.mode)}.
    {:else}Showing {name(v.target)}.{/if}
  </p>

  {#if canControl(o)}
    {#if !fixing}
      <button class="link small faint" onclick={() => { fixing = true; error = ""; }} disabled={!online}>Lights out of step?</button>
    {:else}
      <div class="fix stack">
        <span class="small">What are the lights showing right now? The node takes your word for it and doesn't press.</span>
        <div class="row">
          {#each ["off", ...v.modes] as m (m)}
            <button class="btn sm" disabled={!online || v.busy} onclick={() => sync(m)}>{name(m)}</button>
          {/each}
          <button class="btn ghost sm" onclick={() => (fixing = false)}>Cancel</button>
        </div>
      </div>
    {/if}
  {/if}
  {#if error}<span class="small err" role="alert">{error}</span>{/if}
</div>

<style>
  .cycle { gap: 12px; align-items: center; width: 100%; max-width: 360px; margin-top: 20px; }
  .modes { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; width: 100%; }
  .modes button {
    position: relative; padding: 18px 10px; border-radius: 18px; border: 1px solid var(--line);
    background: rgba(0, 0, 0, 0.3); color: var(--muted); font-weight: 700; letter-spacing: 0.08em;
    text-transform: uppercase; font-size: 0.85rem; cursor: pointer;
  }
  .modes button.active { background: var(--pill); color: var(--pill-text); border-color: transparent; }
  .modes button.here { border-color: var(--accent); border-style: dashed; }
  .modes button:disabled { cursor: default; opacity: 0.6; }
  .now { position: absolute; top: 5px; right: 9px; font-size: 0.6rem; letter-spacing: 0.1em; color: var(--accent); }
  .status { margin: 0; text-align: center; min-height: 1.3em; }
  .link { background: none; border: 0; text-decoration: underline; cursor: pointer; padding: 4px; }
  .fix { gap: 8px; align-items: center; text-align: center; }
  .fix .row { flex-wrap: wrap; justify-content: center; gap: 6px; }
  .err { color: var(--err); }
</style>
