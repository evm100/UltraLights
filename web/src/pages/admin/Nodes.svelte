<script>
  import { onMount } from "svelte";
  import { get, errText } from "../../lib/api.js";
  import { live } from "../../lib/live.svelte.js";
  import { go } from "../../lib/router.svelte.js";
  import { ago, uptime, kb } from "../../lib/format.js";

  let rows = $state([]);
  let status = $state(null);
  let error = $state("");

  async function load() {
    try {
      [rows, status] = await Promise.all([get("/api/admin/nodes"), get("/api/admin/status")]);
    } catch (e) { error = errText(e); }
  }
  onMount(load);

  // Prefer the live view (online, health) when the WebSocket has it.
  let nodes = $derived(rows.map((r) => ({ ...r, ...(live.devices[r.id] ?? {}), contract_errors: r.contract_errors })));
</script>

<div class="row">
  <h2>Nodes</h2>
  <span class="spacer"></span>
  {#if status}
    <span class="tag" class:ok={status.broker} class:err={!status.broker}>broker {status.broker ? "connected" : "offline"}</span>
    <span class="tag">{status.online}/{status.nodes} online</span>
  {/if}
  <a class="btn solid sm" href="#/admin/builder">New node</a>
</div>
{#if error}<div class="msg err">{error}</div>{/if}

<div class="glass tbl">
  <table>
    <thead><tr><th></th><th>Name</th><th>ID</th><th>Chip</th><th>Board</th><th>Firmware</th><th>Signal</th><th>Uptime</th><th>Heap min</th><th>Seen</th><th></th></tr></thead>
    <tbody>
      {#each nodes as n (n.id)}
        {@const h = n.admin?.health}
        <tr class="click" onclick={() => go(`/admin/nodes/${n.id}`)}>
          <td><span class="dot" class:on={n.online} title={n.online ? "online" : "offline"}></span></td>
          <td><strong>{n.name}</strong></td>
          <td class="mono">{n.id}</td>
          <td>{n.chip.replace("esp32", "").toUpperCase()}</td>
          <td class="muted">{n.admin?.board}</td>
          <td>{n.admin?.fw ?? "–"}
            {#if n.admin && n.admin.board_crc && n.admin.running_crc && n.admin.board_crc !== n.admin.running_crc}<span class="tag warn" title="The node runs a different descriptor than the last one built">descriptor differs</span>{/if}
            {#if n.admin?.config_error}<span class="tag err" title={n.admin.config_error}>config rejected</span>{/if}
            {#if n.contract_errors}<span class="tag err">{n.contract_errors} contract errors</span>{/if}
          </td>
          <td>{h ? `${h.rssi} dBm` : "–"}</td>
          <td>{uptime(h?.uptime_s)}</td>
          <td>{kb(h?.heap_min)}</td>
          <td class="muted small">{n.online ? "now" : ago(n.admin?.last_seen)}</td>
          <td>{#if !n.admin?.enrolled}<span class="tag">not enrolled</span>{/if}</td>
        </tr>
      {:else}
        <tr><td colspan="11" class="muted">No nodes yet. Create one in the node builder.</td></tr>
      {/each}
    </tbody>
  </table>
</div>
