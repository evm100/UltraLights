<script>
  // #/invite/<token>: what the link offers, then join. Signed out, the person creates an
  // account here (or signs in to an existing one first); signed in, one tap joins.
  import { onMount } from "svelte";
  import { get, post, errText } from "../lib/api.js";
  import { session, login, restore } from "../lib/session.svelte.js";
  import { loadHomes } from "../lib/homes.svelte.js";
  import { go } from "../lib/router.svelte.js";

  let { token } = $props();
  let offer = $state(null);
  let error = $state("");
  let busy = $state(false);
  let haveAccount = $state(false);
  let f = $state({ username: "", display_name: "", password: "" });

  onMount(async () => {
    try { offer = await get(`/api/invites/${encodeURIComponent(token)}`); }
    catch (e) { error = e.status === 404 ? "This invite link has been used, revoked or has expired. Ask for a new one." : errText(e); }
  });

  async function act(fn) {
    busy = true; error = "";
    try { await fn(); } catch (e) { error = e.status === 409 ? "That username is taken." : errText(e); }
    finally { busy = false; }
  }
  const accept = (body = {}) => act(async () => {
    await post(`/api/invites/${encodeURIComponent(token)}/accept`, body);
    if (!session.user) await restore(); // a new account is signed in by the server
    await loadHomes();
    go("/rooms");
  });
  const create = (e) => { e.preventDefault(); accept({ ...f, username: f.username.trim().toLowerCase() }); };
  const signIn = (e) => { e.preventDefault(); act(() => login(f.username.trim().toLowerCase(), f.password)); };
  const roleText = (r) => (r === "owner" ? "an owner" : "a member");
</script>

<main class="wrap">
  <div class="glass card stack">
    <span class="label">UltraLights</span>
    {#if offer}
      <h1>Join {offer.home}</h1>
      <p class="muted">{offer.invited_by ? `${offer.invited_by} invited you` : "You're invited"} as {roleText(offer.role)}.
        {offer.role === "owner" ? "Owners control everything and manage devices and people." : "Members control everything and make scenes and schedules."}</p>
      {#if error}<div class="msg err" role="alert">{error}</div>{/if}

      {#if session.user}
        <button class="btn solid" disabled={busy} onclick={() => accept()}>Join as {session.user.display_name}</button>
      {:else if haveAccount}
        <form class="stack" onsubmit={signIn}>
          <label class="field">Username<input autocomplete="username" bind:value={f.username} required /></label>
          <label class="field">Password<input type="password" autocomplete="current-password" bind:value={f.password} required /></label>
          <button class="btn solid" disabled={busy}>Sign in</button>
          <button type="button" class="btn ghost sm" onclick={() => (haveAccount = false)}>I need a new account</button>
        </form>
      {:else}
        <form class="stack" onsubmit={create}>
          <label class="field">Username<input autocomplete="username" bind:value={f.username} pattern="[a-zA-Z0-9_.\-]+" minlength="2" maxlength="64" required /></label>
          <label class="field">Your name<input autocomplete="name" bind:value={f.display_name} maxlength="64" /></label>
          <label class="field">Password<input type="password" autocomplete="new-password" bind:value={f.password} minlength="10" required />
            <span class="small faint">At least 10 characters.</span></label>
          <button class="btn solid" disabled={busy}>Create account and join</button>
          <button type="button" class="btn ghost sm" onclick={() => (haveAccount = true)}>I already have an account</button>
        </form>
      {/if}
    {:else if error}
      <h1>Invite</h1>
      <div class="msg err" role="alert">{error}</div>
      <a class="btn" href="#/">Go to UltraLights</a>
    {/if}
  </div>
</main>

<style>
  .wrap { min-height: 100vh; display: grid; place-items: center; padding: 20px; }
  .card { width: min(420px, 100%); padding: 28px; }
</style>
