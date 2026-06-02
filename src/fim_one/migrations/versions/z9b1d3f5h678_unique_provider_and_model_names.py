"""Enforce unique provider names and unique model_name per provider.

Adds:
  * unique index on ``model_providers(name)``
  * unique index on ``model_provider_models(provider_id, model_name)``

Pre-existing duplicates are reconciled first so the indexes can be built:
  * duplicate models are merged into the earliest survivor (group slots that
    referenced a duplicate are repointed to the survivor, then the dup is
    deleted)
  * duplicate provider names are non-destructively suffixed " (2)", " (3)", ...

Revision ID: z9b1d3f5h678
Revises: m5o7q9s1u234
Create Date: 2026-06-02
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "z9b1d3f5h678"
down_revision: Union[str, None] = "m5o7q9s1u234"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from fim_one.migrations.helpers import index_exists

    bind = op.get_bind()

    # -- Reconcile duplicate models on (provider_id, model_name) --------------
    rows = bind.execute(
        sa.text(
            "SELECT id, provider_id, model_name FROM model_provider_models "
            "ORDER BY provider_id, model_name, created_at, id"
        )
    ).fetchall()
    survivors: dict[tuple[str, str], str] = {}
    for row in rows:
        key = (row[1], row[2])
        keep = survivors.get(key)
        if keep is None:
            survivors[key] = row[0]
            continue
        # Duplicate: repoint group references to the survivor, then delete it.
        dup_id = row[0]
        for col in ("general_model_id", "fast_model_id", "reasoning_model_id"):
            bind.execute(
                sa.text(
                    f"UPDATE model_groups SET {col} = :keep WHERE {col} = :dup"
                ),
                {"keep": keep, "dup": dup_id},
            )
        bind.execute(
            sa.text("DELETE FROM model_provider_models WHERE id = :dup"),
            {"dup": dup_id},
        )

    # -- Reconcile duplicate provider names (non-destructive rename) ----------
    prov_rows = bind.execute(
        sa.text("SELECT id, name FROM model_providers ORDER BY created_at, id")
    ).fetchall()
    seen_names: set[str] = set()
    for row in prov_rows:
        pid, name = row[0], row[1]
        if name not in seen_names:
            seen_names.add(name)
            continue
        new_name = name
        suffix = 2
        while new_name in seen_names:
            new_name = f"{name} ({suffix})"
            suffix += 1
        seen_names.add(new_name)
        bind.execute(
            sa.text("UPDATE model_providers SET name = :n WHERE id = :id"),
            {"n": new_name, "id": pid},
        )

    # -- Create the unique indexes (idempotent) ------------------------------
    if not index_exists(bind, "model_provider_models", "uq_provider_model_name"):
        op.create_index(
            "uq_provider_model_name",
            "model_provider_models",
            ["provider_id", "model_name"],
            unique=True,
        )
    if not index_exists(bind, "model_providers", "uq_model_provider_name"):
        op.create_index(
            "uq_model_provider_name",
            "model_providers",
            ["name"],
            unique=True,
        )


def downgrade() -> None:
    op.drop_index("uq_model_provider_name", table_name="model_providers")
    op.drop_index("uq_provider_model_name", table_name="model_provider_models")
