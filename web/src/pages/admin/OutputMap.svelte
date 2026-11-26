<script>
  // Output mapping (contracts config): group board channels into outputs, plus names and rooms.
  import { put, errText } from "../../lib/api.js";

  let { node, homes, onsaved } = $props();

  const ROLES = {
    white: ["w"], cct: ["warm", "cool"], rgb: ["r", "g", "b"], rgbw: ["r", "g", "b", "w"],
    rgbcct: ["r", "g", "b", "warm", "cool"], argb: ["data"], relay: ["coil"], solenoid: ["coil"], motion: ["in"],
    cycle: ["press"],
  };
  const TYPE_FOR_ROLE = { w: "pwm", warm: "pwm", cool: "pwm", r: "pwm", g: "pwm", b: "pwm", data: "argb", coil: "switch", in: "motion", press: "servo" };
  const KIND_NAMES = { white: "White", cct: "Dual white (CCT)", rgb: "RGB", rgbw: "RGBW", rgbcct: "RGB + CCT", argb: "ARGB strip", relay: "Relay", solenoid: "Solenoid", motion: "Motion", cycle: "Button cycler (servo)" };

  const clone = (x) => JSON.parse(JSON.stringify(x));
  let editing = $state(false);
  let draft = $state([]);
  let error = $state("");
  let note = $state("");

  let rooms = $derived(homes.find((h) => h.id === node.home_id)?.rooms ?? []);
  let channels = $derived(node.board.channels);

  function edit() {
    draft = clone(node.config?.outputs ?? node.default_outputs).map((o) => ({ cct_range: [2700, 6500], max_on_ms: 5000, ...o }));
    editing = true;
  }
  function setKind(o, kind) {
    o.kind = kind;
    const ch = {};
    for (const r of ROLES[kind]) ch[r] = o.channels[r] ?? channels.find((c) => c.type === TYPE_FOR_ROLE[r])?.id ?? "";
    o.channels = ch;
  }
  function addOutput() {
    let i = 1;
    while (draft.some((o) => o.key === `out${i}`)) i++;
    const o = { key: `out${i}`, kind: "white", channels: {}, cct_range: [2700, 6500], max_on_ms: 5000 };
    setKind(o, "white");
    draft.push(o);
  }
  function clean(o) {
    const c = { key: o.key, kind: o.kind, channels: o.channels };
    if (o.name?.trim()) c.name = o.name.trim();
    if (o.kind === "cct" || o.kind === "rgbcct") c.cct_range = o.cct_range.map(Number);
    if (o.kind === "argb" && o.pixels) c.pixels = Number(o.pixels);
    if (o.kind === "solenoid") c.max_on_ms = Number(o.max_on_ms);
    if (o.max_level && Number(o.max_level) < 1) c.max_level = Number(o.max_level);
    if (o.min_duty && Number(o.min_duty) > 0) c.min_duty = Number(o.min_duty);
    if (o.power_on && o.power_on !== "last") c.power_on = o.power_on;
    return c;
  }
  async function save(outputs) {
    error = ""; note = "";
    try {
      const r = await put(`/api/admin/nodes/${node.id}/config`, { outputs });
      note = `Mapping rev ${r.rev} ${r.published ? "sent to the node" : "saved; the broker is offline, it will be sent later"}.`;
      editing = false;
      onsaved?.();
    } catch (e) { error = errText(e); }
  }
  async function patch(o, body) {
    try { await put(`/api/admin/nodes/${node.id}/outputs/${o.key}`, body); onsaved?.(); }
    catch (e) { error = errText(e); }
  }
</script>

