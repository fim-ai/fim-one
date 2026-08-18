# Regression Tests

`tests/` is organised by module: `test_{module}.py` pins what a module is
*supposed* to do. This directory pins what once went **wrong**.

A test belongs here when it exists because of a specific failure — a
production incident, a review catch, a reported bug — and its value is
"this exact thing must never come back", not "this module works".

## Naming

```
tests/regressions/test_<slug>.py
```

`<slug>` names the **failure**, not the module: `test_truncated_tool_call_batch.py`,
not `test_openai_compatible.py`. When a GitHub issue or PR exists, put the
number in the module docstring (`Regression for #42.`) rather than in the
filename, so the file stays readable without the tracker open.

## What each file must carry

A module docstring stating, in this order:

1. **What broke** — the observable wrong behaviour.
2. **Why it broke** — the flawed assumption in the old code.
3. **The rule now** — the invariant the test enforces.

Without the "why", the next person reading a strict-looking assertion has no
way to tell an intentional guarantee from an accident, and will relax it.

## What does *not* belong here

- Coverage for a new feature — that goes in `tests/test_{module}.py`.
- Behavioural evals against a real LLM — that is `evals/`.
- A bug fixed before it ever shipped, whose test reads naturally as ordinary
  module coverage.
