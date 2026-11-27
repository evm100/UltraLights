<script>
  import { onMount } from "svelte";
  import { homesState, loadHomes } from "../lib/homes.svelte.js";
  import { live } from "../lib/live.svelte.js";
  import { tileItems } from "../lib/tiles.js";
  import Tiles from "../components/Tiles.svelte";
  import { guestNote } from "../lib/grants.js";

  onMount(loadHomes);
  let homes = $derived(homesState.list);
  let notes = $derived(homes.map(guestNote).filter(Boolean));

  let items = $derived(tileItems(Object.values(live.devices)));
  let rooms = $derived(homes.flatMap((h) => h.rooms));
  let groups = $derived.by(() => {
    const g = rooms.map((r) => ({ name: r.name, items: items.filter((x) => x.room_id === r.id) })).filter((r) => r.items.length);
    const rest = items.filter((x) => !rooms.some((r) => r.id === x.room_id));
    if (rest.length) g.push({ name: rooms.length ? "Not in a room" : "All devices", items: rest });
    return g;
  });
  let lightsOn = $derived(items.filter((x) => x.caps.includes("level") && x.s.on).length);
  let switchesOn = $derived(items.filter((x) => !x.caps.includes("level") && x.caps.includes("onoff") && x.s.on).length);
</script>

<main class="page stack" style="gap:28px">
  <section class="hero stack">
    <h1>Lights</h1>
    <div class="row">
      <span class="chip glass"><svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2V17h5v-1.1c0-.8.4-1.5 1-2A6 6 0 0 0 12 3z" fill="none" stroke="var(--accent)" stroke-width="1.6"/></svg>{lightsOn} on</span>
      {#if items.some((x) => !x.caps.includes("level"))}
        <span class="chip glass"><svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M9 3v5M15 3v5M6 8h12v3a6 6 0 0 1-12 0zM12 17v4" fill="none" stroke="var(--info)" stroke-width="1.6"/></svg>{switchesOn} on</span>
      {/if}
      {#if !live.connected}<span class="tag warn">reconnecting</span>{:else if !live.broker}<span class="tag err">broker offline</span>{/if}
    </div>
  </section>

  {#each notes as n}<div class="glass panel muted">{n}</div>{/each}
  {#if items.length === 0 && !notes.length}
    <div class="glass panel muted">No devices yet{#if Object.keys(live.devices).length === 0}. An admin adds nodes in the node builder.{/if}</div>
  {/if}

  {#each groups as g (g.name)}
    <section class="stack">
      <span class="label">{g.name}</span>
      <Tiles items={g.items} />
    </section>
  {/each}
</main>

<style>
  .hero { padding-top: 24px; gap: 14px; }
  .hero h1 { font-size: clamp(2rem, 6vw, 3rem); }
  .chip { display: inline-flex; gap: 8px; align-items: center; padding: 8px 16px; border-radius: 999px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; font-size: 0.85rem; }
</style>
