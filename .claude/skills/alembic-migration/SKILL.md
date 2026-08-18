---
name: alembic-migration
description: Write an Alembic migration for a new ORM model, column, or index. Covers the SQLite/PG dual-track rules, idempotency helpers, the timezone-aware timestamp trap, dialect branching in data migrations, and when to apply. Use whenever a change adds or alters anything in src/fim_one/db/models/.
---

# Alembic Migration (SQLite dev / PostgreSQL prod)

Dev runs SQLite, production runs PostgreSQL, and **one migration set serves
both**. `start.sh` runs `alembic upgrade head` on startup, so a migration that
works on SQLite and fails on PG takes production down at boot, not at the
first query.

Every new ORM model, column, or index needs a migration. Never
`metadata.create_all()`, never an ad-hoc `ALTER TABLE` in `engine.py`.

## 1. Generate and rewrite

```bash
uv run alembic revision -m "add <thing>"
```

Autogenerate is a starting point at best: it does not know about the
idempotency helpers, and its type inference re-introduces the timestamp bug
below. Read what it produced before keeping any of it.

## 2. Make it idempotent

Migrations get re-run against databases in mixed states (a dev SQLite file
that already has the column, a fresh prod database, a worktree branch merged
out of order). Guard every DDL statement:

```python
from fim_one.migrations.helpers import table_exists, table_has_column, index_exists

def upgrade() -> None:
    if not table_has_column("conversations", "starred"):
        op.add_column("conversations", sa.Column("starred", sa.Boolean(), ...))
```

## 3. Defaults that both engines accept

| Column type | Correct `server_default` | Why |
|---|---|---|
| Boolean | `sa.text("FALSE")` / `sa.text("TRUE")` | PG rejects `"0"` / `"1"` |
| Integer | `"0"` | fine on both |
| Timestamp | `sa.text("(CURRENT_TIMESTAMP)")` | fine on both |

The boolean rule applies to the **ORM model's** `server_default` as well, not
only the migration.

## 4. Timestamps must be timezone-aware

**`sa.DateTime(timezone=True)` in both the ORM model and the migration.** No
exceptions.

The ORM writes tz-aware `datetime.now(UTC)`. asyncpg rejects a tz-aware value
against a naive PG column; SQLite accepts it silently. So a naive column is a
latent **PG-only 500** that every dev test passes.

A one-time scan (`l2n4p6r8t901`) converted every column that existed then, and
`m5o7q9s1u234` records the bug it fixed. Any new naive timestamp column
re-introduces it.

## 5. Dialect branching in data migrations

JSON access differs. Check the bind rather than guessing:

```python
bind = op.get_bind()
if bind.dialect.name == "sqlite":
    expr = "json_extract(payload, '$.key')"
else:
    expr = "payload::json->>'key'"
```

Reference migration: `b2d4e6f8a901`.

## 6. Altering an existing column

SQLite cannot `ALTER COLUMN`. Use the batch operation, which rebuilds the
table on SQLite and issues a plain ALTER on PG:

```python
with op.batch_alter_table("conversations") as batch:
    batch.alter_column("title", existing_type=sa.String(200), nullable=False)
```

## 7. Apply it — but only in the main worktree

```bash
uv run alembic upgrade head
```

Run this immediately after writing the migration **in the main worktree**.

**A worktree agent must not run it.** All worktrees share one SQLite dev
database; an agent that upgrades from its branch desyncs the file from every
other branch's head. Worktree agents write the migration file, ORM model, and
code, and the orchestrator applies it after merge-back.

## 8. Before finishing

- Does the new table have a FK to `users`? If it has no `ondelete` cascade,
  user deletion will hit a FK violation. See the `user-owned-module` skill.
- Does the module write files under `uploads/` or `data/`? Same skill.
