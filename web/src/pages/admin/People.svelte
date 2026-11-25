<script>
  import { onMount } from "svelte";
  import { get, post, del, errText } from "../../lib/api.js";

  let homes = $state([]);
  let users = $state([]);
  let error = $state("");
  let note = $state("");
  let homeName = $state("");
  let roomName = $state({});
  const blank = () => ({ username: "", password: "", is_admin: false, display_name: "", home_id: "", home_role: "owner" });
  let nu = $state(blank());
  let join = $state({}); // user id -> { home_id, role } for the "add to home" row
  const ROLES = ["owner", "member", "guest"];

  async function load() {
    try {
      [homes, users] = await Promise.all([get("/api/admin/homes"), get("/api/admin/users")]);
      for (const u of users) join[u.id] ??= newJoin();
    }
    catch (e) { error = errText(e); }
  }
  onMount(load);

  async function run(fn, ok) {
    error = ""; note = "";
    try { await fn(); note = ok; await load(); } catch (e) { error = errText(e); }
  }
  const addHome = () => run(() => post("/api/admin/homes", { name: homeName.trim() }), "Home created.").then(() => (homeName = ""));
  const addRoom = (h) => run(() => post(`/api/admin/homes/${h.id}/rooms`, { name: (roomName[h.id] || "").trim() }), "Room added.").then(() => (roomName[h.id] = ""));
  function addUser() {
    const body = { ...nu, username: nu.username.trim().toLowerCase() };
    body.home_id = nu.home_id ? Number(nu.home_id) : null;
    run(() => post("/api/admin/users", body), `User ${body.username} created.`).then(() => (nu = blank()));
  }
  function newJoin() { return { home_id: "", role: "member" }; }
  const setMember = (u, home_id, role) =>
    run(() => post(`/api/admin/homes/${home_id}/members`, { user_id: u.id, role }), `${u.username} is now ${role} of ${homeNameOf(home_id)}.`);
  const removeMember = (u, home_id) =>
    run(() => del(`/api/admin/homes/${home_id}/members/${u.id}`), `${u.username} removed from ${homeNameOf(home_id)}.`);
  const notIn = (u) => homes.filter((h) => !u.homes.some((m) => m.home_id === h.id));
  const homeNameOf = (id) => homes.find((h) => h.id === id)?.name ?? `#${id}`;
</script>

{#if error}<div class="msg err">{error}</div>{/if}
{#if note}<div class="msg ok">{note}</div>{/if}

<div class="grid">
  <section class="glass panel stack">
    <h2>Homes and rooms</h2>
    {#each homes as h (h.id)}
      <div class="stack" style="gap:6px">
        <div class="row"><strong>{h.name}</strong><span class="tag">{h.nodes} nodes</span></div>
        <div class="row">{#each h.rooms as r}<span class="tag">{r.name}</span>{:else}<span class="small muted">No rooms</span>{/each}</div>
        <div class="row"><input bind:value={roomName[h.id]} placeholder="New room" maxlength="64" /><button class="btn sm" disabled={!roomName[h.id]?.trim()} onclick={() => addRoom(h)}>Add room</button></div>
      </div>
    {/each}
    <div class="row"><input bind:value={homeName} placeholder="New home" maxlength="64" /><button class="btn sm" disabled={!homeName.trim()} onclick={addHome}>Add home</button></div>
  </section>

  <section class="glass panel stack">
    <h2>People</h2>
    <table><tbody>
      {#each users as u (u.id)}
        <tr>
          <td><strong>{u.username}</strong> <span class="muted small">{u.display_name}</span>{#if u.is_admin} <span class="tag">admin</span>{/if}</td>
          <td class="stack" style="gap:6px">
            {#each u.homes as m (m.home_id)}
              <div class="row">
                <span class="small">{homeNameOf(m.home_id)}</span>
                <select value={m.role} aria-label={`Role in ${homeNameOf(m.home_id)}`} onchange={(e) => setMember(u, m.home_id, e.currentTarget.value)}>
                  {#each ROLES as r}<option value={r}>{r}</option>{/each}
                </select>
                <button class="btn sm" onclick={() => removeMember(u, m.home_id)} aria-label={`Remove from ${homeNameOf(m.home_id)}`}>Remove</button>
              </div>
            {:else}<span class="small muted">No homes</span>{/each}
            {#if notIn(u).length}
              <div class="row">
                <select bind:value={join[u.id].home_id} aria-label="Home"><option value="">Add to home…</option>{#each notIn(u) as h}<option value={h.id}>{h.name}</option>{/each}</select>
                <select bind:value={join[u.id].role} aria-label="Role">{#each ROLES as r}<option value={r}>{r}</option>{/each}</select>
                <button class="btn sm" disabled={!join[u.id].home_id} onclick={() => setMember(u, Number(join[u.id].home_id), join[u.id].role).then(() => (join[u.id] = newJoin()))}>Add</button>
              </div>
            {/if}
          </td>
        </tr>
      {/each}
    </tbody></table>
    <span class="label">Add a person</span>
    <div class="row">
      <input bind:value={nu.username} placeholder="username" autocomplete="off" />
      <input bind:value={nu.display_name} placeholder="Display name" />
      <input type="password" bind:value={nu.password} placeholder="Password (10+ chars)" autocomplete="new-password" />
      <select bind:value={nu.home_id}><option value="">No home</option>{#each homes as h}<option value={h.id}>{h.name}</option>{/each}</select>
      {#if nu.home_id}<select bind:value={nu.home_role} aria-label="Role in that home">{#each ROLES as r}<option value={r}>{r}</option>{/each}</select>{/if}
      <label class="row small"><input type="checkbox" bind:checked={nu.is_admin} /> Admin</label>
      <button class="btn solid sm" disabled={!nu.username || nu.password.length < 10} onclick={addUser}>Create</button>
    </div>
    <p class="small muted">Roles are per home: one person can own one home and be a member, guest or co-owner of another. Owners and members control everything in their homes; owners also manage devices and people. Guests see nothing until grants arrive. Admin is separate and sees every home.</p>
  </section>
</div>

<style>
  .grid { display: grid; grid-template-columns: minmax(0, 1fr); gap: 16px; }
  @media (min-width: 900px) { .grid { grid-template-columns: 1fr 1.3fr; } }
</style>
