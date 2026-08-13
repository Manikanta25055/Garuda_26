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
<div class="deck">
  {#each house.proposals as proposal (proposal.id)}
    <ProposalCard {proposal} onconfirm={confirm} ondiscard={discard} />
  {/each}
</div>

{#if house.orphaned.length > 0}
  <h2>Needs attention</h2>
  <div class="deck">
    {#each house.orphaned as rule (rule.id)}
      <RuleCard {rule} devices={house.devices} ontoggle={toggle} ondelete={remove} />
    {/each}
  </div>
{/if}

{#if house.rules.length === 0 && house.proposals.length === 0 && house.orphaned.length === 0}
  <EmptyState
    title="The house hasn't been taught anything yet"
    body="Tell the house what to do using the box below — for example, “turn the lamp on when I sit at the desk”."
  />
{:else}
  <div class="deck">
    {#each house.rules as rule (rule.id)}
      <RuleCard {rule} devices={house.devices} ontoggle={toggle} ondelete={remove} />
    {/each}
  </div>
{/if}

<style>
  h2 {
    font-size: var(--text-title-3);
    line-height: var(--lh-title-3);
    font-weight: 600;
    letter-spacing: -0.01em;
    margin: var(--space-6) 0 var(--space-2);
  }
  /* One column on a phone; on a laptop the card's own comfortable width
     decides how many fit, rather than a column count that leaves a gap at
     every width it was not chosen for. */
  .deck {
    display: grid;
    gap: var(--space-3);
  }
  /* Two columns once the column itself is wide enough for two cards -- not
     once the window is. auto-fill rather than a fixed count, so there is no
     width at which a gap is left over. */
  @container panel (min-width: 44rem) {
    .deck { grid-template-columns: repeat(auto-fill, minmax(22rem, 1fr)); align-items: start; }
  }
</style>
