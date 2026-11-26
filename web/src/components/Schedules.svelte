<script>
  // One home's schedules: what runs when, the next run, on/off, try it now, delete, and a
  // form for a new one. Times are the home's time zone (Settings → Place).
  import { onMount } from "svelte";
  import { get, post, put, del, errText } from "../lib/api.js";
  import { live } from "../lib/live.svelte.js";
  import { LIGHT_KINDS } from "../lib/tiles.js";
  import { DAY_NAMES, actionText, buildSchedule, scheduleText } from "../lib/schedules.js";

  let { home, scenes = [] } = $props();
  let list = $state([]);
  let error = $state("");
  let note = $state("");
  let adding = $state(false);
  const blank = () => ({ name: "", days: [], atKind: "time", atTime: "19:00", atOffset: 0, untilTime: "",
                         action: "scene", target: "", on: true, runMin: 15 });
  let f = $state(blank());
  let mine = $derived(home.can.includes("scenes"));

  // Outputs of this home a schedule can drive, from the live device list.
  let outputs = $derived(Object.values(live.devices).filter((d) => d.home_id === home.id)
    .flatMap((d) => d.outputs.map((o) => ({ key: `${d.id}/${o.key}`, name: o.name, kind: o.kind }))));
  let switchable = $derived(outputs.filter((o) => o.kind === "relay" || LIGHT_KINDS.includes(o.kind)));
  let valves = $derived(outputs.filter((o) => o.kind === "solenoid"));
  let names = $derived({
    scenes: Object.fromEntries(scenes.map((s) => [s.id, s.name])),
    rooms: Object.fromEntries(home.rooms.map((r) => [r.id, r.name])),
    outputs: Object.fromEntries(outputs.map((o) => [o.key, o.name])),
  });

  async function load() {
    try { list = await get(`/api/homes/${home.id}/schedules`); } catch (e) { error = errText(e); }
  }
  onMount(load);

  async function act(fn, ok) {
    error = ""; note = "";
    try { const r = await fn(); if (ok) note = ok; await load(); return r ?? true; }
    catch (e) { error = errText(e); }
  }
  const toggle = (s) => act(() => put(`/api/schedules/${s.id}`, { ...s.doc, enabled: !s.enabled }));
  const runNow = (s) => act(() => post(`/api/schedules/${s.id}/run`), `${s.name}: done.`);
  const remove = (s) => confirm(`Delete the schedule "${s.name}"?`) && act(() => del(`/api/schedules/${s.id}`));
  async function create(e) {
    e.preventDefault();
    if (await act(() => post(`/api/homes/${home.id}/schedules`, buildSchedule(f)), "Schedule saved.")) { f = blank(); adding = false; }
  }
  function day(d) { f.days = f.days.includes(d) ? f.days.filter((x) => x !== d) : [...f.days, d]; }
  const nextText = (s) => (s.next ? `${s.next.phase === "start" ? "Next" : "Ends"} ${new Date(s.next.at).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" })}` : "");
</script>

