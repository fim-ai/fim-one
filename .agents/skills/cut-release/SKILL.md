---
name: cut-release
description: Cut a release before answering "what's next" / "接下来做什么". Archives the changelog's Unreleased section, marks the roadmap version shipped, bumps the version in both places it lives, and verifies the five-way version chain agrees.
---

# Cut Release

Triggered when the user asks what to work on next ("what's next",
"接下来做什么"). The question is only answerable against a clean line between
what shipped and what has not, so the release is cut **before** answering, not
after.

## The version source chain

```
About dialog (frontend)
  → GET /api/version
    → fim_one.__version__      (src/fim_one/__init__.py)
      = pyproject.toml::version
      = highest ROADMAP "Shipped Versions" entry
      = latest archived CHANGELOG version
```

**All five must agree at all times.** When a drift surfaces, fix it in the
same commit that surfaced it rather than filing it.

## Steps

1. **Archive the changelog.** `docs/changelog.mdx`: rename `## [Unreleased]`
   to `## [vX.Y] - YYYY-MM-DD` and add a fresh empty `## [Unreleased]` above
   it. Use today's real date.

2. **Mark the roadmap version shipped.** `docs/roadmap.mdx`: the version
   heading gets the same date and moves under **Shipped Versions**. Unchecked
   `- [ ]` items in it move to the next planned version — a shipped version
   must not carry open boxes.

3. **Bump the version, both places.** `pyproject.toml::version` and
   `src/fim_one/__init__.py::__version__`, to the version just marked
   shipped. They are separate files and drift silently.

4. **Verify.** `grep` both files and the roadmap/changelog headings and
   confirm all four literals match before moving on.

5. **Answer** with the next priorities, taken from the first unfinished
   version's `- [ ]` items.

Translations follow through the pre-commit hook; commit EN and the generated
locales together.

## Roadmap and changelog are not the same document

Do not merge, mirror, or skip one of them.

- **Roadmap** is a capability index: one line per shipped or planned
  capability, ≤150 chars, naming what ships and the user benefit. It answers
  "where is this product going".
- **Changelog** is release notes: Added / Changed / Fixed / Removed prose per
  version. It answers "what changed in this release and what do I need to
  know to audit the diff".

Different readers, different organizing dimensions. Cutting a release writes
both.
