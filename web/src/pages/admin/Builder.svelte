<script>
  // Node builder: chip first, then what's wired to which pin, checked live with the same
  // rules as the server and firmware (contracts/chips.json). Starts blank; saved templates
  // (the PCB revisions) are the only presets.
  import { onMount } from "svelte";
  import { get, post, put, errText } from "../../lib/api.js";
  import { go } from "../../lib/router.svelte.js";
  import { CHIPS, checkBoard, describe, pinOptions, DEFAULT_HZ } from "../../lib/pins.js";

  let { dev = undefined } = $props();

  let templates = $state([]);
  let homes = $state([]);
  let chip = $state("esp32c3");
  let boardName = $state("");
  let hwRev = $state("");
  let rows = $state([]);
  let resetOn = $state(true);
  let resetGpio = $state(9);
  let resetLow = $state(true);
  let ledOn = $state(false);
  let ledGpio = $state(8);
  let ledLow = $state(false);
  let nodeName = $state("");
  let homeId = $state("");
  let tplName = $state("");
  let error = $state("");
  let ok = $state("");
  let busy = $state(false);
  let editing = $state(null);

  const TYPES = [["pwm", "PWM"], ["argb", "ARGB strip"], ["switch", "Switch"], ["motion", "Motion"], ["servo", "Servo"]];
  // A servo presses an appliance's own mode button: these are the defaults for a new row
  // (tune the angles and hold on the real thing). modes is edited as comma-separated text.
  const SERVO = { press_deg: 60, rest_deg: 90, hold_ms: 250, release_ms: 400, modesText: "on" };
  const modeList = (t) => t.split(",").map((m) => m.trim().toLowerCase()).filter(Boolean);
  const ORDERS = ["GRB", "RGB", "BRG", "RBG", "GBR", "BGR", "GRBW", "RGBW"];

  function load(board) {
    chip = board.chip;
    boardName = board.board ?? "";
    hwRev = board.hw_rev ?? "";
    rows = (board.channels ?? []).map((c) => ({ hz: DEFAULT_HZ, pixels: 60, order: "GRB", chipset: "ws2812", active_low: false, invert: false, name: "", ...SERVO,
                                                ...c, ...(c.modes ? { modesText: c.modes.join(", ") } : {}) }));
    resetOn = !!board.reset_button;
    resetGpio = board.reset_button?.gpio ?? CHIPS[chip].boot_button;
    resetLow = board.reset_button?.active_low ?? true;
    ledOn = !!board.status_led;
    ledGpio = board.status_led?.gpio ?? 8;
    ledLow = board.status_led?.active_low ?? false;
  }

  function blank() {
    load({ chip, board: "", channels: [], reset_button: { gpio: CHIPS[chip].boot_button } });
  }

  onMount(async () => {
    try {
      [templates, homes] = await Promise.all([get("/api/admin/templates"), get("/api/admin/homes")]);
      if (dev) {
        editing = await get(`/api/admin/nodes/${dev}`);
        nodeName = editing.name;
        load(editing.board);
      } else blank();
    } catch (e) { error = errText(e); }
  });

  function startFrom(e) {
    const v = e.target.value;
    if (v === "blank") blank();
    else load(templates.find((t) => String(t.id) === v).board);
    e.target.value = "";
  }

  function nextId(type) {
    const base = { pwm: "pwm", argb: "strip", switch: "sw", motion: "motion", servo: "servo" }[type];
    for (let i = 1; ; i++) if (!rows.some((r) => r.id === `${base}${i}`)) return `${base}${i}`;
  }

  function add(type = "pwm") {
    const used = new Set(rows.map((r) => r.gpio));
    const free = pinOptions(chip).find((p) => !p.note && !used.has(p.gpio) && p.gpio !== resetGpio);
    rows.push({ id: nextId(type), name: "", type, gpio: free?.gpio ?? 0, hz: DEFAULT_HZ, pixels: 60, order: "GRB", chipset: "ws2812", active_low: false, invert: false, ...SERVO });
  }

  let board = $derived.by(() => {
    const b = { v: 1, chip, board: boardName.trim() || "Untitled board", channels: rows.map((r) => {
      const c = { id: r.id, type: r.type, gpio: Number(r.gpio) };
      if (r.name.trim()) c.name = r.name.trim();
      if (r.type === "pwm") { c.hz = Number(r.hz); if (r.invert) c.invert = true; }
      if (r.type === "argb") { c.pixels = Number(r.pixels); c.order = r.order; c.chipset = r.chipset; }
      if ((r.type === "switch" || r.type === "motion") && r.active_low) c.active_low = true;
      if (r.type === "servo") {
        c.press_deg = Number(r.press_deg); c.rest_deg = Number(r.rest_deg);
        c.hold_ms = Number(r.hold_ms); c.release_ms = Number(r.release_ms);
        c.modes = modeList(r.modesText);
      }
      return c;
    }) };
    if (hwRev.trim()) b.hw_rev = hwRev.trim();
    if (resetOn) b.reset_button = { gpio: Number(resetGpio), active_low: resetLow };
    if (ledOn) b.status_led = { gpio: Number(ledGpio), active_low: ledLow };
    return b;
  });
  let idErrors = $derived(rows.filter((r) => !/^[a-z][a-z0-9_]{0,15}$/.test(r.id)).map((r) => `Channel id "${r.id}" must be lower case letters, digits or _ and start with a letter.`));
  let checks = $derived(checkBoard(board));
  let servoErrors = $derived(rows.filter((r) => r.type === "servo").flatMap((r) => {
    const e = [], m = modeList(r.modesText), n = (v, lo, hi) => Number.isInteger(Number(v)) && Number(v) >= lo && Number(v) <= hi;
    if (!n(r.press_deg, 0, 180) || !n(r.rest_deg, 0, 180)) e.push(`${r.id}: angles are 0 to 180 degrees.`);
    if (!n(r.hold_ms, 20, 5000)) e.push(`${r.id}: hold is 20 to 5000 ms.`);
    if (!n(r.release_ms, 50, 5000)) e.push(`${r.id}: release is 50 to 5000 ms.`);
    if (m.length < 1 || m.length > 7) e.push(`${r.id}: list 1 to 7 modes (off is implied).`);
    if (m.some((x) => !/^[a-z][a-z0-9_]{0,15}$/.test(x))) e.push(`${r.id}: mode names are lower case letters, digits or _.`);
    if (m.includes("off")) e.push(`${r.id}: leave "off" out of the modes; it comes after the last one.`);
    if (new Set(m).size !== m.length) e.push(`${r.id}: a mode is listed twice.`);
    return e;
  }));
  let blocked = $derived(checks.errors.length > 0 || idErrors.length > 0 || servoErrors.length > 0 || rows.some((r) => r.type === "argb" && !(r.pixels >= 1 && r.pixels <= 1500)));
  let pins = $derived(pinOptions(chip));

  async function save() {
    busy = true; error = ""; ok = "";
    try {
      if (editing) {
        await put(`/api/admin/nodes/${dev}`, { board, name: nodeName || editing.name });
        go(`/admin/nodes/${dev}`);
      } else {
        const body = { name: nodeName.trim() || board.board, board };
        if (homeId) body.home_id = Number(homeId);
        const r = await post("/api/admin/nodes", body);
        go(`/admin/nodes/${r.id}`);
      }
    } catch (e) { error = errText(e); } finally { busy = false; }
  }

  async function saveTemplate() {
    busy = true; error = ""; ok = "";
    try {
      await post("/api/admin/templates", { name: tplName.trim(), board });
      templates = await get("/api/admin/templates");
      ok = `Template "${tplName}" saved.`;
    } catch (e) { error = errText(e); } finally { busy = false; }
  }