<div class="stack" style="gap:10px">
  <span class="label">Schedules</span>
  {#if error}<div class="msg err" role="alert">{error}</div>{/if}
  {#if note}<div class="msg ok" role="status">{note}</div>{/if}
  {#if !home.timezone}
    <div class="glass panel small muted">Schedules need the home's time zone{home.can.includes("manage") ? "" : ", which an owner sets"}.
      {#if home.can.includes("manage")}Set it in <a href={`#/settings/${home.id}`}>settings</a>.{/if}</div>
  {/if}

  {#if list.length}
    <div class="glass panel rows">
      {#each list as s (s.id)}
        <div class="sched" class:off={!s.enabled}>
          <div class="stack" style="gap:2px;min-width:0">
            <strong>{s.name}</strong>
            <span class="small">{scheduleText(s.doc)} · {actionText(s.doc, names)}</span>
            <span class="small faint">{s.enabled ? nextText(s) : "Paused"}{s.last_result?.error ? ` · last run: ${s.last_result.error}` : ""}</span>
          </div>
          {#if mine}
            <div class="row tools">
              <button class="btn ghost sm" onclick={() => toggle(s)}>{s.enabled ? "Pause" : "Resume"}</button>
              <button class="btn ghost sm" onclick={() => runNow(s)}>Run now</button>
              <button class="btn ghost sm" onclick={() => remove(s)}>Delete</button>
            </div>
          {/if}
        </div>
      {/each}
    </div>
  {:else if home.timezone}
    <p class="small muted">No schedules yet.</p>
  {/if}

  {#if mine && home.timezone}
    {#if !adding}
      <div><button class="btn sm" onclick={() => (adding = true)}>New schedule</button></div>
    {:else}
      <form class="glass panel stack" onsubmit={create}>
        <input bind:value={f.name} placeholder="Name" maxlength="48" required />
        <div class="row days" role="group" aria-label="Days (none = every day)">
          {#each DAY_NAMES as n, i}<button type="button" class:active={f.days.includes(i)} aria-pressed={f.days.includes(i)} onclick={() => day(i)}>{n}</button>{/each}
          <span class="small faint">{f.days.length ? "" : "every day"}</span>
        </div>
        <div class="row">
          <label class="mini">Starts
            <select bind:value={f.atKind}>
              <option value="time">at a time</option>
              <option value="sunrise" disabled={!home.has_location}>at sunrise</option>
              <option value="sunset" disabled={!home.has_location}>at sunset</option>
            </select>
          </label>
          {#if f.atKind === "time"}<input type="time" bind:value={f.atTime} required aria-label="Start time" />
          {:else}<label class="mini">offset (min)<input type="number" min="-180" max="180" bind:value={f.atOffset} /></label>{/if}
          {#if f.action !== "valve"}<label class="mini">until (optional)<input type="time" bind:value={f.untilTime} /></label>{/if}
        </div>
        {#if !home.has_location}<span class="small faint">Sunrise and sunset need the home's location in settings.</span>{/if}
        <div class="row">
          <select bind:value={f.action} onchange={() => (f.target = "")} aria-label="What it does">
            <option value="scene">Apply a scene</option>
            <option value="room">A room's lights</option>
            <option value="output">One light or relay</option>
            {#if valves.length}<option value="valve">Run a valve</option>{/if}
          </select>
          <select bind:value={f.target} required aria-label="Which">
            <option value="" disabled>Choose…</option>
            {#if f.action === "scene"}{#each scenes as s}<option value={String(s.id)}>{s.name}</option>{/each}
            {:else if f.action === "room"}{#each home.rooms as r}<option value={String(r.id)}>{r.name}</option>{/each}
            {:else if f.action === "output"}{#each switchable as o}<option value={o.key}>{o.name}</option>{/each}
            {:else}{#each valves as o}<option value={o.key}>{o.name}</option>{/each}{/if}
          </select>
          {#if f.action === "room" || f.action === "output"}
            <select bind:value={f.on} aria-label="On or off"><option value={true}>on</option><option value={false}>off</option></select>
          {:else if f.action === "valve"}
            <label class="mini">for (min)<input type="number" min="1" max="120" bind:value={f.runMin} required /></label>
          {/if}
        </div>
        <div class="row">
          <button class="btn sm solid" disabled={!f.name.trim() || !f.target}>Save schedule</button>
          <button type="button" class="btn sm ghost" onclick={() => { adding = false; f = blank(); }}>Cancel</button>
        </div>
      </form>
    {/if}
  {/if}
</div>

<style>
  .rows { display: flex; flex-direction: column; padding: 4px 16px; }
  .sched { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 12px 0; border-top: 1px solid var(--line); flex-wrap: wrap; }
  .sched:first-child { border-top: 0; }
  .sched.off { opacity: 0.6; }
  .tools { gap: 2px; }
  .days button { border: 1px solid var(--line); background: transparent; color: var(--muted); border-radius: 999px; padding: 6px 10px; cursor: pointer; font-size: 0.85rem; }
  .days button.active { background: var(--pill); color: var(--pill-text); border-color: transparent; }
  .mini { display: inline-flex; gap: 6px; align-items: center; font-size: 0.85rem; color: var(--muted); }
  .mini input { width: 6em; }
</style>
