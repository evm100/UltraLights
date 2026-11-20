<script>
  import { onMount } from "svelte";
  import { get } from "../lib/api.js";
  import { live, current, sendSet } from "../lib/live.svelte.js";
  import { kelvinToRgb, css } from "../lib/color.js";

  let homes = $state([]);
  onMount(async () => {
    try { homes = await get("/api/homes"); } catch { homes = []; }
  });

  let outputs = $derived(
    Object.values(live.devices).flatMap((d) =>
      d.outputs.filter((o) => o.kind !== "motion").map((o) => ({ dev: d, o }))));
  let rooms = $derived(homes.flatMap((h) => h.rooms));
  let groups = $derived.by(() => {
    const g = rooms.map((r) => ({ name: r.name, items: outputs.filter((x) => x.o.room_id === r.id) })).filter((r) => r.items.length);
    const rest = outputs.filter((x) => !rooms.some((r) => r.id === x.o.room_id));
    if (rest.length) g.push({ name: rooms.length ? "Not in a room" : "All devices", items: rest });
    return g;
  });
  let lightsOn = $derived(outputs.filter((x) => x.o.caps.includes("level") && current(x.o).on).length);
  let switchesOn = $derived(outputs.filter((x) => !x.o.caps.includes("level") && x.o.caps.includes("onoff") && current(x.o).on).length);

  function glow(o) {
    const s = current(o);
    if (!s.on) return "transparent";
    let rgb = [242, 196, 109];
    if (s.color && o.caps.includes("color")) rgb = s.color;
    else if (s.cct && o.caps.includes("cct")) rgb = kelvinToRgb(s.cct);
    return css(rgb, 0.18 + 0.5 * (s.level ?? 1));
  }

  function toggle(e, d, o) {
    e.preventDefault();
    e.stopPropagation();
    sendSet(d.id, o.key, { on: !current(o).on });
  }

  const pct = (o) => (o.caps.includes("level") ? `${Math.round((current(o).level ?? 1) * 100)}%` : "");
</script>

<main class="page stack" style="gap:28px">
  <section class="hero stack">
    <h1>{homes.length === 1 ? homes[0].name : "Home"}</h1>
    <div class="row">
      <span class="chip glass"><svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2V17h5v-1.1c0-.8.4-1.5 1-2A6 6 0 0 0 12 3z" fill="none" stroke="var(--accent)" stroke-width="1.6"/></svg>{lightsOn} on</span>
      {#if outputs.some((x) => !x.o.caps.includes("level"))}
        <span class="chip glass"><svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M9 3v5M15 3v5M6 8h12v3a6 6 0 0 1-12 0zM12 17v4" fill="none" stroke="var(--info)" stroke-width="1.6"/></svg>{switchesOn} on</span>
      {/if}
      {#if !live.connected}<span class="tag warn">reconnecting</span>{:else if !live.broker}<span class="tag err">broker offline</span>{/if}
    </div>
  </section>

  {#if outputs.length === 0}
    <div class="glass panel muted">No devices yet{#if Object.keys(live.devices).length === 0}. An admin adds nodes in the node builder.{/if}</div>
  {/if}

  {#each groups as g (g.name)}
    <section class="stack">
      <span class="label">{g.name}</span>
      <div class="tiles">
        {#each g.items as { dev, o } (dev.id + o.key)}
          {@const s = current(o)}
          <a class="tile glass" class:on={s.on} class:off-line={!dev.online} href={`#/light/${dev.id}/${o.key}`} style={`--glow:${glow(o)}`}>
            <div class="row" style="justify-content:space-between;align-items:flex-start">
              <div class="stack" style="gap:2px">
                <strong>{o.name}</strong>
                <span class="small muted">{dev.online ? (s.on ? `On ${pct(o)}` : "Off") : "Offline"}</span>
              </div>
              {#if o.caps.includes("onoff")}
                <button class="pw" class:on={s.on} aria-label={s.on ? "Turn off" : "Turn on"} onclick={(e) => toggle(e, dev, o)} disabled={!dev.online}>
                  <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M12 3v8M7.5 6.5a7 7 0 1 0 9 0" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
                </button>
              {/if}
            </div>
            {#if o.caps.includes("level")}
              <div class="bar"><span style={`width:${s.on ? Math.round((s.level ?? 1) * 100) : 0}%`}></span></div>
            {/if}
          </a>
        {/each}
      </div>
    </section>
  {/each}
</main>

<style>
  .hero { padding-top: 24px; gap: 14px; }
  .hero h1 { font-size: clamp(2rem, 6vw, 3rem); }
  .chip { display: inline-flex; gap: 8px; align-items: center; padding: 8px 16px; border-radius: 999px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; font-size: 0.85rem; }
  .tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 12px; }
  .tile {
    position: relative; overflow: hidden; display: flex; flex-direction: column; gap: 14px; justify-content: space-between;
    padding: 14px; min-height: 110px; text-decoration: none; transition: transform 0.15s ease, background 0.2s;
  }
  .tile::before { content: ""; position: absolute; inset: 0; background: radial-gradient(120% 90% at 0% 0%, var(--glow), transparent 70%); pointer-events: none; }
  .tile:hover { transform: translateY(-2px); }
  .tile.off-line { opacity: 0.55; }
  .pw { position: relative; width: 36px; height: 36px; border-radius: 50%; border: 1px solid var(--line); background: rgba(0, 0, 0, 0.25); color: var(--muted); display: grid; place-items: center; cursor: pointer; }
  .pw.on { background: var(--pill); color: var(--pill-text); border-color: transparent; }
  .bar { position: relative; height: 4px; border-radius: 4px; background: rgba(255, 255, 255, 0.12); overflow: hidden; }
  .bar span { position: absolute; inset: 0 auto 0 0; background: var(--text); border-radius: 4px; transition: width 0.25s; }
</style>
