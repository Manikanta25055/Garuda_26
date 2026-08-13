<script>
  import { session } from "./lib/session.svelte.js";
  import { house } from "./lib/app.svelte.js";
  import Login from "./routes/Login.svelte";
  import Home from "./routes/Home.svelte";
  import Rules from "./routes/Rules.svelte";
  import Activity from "./routes/Activity.svelte";
  import Settings from "./routes/Settings.svelte";
  import TabBar from "./components/TabBar.svelte";
  import Composer from "./components/Composer.svelte";
  import OfflineBanner from "./components/OfflineBanner.svelte";

  let tab = $state("home");

  async function handleResult(result) {
    // A compiled proposal is a card, so send the user where cards live.
    if (result.proposal_id) {
      tab = "rules";
      await house.loadProposals();
    }
  }
</script>

{#if !session.signedIn}
  <Login />
{:else}
  <OfflineBanner offline={house.offline} />
  <main>
    {#if tab === "home"}<Home />
    {:else if tab === "rules"}<Rules />
    {:else if tab === "activity"}<Activity />
    {:else}<Settings />{/if}
  </main>
  <Composer onresult={handleResult} />
  <TabBar current={tab} onchange={(next) => (tab = next)} />
{/if}

<style>
  /* Content runs to every edge and scrolls under the control layer. */
  main {
    min-height: 100dvh;
    padding: var(--space-4) var(--margin-content);
    padding-top: max(var(--space-4), env(safe-area-inset-top));
    padding-bottom: calc(140px + env(safe-area-inset-bottom));
  }
  main :global(h1) {
    font-size: var(--text-large-title);
    line-height: var(--lh-large-title);
    font-weight: 700;
    margin: 0 0 var(--space-4);
  }
</style>
