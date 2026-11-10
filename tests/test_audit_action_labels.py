"""Every audit action has an English label for the admin audit page.

The page shows ``audit.actionLabels.<action>`` from
``frontend/messages/en/admin.json`` (the dot written as ``__``, since the
message library reads a dot as nesting) and falls back to the raw name. A
name registered in ``core/audit/actions.py`` without a label would be shown
to an administrator as ``org.member_role_change``.
"""

from __future__ import annotations

import json
from pathlib import Path

from fim_one.core.audit import actions

MESSAGES = Path(__file__).resolve().parents[1] / "frontend" / "messages" / "en" / "admin.json"


def _labels() -> dict[str, str]:
    labels = json.loads(MESSAGES.read_text(encoding="utf-8"))["audit"]["actionLabels"]
    assert isinstance(labels, dict)
    return labels


def test_every_registered_action_has_an_english_label() -> None:
    labels = _labels()
    missing = sorted(a for a in actions.ACTIONS if a.replace(".", "__") not in labels)
    assert not missing, (
        "add an English label under audit.actionLabels in "
        f"frontend/messages/en/admin.json for: {missing}"
    )


def test_a_label_is_text_not_the_action_name() -> None:
    for key, label in _labels().items():
        assert isinstance(label, str) and label.strip(), key
        assert "__" not in label and "." not in label, (key, label)


def _messages() -> dict[str, object]:
    audit = json.loads(MESSAGES.read_text(encoding="utf-8"))["audit"]
    assert isinstance(audit, dict)
    return audit


def test_every_domain_has_a_group_label_for_the_filter() -> None:
    """The audit page groups its action filter by domain."""
    groups = _messages()["actionGroups"]
    assert isinstance(groups, dict)
    domains = {name.partition(".")[0] for name in actions.ACTIONS}
    assert domains - set(groups) == set()
    assert set(groups) - domains == set()


def test_the_page_holds_no_list_of_actions_of_its_own() -> None:
    """The filter offered a hand-kept list of 26 names, two of which nothing
    records; it is read from the registry (GET /api/admin/audit-log/actions)."""
    page = MESSAGES.parents[2] / "src" / "components" / "admin" / "admin-audit.tsx"
    source = page.read_text(encoding="utf-8")
    assert "KNOWN_ACTIONS" not in source
    assert "/api/admin/audit-log/actions" in source
