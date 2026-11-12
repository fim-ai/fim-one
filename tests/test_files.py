"""Tests for :mod:`fim_one.web.api.files` — the per-user index lock.

The lock is ``flock`` on POSIX and ``msvcrt.locking`` on Windows; both lock
per open file handle, so two holders in one process still exclude each other.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from fim_one.web.api import files


@pytest.fixture(autouse=True)
def _uploads(tmp_path: Path) -> Iterator[None]:
    with patch.object(files, "UPLOAD_ROOT", tmp_path):
        yield


class TestFileLock:
    @pytest.mark.asyncio
    async def test_second_holder_waits_for_release(self) -> None:
        order: list[str] = []
        first_holding = asyncio.Event()
        release_first = asyncio.Event()

        async def first() -> None:
            async with files._file_lock("u1"):
                order.append("first-acquired")
                first_holding.set()
                await release_first.wait()
                order.append("first-released")

        async def second() -> None:
            await first_holding.wait()
            async with files._file_lock("u1"):
                order.append("second-acquired")

        t1 = asyncio.create_task(first())
        t2 = asyncio.create_task(second())
        await first_holding.wait()
        await asyncio.sleep(0.3)
        assert "second-acquired" not in order

        release_first.set()
        await asyncio.wait_for(asyncio.gather(t1, t2), timeout=5)
        assert order == ["first-acquired", "first-released", "second-acquired"]

    @pytest.mark.asyncio
    async def test_lock_is_reacquirable_after_release(self) -> None:
        for _ in range(3):
            async with files._file_lock("u1"):
                pass

    @pytest.mark.asyncio
    async def test_users_do_not_block_each_other(self) -> None:
        async with files._file_lock("u1"):
            await asyncio.wait_for(self._hold("u2"), timeout=2)

    @staticmethod
    async def _hold(user_id: str) -> None:
        async with files._file_lock(user_id):
            pass

    @pytest.mark.asyncio
    async def test_index_round_trip_under_lock(self) -> None:
        async with files._file_lock("u1"):
            files._save_index("u1", {"f1": {"name": "a.txt"}})
            assert files._load_index("u1") == {"f1": {"name": "a.txt"}}


def _zip(members: dict[str, bytes]) -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


class TestUploadOverArchiveLimits:
    """A document that unpacks beyond the archive limits is refused with a 4xx
    and leaves nothing behind."""

    @pytest.fixture(autouse=True)
    def _limits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARKITDOWN_MAX_ARCHIVE_MEMBERS", "50")

    @staticmethod
    def _client() -> object:
        from types import SimpleNamespace

        from fastapi import FastAPI
        from fastapi.responses import JSONResponse
        from fastapi.testclient import TestClient

        from fim_one.web.auth import get_current_user
        from fim_one.web.exceptions import AppError

        app = FastAPI()
        app.include_router(files.router)

        @app.exception_handler(AppError)
        async def _app_error(_request: object, exc: AppError) -> JSONResponse:
            return JSONResponse(
                {"detail": exc.detail, "error_code": exc.error_code},
                status_code=exc.status_code,
            )

        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="u1")
        return TestClient(app, raise_server_exceptions=False)

    def test_the_upload_is_refused_and_removed(self, tmp_path: Path) -> None:
        from fim_one.core.document.markitdown_core import (
            ArchiveLimitError,
            check_archive_limits,
        )

        bomb = _zip({f"word/m{i}.xml": b"<a/>" for i in range(51)})
        with pytest.raises(ArchiveLimitError) as limit:
            check_archive_limits(bomb)

        client = self._client()
        r = client.post(  # type: ignore[attr-defined]
            "/api/files/upload",
            files={"file": ("report.docx", bomb, "application/octet-stream")},
        )

        assert r.status_code == 422, r.text
        assert r.json() == {"detail": str(limit.value), "error_code": "document_archive_limit"}
        user_dir = tmp_path / "user_u1"
        left = sorted(p.name for p in user_dir.iterdir()) if user_dir.exists() else []
        assert [name for name in left if name != ".lock"] == []
        assert files._load_index("u1") == {}

    def test_a_document_within_the_limits_is_kept(self, tmp_path: Path) -> None:
        client = self._client()
        r = client.post(  # type: ignore[attr-defined]
            "/api/files/upload",
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )

        assert r.status_code == 200, r.text
        file_id = r.json()["data"]["file_id"]
        assert set(files._load_index("u1")) == {file_id}

    def test_images_of_an_office_document_over_the_limits(self, tmp_path: Path) -> None:
        from fim_one.core.document.processor import _extract_with_images_sync

        path = tmp_path / "deck.pptx"
        path.write_bytes(_zip({f"ppt/m{i}.xml": b"<a/>" for i in range(51)}))
        assert _extract_with_images_sync(path) == (None, [])
