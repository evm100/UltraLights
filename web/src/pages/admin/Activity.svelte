<script>
  import { onMount } from "svelte";
  import { get, errText } from "../../lib/api.js";
  import { live } from "../../lib/live.svelte.js";
  import { date } from "../../lib/format.js";

  let rows = $state([]);
  let error = $state("");
  const load = async () => { try { rows = await get("/api/admin/audit?limit=300"); } catch (e) { error = errText(e); } };
  onMount(load);
</script>

<div class="row"><h2>Activity</h2><span class="spacer"></span><button class="btn sm" onclick={load}>Refresh</button></div>
{#if error}<div class="msg err">{error}</div>{/if}

{#if live.events.length}
  <div class="glass panel stack">
    <span class="label">Live device events</span>
    {#each live.events.slice(0, 15) as e}
      <div class="small"><span class="muted">{e.at.toLocaleTimeString()}</span> <a class="mono" href={`#/admin/nodes/${e.dev}`}>{e.dev}</a> <span class="mono">{JSON.stringify(e.evt)}</span></div>
    {/each}
  </div>
{/if}

<div class="glass tbl">
  <table>
    <thead><tr><th>When</th><th>Who</th><th>What</th><th>Target</th><th>Detail</th></tr></thead>
    <tbody>
      {#each rows as a (a.id)}
        <tr>
          <td class="small">{date(a.at)}</td><td class="mono small">{a.src}</td><td>{a.action}</td>
          <td class="mono small">{a.target ?? ""}</td><td class="mono small muted">{a.detail ? JSON.stringify(a.detail) : ""}</td>
        </tr>
      {/each}
    </tbody>
  </table>
</div>
