<script>
  // Light control screen: ON pill, big percentage, tall brightness slider, and WHITE / COLOR
  // tabs that appear only when the output has the cct / color capability.
  // A combined device (a bridged PC) is one light: the same controls drive all its parts or,
  // with the "Applies to" picker under the color sliders, one part.
  import { untrack } from "svelte";
  import VSlider from "../components/VSlider.svelte";
  import HSlider from "../components/HSlider.svelte";
  import { live, current, sendLive, sendSet } from "../lib/live.svelte.js";
  import { liveSender } from "../lib/throttle.js";
  import { kelvinToRgb, hsvToRgb, rgbToHsv, css } from "../lib/color.js";
  import { partsOf, summary, stateOf, reachable, partIcon, PART_ICONS } from "../lib/parts.js";
  import { usableCaps } from "../lib/access.js";
  import Valve from "../components/Valve.svelte";
  import Cycle from "../components/Cycle.svelte";

  let { dev, out } = $props();

  let device = $derived(live.devices[dev]);
  let combined = $derived(!!device?.combined);
  let parts = $derived(combined ? partsOf(device) : []);
  let sel = $state(untrack(() => out) ?? "all"); // combined only: "all" or a part key
  let part = $derived(combined && sel !== "all" ? parts.find((p) => p.key === sel) : null);
  // The output whose capabilities and settings the controls show.
  let o = $derived(combined ? (part ?? parts[0]) : device?.outputs.find((x) => x.key === out));
  // Capabilities the output has and the viewer may use (see lib/access.js).
  let caps = $derived(combined ? [...new Set(parts.flatMap((p) => usableCaps(p)))] : usableCaps(o));
  let cctRange = $derived(o?.meta?.cct_range ?? [2700, 6500]);
  let shownState = $derived(combined ? (part ? stateOf(part) : summary(parts)) : current(o));
  // Who receives what the controls send.
  let targets = $derived(combined ? (part ? [part.key] : parts.map((p) => p.key)) : [out]);
  let unreachable = $derived((part ? [part] : parts).filter((p) => !reachable(p)));

  // Local values follow the device unless the user is holding a control (and for a moment after).
  let on = $state(false);
  let level = $state(1);
  let cct = $state(3000);
  let hue = $state(30);
  let sat = $state(0.8);
  let white = $state(0);
  let holdUntil = 0;
  let resync = $state(0); // bumped when a hold ends, so the device's latest report is shown
  let resyncTimer;
  let tab = $state(null);

  $effect(() => {
    const s = shownState;
    resync;
    if (!o) return;
    untrack(() => {
      if (performance.now() < holdUntil) return;
      on = !!s.on;
      if (s.level !== undefined) level = s.level;
      if (s.cct !== undefined) cct = s.cct;
      if (s.white !== undefined) white = s.white;
      if (s.color) [hue, sat] = rgbToHsv(s.color);
    });
  });
  $effect(() => {
    if (tab === null && o) tab = caps.includes("cct") ? "white" : caps.includes("color") ? "color" : null;
  });

  const sendAll = (fn) => (p) => { for (const k of targets) fn(dev, k, p); };
  const sender = liveSender(sendAll(sendLive), sendAll(sendSet));
  function hold() {
    holdUntil = performance.now() + 1500;
    clearTimeout(resyncTimer);
    resyncTimer = setTimeout(() => resync++, 1550);
  }
  function pick(key) {
    sel = key;
    holdUntil = 0; // show the new target's values right away
    resync++;
  }

  function onLevel(v) { hold(); level = v; on = v > 0; sender.update(v > 0 ? { on: true, level: v } : { on: false }); }
  function endLevel(v) { hold(); level = v; on = v > 0; sender.release(v > 0 ? { on: true, level: v } : { on: false }); }
  function onCct(v) { hold(); cct = v; sender.update({ cct: v }); }
  function endCct(v) { hold(); cct = v; sender.release({ cct: v, on: true }); }
  const rgb = () => hsvToRgb(hue, sat, 1);
  function onHue(v) { hold(); hue = v; sender.update({ color: rgb() }); }
  function endHue(v) { hold(); hue = v; sender.release({ color: rgb(), on: true }); }
  function onSat(v) { hold(); sat = v; sender.update({ color: rgb() }); }
  function endSat(v) { hold(); sat = v; sender.release({ color: rgb(), on: true }); }
  function onWhite(v) { hold(); white = v; sender.update({ white: v }); }
  function endWhite(v) { hold(); white = v; sender.release({ white: v, on: true }); }
  function toggle() { hold(); on = !on; sendAll(sendSet)({ on }); }



  let shown = $derived(
    caps.includes("color") && (tab === "color" || !caps.includes("cct")) ? hsvToRgb(hue, sat, 1)
    : caps.includes("cct") ? kelvinToRgb(cct) : [244, 233, 214]);
  let kelvinTrack = $derived(`linear-gradient(90deg, ${css(kelvinToRgb(cctRange[0]))}, ${css(kelvinToRgb((cctRange[0] + cctRange[1]) / 2))}, ${css(kelvinToRgb(cctRange[1]))})`);
  const hueTrack = "linear-gradient(90deg,#f00,#ff0,#0f0,#0ff,#00f,#f0f,#f00)";
  let satTrack = $derived(`linear-gradient(90deg, #fff, ${css(hsvToRgb(hue, 1, 1))})`);

  // Part picker swatches: a part's own color when lit; "All" mixes the lit parts' colors.
  const tint = (s) => (s.on && s.color ? css(s.color) : "var(--faint)");
  let allTint = $derived.by(() => {
    const lit = parts.map(stateOf).filter((s) => s.on && s.color).map((s) => css(s.color));
    if (lit.length < 2) return lit[0] ?? "var(--faint)";
    return `conic-gradient(${[...lit, lit[0]].join(", ")})`;
  });
  let title = $derived(combined ? device?.name : (o?.name ?? out));
  let subtitle = $derived(combined ? (part ? part.name : `All ${parts.length} parts`) : (device?.name ?? dev));
