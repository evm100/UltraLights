<script>
  // Home settings, behind the gear: rooms and devices (manage ability), people and invites
  // (people ability). Each section shows only when the viewer holds its ability.
  import { onMount } from "svelte";
  import { get, post, put, del, errText } from "../lib/api.js";
  import { homesState, loadHomes, managed } from "../lib/homes.svelte.js";
  import { go } from "../lib/router.svelte.js";
  import { blankGrantForm, buildGrant, grantForm, grantProblem, grantStatus, grantText } from "../lib/grants.js";
  import GrantEditor from "../components/GrantEditor.svelte";

  let { id } = $props();
  let hid = $derived(Number(id));
  let home = $derived(homesState.list.find((h) => h.id === hid));
  let canManage = $derived(!!home?.can.includes("manage"));
  let canPeople = $derived(!!home?.can.includes("people"));

  let devices = $state([]);
  let place = $state({ timezone: "", latitude: "", longitude: "" });
  const ZONES = typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [];
  const BROWSER_ZONE = Intl.DateTimeFormat().resolvedOptions().timeZone;
  let members = $state([]);
  let invites = $state([]);
  let fresh = $state(null); // the link just created, shown once
  let newRoom = $state("");
  let inviteRole = $state("member");
  let inviteGrant = $state(blankGrantForm());
  let editing = $state(null); // { uid, f }: a guest's grant being edited
  let error = $state("");
  let note = $state("");

  async function load() {
    await loadHomes();
    if (!home) return;
    try {
      if (canManage) {
        const p = await get(`/api/homes/${hid}/place`);
        place = { timezone: p.timezone ?? BROWSER_ZONE, latitude: p.latitude ?? "", longitude: p.longitude ?? "", saved: !!p.timezone };
      }
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

  // ---- place
  function savePlace() {
    const body = { timezone: place.timezone };
    if (place.latitude !== "" && place.longitude !== "") { body.latitude = Number(place.latitude); body.longitude = Number(place.longitude); }
    run(() => put(`/api/homes/${hid}/place`, body), "Place saved.");
  }
  function locate() {
    navigator.geolocation?.getCurrentPosition(
      (p) => { place.latitude = Math.round(p.coords.latitude * 1000) / 1000; place.longitude = Math.round(p.coords.longitude * 1000) / 1000; },
      () => (error = "The browser didn't give a location; type it in instead."));
  }

  // ---- devices and outputs
  const renameDevice = (d, name) => name.trim() && name.trim() !== d.name && run(() => put(`/api/devices/${d.id}`, { name: name.trim() }), "Device renamed.");
  const saved = (r) => (r?.published === false ? "Saved. The broker is offline; the device gets it when it's back." : "Saved.");
  const setOutput = (d, o, body) => run(() => put(`/api/devices/${d.id}/outputs/${o.key}/settings`, body), saved);
  function setRun(d, o, min) {
    const v = Math.min(120, Math.max(1, Math.round(Number(min)))) * 60000;
    if (v !== o.max_on_ms) setOutput(d, o, { max_on_ms: v });
  }
  function setLimit(d, o, pct) {
    const v = Math.min(100, Math.max(5, Math.round(Number(pct))));
    if (v / 100 !== o.max_level) setOutput(d, o, { max_level: v / 100 });
  }

  // ---- people
  const ROLE_TEXT = { owner: "an owner", member: "a member", guest: "a guest" };
  function setRole(m, role, select) {
    if (role === "guest" && !confirm(`Make ${m.display_name} a guest? They'll see nothing in ${home.name} until you give them access.`)) {
      select.value = m.role;
      return;
    }
    run(() => put(`/api/homes/${hid}/members/${m.user_id}`, { role }), `${m.display_name} is now ${ROLE_TEXT[role]}.`)
      .then(() => role === "guest" && (editing = { uid: m.user_id, f: blankGrantForm() }));
  }
  let names = $derived({
    rooms: Object.fromEntries((home?.rooms ?? []).map((r) => [r.id, r.name])),
    devices: Object.fromEntries(devices.map((d) => [d.id, d.name])),
  });
  let grantDevices = $derived(devices.map((d) => ({ id: d.id, name: d.name })));
  const editGrant = (m) => (editing = { uid: m.user_id, f: grantForm(m.grant) });
  async function saveGrant(m) {
    const r = await run(() => put(`/api/homes/${hid}/members/${m.user_id}/grant`, buildGrant(editing.f)), `${m.display_name}'s access saved.`);
    if (r) editing = null;
  }
  function removeMember(m) {
    const msg = m.you ? `Leave ${home.name}? You lose access to it.` : `Remove ${m.display_name} from ${home.name}?`;
    if (confirm(msg)) run(() => del(`/api/homes/${hid}/members/${m.user_id}`), m.you ? "You left the home." : "Removed.").then(() => m.you && go("/"));
  }
  async function invite() {
    const body = { role: inviteRole };
    if (inviteRole === "guest") body.grant = buildGrant(inviteGrant);
    const r = await run(() => post(`/api/homes/${hid}/invites`, body), "Invite link created. It works once and expires in 7 days.");
    if (r) { fresh = r; inviteGrant = blankGrantForm(); }
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
      <h2>Place</h2>
      <p class="small muted">Schedules run in the home's time zone. The location is only used to work out sunrise and sunset; three decimals (about 100 m) is plenty.</p>
      {#if !place.saved}<div class="msg warn small">Not saved yet{place.timezone ? `: ${place.timezone} is this browser's time zone` : ""}.</div>{/if}
      <div class="row">
        <label class="mini">Time zone
          {#if ZONES.length}
            <select bind:value={place.timezone}>{#each ZONES as z}<option value={z}>{z}</option>{/each}</select>
          {:else}<input bind:value={place.timezone} placeholder="Europe/London" />{/if}
        </label>
      </div>
      <div class="row">
        <label class="mini">Latitude<input type="number" step="0.001" min="-90" max="90" bind:value={place.latitude} /></label>
        <label class="mini">Longitude<input type="number" step="0.001" min="-180" max="180" bind:value={place.longitude} /></label>
        {#if navigator.geolocation}<button class="btn sm ghost" type="button" onclick={locate}>Use this device's location</button>{/if}
      </div>
      <div><button class="btn sm solid" onclick={savePlace} disabled={!place.timezone}>Save place</button></div>
    </section>

    <section class="glass panel stack">
      <h2>Devices</h2>
      <p class="small muted">Names, rooms and limits are sent to the device. Limits are enforced on the device itself: a light's max level, and a valve's longest run (1 minute to 2 hours). Power-on is what an output does when it starts, before it reaches the server; valves always start closed.</p>
      {#each devices as d (d.id)}
        <div class="stack dev">
          <div class="row">
            <input class="devname" value={d.name} maxlength="64" aria-label="Device name" onchange={(e) => renameDevice(d, e.currentTarget.value)} />
            <span class="tag" class:ok={d.online}>{d.online ? "online" : "offline"}</span>
          </div>
          <div class="tbl"><table>
            <thead><tr><th>Output</th><th>Name</th><th>Room</th><th>Limit</th><th>Power on</th></tr></thead>
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
                    {#if o.max_on_ms != null}
                      <label class="row pct"><input type="number" min="1" max="120" step="1" value={Math.round(o.max_on_ms / 60000)}
                        aria-label={`Longest run of ${o.key}, minutes`} onchange={(e) => setRun(d, o, e.currentTarget.value)} />min</label>
                    {:else if o.max_level != null}
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
      <p class="small muted">Owners manage devices and people. Members control everything and make scenes and schedules. Guests use only what you give them, optionally at set hours and until a date. A home always keeps at least one owner.</p>
      {#each members as m (m.user_id)}
        {@const st = m.role === "guest" ? grantStatus(m.grant_state, m.grant) : null}
        <div class="member">
          <div class="row">
            <span class="who"><strong>{m.display_name}</strong> <span class="small muted">{m.username}{m.you ? " · you" : ""}</span></span>
            {#if st}<span class={`tag ${st.tone}`}>{st.text}</span>{/if}
            <select value={m.role} aria-label={`Role of ${m.display_name}`} onchange={(e) => setRole(m, e.currentTarget.value, e.currentTarget)}>
              <option value="owner">Owner</option><option value="member">Member</option><option value="guest">Guest</option>
            </select>
            <button class="btn sm ghost" onclick={() => removeMember(m)}>{m.you ? "Leave" : "Remove"}</button>
          </div>
          {#if m.role === "guest"}
            {#if editing?.uid === m.user_id}
              <form class="glass panel stack" onsubmit={(e) => { e.preventDefault(); saveGrant(m); }}>
                <GrantEditor bind:f={editing.f} rooms={home.rooms} devices={grantDevices} timezone={home.timezone} />
                {#if grantProblem(editing.f)}<span class="small faint">{grantProblem(editing.f)}</span>{/if}
                <div class="row">
                  <button class="btn sm solid" disabled={!!grantProblem(editing.f)}>Save access</button>
                  <button type="button" class="btn sm ghost" onclick={() => (editing = null)}>Cancel</button>
                </div>
              </form>
            {:else}
              <div class="row small"><span class="muted grow">{grantText(m.grant, names)}</span>
                <button class="btn sm ghost" onclick={() => editGrant(m)}>{m.grant ? "Edit access" : "Give access"}</button></div>
            {/if}
          {/if}
        </div>
      {/each}

      <span class="label">Invite someone</span>
      <div class="row">
        <select bind:value={inviteRole} aria-label="Invite as">
          <option value="member">as a member</option><option value="owner">as an owner</option><option value="guest">as a guest</option>
        </select>
        {#if inviteRole !== "guest"}<button class="btn sm solid" onclick={invite}>Create invite link</button>{/if}
      </div>
      {#if inviteRole === "guest"}
        <form class="glass panel stack" onsubmit={(e) => { e.preventDefault(); invite(); }}>
          <GrantEditor bind:f={inviteGrant} rooms={home.rooms} devices={grantDevices} timezone={home.timezone} />
          {#if grantProblem(inviteGrant)}<span class="small faint">{grantProblem(inviteGrant)}</span>{/if}
          <div><button class="btn sm solid" disabled={!!grantProblem(inviteGrant)}>Create guest invite link</button></div>
        </form>
      {/if}
      {#if fresh}
        <div class="glass panel stack fresh">
          <span class="small">Send this link to the person you're inviting. It's shown only now.</span>
          <div class="row"><input class="link" readonly value={fresh.url} onfocus={(e) => e.currentTarget.select()} /><button class="btn sm" onclick={() => copy(fresh.url)}>Copy</button></div>
        </div>
      {/if}
      {#if invites.length}
        <span class="label">Open invites</span>
        {#each invites as i (i.id)}
          <div class="row small"><span>{i.role === "owner" ? "Owner" : i.role === "guest" ? `Guest${i.grant?.label ? ` (${i.grant.label})` : ""}` : "Member"} invite · expires in {days(i.expires_at)} {days(i.expires_at) === 1 ? "day" : "days"}</span>
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
  .member { display: flex; flex-direction: column; gap: 6px; padding: 8px 0; border-top: 1px solid var(--line); }
  .member:first-of-type { border-top: 0; }
  .grow { flex: 1; min-width: 0; }
  .mini { display: inline-flex; gap: 6px; align-items: center; font-size: 0.85rem; color: var(--muted); }
  .mini input { width: 8em; }
  .link { flex: 1; min-width: 0; font-family: var(--mono); font-size: 0.85rem; }
  .fresh { border: 1px solid var(--accent); }
</style>
