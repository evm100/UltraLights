<script>
  import { onMount } from "svelte";
  import { get, post, errText } from "../../lib/api.js";

  let homes = $state([]);
  let users = $state([]);
  let error = $state("");
  let note = $state("");
  let homeName = $state("");
  let roomName = $state({});
  let nu = $state({ username: "", password: "", role: "owner", display_name: "", home_id: "" });

  async function load() {
    try { [homes, users] = await Promise.all([get("/api/admin/homes"), get("/api/admin/users")]); }
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
    run(() => post("/api/admin/users", body), `User ${body.username} created.`).then(() => (nu = { username: "", password: "", role: "owner", display_name: "", home_id: "" }));
  }
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
        <tr><td><strong>{u.username}</strong> <span class="muted small">{u.display_name}</span></td><td><span class="tag">{u.role}</span></td>
          <td class="small muted">{u.homes.map((m) => `${homeNameOf(m.home_id)} (${m.role})`).join(", ")}</td></tr>
      {/each}
    </tbody></table>
    <span class="label">Add a person</span>
    <div class="row">
      <input bind:value={nu.username} placeholder="username" autocomplete="off" />
      <input bind:value={nu.display_name} placeholder="Display name" />
      <input type="password" bind:value={nu.password} placeholder="Password (10+ chars)" autocomplete="new-password" />
      <select bind:value={nu.role}><option value="owner">Owner</option><option value="member">Member</option><option value="guest">Guest</option><option value="admin">Admin</option></select>
      <select bind:value={nu.home_id}><option value="">No home</option>{#each homes as h}<option value={h.id}>{h.name}</option>{/each}</select>
      <button class="btn solid sm" disabled={!nu.username || nu.password.length < 10} onclick={addUser}>Create</button>
    </div>
    <p class="small muted">Owners see and control the nodes in their homes. Guest grants with time windows come in the Homes and people phase.</p>
  </section>
</div>

<style>
  .grid { display: grid; grid-template-columns: minmax(0, 1fr); gap: 16px; }
  @media (min-width: 900px) { .grid { grid-template-columns: 1fr 1.3fr; } }
</style>
