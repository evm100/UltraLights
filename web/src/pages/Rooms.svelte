<script>
  // Home tab: one card per room with a one-tap ON/OFF for its lights (never its relays or valves)
  // and a dot per light, amber when on. Tapping the card opens the room.
  import { onMount } from "svelte";
  import { post, errText } from "../lib/api.js";
  import { live } from "../lib/live.svelte.js";
  import { homesState, loadHomes } from "../lib/homes.svelte.js";
  import { roomCards, tileItems } from "../lib/tiles.js";
  import { guestNote } from "../lib/grants.js";

  onMount(loadHomes);
  let cards = $derived(roomCards(homesState.list, tileItems(Object.values(live.devices))));
  let busy = $state({});
  let error = $state("");

  async function power(e, r) {
    e.preventDefault();
    e.stopPropagation();
    error = "";
    busy[r.id] = true;
    try { await post(`/api/homes/${r.home_id}/rooms/${r.id}/power`, { on: !r.anyOn }); }
    catch (err) { error = `${r.name}: ${errText(err)}`; }
    finally { busy[r.id] = false; }
  }
</script>

<main class="page stack" style="gap:28px">
  <section class="hero"><h1>Home</h1></section>
  {#if error}<div class="msg err" role="alert">{error}</div>{/if}

  {#each cards as c (c.home.id)}
    <section class="stack">
      {#if cards.length > 1}<span class="label">{c.home.name}</span>{/if}
      {#if guestNote(c.home)}
        <div class="glass panel muted small">{guestNote(c.home)}</div>
      {:else if !c.rooms.length}
        <div class="glass panel muted small">No rooms yet{#if c.home.can.includes("manage")}. Add them in <a href={`#/settings/${c.home.id}`}>settings</a>.{:else}.{/if}</div>
      {/if}
      <div class="rooms">
        {#each c.rooms as r (r.id)}
          <a class="room glass" class:lit={r.anyOn} href={`#/rooms/${r.id}`}>
            <div class="stack" style="gap:6px;min-width:0">
              <strong class="name">{r.name}</strong>
              <div class="dots" aria-label={`${r.on} of ${r.lights.length} lights on`}>
                {#each r.lights as x (x.key)}
                  <span class="d" class:on={x.dev.online && x.s.on} class:gone={!x.dev.online} title={x.name}></span>
                {:else}<span class="small faint">No lights</span>{/each}
              </div>
            </div>
            {#if r.lights.length}
              <button class="pill" class:on={r.anyOn} disabled={!r.control || busy[r.id]} onclick={(e) => power(e, r)}
                aria-label={`${r.name}: turn ${r.anyOn ? "off" : "on"}`}>{r.anyOn ? "ON" : "OFF"}</button>
            {/if}
          </a>
        {/each}
      </div>
    </section>
  {/each}
</main>

<style>
  .hero { padding-top: 24px; }
  .hero h1 { font-size: clamp(2rem, 6vw, 3rem); }
  .rooms { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; }
  .room { display: flex; align-items: center; justify-content: space-between; gap: 14px; padding: 16px 18px; text-decoration: none; transition: transform 0.15s ease; }
  .room:hover { transform: translateY(-2px); }
  .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 1.05rem; }
  .dots { display: flex; flex-wrap: wrap; gap: 6px; min-height: 10px; }
  .d { width: 10px; height: 10px; border-radius: 50%; border: 1px solid var(--line); background: rgba(255, 255, 255, 0.06); }
  .d.on { background: var(--accent); border-color: transparent; box-shadow: 0 0 8px rgba(242, 196, 109, 0.55); }
  .d.gone { border-style: dashed; opacity: 0.5; }
  .pill { flex: none; min-width: 64px; padding: 10px 16px; border-radius: 999px; border: 1px solid var(--line); background: rgba(0, 0, 0, 0.25);
    color: var(--muted); font-weight: 700; letter-spacing: 0.08em; cursor: pointer; }
  .pill.on { background: var(--pill); color: var(--pill-text); border-color: transparent; }
  .pill:disabled { cursor: default; opacity: 0.5; }
</style>
