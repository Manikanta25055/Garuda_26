<script>
  import { onMount } from "svelte";
  import { api } from "../lib/api.js";
  import { house } from "../lib/app.svelte.js";
  import RuleCard from "../components/RuleCard.svelte";
  import ProposalCard from "../components/ProposalCard.svelte";
  import EmptyState from "../components/EmptyState.svelte";

  async function refresh() {
    await Promise.all([house.loadRules(), house.loadProposals(), house.loadDevices()]);
  }

  onMount(refresh);

  async function confirm(id) {
    await api.post(`/api/drishti/proposals/${id}/confirm`);
    await refresh();
  }
  async function discard(id) {
    await api.del(`/api/drishti/proposals/${id}`);
    await refresh();
  }
  async function toggle(id) {
    await api.post(`/api/drishti/rules/${id}/toggle`);
    await refresh();
  }
  async function remove(id) {
    await api.del(`/api/drishti/rules/${id}`);
    await refresh();
  }
</script>

<h1>Rules</h1>

<!-- Proposals sit above saved rules: they are the only thing here waiting on
     the person, and an unanswered one is why a rule they asked for is missing. -->
{#each house.proposals as proposal (proposal.id)}
  <ProposalCard {proposal} onconfirm={confirm} ondiscard={discard} />
{/each}

{#if house.orphaned.length > 0}
  <h2>Needs attention</h2>
  {#each house.orphaned as rule (rule.id)}
    <RuleCard {rule} devices={house.devices} ontoggle={toggle} ondelete={remove} />
  {/each}
{/if}

{#if house.rules.length === 0 && house.proposals.length === 0 && house.orphaned.length === 0}
  <EmptyState
    title="The house hasn't been taught anything yet"
    body="Tell the house what to do using the box below — for example, “turn the lamp on when I sit at the desk”."
  />
{:else}
  {#each house.rules as rule (rule.id)}
    <RuleCard {rule} devices={house.devices} ontoggle={toggle} ondelete={remove} />
  {/each}
{/if}

<style>
  h2 { font-size: var(--text-title-3); font-weight: 600; margin: var(--space-6) 0 var(--space-2); }
  :global(article + article) { margin-top: var(--space-2); }
</style>
