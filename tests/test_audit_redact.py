"""What of a change reaches an audit record (``core/audit/redact.py``)."""

from __future__ import annotations

import json

from fim_one.core.assembly.mcp_address import names_a_secret, value_is_secret_shaped
from fim_one.core.audit import writer as audit
from fim_one.core.audit.redact import diff_changes, hint, safe_text
from fim_one.db.models.invite_code import InviteCode
from tests.audit.harness import World, world  # noqa: F401

SECRET = "sk-live-4f9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c"


class TestDiffChanges:
    def test_only_what_differs_is_listed(self) -> None:
        before = {"role": "member", "name": "Ops", "is_active": True}
        after = {"role": "admin", "name": "Ops", "is_active": False}

        assert diff_changes(before, after) == {
            "role": {"from": "member", "to": "admin"},
            "is_active": {"from": True, "to": False},
        }

    def test_nothing_differs(self) -> None:
        assert diff_changes({"a": 1}, {"a": 1}) is None
        assert diff_changes(None, None) is None

    def test_a_field_listed_as_secret_says_only_that_it_changed(self) -> None:
        changes = diff_changes(
            {"config": "old-value"}, {"config": "new-value"}, secret_fields=["config"]
        )
        assert changes == {"config": {"changed": True}}

    def test_a_field_named_after_a_secret_needs_no_listing(self) -> None:
        changes = diff_changes(
            {"api_key": "old", "webhookSecret": None, "Authorization": "Bearer a"},
            {"api_key": SECRET, "webhookSecret": SECRET, "Authorization": "Bearer b"},
        )
        assert changes == {
            "api_key": {"changed": True},
            "webhookSecret": {"changed": True},
            "Authorization": {"changed": True},
        }
        assert SECRET not in json.dumps(changes)

    def test_a_credential_shaped_value_is_masked_whatever_its_field_is_called(self) -> None:
        changes = diff_changes({"note": "x"}, {"note": SECRET})
        assert changes == {"note": {"from": "x", "to": "***"}}

    def test_a_url_keeps_where_it_points_and_loses_what_authenticates_it(self) -> None:
        changes = diff_changes(
            {"url": "https://hooks.example.com/services/T000/B000?token=hunter2hunter2"},
            {"url": "https://user:pw@hooks.example.com/hook/0123456789abcdef0123456789abcdef"},
        )
        assert changes == {
            "url": {
                "from": "https://hooks.example.com/services/T000/B000",
                "to": "https://hooks.example.com/hook/***",
            }
        }

    def test_a_nested_structure_is_never_copied(self) -> None:
        changes = diff_changes(
            {"headers": {"X-Token": "a"}, "tools": [{"name": "a", "key": SECRET}]},
            {"headers": {"X-Token": SECRET}, "tools": [{"name": "b", "key": SECRET}]},
        )
        assert changes == {
            "headers": {"from": "(changed)", "to": "(changed)"},
            "tools": {"from": "(changed)", "to": "(changed)"},
        }

    def test_a_list_of_ids_is_kept(self) -> None:
        changes = diff_changes({"connector_ids": ["c-1"]}, {"connector_ids": ["c-1", "c-2"]})
        assert changes == {"connector_ids": {"from": ["c-1"], "to": ["c-1", "c-2"]}}

    def test_only_the_named_fields_are_compared(self) -> None:
        changes = diff_changes(
            {"role": "member", "bio": "a"}, {"role": "admin", "bio": "b"}, fields=["role"]
        )
        assert changes == {"role": {"from": "member", "to": "admin"}}

    def test_a_missing_side_is_all_none(self) -> None:
        assert diff_changes(None, {"role": "member"}) == {"role": {"from": None, "to": "member"}}

    def test_long_text_is_cut(self) -> None:
        changes = diff_changes({"description": ""}, {"description": "word " * 100})
        assert changes is not None and len(changes["description"]["to"]) == 200


class TestHints:
    def test_a_code_shows_its_first_characters(self) -> None:
        assert hint("WELCOME-2026-XYZ") == "WELC…"
        assert hint("fim_abcdef123456", keep=8) == "fim_abcd…"
        assert hint(None) is None and hint("") is None

    def test_text_loses_url_secrets(self) -> None:
        assert safe_text("failed for https://a.test/x?key=abc") == "failed for https://a.test/x"
        assert safe_text(None) is None


class TestTheNameAndShapeRulesAreTheAddressRules:
    def test_names(self) -> None:
        assert all(map(names_a_secret, ("api_key", "client_secret", "password", "authToken")))
        assert not any(map(names_a_secret, ("name", "key_file", "no_auth", "max_tokens", "")))

    def test_shapes(self) -> None:
        assert value_is_secret_shaped(SECRET)
        assert value_is_secret_shaped("Bearer abcdefgh12345678")
        assert not value_is_secret_shaped("member")
        assert not value_is_secret_shaped(None)


class TestInviteCodes:
    """The code admits whoever holds it. It was written into the audit row
    in full, and left the system in the audit export."""

    async def test_the_row_and_the_export_show_only_its_first_characters(
        self, world: World
    ) -> None:
        root = await world.user("root", admin=True)

        created = (
            await world.call("POST", "/api/admin/invite-codes", by=root, json={"note": "for bob"})
        ).json()
        code = created["code"]
        assert len(code) == 8

        row = await world.one(
            "invite_code.create", actor=root, target=("invite_code", created["id"])
        )
        assert row.detail == f"code={code[:4]}…"
        world.assert_absent(row, code)
        export = await world.call("GET", "/api/admin/audit-log/export", by=root)
        assert code not in export.text and code[:4] in export.text
        listing = await world.call("GET", "/api/admin/audit-log", by=root)
        assert code not in listing.text

    async def test_revoking_names_the_code_the_same_way(self, world: World) -> None:
        root = await world.user("root", admin=True)
        await world.add(InviteCode(id="code-1", code="WELCOME9", created_by_id=root.id))

        await world.call("DELETE", "/api/admin/invite-codes/code-1", by=root)

        row = await world.one("invite_code.revoke", actor=root, target=("invite_code", "code-1"))
        assert row.detail == "code_id=code-1, code=WELC…"
        world.assert_absent(row, "WELCOME9")


class TestFreeTextDetail:
    async def test_a_url_quoted_in_the_detail_loses_what_authenticates_it(
        self, world: World
    ) -> None:
        root = await world.user("root", admin=True)

        await audit.record(
            "notifications.test",
            actor=audit.actor_of(root),
            detail="POST https://hooks.example.com/bot/v2/hook/0123456789abcdef0123456789abcdef"
            "?key=hunter2hunter2 returned 403",
        )

        row = await world.one("notifications.test", actor=root, ip=None)
        assert row.detail == "POST https://hooks.example.com/bot/v2/hook/*** returned 403"
        world.assert_absent(row, "hunter2hunter2", "0123456789abcdef0123456789abcdef")

    async def test_plain_detail_is_stored_as_written(self, world: World) -> None:
        root = await world.user("root", admin=True)

        await audit.record(
            "settings.update", actor=audit.actor_of(root), detail="registration_mode=invite; a/b"
        )

        row = await world.one("settings.update", actor=root, ip=None)
        assert row.detail == "registration_mode=invite; a/b"
