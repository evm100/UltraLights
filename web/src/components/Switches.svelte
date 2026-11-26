<script>
  // Relays (OFF / ON) and valves (fixed run lengths) as rows: the switches part of a room.
  import { sendSet } from "../lib/live.svelte.js";
  import { stateOf } from "../lib/parts.js";
  import { canControl } from "../lib/access.js";
  import Valve from "./Valve.svelte";

  let { items } = $props(); // [{ dev, o }] from tileItems
</script>

<div class="switches glass panel">
  {#each items as x (x.key)}
    {@const on = stateOf(x.o).on}
    <div class="sw">
      <div class="stack" style="gap:2px;min-width:0">
        <a class="name" href={x.href}>{x.name}</a>
        <span class="small muted">{x.o.kind === "solenoid" ? "Valve" : "Relay"}{x.dev.online ? "" : " · offline"}</span>
      </div>
      {#if x.o.kind === "solenoid"}
        <Valve dev={x.dev.id} o={x.o} online={x.dev.online} />
      {:else}
        <div class="seg" role="group" aria-label={`${x.name} power`}>
          <button class:active={!on} aria-pressed={!on} disabled={!x.dev.online || !canControl(x.o)} onclick={() => sendSet(x.dev.id, x.o.key, { on: false })}>OFF</button>
          <button class:active={on} aria-pressed={on} disabled={!x.dev.online || !canControl(x.o)} onclick={() => sendSet(x.dev.id, x.o.key, { on: true })}>ON</button>
        </div>
      {/if}
    </div>
  {/each}
</div>

<style>
  .switches { display: flex; flex-direction: column; gap: 0; padding: 4px 18px; }
  .sw { display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 14px 0; border-top: 1px solid var(--line); flex-wrap: wrap; }
  .sw:first-child { border-top: 0; }
  .name { font-weight: 600; text-decoration: none; }
  .seg { display: inline-flex; border: 1px solid var(--line); border-radius: 999px; overflow: hidden; }
  .seg button { border: 0; background: transparent; color: var(--muted); padding: 8px 16px; font-weight: 700; letter-spacing: 0.08em; cursor: pointer; }
  .seg button.active { background: var(--pill); color: var(--pill-text); }
  .seg button:disabled { cursor: default; opacity: 0.6; }
</style>
