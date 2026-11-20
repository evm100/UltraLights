<script>
  import { login } from "../lib/session.svelte.js";
  import { errText } from "../lib/api.js";

  let username = $state("");
  let password = $state("");
  let error = $state("");
  let busy = $state(false);

  async function submit(e) {
    e.preventDefault();
    busy = true;
    error = "";
    try {
      await login(username.trim().toLowerCase(), password);
    } catch (err) {
      error = err.status === 401 ? "Wrong username or password." : errText(err);
    } finally {
      busy = false;
    }
  }
</script>

<main class="wrap">
  <form class="glass card stack" onsubmit={submit}>
    <div class="stack" style="gap:4px">
      <span class="label">UltraLights</span>
      <h1>Welcome home</h1>
    </div>
    <label class="field">Username<input autocomplete="username" bind:value={username} required /></label>
    <label class="field">Password<input type="password" autocomplete="current-password" bind:value={password} required /></label>
    {#if error}<div class="msg err" role="alert">{error}</div>{/if}
    <button class="btn solid" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
  </form>
</main>

<style>
  .wrap { min-height: 100vh; display: grid; place-items: center; padding: 16px; }
  .card { width: min(380px, 100%); padding: 28px; gap: 16px; }
</style>
