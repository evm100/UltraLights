<script>
  import { onMount } from "svelte";
  import { get, api, del, errText } from "../../lib/api.js";
  import { date } from "../../lib/format.js";

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
          <td><button class="btn sm danger" onclick={() => remove(p)}>Delete</button></td>
        </tr>
      {:else}
        <tr><td colspan="9" class="muted">No packages yet.</td></tr>
      {/each}
    </tbody>
  </table>
</div>
