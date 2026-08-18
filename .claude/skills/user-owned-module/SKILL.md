---
name: user-owned-module
description: Wire a new user-owned module into user deletion. Use whenever a change adds a table with a FK to users, or writes files under uploads/ or data/ keyed by user, conversation, or any other per-user id. Covers purge_user_data(), the file cleanup registry, and the FK-cascade trap.
---

# User-Owned Module → User Deletion

`purge_user_data()` in `src/fim_one/web/services/user_deletion.py` is the
single path both admin deletion and self-serve account deletion funnel
through. A module that stores per-user state and is not wired in there fails
in one of two ways, neither of which any existing test catches:

- **Orphaned files.** ORM cascade deletes rows. It knows nothing about disk,
  so `uploads/` and `data/` keep the user's content forever after the account
  is gone.
- **A hard 500 on delete.** A new table with a FK to `users` and no
  `ondelete` cascade makes `db.delete(user)` raise a FK violation. SQLite now
  runs with `PRAGMA foreign_keys=ON`, so this fails in dev too — but only if
  someone actually deletes a user.

## What to add

Find the block marked `# 3. Clean up file-system resources before the DB
delete.` in `user_deletion.py`.

### Files

Collect the ids your module keys on, then remove each path. Deletion must be
tolerant of missing paths — the account may be deleted before the module ever
wrote anything:

```python
shutil.rmtree(some_dir / some_id, ignore_errors=True)
(some_dir / f"{some_id}.json").unlink(missing_ok=True)
```

For a glob-shaped layout (one file per user, name-prefixed), glob and unlink
rather than assuming a directory exists.

### Rows

If the new table has a FK to `users` without `ondelete="CASCADE"`, add an
explicit FK-safe `DELETE` in the same function, ordered before the parent row
goes. Prefer declaring the cascade on the FK when the relationship genuinely
is ownership; the explicit delete is for the cases where it is not.

## Current registry

Keep this table and the one in `CLAUDE.md` in step with the code.

| Module | Path | Method |
|---|---|---|
| conversations | `data/sandbox/{conv_id}/`, `uploads/conversations/{conv_id}/`, `data/workspaces/{conv_id}/`, `data/dag_checkpoints/{conv_id}.json` | `shutil.rmtree` / `unlink` |
| knowledge_bases | `uploads/kb/{kb_id}/`, `data/vector_store/user_{user_id}/` | `shutil.rmtree` |
| user uploads | `uploads/user_{user_id}/` | `shutil.rmtree` |
| avatar | `uploads/avatars/{user_id}_*` | `glob` + `unlink` |

## Test it

Deletion is the one path where "it looked fine" is worthless: the failure
shows up only once real user data exists. Add a case to
`tests/test_user_deletion.py` that creates the module's rows **and** its
files, deletes the user, and asserts both are gone.
