<script>
  // The tile grid shared by Home and a room's page: one tile per light or switch (a combined
  // device is one tile), with a power button when the viewer may control it.
  import { sendSet } from "../lib/live.svelte.js";
  import { kelvinToRgb, css } from "../lib/color.js";
  import { stateOf, reachable } from "../lib/parts.js";
  import { cycleStatus } from "../lib/cycle.js";

  let { items } = $props();

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

<div class="tiles">
  {#each items as x (x.key)}
    {@const s = x.s}
    <a class="tile glass" class:on={s.on} class:off-line={!x.dev.online} href={x.href} style={`--glow:${glow(x)}`}>
      <div class="row" style="justify-content:space-between;align-items:flex-start">
        <div class="stack" style="gap:2px">
          <strong>{x.name}</strong>
          <span class="small muted">{!x.dev.online ? "Offline" : x.o?.kind === "solenoid" ? (s.on ? "Open" : "Closed") : x.o?.kind === "cycle" ? cycleStatus(x.o, s) : s.on ? `On ${pct(x)}` : "Off"}</span>
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

<style>
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
