<script>
  // Scenes: tap one to apply it, with Undo. People with the scenes ability save the current
  // look of a room (or the whole home) and rename, update or delete scenes.
  import { onMount, onDestroy } from "svelte";
  import { get, post, put, del, errText } from "../lib/api.js";
  import { homesState, loadHomes } from "../lib/homes.svelte.js";
  import { swatch } from "../lib/scenes.js";
  import Schedules from "../components/Schedules.svelte";

  let scenes = $state({}); // home id -> scenes
  let error = $state("");
  let toast = $state(null); // { text, run_id }
  let busy = $state(null);
  let draft = $state({}); // home id -> { name, room_id }
  let editing = $state(null); // scene id being renamed
  let toastTimer;

  // Guests don't have scenes or schedules (admins see every home with role null).
  let homes = $derived(homesState.list.filter((h) => h.role !== "guest"));

  async function load() {
    await loadHomes();
    try {
      const lists = await Promise.all(homes.map((h) => get(`/api/homes/${h.id}/scenes`)));
      const m = {};
      homes.forEach((h, i) => { m[h.id] = lists[i]; draft[h.id] ??= { name: "", room_id: "" }; });
      scenes = m;
    } catch (e) { error = errText(e); }
  }
  onMount(load);
  onDestroy(() => clearTimeout(toastTimer));

  function show(t) {
    clearTimeout(toastTimer);
    toast = t;
    toastTimer = setTimeout(() => (toast = null), 15000);
  }
  async function act(fn) {
    error = "";
    try { return await fn(); } catch (e) { error = errText(e); }
  }
  async function applyScene(s) {
    busy = s.id;
    const r = await act(() => post(`/api/scenes/${s.id}/apply`));
    busy = null;
    if (!r) return;
    const extra = r.skipped.length ? ` · ${r.skipped.length} skipped` : "";
    show({ text: `${s.name}${r.broker_offline ? " partly applied (broker offline)" : ""}${extra}`, run_id: r.run_id });
  }
  async function undoLast() {
    const r = await act(() => post(`/api/runs/${toast.run_id}/undo`));
    if (r) show({ text: "Undone", run_id: null });
  }
  async function save(h) {
    const d = draft[h.id];
    const r = await act(() => post(`/api/homes/${h.id}/scenes/capture`, { name: d.name.trim(), room_id: d.room_id ? Number(d.room_id) : null }));
    if (r) { draft[h.id] = { name: "", room_id: "" }; await load(); }
  }
  async function rename(s, name) {
    editing = null;
    if (!name.trim() || name.trim() === s.name) return;
    const doc = { v: 1, name: name.trim(), room_id: s.room_id, items: s.items };
    if (s.fade_ms != null) doc.fade_ms = s.fade_ms;
    if (await act(() => put(`/api/scenes/${s.id}`, doc))) await load();
  }
  async function recapture(s) {
    if (await act(() => post(`/api/scenes/${s.id}/recapture`))) { show({ text: `${s.name} updated to the current look`, run_id: null }); await load(); }
  }
  async function remove(s) {
    if (confirm(`Delete the scene "${s.name}"?`) && (await act(() => del(`/api/scenes/${s.id}`)))) await load();
  }
  const roomName = (h, id) => h.rooms.find((r) => r.id === id)?.name;
</script>

<main class="page stack" style="gap:28px">
  <section class="hero"><h1>Scenes and schedules</h1></section>
  {#if error}<div class="msg err" role="alert">{error}</div>{/if}

  {#each homes as h (h.id)}
    {@const list = scenes[h.id] ?? []}
    {@const mine = h.can.includes("scenes")}
    <section class="stack">
      {#if homes.length > 1}<span class="label">{h.name}</span>{/if}
      {#if !list.length}<div class="glass panel muted small">No scenes yet{mine ? ". Set the lights the way you like them, then save the look below." : "."}</div>{/if}
      <div class="scenes">
        {#each list as s (s.id)}
          <div class="scene glass">
            <button class="hit" disabled={busy === s.id} onclick={() => applyScene(s)} aria-label={`Apply ${s.name}`}>
              <div class="sw" aria-hidden="true">{#each s.items.slice(0, 8) as it}<span style={`background:${swatch(it.state)}`}></span>{/each}</div>
              {#if editing === s.id}
                <!-- svelte-ignore a11y_autofocus -->
                <input class="rename" value={s.name} maxlength="48" autofocus onclick={(e) => e.stopPropagation()}
                  onkeydown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); if (e.key === "Escape") editing = null; }}
                  onblur={(e) => rename(s, e.currentTarget.value)} />
              {:else}<strong>{s.name}</strong>{/if}
              <span class="small muted">{roomName(h, s.room_id) ?? "Whole home"} · {s.items.length} {s.items.length === 1 ? "light" : "lights"}</span>
            </button>
            {#if mine}
              <div class="tools">
                <button class="btn ghost sm" onclick={() => (editing = s.id)}>Rename</button>
                <button class="btn ghost sm" onclick={() => recapture(s)} title="Keep these lights, save how they look now">Update</button>
                <button class="btn ghost sm" onclick={() => remove(s)}>Delete</button>
              </div>
            {/if}
          </div>
        {/each}
      </div>
      {#if mine && draft[h.id]}
        <form class="row" onsubmit={(e) => { e.preventDefault(); save(h); }}>
          <input bind:value={draft[h.id].name} placeholder="Name for the current look" maxlength="48" />
          <select bind:value={draft[h.id].room_id} aria-label="Which lights">
            <option value="">Whole home</option>
            {#each h.rooms as r}<option value={r.id}>{r.name}</option>{/each}
          </select>
          <button class="btn sm solid" disabled={!draft[h.id].name.trim()}>Save scene</button>
        </form>
      {/if}
      {#key list.map((s) => s.id).join()}<Schedules home={h} scenes={list} />{/key}
    </section>
  {/each}

  {#if toast}
    <div class="toast glass" role="status">
      <span>{toast.text}</span>
      {#if toast.run_id}<button class="btn sm" onclick={undoLast}>Undo</button>{/if}
    </div>
  {/if}
</main>

<style>
  .hero { padding-top: 24px; }
  .hero h1 { font-size: clamp(2rem, 6vw, 3rem); }
  .scenes { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
  .scene { display: flex; flex-direction: column; overflow: hidden; }
  .hit { all: unset; cursor: pointer; display: flex; flex-direction: column; gap: 8px; padding: 16px; }
  .hit:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; border-radius: var(--radius); }
  .hit:disabled { opacity: 0.6; cursor: progress; }
  .sw { display: flex; gap: 4px; height: 26px; }
  .sw span { flex: 1; border-radius: 6px; border: 1px solid var(--line); }
  .rename { font-weight: 600; }
  .tools { display: flex; gap: 2px; padding: 0 8px 8px; }
  .toast { position: fixed; left: 50%; bottom: 24px; transform: translateX(-50%); display: flex; gap: 14px; align-items: center;
    padding: 10px 12px 10px 18px; border-radius: 999px; box-shadow: var(--shadow); z-index: 20; }
</style>
