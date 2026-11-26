<script>
  // One room: the room toggle and every light and switch in it as tiles.
  import { onMount } from "svelte";
  import { post, errText } from "../lib/api.js";
  import { live } from "../lib/live.svelte.js";
  import { homesState, loadHomes } from "../lib/homes.svelte.js";
  import { roomCards, tileItems } from "../lib/tiles.js";
  import Tiles from "../components/Tiles.svelte";
  import Switches from "../components/Switches.svelte";
  import { SWITCH_KINDS } from "../lib/switches.js";

  let { id } = $props();
  onMount(loadHomes);
  let items = $derived(tileItems(Object.values(live.devices)));
  let room = $derived(roomCards(homesState.list, items).flatMap((c) => c.rooms).find((r) => r.id === Number(id)));
  let inRoom = $derived(items.filter((x) => x.room_id === Number(id)));
  let switches = $derived(inRoom.filter((x) => !x.combined && SWITCH_KINDS.includes(x.o.kind)));
  let others = $derived(inRoom.filter((x) => !switches.includes(x)));
  let error = $state("");
  let busy = $state(false);

  async function power() {
    error = ""; busy = true;
    try { await post(`/api/homes/${room.home_id}/rooms/${room.id}/power`, { on: !room.anyOn }); }
    catch (e) { error = errText(e); }
    finally { busy = false; }
  }
</script>

<main class="page stack" style="gap:24px">
  <a class="small muted" href="#/rooms">← Rooms</a>
  {#if !room}
    {#if homesState.loaded}<p class="muted">This room isn't available.</p>{/if}
  {:else}
    <section class="row" style="justify-content:space-between">
      <div class="stack" style="gap:4px">
        <h1>{room.name}</h1>
        <span class="small muted">{room.lights.length ? `${room.on} of ${room.lights.length} lights on` : "No lights"}</span>
      </div>
      {#if room.lights.length}
        <button class="btn solid" disabled={!room.control || busy} onclick={power}>All {room.anyOn ? "off" : "on"}</button>
      {/if}
    </section>
    {#if error}<div class="msg err" role="alert">{error}</div>{/if}
    {#if others.length}<Tiles items={others} />{/if}
    {#if switches.length}
      <section class="stack"><span class="label">Switches</span><Switches items={switches} /></section>
    {/if}
    {#if !inRoom.length}<div class="glass panel muted small">Nothing in this room yet.</div>{/if}
  {/if}
</main>
