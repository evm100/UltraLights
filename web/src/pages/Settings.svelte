<script>
  // Home settings, behind the gear: rooms and devices (manage ability), people and invites
  // (people ability). Each section shows only when the viewer holds its ability.
  import { onMount } from "svelte";
  import { get, post, put, del, errText } from "../lib/api.js";
  import { homesState, loadHomes, managed } from "../lib/homes.svelte.js";
  import { go } from "../lib/router.svelte.js";

  let { id } = $props();
  let hid = $derived(Number(id));
  let home = $derived(homesState.list.find((h) => h.id === hid));
  let canManage = $derived(!!home?.can.includes("manage"));
  let canPeople = $derived(!!home?.can.includes("people"));

  let devices = $state([]);
  let members = $state([]);
  let invites = $state([]);
  let fresh = $state(null); // the link just created, shown once
  let newRoom = $state("");
  let inviteRole = $state("member");
  let error = $state("");
  let note = $state("");

  async function load() {
    await loadHomes();
    if (!home) return;
    try {
      [devices, members, invites] = await Promise.all([
        canManage ? get(`/api/homes/${hid}/devices`) : [],
        canPeople ? get(`/api/homes/${hid}/members`) : [],
        canPeople ? get(`/api/homes/${hid}/invites`) : [],
      ]);
    } catch (e) { error = errText(e); }
  }
  onMount(load);

  async function run(fn, ok) {
    error = ""; note = "";
    try { const r = await fn(); note = typeof ok === "function" ? ok(r) : ok; await load(); return r; }
    catch (e) { error = errText(e); }
  }

  // ---- rooms
  const addRoom = () => run(() => post(`/api/homes/${hid}/rooms`, { name: newRoom.trim() }), "Room added.").then(() => (newRoom = ""));
  const renameRoom = (r, name) => name.trim() && name.trim() !== r.name && run(() => put(`/api/homes/${hid}/rooms/${r.id}`, { name: name.trim() }), "Room renamed.");
  function move(i, d) {
    const ids = home.rooms.map((r) => r.id);
    [ids[i], ids[i + d]] = [ids[i + d], ids[i]];
    run(() => put(`/api/homes/${hid}/rooms/order`, { room_ids: ids }), "Order saved.");
  }
  function removeRoom(r) {
    if (confirm(`Delete the room "${r.name}"? Its lights stay, without a room.`)) run(() => del(`/api/homes/${hid}/rooms/${r.id}`), "Room deleted.");
  }

  // ---- devices and outputs
  const renameDevice = (d, name) => name.trim() && name.trim() !== d.name && run(() => put(`/api/devices/${d.id}`, { name: name.trim() }), "Device renamed.");
  const saved = (r) => (r?.published === false ? "Saved. The broker is offline; the device gets it when it's back." : "Saved.");
  const setOutput = (d, o, body) => run(() => put(`/api/devices/${d.id}/outputs/${o.key}/settings`, body), saved);
  function setLimit(d, o, pct) {
    const v = Math.min(100, Math.max(5, Math.round(Number(pct))));
    if (v / 100 !== o.max_level) setOutput(d, o, { max_level: v / 100 });
  }

  // ---- people
  const setRole = (m, role) => run(() => put(`/api/homes/${hid}/members/${m.user_id}`, { role }), `${m.display_name} is now ${role === "owner" ? "an owner" : "a member"}.`);
  function removeMember(m) {
    const msg = m.you ? `Leave ${home.name}? You lose access to it.` : `Remove ${m.display_name} from ${home.name}?`;
    if (confirm(msg)) run(() => del(`/api/homes/${hid}/members/${m.user_id}`), m.you ? "You left the home." : "Removed.").then(() => m.you && go("/"));
  }
  async function invite() {
    const r = await run(() => post(`/api/homes/${hid}/invites`, { role: inviteRole }), "Invite link created. It works once and expires in 7 days.");
    if (r) fresh = r;
  }
  async function copy(text) {
    try { await navigator.clipboard.writeText(text); note = "Link copied."; } catch { note = "Select the link and copy it."; }
  }
  const revoke = (i) => run(() => del(`/api/homes/${hid}/invites/${i.id}`), "Invite revoked.").then(() => fresh?.id === i.id && (fresh = null));
  const days = (iso) => Math.max(0, Math.ceil((new Date(iso) - Date.now()) / 86400000));
</script>

