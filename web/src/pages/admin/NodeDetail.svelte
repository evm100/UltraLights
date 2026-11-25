<script>
  import { onMount } from "svelte";
  import { get, post, put, del, errText } from "../../lib/api.js";
  import { live } from "../../lib/live.svelte.js";
  import { go } from "../../lib/router.svelte.js";
  import { ago, uptime, kb, date } from "../../lib/format.js";
  import { describe } from "../../lib/pins.js";
  import { flash, download, b64ToBytes, webSerialAvailable } from "../../lib/flash.js";
  import OutputMap from "./OutputMap.svelte";
  import BridgeDetail from "./BridgeDetail.svelte";

  let { dev } = $props();

  let n = $state(null);
  let homes = $state([]);
  let packages = $state([]);
  let error = $state("");
  let note = $state("");

  // provisioning form (Wi-Fi / DNS remembered per browser for convenience; never the password)
  const remembered = (() => { try { return JSON.parse(localStorage.getItem("ul.provision") || "{}"); } catch { return {}; } })();
  let withToken = $state(true);
  let withWifi = $state(!!remembered.ssid);
  let ssid = $state(remembered.ssid || "");
  let psk = $state("");
  let dns = $state(remembered.dns ?? "192.168.1.99");
  let server = $state("");
  let pkgId = $state("");
  let eraseAll = $state(true);
  let flashLog = $state([]);
  let flashPct = $state(0);
  let busy = $state(false);

  async function load() {
    try {
      const [node, h, fw, st] = await Promise.all([get(`/api/admin/nodes/${dev}`), get("/api/admin/homes"),
        get("/api/admin/firmware"), get("/api/admin/status")]);
      n = node; homes = h; server ||= st.public_host;
      packages = fw.filter((p) => p.chip === node.chip);
      if (!pkgId && packages.length) pkgId = String(packages[0].id);
    } catch (e) { error = errText(e); }
  }
  onMount(load);

  let lv = $derived(live.devices[dev]);
  let online = $derived(lv?.online ?? n?.online);
  let health = $derived(lv?.admin?.health ?? n?.admin?.health);
  let events = $derived(live.events.filter((e) => e.dev === dev).slice(0, 12));
  let pkg = $derived(packages.find((p) => String(p.id) === pkgId) ?? null);
  // Firmware reports a refused descriptor as info.config_error "board rejected: <pins.py codes>"
  // (or "descriptor is for <chip>, this is <chip>"); the node still enrolls but drives nothing.
  let configError = $derived(lv?.admin?.config_error ?? n?.admin?.config_error ?? null);
  let boardRejected = $derived.by(() => {
    if (!configError) return null;
    if (configError.startsWith("board rejected:")) return configError.slice(15).trim().split(/\s+/).filter(Boolean);
    if (configError.startsWith("descriptor is for")) return [configError];
    return null;
  });

  async function act(fn, ok) {
    error = ""; note = "";
    try { const r = await fn(); note = typeof ok === "function" ? ok(r) : ok; await load(); }
    catch (e) { error = errText(e); }
  }
  const cmd = (op, args = {}) => act(() => post(`/api/admin/nodes/${dev}/cmd`, { op, args }),
    (r) => (r.result ? (r.result.ok ? `${op}: done` : `${op}: ${r.result.error}`) : `${op}: sent (no answer within 5 s)`));
  const rename = (name) => act(() => put(`/api/admin/nodes/${dev}`, { name }), "Renamed.");
  const setHome = (v) => act(() => put(`/api/admin/nodes/${dev}`, v ? { home_id: Number(v) } : { clear_home: true }), "Home updated.");

  async function remove() {
    if (!confirm(`Delete ${n.name} (${dev})? Its certificate is revoked and it must be re-provisioned to come back.`)) return;
    await act(() => del(`/api/admin/nodes/${dev}`), "Deleted.");
    go("/admin/nodes");
  }

  async function buildBlob() {
    const body = { token: withToken, server: server.trim() || undefined };
    if (withWifi && ssid.trim()) { body.wifi_ssid = ssid.trim(); if (psk) body.wifi_psk = psk; }
    if (dns.trim()) body.dns = dns.trim();
    try { localStorage.setItem("ul.provision", JSON.stringify({ ssid: withWifi ? ssid.trim() : "", dns: dns.trim() })); } catch {}
    return post(`/api/admin/nodes/${dev}/provision`, body);
  }

  async function downloadBlob() {
    error = ""; note = "";
    try {
      const r = await buildBlob();
      download(b64ToBytes(r.blob_b64), `ul_board-${dev}.bin`);
      note = `Blob ${r.crc} downloaded${r.token_issued ? "; a new one-time token was issued (7 days)" : ""}.`;
      await load();
    } catch (e) { error = errText(e); }
  }

  async function doFlash(descriptorOnly) {
    error = ""; note = ""; flashLog = []; flashPct = 0; busy = true;
    const log = (s) => (flashLog = [...flashLog.slice(-200), String(s).trimEnd()]);
    try {
      if (!pkg) throw new Error(`Upload a firmware package for ${n.chip} first (its manifest says where the descriptor goes).`);
      const r = await buildBlob();
      log(`Descriptor ${r.crc}${r.token_issued ? " with a new enrollment token" : ""}${r.server_ca ? ", dev server CA included" : ""}`);
      await flash({ pkg: descriptorOnly ? null : pkg, blob: b64ToBytes(r.blob_b64), boardOffset: pkg.manifest.board_partition.offset,
        chip: n.chip, eraseAll: !descriptorOnly && eraseAll, log,
        progress: (i, total, f) => (flashPct = Math.round(((i + f) / total) * 100)) });
      flashPct = 100;
      note = "Flashed. The node will open its setup network if it has no Wi-Fi, then enroll.";
      await load();
    } catch (e) { error = errText(e); log(`Error: ${errText(e)}`); } finally { busy = false; }
  }

  async function revokeToken(id) { await act(() => del(`/api/admin/tokens/${id}`), "Token revoked."); }
