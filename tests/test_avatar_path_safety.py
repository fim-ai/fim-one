"""The stored avatar value is never used as a path.

Properties:

* ``PATCH /api/auth/profile`` accepts a built-in avatar token or nothing.
* Serving and deleting touch only files the application wrote for that
  user inside ``uploads/avatars``, whatever the column holds — including
  values stored before the field was validated.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fim_one.db.models  # noqa: F401
from fim_one.db import get_session
from fim_one.db.base import Base
from fim_one.db.models import User
from fim_one.web import avatars
from fim_one.web.auth import get_current_user

_SECRET = "OTHER-USER-CONTENT-91c2"


class _Env:
    def __init__(self, root: Path, client: AsyncClient, sessions: async_sessionmaker[AsyncSession], me: str, other: str) -> None:
        self.root = root
        self.uploads = root / "uploads"
        self.client = client
        self.sessions = sessions
        self.me = me
        self.other = other

    async def store_raw(self, value: str | None, user_id: str | None = None) -> None:
        """Write the column directly, as an old row or another bug would."""
        async with self.sessions() as s:
            await s.execute(update(User).where(User.id == (user_id or self.me)).values(avatar=value))
            await s.commit()

    def snapshot(self) -> dict[str, bytes]:
        return {
            str(p.relative_to(self.root)): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file() and not p.name.startswith("app.db")
        }


@pytest_asyncio.fixture()
async def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Env]:
    uploads = tmp_path / "uploads"
    monkeypatch.setenv("UPLOADS_DIR", str(uploads))
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'app.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    me, other = str(uuid.uuid4()), str(uuid.uuid4())
    async with sessions() as s:
        s.add_all(
            [
                User(id=me, email="me@x.test", username="me", password_hash="x"),
                User(id=other, email="other@x.test", username="other", password_hash="x"),
            ]
        )
        await s.commit()

    # Things that must stay unreadable and undeleted.
    vdir = uploads / f"user_{other}"
    vdir.mkdir(parents=True)
    (vdir / "index.json").write_text(f'{{"f1": "{_SECRET}"}}')
    (vdir / "f1_contract.txt").write_text(_SECRET)
    (uploads / "kb" / "kb1").mkdir(parents=True)
    (uploads / "kb" / "kb1" / "doc.txt").write_text(_SECRET)
    (uploads / "avatars").mkdir()
    (uploads / "avatars" / f"{other}_1700000000.png").write_bytes(b"\x89PNG-other")
    (uploads / "avatars" / "sub").mkdir()
    (uploads / "avatars" / "sub" / "x.png").write_text(_SECRET)
    (tmp_path / "outside.db").write_text(_SECRET)
    (tmp_path / "outside.cfg").write_text(f"VALUE={_SECRET}")

    from fim_one.web.api.auth import router

    app = FastAPI()
    app.include_router(router)

    async def _session() -> AsyncIterator[AsyncSession]:
        async with sessions() as s:
            yield s

    async def _me() -> User:
        async with sessions() as s:
            user = await s.get(User, me)
            assert user is not None
            return user

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = _me
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        yield _Env(tmp_path, client, sessions, me, other)
    await engine.dispose()


def _unsafe_values(env: _Env) -> list[str]:
    """Values that are neither a built-in token nor a name the server generated."""
    v = env.other
    return [
        f"user_{v}/index.json",
        "kb/kb1/doc.txt",
        f"avatars/{v}_1700000000.png",          # another account's image
        "avatars/sub/x.png",
        f"avatars/{env.me}_1.txt",
        f"avatars/{env.me}_1.svg",
        f"avatars/{env.me}-1.png",
        "avatars/image.png",
        "outside.db",
        "not-an-avatar",
        "value with spaces",
        "avatars",
        "avatars/",
        "Builtin:cat",
        "builtin:",
        "builtin:Cat",
        "builtin:cat:",
        "https://example.com/a.png",
        "x" * 300,
    ]


class TestProfileFieldAcceptsOnlyBuiltinTokens:
    async def test_unsafe_values_are_rejected_and_not_stored(self, env: _Env) -> None:
        for value in _unsafe_values(env):
            r = await env.client.patch("/api/auth/profile", json={"avatar": value})
            assert r.status_code == 422, f"{value!r} accepted"
            async with env.sessions() as s:
                user = await s.get(User, env.me)
                assert user is not None and user.avatar is None

    @pytest.mark.parametrize("value", ["builtin:cat", "builtin:cat:blue", "builtin:gamepad:slate"])
    async def test_builtin_tokens_are_stored(self, env: _Env, value: str) -> None:
        r = await env.client.patch("/api/auth/profile", json={"avatar": value})
        assert r.status_code == 200
        assert r.json()["data"]["avatar"] == value

    async def test_own_uploaded_path_cannot_be_set_through_profile(self, env: _Env) -> None:
        r = await env.client.patch(
            "/api/auth/profile", json={"avatar": f"avatars/{env.me}_1700000000.png"}
        )
        assert r.status_code == 422


class TestStoredValueIsNeverUsedAsAPath:
    """Rows that already hold an unsafe value (written before validation)."""

    async def test_serving_ignores_unsafe_stored_values(self, env: _Env) -> None:
        for value in _unsafe_values(env):
            try:
                await env.store_raw(value)
            except Exception:
                continue  # the database itself refused the value (e.g. NUL)
            r = await env.client.get(f"/api/auth/avatar/{env.me}")
            assert r.status_code == 404, f"{value!r} served: {r.content[:60]!r}"
            assert _SECRET.encode() not in r.content

    async def test_deleting_ignores_unsafe_stored_values(self, env: _Env) -> None:
        before = env.snapshot()
        for value in _unsafe_values(env):
            try:
                await env.store_raw(value)
            except Exception:
                continue
            r = await env.client.delete("/api/auth/avatar")
            assert r.status_code == 200
            assert env.snapshot() == before, f"{value!r} changed files on disk"

    async def test_switching_to_builtin_ignores_unsafe_stored_values(self, env: _Env) -> None:
        before = env.snapshot()
        for value in _unsafe_values(env):
            try:
                await env.store_raw(value)
            except Exception:
                continue
            r = await env.client.patch("/api/auth/profile", json={"avatar": "builtin:cat"})
            assert r.status_code == 200
            assert env.snapshot() == before

    async def test_unsafe_stored_values_are_not_echoed(self, env: _Env) -> None:
        await env.store_raw(f"user_{env.other}/index.json")
        r = await env.client.patch("/api/auth/profile", json={"display_name": "n"})
        assert r.json()["data"]["avatar"] is None

    async def test_only_regular_files_are_served(self, env: _Env) -> None:
        entry = env.uploads / "avatars" / f"{env.me}_1.png"
        entry.mkdir()
        await env.store_raw(f"avatars/{env.me}_1.png")

        r = await env.client.get(f"/api/auth/avatar/{env.me}")
        assert r.status_code == 404
        r = await env.client.delete("/api/auth/avatar")
        assert r.status_code == 200


class TestUploadedAvatarLifecycle:
    async def test_upload_serve_delete(self, env: _Env) -> None:
        r = await env.client.post(
            "/api/auth/avatar", files={"file": ("my photo.PNG", b"\x89PNG-me", "image/png")}
        )
        assert r.status_code == 200
        stored = r.json()["data"]["avatar"]
        assert avatars.uploaded_avatar_name(env.me, stored) is not None

        r = await env.client.get(f"/api/auth/avatar/{env.me}")
        assert r.status_code == 200 and r.content == b"\x89PNG-me"
        assert r.headers["x-content-type-options"] == "nosniff"

        before_other = (env.uploads / "avatars" / f"{env.other}_1700000000.png").read_bytes()
        r = await env.client.delete("/api/auth/avatar")
        assert r.status_code == 200 and r.json()["data"]["avatar"] is None
        assert not list((env.uploads / "avatars").glob(f"{env.me}_*"))
        assert (env.uploads / "avatars" / f"{env.other}_1700000000.png").read_bytes() == before_other
        assert (await env.client.get(f"/api/auth/avatar/{env.me}")).status_code == 404

    @pytest.mark.parametrize("name", ["a.svg", "a.html", "a", "a.png.exe", ".png"])
    async def test_upload_rejects_other_extensions(self, env: _Env, name: str) -> None:
        r = await env.client.post("/api/auth/avatar", files={"file": (name, b"x", "image/png")})
        assert r.status_code == 400

    async def test_another_users_own_avatar_is_still_served(self, env: _Env) -> None:
        await env.store_raw(f"avatars/{env.other}_1700000000.png", env.other)
        r = await env.client.get(f"/api/auth/avatar/{env.other}")
        assert r.status_code == 200 and r.content == b"\x89PNG-other"


class TestArtifactDownloadIds:
    """Sibling route: ids are single segments and only regular files are served."""

    async def test_ids_must_be_plain_segments(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from fim_one.web.api import artifacts

        uploads = tmp_path / "uploads"
        monkeypatch.setenv("UPLOADS_DIR", str(uploads))
        good = uploads / "conversations" / "c1" / "artifacts"
        good.mkdir(parents=True)
        (good / "abc_report.html").write_text("ok")
        (good / "dir_entry").mkdir()  # not a regular file
        monkeypatch.setattr(
            artifacts, "_artifacts_dir", lambda cid: uploads / "conversations" / cid / "artifacts"
        )

        app = FastAPI()
        app.include_router(artifacts.router)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            assert (await c.get("/api/conversations/c1/artifacts/abc")).status_code == 200
            for cid, aid in (("c1", "dir"), ("c 1", "abc"), ("c1", "a.b"), ("-c1", "abc"), ("c1", "-abc")):
                r = await c.get(f"/api/conversations/{cid}/artifacts/{aid}")
                assert r.status_code == 404, (cid, aid)
