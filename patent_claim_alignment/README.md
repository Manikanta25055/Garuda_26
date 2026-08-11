# Patent Claim Alignment — Garuda_26

This folder holds the reconciliation between the **AR933 patent claims** and this
codebase, plus the **code changes to make from our (inventor) side**. It exists so Fable
(or any coding agent) can implement the code-side fixes without re-deriving the analysis.

- **Repo:** `Manikanta25055/Garuda_26` — https://github.com/Manikanta25055/Garuda_26
- **Working branch for these fixes:** `claim-alignment-bucket1`
- **Code of record:** `basic_pipelines/Garuda_web.py` (production web/alert server),
  `basic_pipelines/garuda_cascade.py` (standalone 3-model cascade).

## Two separate workstreams — do not confuse them

1. **Claim amendments — patent agent's job, NOT code.** Claims 1, 5, and 7 are being
   *re-drafted* by the patent agent because the code cannot deliver them as worded (see the
   PDF). **Fable does not need to build anything for these** — in particular, do **not**
   try to make the production secondary network live, route weapons through it, or hit a
   sub-4 ms secondary latency. Those are being handled by narrowing the claim language.
2. **Code fixes — our side, this document.** The claims below stay as originally drafted;
   we bring the code into agreement with them. That is the task list in
   `CODE_CHANGES_FOR_FABLE.md`.

## Files here

| File | What it is |
|---|---|
| `CODE_CHANGES_FOR_FABLE.md` | The actionable code-change checklist. Start here. |
| `AR933_Code_vs_Claims_Technical_Report.pdf` | Formal note to the patent agent: which claims to reconstruct and the exact replacement wording (Why / How / What). Context, not a code task. |
| `AR933_Code_vs_Claims_Technical_Report.tex` | LaTeX source of the above PDF. |
| `AR933_Code_vs_Claims_Technical_Note_for_Agent.md` | The full line-by-line audit (code vs. every claim), with file:line evidence. Reference material behind the checklist. |

## Important note on line numbers

The `file:line` references in these documents were captured at audit time. Branch
`claim-alignment-bucket1` already has some fixes applied, so **line numbers may have
shifted**. Always locate the current code by symbol/string, not by line number, and verify
the present state of each item before editing — several may already be done.
