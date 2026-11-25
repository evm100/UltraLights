<script>
  import { onMount } from "svelte";
  import { session, restore, logout } from "./lib/session.svelte.js";
  import { live } from "./lib/live.svelte.js";
  import { route, go } from "./lib/router.svelte.js";
  import Login from "./pages/Login.svelte";
  import Home from "./pages/Home.svelte";
  import Light from "./pages/Light.svelte";
  import Admin from "./pages/admin/Admin.svelte";

  onMount(restore);

  let section = $derived(route.parts[0] || "home");
  let isAdmin = $derived(session.user?.role === "admin");
</script>

{#if !session.ready}
  <div class="boot"><span class="label">UltraLights</span></div>
{:else if !session.user}
  <Login />
{:else if section === "light"}
  {#key route.parts.slice(1, 3).join("/")}<Light dev={route.parts[1]} out={route.parts[2]} />{/key}
{:else}
  <header class="top">
    <a class="brand" href="#/" aria-label="Home">
      <svg viewBox="0 0 32 32" width="22" height="22" aria-hidden="true"><circle cx="16" cy="14" r="9" fill="var(--accent)" /><rect x="12" y="24" width="8" height="4" rx="1.5" fill="var(--text)" /></svg>
      <span>UltraLights</span>
    </a>
    <nav>
      <a href="#/" class:active={section === "home"}>Home</a>
      {#if isAdmin}<a href="#/admin" class:active={section === "admin"}>Admin</a>{/if}
    </nav>
    <span class="spacer"></span>
    <span class="conn" title={live.connected ? (live.broker ? "Live" : "Live, broker offline") : "Reconnecting"}>
      <span class="dot" class:on={live.connected && live.broker}></span>
    </span>
    <button class="btn ghost sm" onclick={() => logout().then(() => go("/"))}>{session.user.display_name} · Sign out</button>
  </header>
  {#if section === "admin" && isAdmin}
    <Admin parts={route.parts.slice(1)} />
  {:else}
    <Home />
  {/if}
{/if}

<style>
  .boot { min-height: 100vh; display: grid; place-items: center; }
  .top {
    position: sticky; top: 0; z-index: 10; display: flex; align-items: center; gap: 18px;
    padding: 12px 16px; background: rgba(20, 17, 15, 0.6); backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px); border-bottom: 1px solid var(--line);
  }
  .brand { display: flex; align-items: center; gap: 8px; text-decoration: none; font-weight: 700; letter-spacing: 0.02em; }
  nav { display: flex; gap: 4px; }
  nav a { text-decoration: none; padding: 6px 12px; border-radius: 999px; color: var(--muted); font-size: 0.9rem; }
  nav a.active { color: var(--text); background: var(--glass-2); }
  .spacer { flex: 1; }
  @media (max-width: 520px) { .brand span { display: none; } }
</style>
