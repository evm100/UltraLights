<script>
  // Recent OTA updates (all nodes, or one with `dev`), polled fast while any is in flight.
  import { onDestroy, untrack } from "svelte";
  import { get, errText } from "../../lib/api.js";
  import { ago, date } from "../../lib/format.js";

  let { dev = null, bump = 0 } = $props();
  let updates = $state([]);
  let error = $state("");
  let timer;

  const ACTIVE = ["sent", "downloading", "restarting"];
  const TAG = { done: "ok", failed: "err", sent: "", downloading: "warn", restarting: "warn" };

  async function load() {
    clearTimeout(timer);
    try {
      const d = await get(`/api/admin/ota${dev ? `?dev=${encodeURIComponent(dev)}` : ""}`);
      updates = dev ? d.updates.slice(0, 10) : d.updates;
      error = "";
    } catch (e) { error = errText(e); }
    timer = setTimeout(load, updates.some((u) => ACTIVE.includes(u.status)) ? 1500 : 15000);
  }
  $effect(() => { bump; dev; untrack(load); });
  onDestroy(() => clearTimeout(timer));
</script>

{#if error}<div class="msg err">{error}</div>{/if}
<div class="glass tbl">
  <table>
    <thead><tr>{#if !dev}<th>Node</th>{/if}<th>Update</th><th>Status</th><th>Started</th><th>By</th></tr></thead>
    <tbody>
      {#each updates as u (u.id)}
        <tr>
          {#if !dev}<td><a href={`#/admin/nodes/${u.dev}`}><strong>{u.name}</strong></a> <span class="mono small muted">{u.dev}</span></td>{/if}
          <td class="small">{u.from_version ?? "?"} → <strong>{u.to_version}</strong> <span class="muted">{u.profile}</span></td>
          <td style="min-width:12em">
            <span class="tag {TAG[u.status]}">{u.status}</span>
            {#if u.status === "downloading"}
              <div class="bar"><span style={`width:${u.progress}%`}></span></div>
            {:else if u.error}<div class="small muted">{u.error}</div>{/if}
          </td>
          <td class="small muted" title={date(u.created_at)}>{ago(u.created_at)}</td>
          <td class="small muted mono">{u.src}</td>
        </tr>
      {:else}
        <tr><td colspan={dev ? 4 : 5} class="muted">No updates yet.</td></tr>
      {/each}
    </tbody>
  </table>
</div>

<style>
  .bar { height: 5px; margin-top: 5px; border-radius: 5px; background: rgba(255,255,255,0.1); overflow: hidden; }
  .bar span { display: block; height: 100%; background: var(--accent); transition: width 0.3s; }
</style>
