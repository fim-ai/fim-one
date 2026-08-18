# Behavioral Evals

Unit tests (`tests/`) pin **code and prompt text**; this suite pins **model
behavior**: it runs the real ReAct loop against a real LLM and asserts on
the **trajectory** — which tools were called, what landed in the sandbox —
never on answer wording.

## Scope (vs `tests/`)

- `pyproject.toml` sets `testpaths = ["tests"]`, so `uv run pytest` /
  `uv run pytest tests/` **never** reaches this directory. CI and the
  pre-commit hook are unaffected.
- Evals run **explicitly**: `uv run pytest evals/ -q`
- A real LLM is required: conftest loads the project root `.env`; when
  `LLM_API_KEY` is missing the whole directory auto-skips (no failures).
- Real token cost, minutes per case, non-deterministic results — the three
  reasons this stays out of the regular test loop.

**The harness is not part of what these cases test.** Every case reaches its
assertion through the same plumbing: the sandboxed `FileOpsTool`, the
recording image tool, the `EvalRun` accessors, the pass@k retry. That
plumbing is pinned in `tests/test_eval_harness.py` against a scripted model,
where it costs nothing — so a red eval here means the model regressed, not
that an accessor drifted. Keep it that way: harness changes get covered
there, and nothing there asserts what a model *chooses* to do.

## When to run

Run once, manually, before committing a change to any of:

- `src/fim_one/core/agent/system_prompt.py` (any prompt rule change)
- any builtin tool's `description` / `parameters_schema`
- the ReAct loop's tool-selection / final-answer paths

**Enforced by pre-commit.** A green run stamps `evals/.eval-stamp` with a
fingerprint of the watched files (list in `scripts/eval_stamp.py`); the
pre-commit hook rejects a commit that stages changes to those files
without a matching stamp. The check itself is instant and free — only
the eval run costs tokens. Escape hatches:

- `SKIP_EVALS=1 git commit ...` — emergencies, machines without
  `LLM_API_KEY`, and agent worktrees (the orchestrator runs evals after
  merge-back).
- An all-skipped run (no `LLM_API_KEY`) does **not** stamp — a skip
  proves nothing.
- A **partial** run does not stamp either. `pytest evals/ -k restyle` exits
  zero having exercised one case; stamping there would certify the whole
  watch list against a run that never touched most of it, and the hook would
  then wave through the prompt change the other cases exist to catch. Any
  skip or deselection blocks the stamp and says so on stderr.

## Handling non-determinism

Model behavior is stochastic — a single failure is a signal, not a verdict.
`EVAL_ATTEMPTS` controls retries per case (default 1; any passing attempt
passes the case):

```bash
EVAL_ATTEMPTS=3 uv run pytest evals/ -q
```

Treat only repeated failures as a real regression.

## Adding cases

**Every agent-behavior bug found in production (or dogfooding) gets frozen
into a case after the fix.** No backfilling of historical misses — the
suite grows forward from the moment a failure is observed. Recipe:

1. Seed the sandbox in `harness.py` with the preconditions (e.g. "the file
   delivered in a previous turn").
2. Use the user's wording that triggered the bug as the query — **verbatim,
   in its original language**. Do not translate or normalize it: model
   behavior is language-sensitive, and a cleaned-up English paraphrase may
   no longer exercise the same decision path. Chinese queries in the cases
   below are intentional — that is what production traffic looks like.
   Language *coverage* is added via extra case variants, never by
   replacing the original.
3. Assert on the trajectory, not the text: tool-call names, existence and
   content of sandbox files. Text assertions are reserved for
   claim-vs-deed checks (claimed a file was written ⇒ the file must exist).

## Current cases

| case | source bug (user session, 2026-07-05) |
|---|---|
| `test_restyle_html_edits_file_not_image` | Asked to restyle a delivered HTML scorecard "in Claude style", the agent called `generate_image` instead, and pasted code in chat without writing the file |
| `test_html_deliverable_is_written_to_disk` | Agent claimed "scorecard.html generated" without ever calling a file tool; default styling was the generic AI-purple gradient |
