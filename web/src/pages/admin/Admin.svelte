<script>
  import Nodes from "./Nodes.svelte";
  import NodeDetail from "./NodeDetail.svelte";
  import Builder from "./Builder.svelte";
  import Firmware from "./Firmware.svelte";
  import Builds from "./Builds.svelte";
  import Security from "./Security.svelte";
  import People from "./People.svelte";
  import Activity from "./Activity.svelte";

  let { parts } = $props();
  let tab = $derived(parts[0] || "nodes");
  const TABS = [
    ["nodes", "Nodes"],
    ["builder", "Node builder"],
    ["firmware", "Firmware"],
    ["builds", "Builds"],
    ["security", "Tokens & certificates"],
    ["people", "Homes & people"],
    ["activity", "Activity"],
  ];
</script>

<main class="page stack" style="gap:18px">
  <nav class="tabs" aria-label="Admin sections">
    {#each TABS as [id, name]}
      <a href={`#/admin/${id}`} class:active={tab === id}>{name}</a>
    {/each}
  </nav>
  {#if tab === "nodes" && parts[1]}
    {#key parts[1]}<NodeDetail dev={parts[1]} />{/key}
  {:else if tab === "nodes"}
    <Nodes />
  {:else if tab === "builder"}
    {#key parts[1]}<Builder dev={parts[1]} />{/key}
  {:else if tab === "firmware"}
    <Firmware />
  {:else if tab === "builds"}
    <Builds />
  {:else if tab === "security"}
    <Security />
  {:else if tab === "people"}
    <People />
  {:else if tab === "activity"}
    <Activity />
  {/if}
</main>

<style>
  .tabs { display: flex; gap: 4px; overflow-x: auto; padding-bottom: 2px; }
  .tabs a { text-decoration: none; padding: 7px 14px; border-radius: 999px; color: var(--muted); white-space: nowrap; font-size: 0.88rem; border: 1px solid transparent; }
  .tabs a.active { color: var(--text); background: var(--glass-2); border-color: var(--line); }
</style>
