<script>
  // What a guest may use: where (whole home, rooms or devices), which kinds, what they may
  // do, and when. Edits a form from lib/grants.js; the parent saves buildGrant(f).
  import { DAY_NAMES } from "../lib/schedules.js";
  import { KIND_LABELS } from "../lib/grants.js";

  let { f = $bindable(), rooms = [], devices = [], timezone = null } = $props();

  const toggle = (list, v) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
</script>

<div class="stack grant">
  <input bind:value={f.label} placeholder="Label, e.g. Pool party (optional)" maxlength="48" aria-label="Label" />

  <fieldset class="stack">
    <legend class="small muted">Where</legend>
    <label class="opt"><input type="radio" checked={f.whole} onchange={() => (f.whole = true)} /> The whole home</label>
    <label class="opt"><input type="radio" checked={!f.whole} onchange={() => (f.whole = false)} /> Only some rooms or devices</label>
    {#if !f.whole}
      <div class="chips" role="group" aria-label="Rooms">
        {#each rooms as r (r.id)}
          <button type="button" class:active={f.rooms.includes(r.id)} aria-pressed={f.rooms.includes(r.id)}
            onclick={() => (f.rooms = toggle(f.rooms, r.id))}>{r.name}</button>
        {:else}<span class="small faint">No rooms yet.</span>{/each}
      </div>
      {#if devices.length}
        <div class="chips" role="group" aria-label="Devices">
          {#each devices as d (d.id)}
            <button type="button" class:active={f.devices.includes(d.id)} aria-pressed={f.devices.includes(d.id)}
              onclick={() => (f.devices = toggle(f.devices, d.id))}>{d.name}</button>
          {/each}
        </div>
      {/if}
      <span class="small faint">A room covers whatever is assigned to it, now and later. The guest won't see other rooms' names.</span>
    {/if}
  </fieldset>

  <fieldset class="stack">
    <legend class="small muted">Which things</legend>
    <div class="chips" role="group" aria-label="Kinds">
      {#each Object.entries(KIND_LABELS) as [k, label]}
        <button type="button" class:active={f.kinds.includes(k)} aria-pressed={f.kinds.includes(k)}
          onclick={() => (f.kinds = toggle(f.kinds, k))}>{label}</button>
      {/each}
    </div>
    {#if f.kinds.includes("solenoid")}<span class="small warn-text">Valves open water lines. Only include them for someone you'd trust with the garden.</span>{/if}
  </fieldset>

  <fieldset class="stack">
    <legend class="small muted">What they may do</legend>
    <label class="opt"><input type="checkbox" bind:checked={f.control} /> Switch on and off, dim{f.kinds.includes("solenoid") ? ", run valves" : ""}</label>
    <label class="opt"><input type="checkbox" bind:checked={f.color} /> Change color, warmth and effects</label>
    {#if !f.control && !f.color}<span class="small faint">Neither: they can look but not touch.</span>{/if}
  </fieldset>

  <fieldset class="stack">
    <legend class="small muted">When</legend>
    <label class="opt"><input type="checkbox" bind:checked={f.timed} disabled={!timezone} /> Only at certain hours</label>
    {#if f.timed}
      <div class="chips" role="group" aria-label="Days (none = every day)">
        {#each DAY_NAMES as n, i}<button type="button" class:active={f.days.includes(i)} aria-pressed={f.days.includes(i)}
          onclick={() => (f.days = toggle(f.days, i))}>{n}</button>{/each}
        <span class="small faint">{f.days.length ? "" : "every day"}</span>
      </div>
      <div class="row">
        <label class="mini">from<input type="time" bind:value={f.from} required /></label>
        <label class="mini">to<input type="time" bind:value={f.to} required /></label>
        {#if f.to < f.from}<span class="small faint">ends the next morning</span>{/if}
      </div>
    {/if}
    <label class="mini">Last day (optional)<input type="date" bind:value={f.expires} disabled={!timezone} /></label>
    {#if timezone}<span class="small faint">Times are {timezone}.</span>
    {:else}<span class="small faint">Hours and an end date need the home's time zone (Place, above).</span>{/if}
  </fieldset>
</div>

<style>
  fieldset { border: 0; padding: 0; margin: 0; gap: 8px; }
  legend { padding: 0; margin-bottom: 6px; }
  .opt { display: flex; gap: 8px; align-items: center; font-size: 0.9rem; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
  .chips button { border: 1px solid var(--line); background: transparent; color: var(--muted); border-radius: 999px; padding: 6px 12px; cursor: pointer; font-size: 0.85rem; }
  .chips button.active { background: var(--pill); color: var(--pill-text); border-color: transparent; }
  .mini { display: inline-flex; gap: 6px; align-items: center; font-size: 0.85rem; color: var(--muted); }
  .warn-text { color: var(--warn, #e0a040); }
</style>
