"""A deleted user's files go after the deletion is committed, never before.

``purge_user_data`` removed the files first and the caller committed
afterwards: when the commit (or the audit record staged with it) failed, the
account was still there and its uploads were gone for good. A file left
behind by the opposite order is only an orphan.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fim_one.db.models import User
from tests.audit.harness import World, world  # noqa: F401


@pytest.fixture()
def uploads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "uploads"
    monkeypatch.setenv("UPLOADS_DIR", str(root))
    monkeypatch.setenv("VECTOR_STORE_DIR", str(tmp_path / "vector_store"))
    return root


def _files_of(uploads: Path, user_id: str) -> list[Path]:
    own = uploads / f"user_{user_id}" / "report.pdf"
    avatar = uploads / "avatars" / f"{user_id}_face.png"
    for path in (own, avatar):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"kept until the deletion is committed")
    return [own, avatar]


class _Refused(RuntimeError):
    pass


async def _refuse(*_args: Any, **_kwargs: Any) -> None:
    raise _Refused("the audit record could not be staged")


class TestAnAdministratorDeletesAUser:
    async def test_the_files_go_with_the_account(self, world: World, uploads: Path) -> None:
        root = await world.user("root", admin=True)
        bob = await world.user("bob")
        files = _files_of(uploads, bob.id)

        await world.call("DELETE", f"/api/admin/users/{bob.id}", by=root)

        assert await world.get(User, bob.id) is None
        assert [path.exists() for path in files] == [False, False]
        await world.one("user.delete", actor=root, target=("user", bob.id))

    async def test_a_deletion_that_does_not_commit_keeps_the_files(
        self, world: World, uploads: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = await world.user("root", admin=True)
        bob = await world.user("bob")
        files = _files_of(uploads, bob.id)
        monkeypatch.setattr("fim_one.web.api.admin.stage_audit", _refuse)

        with pytest.raises(_Refused):
            await world.call("DELETE", f"/api/admin/users/{bob.id}", by=root)

        assert await world.get(User, bob.id) is not None
        assert [path.exists() for path in files] == [True, True]


class TestAUserDeletesTheirOwnAccount:
    async def test_the_files_go_with_the_account(self, world: World, uploads: Path) -> None:
        dave = await world.user("dave")
        files = _files_of(uploads, dave.id)

        await world.call("DELETE", "/api/auth/account", by=dave)

        assert await world.get(User, dave.id) is None
        assert [path.exists() for path in files] == [False, False]

    async def test_a_deletion_that_does_not_commit_keeps_the_files(
        self, world: World, uploads: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dave = await world.user("dave")
        files = _files_of(uploads, dave.id)
        monkeypatch.setattr("fim_one.web.api.auth.audit.record", _refuse)

        with pytest.raises(_Refused):
            await world.call("DELETE", "/api/auth/account", by=dave)

        assert await world.get(User, dave.id) is not None
        assert [path.exists() for path in files] == [True, True]
