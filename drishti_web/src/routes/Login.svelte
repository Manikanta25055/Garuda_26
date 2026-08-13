<script>
  import { session } from "../lib/session.svelte.js";

  let username = $state("");
  let password = $state("");
  let error = $state("");
  let busy = $state(false);

  async function submit(event) {
    event.preventDefault();
    error = "";
    busy = true;
    try {
      await session.signIn(username, password);
    } catch (err) {
      error = err.detail ?? "Something went wrong.";
    } finally {
      busy = false;
    }
  }
</script>

<div class="wrap">
  <form onsubmit={submit}>
    <h1>Drishti</h1>
    <p class="sub">Sign in to your house.</p>

    <label for="u">Username</label>
    <input id="u" bind:value={username} autocomplete="username" autocapitalize="none" />

    <label for="p">Password</label>
    <input id="p" type="password" bind:value={password} autocomplete="current-password" />

    {#if error}<p class="err" role="alert">{error}</p>{/if}

    <button type="submit" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
  </form>
</div>

<style>
  .wrap {
    min-height: 100dvh;
    display: grid;
    place-items: center;
    padding: max(var(--space-6), env(safe-area-inset-top)) var(--space-4);
  }
  form { width: 100%; max-width: 22rem; display: grid; gap: var(--space-2); }
  h1 {
    font-size: var(--text-large-title); line-height: var(--lh-large-title);
    margin: 0; font-weight: var(--weight-bold); letter-spacing: -0.02em;
  }
  .sub { color: var(--label-secondary); margin: 0 0 var(--space-4); }
  label { font-size: var(--text-subhead); color: var(--label-secondary); margin-top: var(--space-2); }
  input {
    min-height: 44px;
    padding: 0 var(--space-3);
    border-radius: var(--radius-control);
    border: 1px solid var(--separator);
    background: var(--bg-secondary);
    transition: border-color var(--dur-fast) var(--ease-standard);
  }
  input:focus { border-color: var(--accent); }
  .err { color: var(--danger); font-size: var(--text-footnote); margin: var(--space-2) 0 0; }
  button {
    margin-top: var(--space-5);
    min-height: 44px;
    border-radius: var(--radius-control);
    background: var(--accent);
    color: #fff;
    font-weight: var(--weight-semibold);
    transition: opacity var(--dur-fast) var(--ease-standard);
  }
  button:hover:not(:disabled) { opacity: 0.9; }
  button:disabled { opacity: 0.5; cursor: default; }

  /* On a laptop a card centred in the viewport reads as a dialog rather than
     a page that failed to fill its window. */
  @media (min-width: 768px) {
    form {
      max-width: 24rem;
      padding: var(--space-8);
      background: var(--surface);
      border: 0.5px solid var(--separator);
      border-radius: var(--radius-sheet);
      box-shadow: 0 20px 60px rgb(0 0 0 / 0.12);
    }
  }
</style>
