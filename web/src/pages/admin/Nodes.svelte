<script>
  import { onMount } from "svelte";
  import { get, post, errText } from "../../lib/api.js";
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

  // Add a PC whose lighting OpenRGB drives: creates the bridged device and a one-time pairing code.
  let adding = $state(false);
  let pcName = $state("Desktop");
  let pcHome = $state("");
  let homes = $state([]);
  async function startAdd() {
    adding = !adding;
    if (adding && !homes.length) try { homes = await get("/api/admin/homes"); } catch {}
  }
  async function addPc() {
    error = "";
    try {
      const r = await post("/api/admin/bridges", { name: pcName.trim(), bridge: "openrgb", home_id: pcHome ? Number(pcHome) : null });
      try { sessionStorage.setItem(`ul.pairing.${r.id}`, JSON.stringify(r)); } catch {}
      go(`/admin/nodes/${r.id}`);
    } catch (e) { error = errText(e); }
  }

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
  <button class="btn sm" onclick={startAdd}>Add PC (OpenRGB)</button>
  <a class="btn solid sm" href="#/admin/builder">New node</a>
</div>
{#if adding}
  <form class="glass panel row" onsubmit={(e) => { e.preventDefault(); addPc(); }}>
    <label class="field" style="flex:1">Name<input bind:value={pcName} maxlength="64" required /></label>
    <label class="field" style="flex:1">Home
      <select bind:value={pcHome}><option value="">No home yet</option>{#each homes as h}<option value={String(h.id)}>{h.name}</option>{/each}</select>
    </label>
    <button class="btn solid" type="submit" disabled={!pcName.trim()}>Create and get pairing code</button>
    <span class="small muted" style="flex-basis:100%">The PC shows up as one light. Its parts (GPU, memory, board, fans) come from the OpenRGB agent you install with the pairing code.</span>
  </form>
{/if}
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
          <td>{n.kind === "bridge" ? "PC" : n.chip.replace("esp32", "").toUpperCase()}</td>
          <td class="muted">{n.kind === "bridge" ? `OpenRGB · ${n.outputs.length} parts` : n.admin?.board}</td>
          <td>{n.admin?.fw ?? "–"}
            {#if n.admin && n.admin.board_crc && n.admin.running_crc && n.admin.board_crc !== n.admin.running_crc}<span class="tag warn" title="The node runs a different descriptor than the last one built">descriptor differs</span>{/if}
            {#if n.admin?.config_error}<span class="tag err" title={n.admin.config_error}>{n.admin.config_error.startsWith("board rejected") || n.admin.config_error.startsWith("descriptor is for") ? "board rejected" : "config rejected"}</span>{/if}
            {#if n.contract_errors}<span class="tag err">{n.contract_errors} contract errors</span>{/if}
          </td>
          <td>{h ? `${h.rssi} dBm` : "–"}</td>
          <td>{uptime(h?.uptime_s)}</td>
          <td>{kb(h?.heap_min)}</td>
          <td class="muted small">{n.online ? "now" : ago(n.admin?.last_seen)}</td>
          <td>{#if !n.admin?.enrolled}<span class="tag">{n.kind === "bridge" ? "not paired" : "not enrolled"}</span>{:else if n.kind === "bridge" && n.online && n.admin?.sources?.some((s) => !s.online)}<span class="tag warn">server down</span>{/if}</td>
        </tr>
      {:else}
        <tr><td colspan="11" class="muted">No nodes yet. Create one in the node builder.</td></tr>
      {/each}
    </tbody>
  </table>
</div>