</script>

<div class="row">
  <h2>{editing ? `Edit board: ${editing.name}` : "Node builder"}</h2>
  <span class="spacer"></span>
  {#if !editing}
    <select onchange={startFrom} aria-label="Start from">
      <option value="">Start from…</option>
      <option value="blank">Blank board</option>
      {#each templates as t}<option value={t.id}>{t.name}</option>{/each}
    </select>
  {/if}
</div>

<div class="grid">
  <section class="glass panel stack">
    <div class="row">
      <label class="field">Chip
        <select bind:value={chip} disabled={editing?.admin?.enrolled}>
          {#each Object.keys(CHIPS).filter((k) => k.startsWith("esp32")) as k}<option value={k}>{CHIPS[k].name}</option>{/each}
        </select>
      </label>
      <label class="field grow">Board name<input bind:value={boardName} placeholder="e.g. Kitchen C3, PCB rev A" maxlength="48" /></label>
      <label class="field" style="width:90px">HW rev<input bind:value={hwRev} maxlength="16" /></label>
    </div>

    <div class="chan head" aria-hidden="true"><span>ID</span><span>Name</span><span>Type</span><span>GPIO</span><span>Options</span><span></span></div>
    {#each rows as r, i (i)}
      <div class="chan">
        <input bind:value={r.id} aria-label="Channel id" class="mono" />
        <input bind:value={r.name} aria-label="Channel name" placeholder="–" />
        <select bind:value={r.type} aria-label="Type">{#each TYPES as [v, n]}<option value={v}>{n}</option>{/each}</select>
        <select bind:value={r.gpio} aria-label="GPIO">
          {#each pins as p}<option value={p.gpio}>GPIO{p.gpio}{p.note ? " ⚠" : ""}</option>{/each}
        </select>
        <div class="opts">
          {#if r.type === "pwm"}
            <label class="mini">Hz<input type="number" min="100" max="40000" bind:value={r.hz} /></label>
            <label class="mini chk"><input type="checkbox" bind:checked={r.invert} />invert</label>
          {:else if r.type === "argb"}
            <label class="mini">px<input type="number" min="1" max="1500" bind:value={r.pixels} /></label>
            <select bind:value={r.order} aria-label="Color order">{#each ORDERS as o}<option>{o}</option>{/each}</select>
            <select bind:value={r.chipset} aria-label="Chipset"><option>ws2812</option><option>sk6812</option><option>ws2811</option></select>
          {:else if r.type === "servo"}
            <label class="mini" title="Angle that pushes the button">press°<input type="number" min="0" max="180" bind:value={r.press_deg} /></label>
            <label class="mini" title="Angle clear of the button">rest°<input type="number" min="0" max="180" bind:value={r.rest_deg} /></label>
            <label class="mini" title="How long it holds the press, travel included">hold ms<input type="number" min="20" max="5000" bind:value={r.hold_ms} /></label>
            <label class="mini" title="Time back to rest before the next press">release ms<input type="number" min="50" max="5000" bind:value={r.release_ms} /></label>
            <label class="mini modes" title="The appliance's modes in button order, starting after off">modes<input bind:value={r.modesText} placeholder="white, color, alternating" /></label>
          {:else}
            <label class="mini chk"><input type="checkbox" bind:checked={r.active_low} />active low</label>
          {/if}
        </div>
        <button class="btn ghost sm" onclick={() => rows.splice(i, 1)} aria-label="Remove channel">✕</button>
      </div>
    {/each}
    <div class="row">
      <button class="btn sm" onclick={() => add("pwm")}>+ PWM</button>
      <button class="btn sm" onclick={() => add("argb")}>+ Strip</button>
      <button class="btn sm" onclick={() => add("switch")}>+ Switch</button>
      <button class="btn sm" onclick={() => add("servo")}>+ Servo</button>
    </div>

    <div class="row">
      <label class="mini chk"><input type="checkbox" bind:checked={resetOn} />Reset button</label>
      {#if resetOn}
        <select bind:value={resetGpio} aria-label="Reset button GPIO">{#each pins as p}<option value={p.gpio}>GPIO{p.gpio}{p.gpio === CHIPS[chip].boot_button ? " (BOOT)" : ""}</option>{/each}</select>
        <label class="mini chk"><input type="checkbox" bind:checked={resetLow} />active low</label>
      {/if}
      <span class="spacer"></span>
      <label class="mini chk"><input type="checkbox" bind:checked={ledOn} />Status LED</label>
      {#if ledOn}
        <select bind:value={ledGpio} aria-label="Status LED GPIO">{#each pins as p}<option value={p.gpio}>GPIO{p.gpio}</option>{/each}</select>
        <label class="mini chk"><input type="checkbox" bind:checked={ledLow} />active low</label>
      {/if}
    </div>
  </section>

  <section class="stack">
    <div class="glass panel stack">
      <span class="label">Checks · {CHIPS[chip].name}</span>
      <ul class="checks">
        {#each [...idErrors, ...servoErrors] as m}<li class="err">{m}</li>{/each}
        {#each checks.errors as c}<li class="err">{describe(c, chip)}</li>{/each}
        {#each checks.warnings as c}<li class="warn">{describe(c, chip)}</li>{/each}
        {#if !blocked && checks.warnings.length === 0}<li class="ok">Ready to flash.</li>{/if}
      </ul>
    </div>
    <div class="glass panel stack">
      {#if !editing}
        <label class="field">Node name<input bind:value={nodeName} placeholder={board.board} maxlength="64" /></label>
        <label class="field">Home
          <select bind:value={homeId}><option value="">No home yet</option>{#each homes as h}<option value={h.id}>{h.name}</option>{/each}</select>
        </label>
      {/if}
      {#if error}<div class="msg err">{error}</div>{/if}
      {#if ok}<div class="msg ok">{ok}</div>{/if}
      <button class="btn solid" disabled={blocked || busy} onclick={save}>{editing ? "Save board" : "Create node"}</button>
      <div class="row">
        <input bind:value={tplName} placeholder="Template name" maxlength="48" style="flex:1" />
        <button class="btn sm" disabled={blocked || busy || !tplName.trim()} onclick={saveTemplate}>Save template</button>
      </div>
    </div>
    <details class="glass panel"><summary class="label">Board descriptor</summary><pre>{JSON.stringify(board, null, 2)}</pre></details>
  </section>
</div>

<style>
  .grid { display: grid; grid-template-columns: minmax(0, 1fr); gap: 16px; }
  @media (min-width: 980px) { .grid { grid-template-columns: minmax(0, 2.2fr) minmax(0, 1fr); } }
  .grow { flex: 1; }
  .chan { display: grid; grid-template-columns: 76px minmax(70px, 1fr) 112px 98px minmax(250px, 2.2fr) 32px; gap: 8px; align-items: center; }
  .chan select, .chan input { width: 100%; }
  .opts select { width: auto; }
  .chan.head { font-size: 0.7rem; color: var(--muted); letter-spacing: 0.1em; text-transform: uppercase; }
  @media (max-width: 760px) {
    .chan { grid-template-columns: 1fr 1fr; padding-bottom: 10px; border-bottom: 1px solid var(--line); }
    .chan.head { display: none; }
  }
  .opts { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
  .mini.modes { flex: 1 1 100%; }
  .mini.modes input { flex: 1; width: auto; }
  .opts select { padding: 8px 4px; }
  .mini { display: inline-flex; align-items: center; gap: 4px; font-size: 0.8rem; color: var(--muted); }
  .mini input[type="number"] { width: 70px; }
  .opts .mini input[type="checkbox"] { width: auto; }
  .chk input { accent-color: var(--accent); }
  .checks { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; font-size: 0.88rem; }
  .checks li { padding-left: 20px; position: relative; }
  .checks li::before { position: absolute; left: 0; font-weight: 700; }
  .checks .err { color: #ffc2b9; } .checks .err::before { content: "✕"; color: var(--err); }
  .checks .warn { color: #f8dfb8; } .checks .warn::before { content: "!"; color: var(--warn); left: 4px; }
  .checks .ok { color: #c9f1d6; } .checks .ok::before { content: "✓"; color: var(--ok); }
  pre { margin: 8px 0 0; white-space: pre-wrap; font-size: 0.78rem; color: var(--muted); }
</style>
