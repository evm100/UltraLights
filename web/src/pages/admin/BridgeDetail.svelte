<script>
  // Admin view of a bridged device (the OpenRGB agent on a PC): its OpenRGB servers, its parts,
  // where it lives, and the one-time pairing command that installs or re-enrolls the agent.
  import { get, post, put, del, errText } from "../../lib/api.js";
  import { live } from "../../lib/live.svelte.js";
  import { go } from "../../lib/router.svelte.js";
  import { ago, date } from "../../lib/format.js";
  import { partIcon, reachable, roomOf } from "../../lib/parts.js";

  let { node, homes, onchange } = $props();

  let error = $state("");
  let note = $state("");
  let pairing = $state(null);
  let busy = $state(false);
  // Pairing codes are shown once; one made on the Nodes page is handed over through sessionStorage.
  try {
    const stashed = sessionStorage.getItem(`ul.pairing.${node.id}`);
    if (stashed) { pairing = JSON.parse(stashed); sessionStorage.removeItem(`ul.pairing.${node.id}`); }
  } catch {}

  let lv = $derived(live.devices[node.id]);
  let dev = $derived(lv ?? node);
  let online = $derived(dev.online);
  let admin = $derived(lv?.admin ?? node.admin);
  let sources = $derived(admin?.sources ?? []);
  let outputs = $derived(dev.outputs ?? []);
  let rooms = $derived(homes.find((h) => h.id === node.home_id)?.rooms ?? []);
  let room = $derived(roomOf(dev));
  let events = $derived(live.events.filter((e) => e.dev === node.id).slice(0, 12));

  async function act(fn, ok) {
    error = ""; note = "";
    try { const r = await fn(); note = typeof ok === "function" ? ok(r) : ok; await onchange?.(); }
    catch (e) { error = errText(e); }
  }
  const cmd = (op, args = {}) => act(() => post(`/api/admin/nodes/${node.id}/cmd`, { op, args }),
    (r) => (r.result ? (r.result.ok ? `${op}: done` : `${op}: ${r.result.error}`) : `${op}: sent (no answer within 5 s)`));
  const rename = (name) => act(() => put(`/api/admin/nodes/${node.id}`, { name }), "Renamed.");
  const setHome = (v) => act(() => put(`/api/admin/nodes/${node.id}`, v ? { home_id: Number(v) } : { clear_home: true }), "Home updated.");
  const renamePart = (key, name) => act(() => put(`/api/admin/nodes/${node.id}/outputs/${key}`, { name }), "Part renamed.");
  // The PC is one light, so all its parts share a room.
  const setRoom = (v) => act(() => Promise.all(outputs.map((o) =>
    put(`/api/admin/nodes/${node.id}/outputs/${o.key}`, v ? { room_id: Number(v) } : { clear_room: true }))), "Room updated.");

  async function newPairing() {
    busy = true;
    await act(async () => { pairing = await post(`/api/admin/nodes/${node.id}/pairing`); }, "");
    busy = false;
  }
  async function revokeToken(id) { await act(() => del(`/api/admin/tokens/${id}`), "Token revoked."); }
  async function remove() {
    if (!confirm(`Delete ${node.name} (${node.id})? Its certificate is revoked; the agent needs a new pairing code to come back.`)) return;
    await act(() => del(`/api/admin/nodes/${node.id}`), "Deleted.");
    go("/admin/nodes");
  }

  let enrollCmd = $derived(pairing && `sudo ul-openrgb enroll --server ${pairing.server} --device ${node.id} --token ${pairing.token} && sudo systemctl restart ul-openrgb`);
  let installCmd = $derived(pairing && `sudo bridges/openrgb/install.sh --server ${pairing.server} --device ${node.id} --token ${pairing.token} \\\n  --source pve=127.0.0.1,"Proxmox host" --source vm=GPU_VM_IP,"GPU VM"`);
  const copy = (t) => navigator.clipboard?.writeText(t).then(() => (note = "Copied."), () => {});
</script>

