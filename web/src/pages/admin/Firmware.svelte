<script>
  import { onMount } from "svelte";
  import { get, api, post, del, errText } from "../../lib/api.js";
  import { date } from "../../lib/format.js";
  import OtaUpdates from "./OtaUpdates.svelte";

  let pkgs = $state([]);
  let error = $state("");
  let note = $state("");
  let busy = $state(false);
  let input;

  const load = async () => { try { pkgs = await get("/api/admin/firmware"); } catch (e) { error = errText(e); } };
  onMount(load);

  async function upload() {
    const f = input.files?.[0];
    if (!f) return;
    busy = true; error = ""; note = "";
    try {
      const fd = new FormData();
      fd.append("file", f);
      const p = await api("POST", "/api/admin/firmware", fd);
      note = `Uploaded ${p.profile} ${p.version}.`;
      input.value = "";
      await load();
    } catch (e) { error = errText(e); } finally { busy = false; }
  }
  async function remove(p) {
    if (!confirm(`Delete ${p.profile} ${p.version}?`)) return;
    try { await del(`/api/admin/firmware/${p.id}`); await load(); } catch (e) { error = errText(e); }
  }
  const hex = (n) => `0x${n.toString(16)}`;

  // OTA: pick nodes on this package's profile and push it to them.
  let otaFor = $state(null); // package id whose target list is open
  let targets = $state([]);
  let picked = $state({});
  let force = $state(false);
  let pushing = $state(false);
  let bump = $state(0);
  let results = $state([]);

  async function openTargets(p) {
    results = []; force = false;
    if (otaFor === p.id) { otaFor = null; return; }
    otaFor = p.id;
    try {
      targets = await get(`/api/admin/firmware/${p.id}/targets`);
      picked = Object.fromEntries(targets.filter((t) => t.eligible).map((t) => [t.dev, true]));
    } catch (e) { error = errText(e); }
  }
  const canPick = (t) => t.eligible || (force && t.same_version && t.online);
  let chosen = $derived(targets.filter((t) => picked[t.dev] && canPick(t)).map((t) => t.dev));

  async function pushOta(p) {
    if (!confirm(`Update ${chosen.length} node(s) to ${p.profile} ${p.version}? Each restarts when the image is written.`)) return;
    pushing = true; error = "";
    try {
      results = (await post("/api/admin/ota", { package_id: p.id, devices: chosen, force })).results;
      bump++;
      targets = await get(`/api/admin/firmware/${p.id}/targets`);
    } catch (e) { error = errText(e); } finally { pushing = false; }
  }
</script>

<div class="row"><h2>Firmware packages</h2></div>
<div class="glass panel stack">
  <p class="small muted" style="margin:0">A package is the .zip made by <code>firmware/tools/package.py</code>: <code>manifest.json</code> plus the bootloader, partition table and app. Images are per chip and profile, never per node. Every file's size and SHA-256 are checked on upload and again in the browser before flashing.</p>
  <div class="row">
    <input type="file" accept=".zip,application/zip" bind:this={input} />
    <button class="btn solid sm" onclick={upload} disabled={busy}>{busy ? "Uploading…" : "Upload"}</button>
  </div>
  {#if error}<div class="msg err">{error}</div>{/if}
  {#if note}<div class="msg ok">{note}</div>{/if}
</div>

<div class="glass tbl">
  <table>
    <thead><tr><th>Profile</th><th>Version</th><th>Chip</th><th>Flash</th><th>Files</th><th>Descriptor at</th><th>Build</th><th>Uploaded</th><th></th></tr></thead>
    <tbody>
      {#each pkgs as p (p.id)}
        <tr>
          <td><strong>{p.profile}</strong></td><td>{p.version}</td><td>{p.chip}</td>
          <td class="small">{p.manifest.flash.size} {p.manifest.flash.mode}</td>
          <td class="small muted">{p.manifest.files.map((f) => `${f.name}@${hex(f.offset)}`).join(", ")}</td>
          <td class="mono small">{hex(p.manifest.board_partition.offset)}</td>
          <td class="small muted">IDF {p.manifest.idf} · {p.manifest.commit}</td>
          <td class="small muted">{date(p.uploaded_at)}</td>
          <td class="row" style="gap:6px;justify-content:flex-end">
            <button class="btn sm" onclick={() => openTargets(p)}>{otaFor === p.id ? "Close" : "Update nodes"}</button>
            <button class="btn sm danger" onclick={() => remove(p)}>Delete</button>
          </td>
        </tr>
        {#if otaFor === p.id}
          <tr class="ota"><td colspan="9">
            <div class="stack" style="gap:8px">
              {#each targets as t (t.dev)}
                <label class="row small" style="gap:8px" class:muted={!canPick(t)}>
                  <input type="checkbox" bind:checked={picked[t.dev]} disabled={!canPick(t)} />
                  <span class="dot" class:on={t.online}></span>
                  <strong>{t.name}</strong> <span class="mono">{t.dev}</span>
                  <span>{t.fw ?? "?"}{t.home ? ` · ${t.home}` : ""}</span>
                  {#if t.reason}<span class="muted">· {t.reason}</span>{/if}
                </label>
              {:else}
                <div class="small muted">No nodes run {p.profile}.</div>
              {/each}
              <div class="row">
                <label class="small" title="Reinstall on nodes that already run this version"><input type="checkbox" bind:checked={force} /> Reinstall same version</label>
                <button class="btn solid sm" disabled={pushing || !chosen.length} onclick={() => pushOta(p)}>
                  {pushing ? "Sending…" : `Update ${chosen.length} node${chosen.length === 1 ? "" : "s"} to ${p.version}`}</button>
              </div>
              {#each results.filter((r) => !r.ok) as r}<div class="small" style="color:var(--err)">{r.dev}: {r.detail}</div>{/each}
            </div>
          </td></tr>
        {/if}
      {:else}
        <tr><td colspan="9" class="muted">No packages yet.</td></tr>
      {/each}
    </tbody>
  </table>
</div>

<div class="row"><h3>Updates</h3></div>
<p class="small muted" style="margin:0">Nodes download the app image over HTTPS, check its version and SHA-256, and restart into it. An update is done when the node reports the new version; if the new image never reaches the broker, the bootloader rolls back and the update shows as failed.</p>
<OtaUpdates {bump} />

<style>
  .ota td { background: var(--glass-2); padding: 12px 14px; }
</style>
