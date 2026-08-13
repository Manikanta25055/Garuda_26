<script>
  import { session } from "./lib/session.svelte.js";
  import Login from "./routes/Login.svelte";
  import TabBar from "./components/TabBar.svelte";
  import OfflineBanner from "./components/OfflineBanner.svelte";

  let tab = $state("home");
  let offline = $state(false);
</script>

{#if !session.signedIn}
  <Login />
{:else}
  <OfflineBanner {offline} />
  <main>
    {#if tab === "home"}<h1>Home</h1>
    {:else if tab === "rules"}<h1>Rules</h1>
    {:else if tab === "activity"}<h1>Activity</h1>
    {:else}<h1>Settings</h1>{/if}
  </main>
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
  h1 { font-size: var(--text-large-title); line-height: var(--lh-large-title); font-weight: 700; margin: 0 0 var(--space-4); }
</style>