</script>

{#if !n}
  {#if error}<div class="msg err">{error}</div>{:else}<p class="muted">Loading…</p>{/if}
{:else if n.kind === "bridge"}
  <BridgeDetail node={n} {homes} onchange={load} />
{:else}
  <div class="row">
    <a href="#/admin/nodes" class="btn ghost sm">← Nodes</a>
    <span class="dot" class:on={online}></span>
    <h2>{n.name}</h2>
    <span class="mono muted">{n.id}</span>
    <span class="tag">{n.chip}</span>
    {#if !n.admin.enrolled}<span class="tag warn">not enrolled</span>{/if}
    <span class="spacer"></span>
    <button class="btn sm" disabled={!online} onclick={() => cmd("identify", { ms: 5000 })}>Identify</button>
    <button class="btn sm" disabled={!online} onclick={() => cmd("reboot")}>Reboot</button>
    <button class="btn sm" disabled={!online} onclick={() => confirm("Forget Wi-Fi and open the setup network?") && cmd("wifi_reset")}>Wi-Fi reset</button>
  </div>
  {#if error}<div class="msg err">{error}</div>{/if}
  {#if note}<div class="msg ok">{note}</div>{/if}
  {#if boardRejected}
    <div class="msg err stack" style="gap:4px" role="alert">
      <strong>The node rejected its board descriptor and drives no outputs.</strong>
      {#each boardRejected as code}<span>• {code.includes(":") && !code.includes(" ") ? `${describe(code, n.chip)} (${code})` : code}</span>{/each}
      <span class="small">Fix the board in the builder, then use "Write descriptor only" below.</span>
    </div>
  {/if}

  <div class="grid">
    <section class="glass panel stack">
      <span class="label">Status</span>
      <dl>
        <dt>Connection</dt><dd>{online ? "online" : `offline, seen ${ago(n.admin.last_seen)}`}</dd>
        <dt>Firmware</dt><dd>{n.admin.fw ?? "–"} {n.admin.profile ? `(${n.admin.profile})` : ""}</dd>
        <dt>Descriptor</dt><dd class="mono">{n.admin.running_crc ?? "–"}{#if n.admin.board_crc && n.admin.running_crc && n.admin.board_crc !== n.admin.running_crc} <span class="tag warn">last built {n.admin.board_crc}</span>{/if}</dd>
        <dt>Config</dt><dd>rev {n.admin.running_config_rev ?? "–"} running / {n.admin.config_rev} saved {#if n.admin.config_error}<span class="tag err">{n.admin.config_error}</span>{/if}</dd>
        <dt>Signal</dt><dd>{health ? `${health.rssi} dBm, ch ${health.channel ?? "?"}` : "–"}</dd>
        <dt>IP / DNS</dt><dd>{health ? `${health.ip} / ${health.dns ?? "dhcp"}` : "–"}</dd>
        <dt>Uptime</dt><dd>{uptime(health?.uptime_s)} · reset: {health?.reset_reason ?? "–"}</dd>
        <dt>Heap</dt><dd>{kb(health?.heap_free)} free, {kb(health?.heap_min)} min</dd>
        <dt>Reconnects</dt><dd>{health ? `Wi-Fi ${health.wifi_reconnects}, MQTT ${health.mqtt_reconnects}` : "–"}</dd>
      </dl>
      <div class="row">
        <label class="field" style="flex:1">Name<input value={n.name} onchange={(e) => rename(e.target.value)} maxlength="64" /></label>
        <label class="field" style="flex:1">Home
          <select value={n.home_id ?? ""} onchange={(e) => setHome(e.target.value)}>
            <option value="">No home</option>{#each homes as h}<option value={h.id}>{h.name}</option>{/each}
          </select>
        </label>
      </div>
    </section>

    <section class="glass panel stack">
      <div class="row"><span class="label">Board · {n.board.board}</span><span class="spacer"></span><a class="btn sm" href={`#/admin/builder/${dev}`}>Edit board</a></div>
      <table>
        <thead><tr><th>Channel</th><th>Type</th><th>GPIO</th><th></th></tr></thead>
        <tbody>
          {#each n.board.channels as c}
            <tr><td><span class="mono">{c.id}</span> {c.name ?? ""}</td><td>{c.type}</td><td>{c.gpio}</td>
              <td class="muted small">{c.type === "pwm" ? `${c.hz ?? 19000} Hz` : c.type === "argb" ? `${c.pixels} px ${c.order ?? "GRB"}` : c.active_low ? "active low" : ""}</td></tr>
          {/each}
          {#if n.board.reset_button}<tr><td>reset button</td><td>button</td><td>{n.board.reset_button.gpio}</td><td></td></tr>{/if}
        </tbody>
      </table>
      {#each n.checks.warnings as w}<div class="small" style="color:var(--warn)">! {describe(w, n.chip)}</div>{/each}
    </section>
  </div>

  <OutputMap node={n} {homes} onsaved={load} />

  <section class="glass panel stack">
    <span class="label">Provision and flash</span>
    <div class="row">
      <label class="field" style="flex:1">Server name<input bind:value={server} /></label>
      <label class="field" style="flex:1">DNS server (optional)<input bind:value={dns} placeholder="192.168.1.99" /></label>
      <label class="field" style="flex:1">Firmware package
        <select bind:value={pkgId}>
          {#each packages as p}<option value={String(p.id)}>{p.profile} {p.version}</option>{:else}<option value="">none for {n.chip}</option>{/each}
        </select>
      </label>
    </div>
    <div class="row">
      <label class="mini"><input type="checkbox" bind:checked={withWifi} /> Write Wi-Fi (skip the setup page)</label>
      {#if withWifi}
        <input bind:value={ssid} placeholder="SSID" maxlength="32" />
        <input type="password" bind:value={psk} placeholder="Password" maxlength="64" autocomplete="new-password" />
      {/if}
    </div>
    <div class="row">
      <label class="mini"><input type="checkbox" bind:checked={withToken} /> New enrollment token {n.admin.enrolled ? "(re-enrolls the node)" : ""}</label>
      <label class="mini"><input type="checkbox" bind:checked={eraseAll} /> Erase flash first (clears old Wi-Fi and identity)</label>
    </div>
    <div class="row">
      <button class="btn solid" disabled={busy || !pkg || !webSerialAvailable()} onclick={() => doFlash(false)}>Flash firmware + descriptor</button>
      <button class="btn" disabled={busy || !pkg || !webSerialAvailable()} onclick={() => doFlash(true)}>Write descriptor only</button>
      <button class="btn ghost" disabled={busy} onclick={downloadBlob}>Download blob</button>
      {#if !webSerialAvailable()}<span class="small muted">Flashing needs Chrome or Edge.</span>{/if}
    </div>
    {#if flashLog.length}
      <div class="bar"><span style={`width:${flashPct}%`}></span></div>
      <pre class="log">{flashLog.join("\n")}</pre>
    {/if}
  </section>

  <div class="grid">
    <section class="glass panel stack">
      <span class="label">Enrollment tokens</span>
      <table><tbody>
        {#each n.tokens as t}
          <tr><td><span class="tag" class:ok={t.status === "active"} class:warn={t.status === "expired"}>{t.status}</span></td>
            <td class="small muted">issued {date(t.created_at)}</td>
            <td class="small muted">{t.used_at ? `used ${date(t.used_at)}` : `expires ${date(t.expires_at)}`}</td>
            <td>{#if t.status === "active"}<button class="btn sm danger" onclick={() => revokeToken(t.id)}>Revoke</button>{/if}</td></tr>
        {:else}<tr><td class="muted">No tokens yet. Flashing or downloading a blob issues one.</td></tr>{/each}
      </tbody></table>
      <span class="label">Certificates</span>
      <table><tbody>
        {#each n.certs as c}
          <tr><td class="mono small">{c.serial.slice(0, 12)}…</td><td class="small muted">issued {date(c.issued_at)}</td>
            <td>{#if c.revoked_at}<span class="tag err">{c.revoke_reason}</span>{:else}<span class="tag ok">valid</span>{/if}</td></tr>
        {:else}<tr><td class="muted">Not enrolled.</td></tr>{/each}
      </tbody></table>
    </section>
    <section class="glass panel stack">
      <span class="label">Recent events</span>
      {#each events as e}
        <div class="small"><span class="muted">{e.at.toLocaleTimeString()}</span> <span class="mono">{JSON.stringify(e.evt)}</span></div>
      {:else}<div class="small muted">Nothing since this page opened.</div>{/each}
      {#if n.contract_errors.length}
        <span class="label" style="color:var(--err)">Contract errors</span>
        {#each n.contract_errors as ce}<div class="small mono">{ce.topic}: {ce.error}</div>{/each}
      {/if}
      <div class="spacer"></div>
      <button class="btn danger sm" style="align-self:flex-start" onclick={remove}>Delete node</button>
    </section>
  </div>
{/if}

<style>
  .grid { display: grid; grid-template-columns: minmax(0, 1fr); gap: 16px; }
  @media (min-width: 900px) { .grid { grid-template-columns: 1fr 1fr; } }
  dl { display: grid; grid-template-columns: 110px 1fr; gap: 6px 12px; margin: 0; font-size: 0.88rem; }
  dt { color: var(--muted); }
  dd { margin: 0; }
  .mini { display: inline-flex; gap: 6px; align-items: center; font-size: 0.85rem; color: var(--muted); }
  .mini input { accent-color: var(--accent); }
  .bar { height: 6px; border-radius: 6px; background: rgba(255,255,255,0.1); overflow: hidden; }
  .bar span { display: block; height: 100%; background: var(--accent); transition: width 0.2s; }
  .log { max-height: 220px; overflow: auto; margin: 0; padding: 10px; background: rgba(0,0,0,0.35); border-radius: var(--radius-sm); font-size: 0.75rem; color: var(--muted); }
</style>