<section class="glass panel stack">
  <div class="row">
    <span class="label">Outputs {node.config ? `· mapping rev ${node.config.rev}` : "· default mapping"}</span>
    <span class="spacer"></span>
    {#if !editing}<button class="btn sm" onclick={edit}>Edit mapping</button>{/if}
  </div>
  {#if error}<div class="msg err">{error}</div>{/if}
  {#if note}<div class="msg ok">{note}</div>{/if}

  {#if !editing}
    <table>
      <thead><tr><th>Output</th><th>Kind</th><th>Channels</th><th>Name</th><th>Room</th><th>State</th></tr></thead>
      <tbody>
        {#each node.outputs as o (o.key)}
          <tr>
            <td class="mono">{o.key}</td>
            <td>{KIND_NAMES[o.kind] ?? o.kind}</td>
            <td class="small muted">{Object.entries(o.meta?.channels ?? {}).map(([r, c]) => `${r}:${c}`).join(" ")}</td>
            <td><input value={o.name} onchange={(e) => patch(o, { name: e.target.value })} maxlength="64" /></td>
            <td>
              <select value={o.room_id ?? ""} onchange={(e) => patch(o, e.target.value ? { room_id: Number(e.target.value) } : { clear_room: true })} disabled={!rooms.length}>
                <option value="">{rooms.length ? "No room" : "Set a home first"}</option>
                {#each rooms as r}<option value={r.id}>{r.name}</option>{/each}
              </select>
            </td>
            <td class="small">{o.state ? (o.state.on ? `on${o.state.level !== undefined ? ` ${Math.round(o.state.level * 100)}%` : ""}${o.state.mode ? ` · ${o.state.mode}` : ""}` : "off") : "–"}{o.state?.at && o.state.at !== (o.state.on ? o.state.mode : "off") ? ` (at ${o.state.at})` : ""}
              <a href={`#/light/${node.id}/${o.key}`} class="small">control</a></td>
          </tr>
        {:else}
          <tr><td colspan="6" class="muted">Outputs appear when the node connects and reports its info.</td></tr>
        {/each}
      </tbody>
    </table>
  {:else}
    {#each draft as o, i (i)}
      <div class="out">
        <input class="mono" bind:value={o.key} aria-label="Output key" />
        <select value={o.kind} onchange={(e) => setKind(o, e.target.value)} aria-label="Kind">
          {#each Object.keys(ROLES) as k}<option value={k}>{KIND_NAMES[k]}</option>{/each}
        </select>
        <input bind:value={o.name} placeholder="Name" aria-label="Name" />
        <div class="roles">
          {#each ROLES[o.kind] as role}
            <label class="mini">{role}
              <select bind:value={o.channels[role]}>
                <option value="">–</option>
                {#each channels.filter((c) => c.type === TYPE_FOR_ROLE[role]) as c}<option value={c.id}>{c.id}{c.name ? ` (${c.name})` : ""}</option>{/each}
              </select>
            </label>
          {/each}
          {#if o.kind === "cct" || o.kind === "rgbcct"}
            <label class="mini">K<input type="number" bind:value={o.cct_range[0]} style="width:72px" />–<input type="number" bind:value={o.cct_range[1]} style="width:72px" /></label>
          {/if}
          {#if o.kind === "argb"}<label class="mini">px<input type="number" bind:value={o.pixels} style="width:72px" /></label>{/if}
          {#if o.kind === "solenoid"}<label class="mini">max ms<input type="number" bind:value={o.max_on_ms} style="width:84px" /></label>{/if}
        </div>
        <button class="btn ghost sm" onclick={() => draft.splice(i, 1)} aria-label="Remove output">✕</button>
      </div>
    {/each}
    <div class="row">
      <button class="btn sm" onclick={addOutput}>+ Output</button>
      <span class="spacer"></span>
      <button class="btn ghost sm" onclick={() => (editing = false)}>Cancel</button>
      <button class="btn sm" onclick={() => save(null)}>Use default</button>
      <button class="btn solid sm" onclick={() => save(draft.map(clean))}>Save mapping</button>
    </div>
  {/if}
</section>

<style>
  .out { display: grid; grid-template-columns: 100px 160px 1fr 2.4fr 36px; gap: 8px; align-items: center; }
  @media (max-width: 860px) { .out { grid-template-columns: 1fr 1fr; padding-bottom: 8px; border-bottom: 1px solid var(--line); } }
  .roles { display: flex; gap: 8px; flex-wrap: wrap; }
  .mini { display: inline-flex; gap: 4px; align-items: center; font-size: 0.8rem; color: var(--muted); }
</style>
