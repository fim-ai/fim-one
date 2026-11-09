"""Every entry point against the one rule (see ``matrix.py``)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.core.assembly.principal import Binding, CallContext, provisional_principal
from fim_one.core.assembly.visibility import ResourceKind, authorize_use
from fim_one.db.base import Base

from . import matrix as m


@pytest.fixture()
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    # A file, not ``:memory:``: probes open several sessions at once.
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'matrix.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _actual_deviations(
    entry: str, factory: async_sessionmaker[AsyncSession]
) -> tuple[dict[tuple[str, m.Cell], m.Outcome], int]:
    """Drive *entry* through every scene; where it differs from the rule."""
    deviations: dict[tuple[str, m.Cell], m.Outcome] = {}
    probed = 0
    for key in m.all_scene_keys():
        if entry in m.AUTHOR_ENTRIES and key.role != "owner":
            continue
        scene = await m.seed_scene(factory, key)
        for cell, actual in (await m.probe(entry, scene, factory)).items():
            probed += 1
            if actual != m.expected(entry, cell):
                deviations[(entry, cell)] = actual
    return deviations, probed


@pytest.mark.timeout(120)
@pytest.mark.parametrize("entry", m.ENTRIES)
async def test_deviations_are_exactly_the_registered_ones(
    entry: str, factory: async_sessionmaker[AsyncSession]
) -> None:
    """actual deviations == registered deviations, cell for cell.

    Fails when an entry point drifts from the rule in a new way, and when a
    deviation is fixed without removing its registration from
    ``KNOWN_DEVIATIONS``.
    """
    actual, probed = await _actual_deviations(entry, factory)
    registered = {k: v for k, v in m.registered_deviations().items() if k[0] == entry}

    assert probed == len(m.all_cells(entry)), "every applicable cell was probed"

    problems: list[str] = []
    for key in sorted(set(actual) | set(registered), key=lambda k: k[1]):
        _, cell = key
        rule = m.expected(entry, cell)
        if key not in registered:
            problems.append(f"UNREGISTERED  {cell}: {m.describe(rule, actual[key])}")
        elif key not in actual:
            problems.append(
                f"FIXED, still registered  {cell}: follows the rule now; registered as "
                f"{m.describe(rule, registered[key])}"
            )
        elif actual[key] != registered[key]:
            problems.append(
                f"DIFFERENT  {cell}: does [{m.describe(rule, actual[key])}], "
                f"registered as [{m.describe(rule, registered[key])}]"
            )
    assert not problems, f"{entry}: {len(problems)} cell(s)\n  " + "\n  ".join(problems)


@pytest.mark.parametrize("deviation", m.KNOWN_DEVIATIONS, ids=lambda d: d.name)
def test_every_registered_deviation_changes_something(deviation: m.Deviation) -> None:
    """A registration that no longer changes any cell is dead and must go."""
    remaining = tuple(d for d in m.KNOWN_DEVIATIONS if d is not deviation)

    def without(entry: str, cell: m.Cell) -> m.Outcome:
        outcome = m.expected(entry, cell)
        for other in remaining:
            if entry in other.entries and cell.resource in other.resources:
                outcome = other.today(entry, cell, outcome) or outcome
        return outcome

    live = [
        (entry, cell)
        for entry in deviation.entries
        for cell in m.all_cells(entry)
        if cell.resource in deviation.resources and m.as_built(entry, cell) != without(entry, cell)
    ]
    assert live, f"{deviation.name} changes no cell"


def test_the_registry_is_well_formed() -> None:
    names = [d.name for d in m.KNOWN_DEVIATIONS]
    assert len(names) == len(set(names))
    for d in m.KNOWN_DEVIATIONS:
        assert set(d.entries) <= set(m.ENTRIES)
        assert set(d.resources) <= set(m.RESOURCES)
        for entry in d.entries:
            assert set(d.resources) & set(m.APPLICABLE[entry]), (d.name, entry)
        assert d.what and d.ref and d.removed_in


# ---------------------------------------------------------------------------
# The same table against the layer itself
# ---------------------------------------------------------------------------

_KINDS = {
    "api_bearer": ResourceKind.CONNECTOR,
    "api_none": ResourceKind.CONNECTOR,
    "db": ResourceKind.CONNECTOR,
    "mcp_stdio": ResourceKind.MCP_SERVER,
    "mcp_http": ResourceKind.MCP_SERVER,
    "kb": ResourceKind.KNOWLEDGE_BASE,
    "skill": ResourceKind.SKILL,
    "workflow": ResourceKind.WORKFLOW,
    "agent": ResourceKind.AGENT,
}


@pytest.mark.timeout(60)
@pytest.mark.parametrize("bound", [False, True], ids=["direct", "through-owner-binding"])
async def test_authorize_use_implements_the_visibility_part_of_the_rule(
    bound: bool, factory: async_sessionmaker[AsyncSession]
) -> None:
    """``authorize_use`` decides visibility, the switch and owner-only as the table says.

    Credentials, records and confirmation are not its job; a cell the rule
    refuses for want of a credential is usable as far as visibility goes.
    """
    mismatches: list[str] = []
    # Visibility does not read the credential switches: one scene per (role, is_active).
    for key in (k for k in m.all_scene_keys() if not k.allow_fallback and not k.has_personal):
        scene = await m.seed_scene(factory, key)
        binding = (
            Binding(kind="agent", id=scene.carrier_agent_id, owner_id=scene.users["owner"])
            if bound
            else None
        )
        ctx = CallContext(
            principal=provisional_principal(scene.actor), binding=binding, entry="chat"
        )
        async with factory() as db:
            for resource in m.RESOURCES:
                cell = key.cell(resource)
                # ``wf_agent_node`` binds every resource kind it reaches and
                # surfaces refusal codes for the agent; ``trigger`` does for a
                # directly used one. Either gives the rule with codes kept.
                want = m.rule("trigger", cell, subject="owner" if bound else key.role)
                decision = await authorize_use(db, ctx, _KINDS[resource], [scene.ids[resource]])
                got = None if decision.usable else decision.refused[scene.ids[resource]].error_code
                want_code = (
                    None if want.refusal_code == "credential_required" else want.refusal_code
                )
                if got != want_code:
                    mismatches.append(f"{cell}: rule {want_code!r}, authorize_use {got!r}")
    assert not mismatches, "\n  ".join(mismatches)