</script>

<main class="screen" style={`--tint:${css(shown, on ? 0.18 + level * 0.35 : 0.04)}`}>
  <header>
    <a class="back" href="#/lights" aria-label="Back" onclick={(e) => { if (history.length > 1) { e.preventDefault(); history.back(); } }}>
      <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path d="M15 5l-7 7 7 7" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
    </a>
    <div class="title">
      <div class="name">{title}</div>
      <div class="sub">{subtitle}{device && !device.online ? " · offline" : ""}</div>
    </div>
    <span class="back"></span>
  </header>

  {#if !o}
    <p class="muted center">This output isn't available.</p>
  {:else}
    <section class="main">
      {#if caps.includes("onoff")}
        <button class="pill" class:on onclick={toggle} disabled={!device.online}>{on ? "ON" : "OFF"}</button>
      {/if}
      {#if caps.includes("level")}
        <div class="pct">{on ? Math.round(level * 100) : 0}%</div>
        <VSlider value={on ? level : 0} oninput={onLevel} onchange={endLevel} fill={css(shown.map((c) => Math.round(c * 0.25 + 244 * 0.75)), 0.95)} disabled={!device.online} />
      {:else if o.kind === "solenoid"}
        <Valve {dev} {o} online={device.online} large />
      {:else if o.kind === "cycle"}
        <Cycle {dev} {o} online={device.online} />
      {:else if caps.includes("onoff")}
        <div class="big-switch" class:on>{on ? "On" : "Off"}</div>
      {/if}
      {#if combined && device.online && unreachable.length}
        <p class="small muted away">
          {unreachable.map((p) => p.name).join(", ")} {unreachable.length === 1 ? "isn't" : "aren't"} reachable right now
          (that machine or its OpenRGB is off). {unreachable.length === 1 ? "It gets" : "They get"} these settings when {unreachable.length === 1 ? "it's" : "they're"} back.
        </p>
      {/if}
    </section>

    {#if caps.includes("cct") || caps.includes("color") || combined}
      <section class="sheet">
        <div class="tabs" role="tablist">
          {#if caps.includes("color")}
            <button role="tab" aria-selected={tab === "color"} class:active={tab === "color"} onclick={() => (tab = "color")}>
              <span class="sw" style="background:conic-gradient(#f55,#fd5,#5f8,#5cf,#a6f,#f55)"></span>COLOR
            </button>
          {/if}
          {#if caps.includes("cct")}
            <button role="tab" aria-selected={tab === "white"} class:active={tab === "white"} onclick={() => (tab = "white")}>
              <span class="sw" style="background:linear-gradient(90deg,#ffb46b,#fff,#cfe3ff)"></span>WHITE
            </button>
          {/if}
        </div>
        {#if tab === "white" && caps.includes("cct")}
          <div class="ctl"><span class="label">{Math.round(cct / 50) * 50}K</span>
            <HSlider value={cct} min={cctRange[0]} max={cctRange[1]} step={50} track={kelvinTrack} thumb={css(kelvinToRgb(cct))} label="Color temperature" oninput={onCct} onchange={endCct} disabled={!device.online} />
          </div>
        {:else if caps.includes("color")}
          <div class="ctl"><span class="label">Hue</span>
            <HSlider value={hue} min={0} max={359} step={1} track={hueTrack} thumb={css(hsvToRgb(hue, 1, 1))} label="Hue" oninput={onHue} onchange={endHue} disabled={!device.online} />
          </div>
          <div class="ctl"><span class="label">Vibrancy</span>
            <HSlider value={sat} track={satTrack} thumb={css(hsvToRgb(hue, sat, 1))} label="Saturation" oninput={onSat} onchange={endSat} disabled={!device.online} />
          </div>
          {#if caps.includes("white")}
            <div class="ctl"><span class="label">White</span>
              <HSlider value={white} track="linear-gradient(90deg,#333,#fff)" label="White channel" oninput={onWhite} onchange={endWhite} disabled={!device.online} />
            </div>
          {/if}
        {/if}

        {#if combined && parts.length > 1}
          <div class="applies">
            <span class="label">Applies to</span>
            <div class="chips" role="radiogroup" aria-label="Which part the controls change">
              <button role="radio" aria-checked={sel === "all"} class:active={sel === "all"} onclick={() => pick("all")} title="All parts">
                <span class="ico ring" style={`background:${allTint}`}>
                  <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d={PART_ICONS.all} fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
                </span><span class="nm">All</span>
              </button>
              {#each parts as p (p.key)}
                {@const ps = stateOf(p)}
                <button role="radio" aria-checked={sel === p.key} class:active={sel === p.key} class:gone={!reachable(p)} onclick={() => pick(p.key)}
                        title={reachable(p) ? `${p.name}: ${ps.on ? `on ${Math.round((ps.level ?? 1) * 100)}%` : "off"}` : `${p.name}: unreachable`}>
                  <span class="ico" style={`color:${tint(ps)}`}>
                    <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d={partIcon(p)} fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
                  </span><span class="nm">{p.name}</span>
                </button>
              {/each}
            </div>
          </div>
        {/if}
      </section>
    {/if}
  {/if}
</main>

<style>
  .screen {
    min-height: 100vh; display: flex; flex-direction: column; max-width: 520px; margin: 0 auto;
    background: radial-gradient(90% 60% at 50% 30%, var(--tint), transparent 75%);
    transition: background 0.3s;
  }
  header { display: grid; grid-template-columns: 44px 1fr 44px; align-items: center; padding: 14px 12px 4px; }
  .back { width: 44px; height: 44px; display: grid; place-items: center; color: var(--text); }
  .title { text-align: center; }
  .name { font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase; font-size: 0.85rem; }
  .sub { color: var(--muted); font-size: 0.78rem; }
  .main { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 14px; padding: 18px 16px; }
  .pill { padding: 6px 26px; border-radius: 999px; border: 1px solid var(--line); background: rgba(0, 0, 0, 0.3); font-weight: 700; letter-spacing: 0.12em; cursor: pointer; }
  .pill.on { background: var(--pill); color: var(--pill-text); border-color: transparent; }
  .pct { font-size: 2.6rem; font-weight: 300; letter-spacing: 0.02em; }
  .big-switch { margin-top: 40px; font-size: 3rem; font-weight: 300; color: var(--muted); }
  .big-switch.on { color: var(--text); }
  .away { max-width: 34ch; text-align: center; margin: 0; }
  .sheet {
    background: var(--glass-strong); backdrop-filter: blur(22px); -webkit-backdrop-filter: blur(22px);
    border-top: 1px solid var(--line); border-radius: 22px 22px 0 0; padding: 14px 22px 28px; display: flex; flex-direction: column; gap: 12px;
  }
  .tabs { display: flex; justify-content: space-around; }
  .tabs button { background: none; border: 0; display: flex; align-items: center; gap: 8px; padding: 8px 12px; color: var(--muted); font-weight: 700; letter-spacing: 0.12em; font-size: 0.8rem; cursor: pointer; }
  .tabs button.active { color: var(--text); }
  .sw { width: 18px; height: 18px; border-radius: 50%; display: inline-block; }
  .ctl { display: flex; flex-direction: column; align-items: center; gap: 2px; }
  .ctl :global(.hs) { width: 100%; }
  .applies { display: flex; flex-direction: column; align-items: center; gap: 8px; padding-top: 10px; border-top: 1px solid var(--line); }
  /* Equal columns: five or six parts fit a phone; more wrap to a second row. */
  .chips { display: grid; grid-template-columns: repeat(auto-fit, minmax(58px, 1fr)); gap: 6px; width: 100%; }
  .chips button {
    display: flex; flex-direction: column; align-items: center; gap: 5px; padding: 8px 4px 7px; border-radius: 16px; min-width: 0;
    border: 1px solid transparent; background: none; color: var(--muted); font-size: 0.72rem; font-weight: 600; cursor: pointer;
  }
  .chips button.active { background: var(--glass-2); border-color: var(--line); color: var(--text); }
  .chips button.gone .ico { border-style: dashed; opacity: 0.55; }
  .nm { max-width: 100%; overflow: hidden; text-align: center; line-height: 1.15; display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; hyphens: auto; overflow-wrap: break-word; }
  .ico { width: 40px; height: 40px; border-radius: 50%; display: grid; place-items: center; background: rgba(0, 0, 0, 0.35); border: 1.5px solid var(--line); }
  .chips button.active .ico { border-color: var(--text); }
  .ring { color: #fff; }
  .ring svg { filter: drop-shadow(0 0 2px rgba(0, 0, 0, 0.8)); }
  .center { text-align: center; margin-top: 30vh; }
</style>
