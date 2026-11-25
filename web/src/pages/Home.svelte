<script>
  import { onMount } from "svelte";
  import { get } from "../lib/api.js";
  import { live, current, sendSet } from "../lib/live.svelte.js";
  import { kelvinToRgb, css } from "../lib/color.js";
  import { tiles, partsOf, summary, stateOf, reachable } from "../lib/parts.js";
  import { canControl } from "../lib/access.js";

  let homes = $state([]);
  onMount(async () => {
    try { homes = await get("/api/homes"); } catch { homes = []; }
  });

  // One tile per output, except a combined device (a bridged PC), which is one tile for all its parts.
  let items = $derived(tiles(Object.values(live.devices)).map((x) => {
    if (!x.combined) return { ...x, key: x.dev.id + "/" + x.o.key, name: x.o.name, caps: x.o.caps, control: canControl(x.o), s: current(x.o), href: `#/light/${x.dev.id}/${x.o.key}` };
    const parts = partsOf(x.dev);
    return { ...x, key: x.dev.id, name: x.dev.name, caps: ["onoff", "level", "color"], control: parts.some(canControl), s: summary(parts), parts,
             href: `#/light/${x.dev.id}` };
  }));
  let rooms = $derived(homes.flatMap((h) => h.rooms));
  let groups = $derived.by(() => {
    const g = rooms.map((r) => ({ name: r.name, items: items.filter((x) => x.room_id === r.id) })).filter((r) => r.items.length);
    const rest = items.filter((x) => !rooms.some((r) => r.id === x.room_id));
    if (rest.length) g.push({ name: rooms.length ? "Not in a room" : "All devices", items: rest });
    return g;
  });
  let lightsOn = $derived(items.filter((x) => x.caps.includes("level") && x.s.on).length);
  let switchesOn = $derived(items.filter((x) => !x.caps.includes("level") && x.caps.includes("onoff") && x.s.on).length);

  function glow(x) {
    const s = x.s;
    if (!s.on) return "transparent";
    let rgb = [242, 196, 109];
    if (s.color && x.caps.includes("color")) rgb = s.color;
    else if (s.cct && x.caps.includes("cct")) rgb = kelvinToRgb(s.cct);
    return css(rgb, 0.18 + 0.5 * (s.level ?? 1));
  }

  function toggle(e, x) {
    e.preventDefault();
    e.stopPropagation();
    if (x.combined) for (const p of x.parts) sendSet(x.dev.id, p.key, { on: !x.s.on });
    else sendSet(x.dev.id, x.o.key, { on: !x.s.on });
  }

  const pct = (x) => (x.caps.includes("level") ? `${Math.round((x.s.level ?? 1) * 100)}%` : "");
  const dot = (p) => { const s = stateOf(p); return s.on && s.color ? css(s.color) : "transparent"; };
</script>

<main class="page stack" style="gap:28px">
  <section class="hero stack">
    <h1>{homes.length === 1 ? homes[0].name : "Home"}</h1>
    <div class="row">
      <span class="chip glass"><svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2V17h5v-1.1c0-.8.4-1.5 1-2A6 6 0 0 0 12 3z" fill="none" stroke="var(--accent)" stroke-width="1.6"/></svg>{lightsOn} on</span>
      {#if items.some((x) => !x.caps.includes("level"))}
        <span class="chip glass"><svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M9 3v5M15 3v5M6 8h12v3a6 6 0 0 1-12 0zM12 17v4" fill="none" stroke="var(--info)" stroke-width="1.6"/></svg>{switchesOn} on</span>
      {/if}
      {#if !live.connected}<span class="tag warn">reconnecting</span>{:else if !live.broker}<span class="tag err">broker offline</span>{/if}
    </div>
  </section>

  {#if items.length === 0}
    <div class="glass panel muted">No devices yet{#if Object.keys(live.devices).length === 0}. An admin adds nodes in the node builder.{/if}</div>
  {/if}

  {#each groups as g (g.name)}
    <section class="stack">
      <span class="label">{g.name}</span>
      <div class="tiles">
        {#each g.items as x (x.key)}
          {@const s = x.s}
          <a class="tile glass" class:on={s.on} class:off-line={!x.dev.online} href={x.href} style={`--glow:${glow(x)}`}>
            <div class="row" style="justify-content:space-between;align-items:flex-start">
              <div class="stack" style="gap:2px">
                <strong>{x.name}</strong>
                <span class="small muted">{x.dev.online ? (s.on ? `On ${pct(x)}` : "Off") : "Offline"}</span>
              </div>
              {#if x.caps.includes("onoff") && x.control}
                <button class="pw" class:on={s.on} aria-label={s.on ? "Turn off" : "Turn on"} onclick={(e) => toggle(e, x)} disabled={!x.dev.online}>
                  <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M12 3v8M7.5 6.5a7 7 0 1 0 9 0" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
                </button>
              {/if}
            </div>
            {#if x.combined}
              <div class="parts" aria-label="Parts">
                {#each x.parts as p (p.key)}
                  <span class="pd" class:gone={!reachable(p)} style={`background:${dot(p)}`} title={`${p.name}${reachable(p) ? "" : " (unreachable)"}`}></span>
                {/each}
              </div>
            {/if}
            {#if x.caps.includes("level")}
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
  .parts { position: relative; display: flex; gap: 5px; margin-top: -6px; }
  .pd { width: 9px; height: 9px; border-radius: 50%; border: 1px solid var(--line); }
  .pd.gone { border-style: dashed; opacity: 0.5; }
  .bar { position: relative; height: 4px; border-radius: 4px; background: rgba(255, 255, 255, 0.12); overflow: hidden; }
  .bar span { position: absolute; inset: 0 auto 0 0; background: var(--text); border-radius: 4px; transition: width 0.25s; }
</style>
