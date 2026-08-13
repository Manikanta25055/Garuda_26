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
  <TabBar current={tab} onchange={(next) => (tab = next)} />

  <div class="frame">
    <main id="panel" role="tabpanel" aria-labelledby="tab-{tab}" tabindex="-1">
      {#if tab === "home"}<Home />
      {:else if tab === "rules"}<Rules />
      {:else if tab === "activity"}<Activity />
      {:else}<Settings />{/if}
    </main>
    <Composer onresult={handleResult} />
  </div>
{/if}

<style>
  /* The frame is what the rail pushes over, and what the composer measures
     itself against. Keeping both inside one element means the composer stays
     centred on the content rather than on the window. */
  .frame {
    --rail: 0px;
    padding-left: var(--rail);
  }

  main {
    max-width: 56rem;
    margin: 0 auto;
    padding: var(--space-4) var(--margin-content);
    padding-top: max(var(--space-4), env(safe-area-inset-top));
    /* Clears the composer and the bar beneath it. */
    padding-bottom: calc(150px + env(safe-area-inset-bottom));
  }
  main:focus { outline: none; }

  main :global(h1) {
    font-size: var(--text-large-title);
    line-height: var(--lh-large-title);
    font-weight: 700;
    letter-spacing: -0.02em;
    margin: 0 0 var(--space-4);
  }

  @media (min-width: 768px) {
    .frame { --rail: 15rem; }
    main {
      padding-top: var(--space-8);
      padding-bottom: calc(140px + env(safe-area-inset-bottom));
    }
    /* A large title that works on a 390px phone is undersized on a laptop. */
    main :global(h1) { font-size: var(--text-title-1); line-height: var(--lh-title-1); }
  }

  @media (min-width: 1200px) {
    main { max-width: 64rem; }
  }
</style>
