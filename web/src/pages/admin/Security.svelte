<script>
  import { onMount } from "svelte";
  import { get, post, del, errText } from "../../lib/api.js";
  import { date } from "../../lib/format.js";

  let certs = $state([]);
  let tokens = $state([]);
  let error = $state("");
  let showAll = $state(false);

  async function load() {
    try { [certs, tokens] = await Promise.all([get("/api/admin/certs"), get("/api/admin/tokens")]); }
    catch (e) { error = errText(e); }
  }
  onMount(load);

  async function revoke(c) {
    if (!confirm(`Revoke ${c.cn}'s certificate? The broker refuses it once its revocation list is reloaded; the node needs a new token to come back.`)) return;
    try { await post(`/api/admin/certs/${c.id}/revoke`); await load(); } catch (e) { error = errText(e); }
  }
  async function revokeToken(t) {
    try { await del(`/api/admin/tokens/${t.id}`); await load(); } catch (e) { error = errText(e); }
  }
  let shownCerts = $derived(showAll ? certs : certs.filter((c) => !c.revoked_at));
</script>

<div class="row"><h2>Certificates</h2><span class="spacer"></span>
  <label class="small muted"><input type="checkbox" bind:checked={showAll} /> show revoked</label></div>
{#if error}<div class="msg err">{error}</div>{/if}
<div class="glass tbl">
  <table>
    <thead><tr><th>Device</th><th>Serial</th><th>Issued</th><th>Expires</th><th>Status</th><th></th></tr></thead>
    <tbody>
      {#each shownCerts as c (c.id)}
        <tr>
          <td><a href={`#/admin/nodes/${c.cn}`} class="mono">{c.cn}</a></td>
          <td class="mono small">{c.serial}</td>
          <td class="small">{date(c.issued_at)}</td>
          <td class="small">{date(c.not_after)}</td>
          <td>{#if c.revoked_at}<span class="tag err">revoked: {c.revoke_reason}</span>{:else}<span class="tag ok">valid</span>{/if}</td>
          <td>{#if !c.revoked_at}<button class="btn sm danger" onclick={() => revoke(c)}>Revoke</button>{/if}</td>
        </tr>
      {:else}<tr><td colspan="6" class="muted">No certificates issued yet.</td></tr>{/each}
    </tbody>
  </table>
</div>

<div class="row"><h2>Enrollment tokens</h2></div>
<div class="glass tbl">
  <table>
    <thead><tr><th>Device</th><th>Status</th><th>Issued</th><th>Expires / used</th><th></th></tr></thead>
    <tbody>
      {#each tokens as t (t.id)}
        <tr>
          <td><a href={`#/admin/nodes/${t.device_id}`} class="mono">{t.device_id}</a></td>
          <td><span class="tag" class:ok={t.status === "active"} class:warn={t.status === "expired"}>{t.status}</span></td>
          <td class="small">{date(t.created_at)}</td>
          <td class="small">{t.used_at ? `used ${date(t.used_at)}` : date(t.expires_at)}</td>
          <td>{#if t.status === "active"}<button class="btn sm danger" onclick={() => revokeToken(t)}>Revoke</button>{/if}</td>
        </tr>
      {:else}<tr><td colspan="5" class="muted">No tokens.</td></tr>{/each}
    </tbody>
  </table>
</div>
<p class="small muted">Tokens are single use, tied to one node, and expire after 7 days. Only their hashes are stored.</p>