<div class="row">
  <a href="#/admin/nodes" class="btn ghost sm">← Nodes</a>
  <span class="dot" class:on={online}></span>
  <h2>{node.name}</h2>
  <span class="mono muted">{node.id}</span>
  <span class="tag">OpenRGB bridge</span>
  {#if !node.admin.enrolled}<span class="tag warn">not paired</span>{/if}
  <span class="spacer"></span>
  <button class="btn sm" disabled={!online} onclick={() => cmd("identify", { ms: 5000 })}>Identify</button>
  <button class="btn sm" disabled={!online} onclick={() => confirm("Restart the agent? Lights keep their state.") && cmd("reboot")}>Restart agent</button>
</div>
{#if error}<div class="msg err">{error}</div>{/if}
{#if note}<div class="msg ok">{note}</div>{/if}

<div class="grid">
  <section class="glass panel stack">
    <span class="label">Agent</span>
    <dl>
      <dt>Connection</dt><dd>{online ? "online" : `offline, seen ${ago(admin?.last_seen)}`}</dd>
      <dt>Version</dt><dd>{admin?.fw ?? "–"}</dd>
      <dt>Runs on</dt><dd>{admin?.host ?? "–"}</dd>
    </dl>
    <div class="row">
      <label class="field" style="flex:1">Name<input value={node.name} onchange={(e) => rename(e.target.value)} maxlength="64" /></label>
      <label class="field" style="flex:1">Home
        <select value={node.home_id ?? ""} onchange={(e) => setHome(e.target.value)}>
          <option value="">No home</option>{#each homes as h}<option value={h.id}>{h.name}</option>{/each}
        </select>
      </label>
      <label class="field" style="flex:1">Room
        <select value={room ?? ""} onchange={(e) => setRoom(e.target.value)} disabled={!node.home_id || !outputs.length}>
          <option value="">No room</option>{#each rooms as r}<option value={r.id}>{r.name}</option>{/each}
        </select>
      </label>
    </div>
  </section>

  <section class="glass panel stack">
    <span class="label">OpenRGB servers</span>
    {#each sources as s (s.key)}
      <div class="src">
        <div class="row" style="gap:8px">
          <span class="dot" class:on={online && s.online}></span>
          <strong>{s.name ?? s.key}</strong><span class="mono small muted">{s.address ?? ""}</span>
        </div>
        {#if online && !s.online}<div class="small" style="color:var(--err)">{s.error ?? "unreachable"}</div>{/if}
        {#if s.devices?.length}<div class="small muted">{s.devices.join(" · ")}</div>{/if}
      </div>
    {:else}
      <div class="small muted">The agent reports its servers once it connects.</div>
    {/each}
  </section>
</div>

<section class="glass panel stack">
  <span class="label">Parts</span>
  <p class="small muted" style="margin:0">Parts and their OpenRGB devices come from <code>/etc/ul-openrgb/agent.toml</code> on the agent's machine. Users see the PC as one light and pick a part under its color sliders.</p>
  <table>
    <thead><tr><th></th><th>Name</th><th>Key</th><th>Server</th><th>LEDs</th><th>State</th><th></th></tr></thead>
    <tbody>
      {#each outputs as o (o.key)}
        {@const s = o.state ?? o.desired}
        <tr>
          <td><svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d={partIcon(o)} fill="none" stroke={s?.on && s?.color ? `rgb(${s.color})` : "currentColor"} stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></td>
          <td><input value={o.name} maxlength="48" onchange={(e) => renamePart(o.key, e.target.value)} /></td>
          <td class="mono small">{o.key}</td>
          <td class="small">{sources.find((x) => x.key === o.meta.source)?.name ?? o.meta.source ?? "–"}</td>
          <td class="small">{o.meta.leds ?? "–"}</td>
          <td>{#if !online}<span class="tag">offline</span>{:else if !reachable(o)}<span class="tag err">unreachable</span>{:else if s?.on}<span class="tag ok">on {Math.round((s.level ?? 1) * 100)}%</span>{:else}<span class="tag">off</span>{/if}</td>
          <td><button class="btn sm ghost" disabled={!online || !reachable(o)} onclick={() => cmd("identify", { ms: 3000, out: o.key })}>Identify</button></td>
        </tr>
      {:else}
        <tr><td colspan="7" class="muted">No parts yet. They appear when the agent connects.</td></tr>
      {/each}
    </tbody>
  </table>
</section>

<section class="glass panel stack">
  <div class="row"><span class="label">Pairing</span><span class="spacer"></span>
    <button class="btn sm" disabled={busy} onclick={newPairing}>{node.admin.enrolled ? "New pairing code (re-enroll)" : "New pairing code"}</button></div>
  {#if pairing}
    <p class="small" style="margin:0">One-time code, valid until {date(pairing.token_expires)}. It's shown only now.</p>
    <span class="small muted">First install, on the Proxmox host from a clone of the UltraLights repo (set the GPU VM's address):</span>
    <div class="cmd"><pre>{installCmd}</pre><button class="btn sm ghost" onclick={() => copy(installCmd)}>Copy</button></div>
    <span class="small muted">Agent already installed (moved host, lost key, re-pair):</span>
    <div class="cmd"><pre>{enrollCmd}</pre><button class="btn sm ghost" onclick={() => copy(enrollCmd)}>Copy</button></div>
  {:else}
    <p class="small muted" style="margin:0">The agent enrolls like a node: a one-time code gets it a certificate from the device CA, then it connects to the broker over mutual TLS.</p>
  {/if}
  <table><tbody>
    {#each node.tokens as t}
      <tr><td><span class="tag" class:ok={t.status === "active"} class:warn={t.status === "expired"}>{t.status}</span></td>
        <td class="small muted">issued {date(t.created_at)}</td>
        <td class="small muted">{t.used_at ? `used ${date(t.used_at)}` : `expires ${date(t.expires_at)}`}</td>
        <td>{#if t.status === "active"}<button class="btn sm danger" onclick={() => revokeToken(t.id)}>Revoke</button>{/if}</td></tr>
    {/each}
    {#each node.certs as c}
      <tr><td><span class="tag" class:ok={!c.revoked_at} class:err={c.revoked_at}>{c.revoked_at ? c.revoke_reason : "certificate"}</span></td>
        <td class="mono small">{c.serial.slice(0, 12)}…</td><td class="small muted">issued {date(c.issued_at)}</td><td></td></tr>
    {/each}
  </tbody></table>
</section>

<section class="glass panel stack">
  <span class="label">Recent events</span>
  {#each events as e}
    <div class="small"><span class="muted">{e.at.toLocaleTimeString()}</span> <span class="mono">{JSON.stringify(e.evt)}</span></div>
  {:else}<div class="small muted">Nothing since this page opened.</div>{/each}
  {#if node.contract_errors.length}
    <span class="label" style="color:var(--err)">Contract errors</span>
    {#each node.contract_errors as ce}<div class="small mono">{ce.topic}: {ce.error}</div>{/each}
  {/if}
  <button class="btn danger sm" style="align-self:flex-start" onclick={remove}>Delete bridge</button>
</section>

<style>
  .grid { display: grid; grid-template-columns: minmax(0, 1fr); gap: 16px; }
  @media (min-width: 900px) { .grid { grid-template-columns: 1fr 1fr; } }
  dl { display: grid; grid-template-columns: 110px 1fr; gap: 6px 12px; margin: 0; font-size: 0.88rem; }
  dt { color: var(--muted); }
  dd { margin: 0; }
  .src { display: flex; flex-direction: column; gap: 3px; padding: 8px 0; border-bottom: 1px solid var(--line); }
  .src:last-child { border-bottom: 0; }
  td input { width: 100%; max-width: 220px; }
  .cmd { display: flex; gap: 8px; align-items: flex-start; }
  .cmd pre { flex: 1; margin: 0; padding: 10px; background: rgba(0, 0, 0, 0.35); border-radius: var(--radius-sm); font-size: 0.75rem; white-space: pre-wrap; word-break: break-all; }
</style>
