<script>
  import { onMount, onDestroy } from "svelte";
  import { get, post, del, errText } from "../../lib/api.js";
  import { ago, date } from "../../lib/format.js";

  let jobs = $state([]);
  let workers = $state([]);
  let profiles = $state([]);
  let error = $state("");
  let note = $state("");
  let busy = $state(false);

  let profile = $state("");
  let ref = $state("beta");
  let replace = $state(false);

  let open = $state(null); // build id whose log is shown
  let detail = $state(null);
  let logEl = $state();
  let follow = $state(true);

  let newWorker = $state("");
  let shownToken = $state(null); // {name, token} right after create/rotate, shown once

  const ACTIVE = ["queued", "running"];
  const TAG = { succeeded: "ok", failed: "err", cancelled: "", running: "warn", queued: "" };

  async function load() {
    try {
      const d = await get("/api/admin/builds");
      jobs = d.jobs;
      workers = d.workers;
      if (open != null) await loadDetail();
    } catch (e) { error = errText(e); }
  }
  async function loadDetail() {
    detail = await get(`/api/admin/builds/${open}`);
    if (follow && logEl) requestAnimationFrame(() => { if (logEl) logEl.scrollTop = logEl.scrollHeight; });
  }

  let timer;
  onMount(async () => {
    try {
      profiles = await get("/api/admin/build-profiles");
      profile = profiles[0] || "";
    } catch (e) { error = errText(e); }
    await load();
    // Fast while something is in flight or a log is open, slow otherwise.
    const tick = async () => {
      await load();
      const live = jobs.some((j) => ACTIVE.includes(j.status)) || (detail && ACTIVE.includes(detail.status));
      timer = setTimeout(tick, live ? 2000 : 15000);
    };
    timer = setTimeout(tick, 2000);
  });
  onDestroy(() => clearTimeout(timer));

  async function queue() {
    busy = true; error = ""; note = "";
    try {
      const j = await post("/api/admin/builds", { profile, ref: ref.trim(), replace });
      note = `Queued build ${j.id}: ${j.profile} at ${j.ref}.`;
      open = j.id; follow = true;
      await load();
    } catch (e) { error = errText(e); } finally { busy = false; }
  }
  async function cancel(j) {
    try { await post(`/api/admin/builds/${j.id}/cancel`); await load(); } catch (e) { error = errText(e); }
  }
  async function show(j) {
    open = open === j.id ? null : j.id;
    detail = null; follow = true;
    if (open != null) try { await loadDetail(); } catch (e) { error = errText(e); }
  }
  function onScroll() {
    if (logEl) follow = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 40;
  }

  async function addWorker() {
    error = "";
    try {
      const w = await post("/api/admin/workers", { name: newWorker.trim(), kinds: ["build"] });
      shownToken = { name: w.name, token: w.token };
      newWorker = "";
      await load();
    } catch (e) { error = errText(e); }
  }
  async function rotate(w) {
    if (!confirm(`New token for ${w.name}? The old one stops working immediately.`)) return;
    try { const r = await post(`/api/admin/workers/${w.id}/token`); shownToken = { name: r.name, token: r.token }; } catch (e) { error = errText(e); }
  }
  async function removeWorker(w) {
    if (!confirm(`Delete worker ${w.name}? A build it's running is marked failed.`)) return;
    try { await del(`/api/admin/workers/${w.id}`); await load(); } catch (e) { error = errText(e); }
  }
  const took = (j) => (j.started_at && j.finished_at
    ? `${Math.round((new Date(j.finished_at) - new Date(j.started_at)) / 1000)} s` : "");
  const tokenCmd = (t) => `install -Dm600 /dev/stdin ~/.config/ultralights/worker.token <<< '${t}'`;
</script>

<div class="row"><h2>Builds</h2></div>

