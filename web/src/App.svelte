<script>
  import { onMount } from "svelte";
  import { session, restore, logout } from "./lib/session.svelte.js";
  import { live } from "./lib/live.svelte.js";
  import { route, go } from "./lib/router.svelte.js";
  import Login from "./pages/Login.svelte";
  import Home from "./pages/Home.svelte";
  import Light from "./pages/Light.svelte";
  import Admin from "./pages/admin/Admin.svelte";
  import Rooms from "./pages/Rooms.svelte";
  import Room from "./pages/Room.svelte";
  import Settings from "./pages/Settings.svelte";
  import Scenes from "./pages/Scenes.svelte";
  import Invite from "./pages/Invite.svelte";
  import { homesState, loadHomes, managed } from "./lib/homes.svelte.js";

  onMount(restore);

  let section = $derived(route.parts[0] || "home");
  let isAdmin = $derived(!!session.user?.admin);
  // Reload homes on every (re)connect: the server closes the socket when access changes.
  $effect(() => { if (session.user && live.connected) loadHomes(); });
  let settingsHome = $derived(managed(homesState.list)[0]);
  // Scenes and schedules belong to owners and members; a guest-only account doesn't get the tab.
  let hasScenes = $derived(isAdmin || homesState.list.some((h) => h.role !== "guest"));
</script>

{#if !session.ready}
  <div class="boot"><span class="label">UltraLights</span></div>
{:else if section === "invite"}
  <Invite token={route.parts[1] ?? ""} />
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
      <a href="#/" class:active={section === "home" || section === "rooms"}>Home</a>
      <a href="#/lights" class:active={section === "lights"}>Lights</a>
      {#if hasScenes}<a href="#/scenes" class:active={section === "scenes"}>Scenes</a>{/if}
      {#if isAdmin}<a href="#/admin" class:active={section === "admin"}>Admin</a>{/if}
    </nav>
    <span class="spacer"></span>
    {#if settingsHome}
      <a class="gear" class:active={section === "settings"} href={`#/settings/${settingsHome.id}`} aria-label="Home settings" title="Home settings">
        <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4z" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M19.4 13.5a7.6 7.6 0 0 0 0-3l2-1.6-2-3.4-2.4 1a7.6 7.6 0 0 0-2.6-1.5L14 2.5h-4l-.4 2.5A7.6 7.6 0 0 0 7 6.5l-2.4-1-2 3.4 2 1.6a7.6 7.6 0 0 0 0 3l-2 1.6 2 3.4 2.4-1a7.6 7.6 0 0 0 2.6 1.5l.4 2.5h4l.4-2.5a7.6 7.6 0 0 0 2.6-1.5l2.4 1 2-3.4z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>
      </a>
    {/if}
    <span class="conn" title={live.connected ? (live.broker ? "Live" : "Live, broker offline") : "Reconnecting"}>
      <span class="dot" class:on={live.connected && live.broker}></span>
    </span>
    <button class="btn ghost sm" onclick={() => logout().then(() => go("/"))}>{session.user.display_name} · Sign out</button>
  </header>
  {#if section === "admin" && isAdmin}
    <Admin parts={route.parts.slice(1)} />
  {:else if section === "rooms" && route.parts[1]}
    {#key route.parts[1]}<Room id={route.parts[1]} />{/key}
  {:else if section === "lights"}
    <Home />
  {:else if section === "scenes"}
    <Scenes />
  {:else if section === "settings" && route.parts[1]}
    {#key route.parts[1]}<Settings id={route.parts[1]} />{/key}
  {:else}
    <Rooms />
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
  .gear { display: grid; place-items: center; width: 34px; height: 34px; border-radius: 50%; color: var(--muted); }
  .gear:hover, .gear.active { color: var(--text); background: var(--glass-2); }
  @media (max-width: 520px) { .brand span { display: none; } }
</style>
