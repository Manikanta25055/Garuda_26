<script>
  import { api } from "../lib/api.js";

  let { onresult } = $props();

  let text = $state("");
  let busy = $state(false);
  let answer = $state(null);   // a local or already-known result
  let failure = $state(null);  // a compile refusal

  async function send(event) {
    event?.preventDefault();
    const instruction = text.trim();
    if (!instruction || busy) return;

    busy = true;
    answer = null;
    failure = null;
    try {
      const result = await api.post("/api/drishti/instruct", { text: instruction });
      text = "";
      if (result.lane === "compile" && result.ok) {
        // A proposal is a card, not a message. The parent shelves it.
        onresult(result);
      } else if (result.lane === "compile") {
        failure = result;
      } else {
        answer = result;
      }
    } catch (err) {
      failure = { reason: err.detail, still_working: err.offline };
    } finally {
      busy = false;
    }
  }
</script>

<div class="dock">
  {#if answer}
    <div class="answer" role="status">
      <p>{answer.lane === "known"
        ? `The house already knows this — “${answer.rule.source_utterance}”.`
        : answer.text}</p>
      <span class="mark">{answer.lane === "known" ? "Already known" : "Answered on device"}</span>
    </div>
  {/if}

  {#if failure}
    <div class="answer failure" role="alert">
      <p>{failure.reason}</p>
      {#if failure.still_working}
        <span class="mark">Your rules are still running.</span>
      {/if}
    </div>
  {/if}

  <form onsubmit={send}>
    <input
      type="text"
      bind:value={text}
      placeholder="Tell the house what to do"
      aria-label="Tell the house what to do"
      enterkeyhint="send"
      autocapitalize="sentences"
    />
    <button type="submit" aria-label="Send" disabled={busy}>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M12 19V5M5 12l7-7 7 7" />
      </svg>
    </button>
  </form>
</div>

<style>
  /* Docks above the tab bar and is reachable from every screen. The composer
     is an action, not a place — it is never a tab, and never a transcript. */
  .dock {
    position: fixed;
    inset: auto 0 calc(var(--dock-clear) + env(safe-area-inset-bottom)) 0;
    z-index: 21;
    padding: 0 var(--margin-content) var(--space-2);
    display: grid;
    gap: var(--space-2);
    /* Matches the content column so the field does not float over the rail or
       stretch the width of a 27-inch display. */
    max-width: var(--measure);
    margin: 0 auto;
  }

  @media (min-width: 768px) {
    .dock {
      left: var(--rail);
      bottom: var(--space-5);
      padding-bottom: 0;
    }
  }

  @media (min-width: 1200px) {
    .dock { max-width: 64rem; }
  }

  form {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: var(--space-2);
    align-items: center;
    padding: var(--space-1);
    border-radius: 9999px;
    background: color-mix(in srgb, var(--surface) 72%, transparent);
    backdrop-filter: blur(24px) saturate(180%);
    -webkit-backdrop-filter: blur(24px) saturate(180%);
    border: 0.5px solid color-mix(in srgb, var(--separator) 60%, transparent);
  }
  input {
    min-height: 44px;
    padding: 0 var(--space-3);
    border: 0;
    background: none;
  }
  input:focus { outline: none; }
  button {
    min-width: 44px;
    min-height: 44px;
    display: grid;
    place-items: center;
    border-radius: 9999px;
    background: var(--accent);
    color: #fff;
  }
  button:disabled { opacity: 0.5; }
  svg { width: 20px; height: 20px; }

  .answer {
    padding: var(--space-3);
    border-radius: var(--radius-card);
    background: var(--bg-secondary);
    border: 0.5px solid var(--separator);
  }
  .answer p { margin: 0; }
  .failure { border-color: color-mix(in srgb, var(--danger) 50%, var(--separator)); }
  .mark {
    display: block;
    margin-top: var(--space-1);
    font-size: var(--text-caption-1);
    color: var(--label-secondary);
  }

  @media (prefers-reduced-transparency: reduce), (prefers-contrast: more) {
    form { backdrop-filter: none; -webkit-backdrop-filter: none; background: var(--surface); }
  }
</style>