<div class="glass panel stack">
  <p class="small muted" style="margin:0">A build worker on the workstation claims each build, checks out the ref in its own clean clone, runs <code>build.sh</code> and <code>package.py</code>, and uploads the package to <a href="#/admin/firmware">Firmware</a>. Packages are per chip and profile; the version comes from <code>firmware/version.txt</code> at that ref.</p>
  <div class="row" style="flex-wrap:wrap;gap:10px">
    <label class="small">Profile
      <select bind:value={profile}>{#each profiles as p}<option value={p}>{p}</option>{/each}</select>
    </label>
    <label class="small">Ref <input bind:value={ref} placeholder="branch, tag or commit" style="width:12em" /></label>
    <label class="small" title="Allow replacing a package with the same version built from a different commit">
      <input type="checkbox" bind:checked={replace} /> Replace same version
    </label>
    <button class="btn solid sm" onclick={queue} disabled={busy || !profile || !ref.trim()}>{busy ? "Queuing…" : "Queue build"}</button>
  </div>
  {#if !workers.some((w) => w.online)}
    <div class="msg warn">No build worker is online. Builds wait in the queue until one connects.</div>
  {/if}
  {#if error}<div class="msg err">{error}</div>{/if}
  {#if note}<div class="msg ok">{note}</div>{/if}
</div>

<div class="glass tbl">
  <table>
    <thead><tr><th>#</th><th>Profile</th><th>Ref</th><th>Status</th><th>Result</th><th>Worker</th><th>Queued</th><th></th></tr></thead>
    <tbody>
      {#each jobs as j (j.id)}
        <tr class:sel={open === j.id}>
          <td class="mono small">{j.id}</td>
          <td><strong>{j.profile}</strong></td>
          <td class="mono small">{j.ref}{#if j.replace} <span class="tag">replace</span>{/if}</td>
          <td><span class="tag {TAG[j.status]}">{j.cancel_requested && j.status === "running" ? "cancelling" : j.status}</span></td>
          <td class="small">
            {#if j.status === "succeeded"}{j.version} · <span class="mono">{j.commit}</span> <span class="muted">{took(j)}</span>
            {:else if j.error}<span class="muted">{j.error}</span>{/if}
          </td>
          <td class="small muted">{j.worker || "–"}</td>
          <td class="small muted" title={date(j.created_at)}>{ago(j.created_at)}</td>
          <td class="row" style="gap:6px;justify-content:flex-end">
            <button class="btn sm" onclick={() => show(j)}>{open === j.id ? "Hide log" : "Log"}</button>
            {#if ACTIVE.includes(j.status) && !j.cancel_requested}<button class="btn sm danger" onclick={() => cancel(j)}>Cancel</button>{/if}
          </td>
        </tr>
        {#if open === j.id}
          <tr class="logrow"><td colspan="8">
            {#if detail && detail.id === j.id}
              <pre class="log" bind:this={logEl} onscroll={onScroll}>{detail.log || (detail.status === "queued" ? "Waiting for a worker…" : "")}</pre>
            {:else}<div class="small muted">Loading…</div>{/if}
          </td></tr>
        {/if}
      {:else}
        <tr><td colspan="8" class="muted">No builds yet.</td></tr>
      {/each}
    </tbody>
  </table>
</div>

<div class="row"><h3>Workers</h3></div>
<div class="glass panel stack">
  <p class="small muted" style="margin:0">Workers dial out over HTTPS with a token; nothing on the workstation listens. Run <code>workers/build/ulworker.py</code> there (see <code>workers/build/README.md</code>).</p>
  {#if shownToken}
    <div class="msg ok stack" style="gap:6px">
      <div>Token for <strong>{shownToken.name}</strong>. It's shown once; save it on the workstation:</div>
      <code class="mono small" style="word-break:break-all">{tokenCmd(shownToken.token)}</code>
      <div><button class="btn sm" onclick={() => navigator.clipboard?.writeText(tokenCmd(shownToken.token))}>Copy command</button>
        <button class="btn sm" onclick={() => (shownToken = null)}>Done</button></div>
    </div>
  {/if}
  <div class="row">
    <input bind:value={newWorker} placeholder="name, e.g. workstation" style="width:14em" />
    <button class="btn sm" onclick={addWorker} disabled={newWorker.trim().length < 2}>Add worker</button>
  </div>
</div>
<div class="glass tbl">
  <table>
    <thead><tr><th>Name</th><th>Status</th><th>Last seen</th><th>From</th><th>Created</th><th></th></tr></thead>
    <tbody>
      {#each workers as w (w.id)}
        <tr>
          <td><strong>{w.name}</strong> <span class="small muted">{w.kinds.join(", ")}</span></td>
          <td><span class="dot" class:on={w.online}></span> <span class="small">{w.online ? "online" : "offline"}</span></td>
          <td class="small muted" title={date(w.last_seen_at)}>{ago(w.last_seen_at)}</td>
          <td class="small muted mono">{w.last_ip || "–"}</td>
          <td class="small muted">{date(w.created_at)}</td>
          <td class="row" style="gap:6px;justify-content:flex-end">
            <button class="btn sm" onclick={() => rotate(w)}>New token</button>
            <button class="btn sm danger" onclick={() => removeWorker(w)}>Delete</button>
          </td>
        </tr>
      {:else}
        <tr><td colspan="6" class="muted">No workers yet. Add one, then start it on the workstation.</td></tr>
      {/each}
    </tbody>
  </table>
</div>

<style>
  tr.sel td { background: var(--glass-2); }
  .logrow td { padding: 0 8px 10px; }
  .log { margin: 0; max-height: 26rem; overflow: auto; font-size: 0.76rem; line-height: 1.35; padding: 10px 12px;
    background: rgba(0, 0, 0, 0.35); border-radius: var(--radius-sm); white-space: pre-wrap; word-break: break-word; }
</style>