<main class="page stack" style="gap:20px">
  <div class="row" style="justify-content:space-between;align-items:flex-end">
    <div class="stack" style="gap:4px">
      <span class="label">Settings</span>
      <h1>{home?.name ?? ""}</h1>
    </div>
    {#if managed(homesState.list).length > 1}
      <select aria-label="Home" value={hid} onchange={(e) => go(`/settings/${e.currentTarget.value}`)}>
        {#each managed(homesState.list) as h}<option value={h.id}>{h.name}</option>{/each}
      </select>
    {/if}
  </div>

  {#if error}<div class="msg err" role="alert">{error}</div>{/if}
  {#if note}<div class="msg ok" role="status">{note}</div>{/if}

  {#if homesState.loaded && (!home || (!canManage && !canPeople))}
    <p class="muted">You can't change this home's settings.</p>
  {/if}

  {#if canManage}
    <section class="glass panel stack">
      <h2>Rooms</h2>
      {#each home.rooms as r, i (r.id)}
        <div class="row">
          <input value={r.name} maxlength="64" aria-label="Room name" onchange={(e) => renameRoom(r, e.currentTarget.value)} />
          <button class="btn sm ghost" disabled={i === 0} onclick={() => move(i, -1)} aria-label={`Move ${r.name} up`}>↑</button>
          <button class="btn sm ghost" disabled={i === home.rooms.length - 1} onclick={() => move(i, 1)} aria-label={`Move ${r.name} down`}>↓</button>
          <button class="btn sm danger" onclick={() => removeRoom(r)}>Delete</button>
        </div>
      {:else}<p class="small muted">No rooms yet.</p>{/each}
      <form class="row" onsubmit={(e) => { e.preventDefault(); addRoom(); }}>
        <input bind:value={newRoom} placeholder="New room" maxlength="64" />
        <button class="btn sm" disabled={!newRoom.trim()}>Add room</button>
      </form>
    </section>

    <section class="glass panel stack">
      <h2>Devices</h2>
      <p class="small muted">Names, rooms and limits are sent to the device. Max level is a hard cap applied on the device; power-on is what a light does when it starts before it reaches the server.</p>
      {#each devices as d (d.id)}
        <div class="stack dev">
          <div class="row">
            <input class="devname" value={d.name} maxlength="64" aria-label="Device name" onchange={(e) => renameDevice(d, e.currentTarget.value)} />
            <span class="tag" class:ok={d.online}>{d.online ? "online" : "offline"}</span>
          </div>
          <div class="tbl"><table>
            <thead><tr><th>Output</th><th>Name</th><th>Room</th><th>Max level</th><th>Power on</th></tr></thead>
            <tbody>
              {#each d.outputs as o (o.key)}
                <tr>
                  <td class="small muted">{o.key} · {o.kind}</td>
                  <td><input value={o.name} maxlength="64" aria-label={`Name of ${o.key}`} onchange={(e) => setOutput(d, o, { name: e.currentTarget.value })} /></td>
                  <td>
                    <select value={o.room_id ?? ""} aria-label={`Room of ${o.key}`} onchange={(e) => setOutput(d, o, { room_id: e.currentTarget.value ? Number(e.currentTarget.value) : null })}>
                      <option value="">No room</option>
                      {#each home.rooms as r}<option value={r.id}>{r.name}</option>{/each}
                    </select>
                  </td>
                  <td>
                    {#if o.max_level != null}
                      <label class="row pct"><input type="number" min="5" max="100" step="5" value={Math.round(o.max_level * 100)}
                        aria-label={`Max level of ${o.key}, percent`} onchange={(e) => setLimit(d, o, e.currentTarget.value)} />%</label>
                    {:else}<span class="faint">–</span>{/if}
                  </td>
                  <td>
                    {#if o.power_on != null}
                      <select value={o.power_on} aria-label={`Power-on state of ${o.key}`} onchange={(e) => setOutput(d, o, { power_on: e.currentTarget.value })}>
                        <option value="last">Last state</option><option value="off">Off</option><option value="on">On</option>
                      </select>
                    {:else}<span class="faint">–</span>{/if}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table></div>
        </div>
      {:else}<p class="small muted">No devices in this home yet.</p>{/each}
    </section>
  {/if}

  {#if canPeople}
    <section class="glass panel stack">
      <h2>People</h2>
      <p class="small muted">Owners manage devices and people. Members control everything and make scenes and schedules. A home always keeps at least one owner.</p>
      {#each members as m (m.user_id)}
        <div class="row">
          <span class="who"><strong>{m.display_name}</strong> <span class="small muted">{m.username}{m.you ? " · you" : ""}</span></span>
          {#if m.role === "guest"}
            <span class="tag">guest</span>
          {:else}
            <select value={m.role} aria-label={`Role of ${m.display_name}`} onchange={(e) => setRole(m, e.currentTarget.value)}>
              <option value="owner">Owner</option><option value="member">Member</option>
            </select>
          {/if}
          <button class="btn sm ghost" onclick={() => removeMember(m)}>{m.you ? "Leave" : "Remove"}</button>
        </div>
      {/each}

      <span class="label">Invite someone</span>
      <div class="row">
        <select bind:value={inviteRole} aria-label="Invite as"><option value="member">as a member</option><option value="owner">as an owner</option></select>
        <button class="btn sm solid" onclick={invite}>Create invite link</button>
      </div>
      {#if fresh}
        <div class="glass panel stack fresh">
          <span class="small">Send this link to the person you're inviting. It's shown only now.</span>
          <div class="row"><input class="link" readonly value={fresh.url} onfocus={(e) => e.currentTarget.select()} /><button class="btn sm" onclick={() => copy(fresh.url)}>Copy</button></div>
        </div>
      {/if}
      {#if invites.length}
        <span class="label">Open invites</span>
        {#each invites as i (i.id)}
          <div class="row small"><span>{i.role === "owner" ? "Owner" : "Member"} invite · expires in {days(i.expires_at)} {days(i.expires_at) === 1 ? "day" : "days"}</span>
            <button class="btn sm ghost" onclick={() => revoke(i)}>Revoke</button></div>
        {/each}
      {/if}
    </section>
  {/if}
</main>

<style>
  .dev { padding-top: 8px; border-top: 1px solid var(--line); }
  .dev:first-of-type { border-top: 0; }
  .devname { font-weight: 600; }
  .pct { gap: 4px; flex-wrap: nowrap; }
  .pct input { width: 5.5em; }
  .who { flex: 1; min-width: 0; }
  .link { flex: 1; min-width: 0; font-family: var(--mono); font-size: 0.85rem; }
  .fresh { border: 1px solid var(--accent); }
</style>
