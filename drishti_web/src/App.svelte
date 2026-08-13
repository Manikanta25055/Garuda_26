<script>
  import { session } from "./lib/session.svelte.js";
  import { house } from "./lib/app.svelte.js";
  import { viewport } from "./lib/viewport.svelte.js";
  import Login from "./routes/Login.svelte";
  import House from "./routes/House.svelte";
  import Rules from "./routes/Rules.svelte";
  import Activity from "./routes/Activity.svelte";
  import Settings from "./routes/Settings.svelte";
  import PhoneShell from "./shells/PhoneShell.svelte";
  import DeskShell from "./shells/DeskShell.svelte";
  import Composer from "./components/Composer.svelte";
  import OfflineBanner from "./components/OfflineBanner.svelte";

  let tab = $state("house");

  // One of two, never both. Each shell owns its layout entirely, so neither
  // carries a rule written for the other and there is nothing to override.
  const Shell = $derived(viewport.isDesktop ? DeskShell : PhoneShell);

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

  <Shell current={tab} onchange={(next) => (tab = next)}>
    {#if tab === "house"}<House />
    {:else if tab === "rules"}<Rules />
    {:else if tab === "activity"}<Activity />
    {:else}<Settings />{/if}
  </Shell>

  <Composer onresult={handleResult} />
{/if}
