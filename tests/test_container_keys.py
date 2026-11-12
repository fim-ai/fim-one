"""The container's key store never loses a key and never makes one up for existing data.

``CREDENTIAL_ENCRYPTION_KEY`` decrypts every stored credential. The entrypoint
takes it (and ``JWT_SECRET_KEY``) from the environment or from a key store
of root's outside ``data/``; it generates one only on a first start, stores
it before using it, and never deletes, moves or replaces key material
(``core/security/container_keys.py``, ``docker-entrypoint.sh``).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from fim_one.core.security import container_keys as keys

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")

JWT = "a" * 64
ENC = "b" * 64


_REAL_MOUNT_POINT = keys._mount_point


@pytest.fixture(autouse=True)
def _store_is_a_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    """A temporary directory stands in for the mounted key store: a volume of its own."""
    monkeypatch.setattr(keys, "_mount_point", os.path.realpath)


def _mount_table(path: Path, *mounts: tuple[str, str]) -> Path:
    """A mount table at *path* of *mounts* (mount point, type) plus ``/``."""
    rows = [("/", "overlay"), *mounts]
    path.write_text(
        "".join(
            f"{n} 1 0:{n} / {point.replace(' ', chr(92) + '040')} rw - {kind} src rw\n"
            for n, (point, kind) in enumerate(rows, start=20)
        )
    )
    return path


def _mounts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *mounts: tuple[str, str]) -> None:
    """The real check, against a mount table of *mounts* (mount point, type) plus ``/``."""
    monkeypatch.delenv("FIM_MOUNTINFO_PATH", raising=False)
    monkeypatch.setattr(keys, "_MOUNTINFO", str(_mount_table(tmp_path / "mountinfo", *mounts)))
    monkeypatch.setattr(keys, "_mount_point", _REAL_MOUNT_POINT)


def _env(store: Path, **extra: str) -> dict[str, str]:
    return {"FIM_SECRETS_DIR": str(store), **extra}


def _store(tmp_path: Path, content: str | None = None) -> Path:
    store = tmp_path / "secrets"
    store.mkdir(mode=0o700)
    if content is not None:
        (store / "keys.env").write_text(content)
        (store / "keys.env").chmod(0o400)
    return store


class TestLoad:
    def test_first_start_nothing_anywhere(self, tmp_path: Path) -> None:
        found, missing = keys.load(_env(tmp_path / "secrets"))

        assert found == {} and missing == list(keys.KEY_NAMES)
        assert not (tmp_path / "secrets").exists()  # looking creates nothing

    def test_restart_keys_come_from_the_store(self, tmp_path: Path) -> None:
        store = _store(tmp_path, f"JWT_SECRET_KEY={JWT}\nCREDENTIAL_ENCRYPTION_KEY={ENC}\n")

        found, missing = keys.load(_env(store))

        assert found == {"JWT_SECRET_KEY": JWT, "CREDENTIAL_ENCRYPTION_KEY": ENC} and not missing

    def test_the_environment_wins_and_is_not_written(self, tmp_path: Path) -> None:
        store = _store(tmp_path, f"JWT_SECRET_KEY={JWT}\n")

        found, missing = keys.load(
            _env(store, JWT_SECRET_KEY="from-env", CREDENTIAL_ENCRYPTION_KEY="from-env-too")
        )

        assert found == {} and missing == []
        assert (store / "keys.env").read_text() == f"JWT_SECRET_KEY={JWT}\n"

    def test_one_key_in_the_environment_one_missing(self, tmp_path: Path) -> None:
        found, missing = keys.load(_env(_store(tmp_path), JWT_SECRET_KEY="from-env"))

        assert found == {} and missing == ["CREDENTIAL_ENCRYPTION_KEY"]


class TestGenerate:
    def test_new_keys_are_on_disk_before_they_are_returned(self, tmp_path: Path) -> None:
        store = tmp_path / "secrets"

        created = keys.generate(_env(store))

        assert set(created) == set(keys.KEY_NAMES)
        assert all(re.fullmatch(r"[0-9a-f]{64}", value) for value in created.values())
        on_disk = dict(line.split("=") for line in (store / "keys.env").read_text().splitlines())
        assert on_disk == created
        assert (store / "keys.env").stat().st_mode & 0o777 == 0o400
        assert store.stat().st_mode & 0o777 == 0o700
        assert keys.load(_env(store)) == (created, [])

    def test_a_stored_key_is_never_replaced(self, tmp_path: Path) -> None:
        store = _store(tmp_path, f"CREDENTIAL_ENCRYPTION_KEY={ENC}\n")

        created = keys.generate(_env(store))

        assert set(created) == {"JWT_SECRET_KEY"}
        assert f"CREDENTIAL_ENCRYPTION_KEY={ENC}" in (store / "keys.env").read_text()

    def test_nothing_to_generate_writes_nothing(self, tmp_path: Path) -> None:
        store = _store(tmp_path, f"JWT_SECRET_KEY={JWT}\nCREDENTIAL_ENCRYPTION_KEY={ENC}\n")
        before = (store / "keys.env").stat().st_mtime_ns

        assert keys.generate(_env(store)) == {}
        assert (store / "keys.env").stat().st_mtime_ns == before

    def test_a_failed_write_leaves_no_key_behind_and_hands_none_out(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _store(tmp_path, f"JWT_SECRET_KEY={JWT}\n")

        def no_sync(_fd: int) -> None:
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(keys.os, "fsync", no_sync)

        with pytest.raises(OSError):
            keys.generate(_env(store))
        assert (store / "keys.env").read_text() == f"JWT_SECRET_KEY={JWT}\n"
        assert sorted(p.name for p in store.iterdir()) == ["keys.env"]


class TestAKeyStoreThatIsNotAVolume:
    """A key generated into the container's own filesystem is lost on the next recreate."""

    def test_nothing_is_generated_and_the_message_says_what_to_do(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mounts(monkeypatch, tmp_path)
        store = _store(tmp_path)

        with pytest.raises(keys.KeyStoreError) as refused:
            keys.generate(_env(store, JWT_SECRET_KEY=JWT))

        message = str(refused.value)
        assert f"{store} is not on a mounted volume" in message
        assert "CREDENTIAL_ENCRYPTION_KEY" in message and "JWT_SECRET_KEY" not in message
        assert "FIM_ALLOW_UNMOUNTED_KEY_STORE=true" in message
        assert list(store.iterdir()) == []

    def test_a_store_that_does_not_exist_is_not_created(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mounts(monkeypatch, tmp_path)

        with pytest.raises(keys.KeyStoreError):
            keys.generate(_env(tmp_path / "secrets"))
        assert not (tmp_path / "secrets").exists()

    def test_the_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _mounts(monkeypatch, tmp_path)
        store = _store(tmp_path)

        created = keys.generate(_env(store, FIM_ALLOW_UNMOUNTED_KEY_STORE="true"))

        assert set(created) == set(keys.KEY_NAMES)


class TestWhereTheKeyStoreLives:
    def test_a_store_inside_a_mounted_directory_counts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mounts(monkeypatch, tmp_path, (str(tmp_path / "persist dir"), "ext4"))

        assert keys._is_mount_point(str(tmp_path / "persist dir" / "fim" / "secrets"))
        assert keys._is_mount_point(str(tmp_path / "persist dir"))

    def test_nothing_mounted_but_the_root_does_not_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mounts(monkeypatch, tmp_path, (str(tmp_path / "elsewhere"), "ext4"))

        assert not keys._is_mount_point(str(tmp_path / "secrets"))
        assert not keys._is_mount_point("/var/lib/fim-one/secrets")

    def test_a_store_in_memory_does_not_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The nearest mount above the store decides, not one further up.
        _mounts(
            monkeypatch,
            tmp_path,
            (str(tmp_path / "data"), "ext4"),
            (str(tmp_path / "data" / "run"), "tmpfs"),
        )

        assert not keys._is_mount_point(str(tmp_path / "data" / "run" / "secrets"))
        assert keys._is_mount_point(str(tmp_path / "data" / "secrets"))

    def test_the_mount_table_can_be_another_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        table = _mount_table(tmp_path / "table", (str(tmp_path / "vol"), "ext4"))
        monkeypatch.setenv("FIM_MOUNTINFO_PATH", str(table))
        monkeypatch.setattr(keys, "_MOUNTINFO", str(tmp_path / "absent"))
        monkeypatch.setattr(keys, "_mount_point", _REAL_MOUNT_POINT)

        assert keys._is_mount_point(str(tmp_path / "vol" / "secrets"))
        assert not keys._is_mount_point(str(tmp_path / "secrets"))

    def test_without_a_mount_table_the_mount_points_decide(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FIM_MOUNTINFO_PATH", raising=False)
        monkeypatch.setattr(keys, "_MOUNTINFO", str(tmp_path / "absent"))
        monkeypatch.setattr(keys, "_mount_point", _REAL_MOUNT_POINT)
        mounted = tmp_path / "mounted"
        monkeypatch.setattr(os.path, "ismount", lambda path: path in (str(mounted), "/"))

        assert keys._is_mount_point(str(mounted / "secrets"))
        assert not keys._is_mount_point(str(tmp_path / "secrets"))


class TestWhereTheKeyStoreMayNotBe:
    """Where the application's user could replace it, the store is not used."""

    @pytest.mark.parametrize("inside", ["data", "data/keys", "uploads/x"])
    def test_inside_the_applications_directories(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inside: str
    ) -> None:
        monkeypatch.chdir(tmp_path)
        store = tmp_path / inside / "secrets"
        store.mkdir(parents=True, mode=0o700)

        for call in (keys.load, keys.generate):
            with pytest.raises(keys.KeyStoreError, match="inside"):
                call(_env(store))
        assert list(store.iterdir()) == []

    def test_behind_a_symlink_into_them(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "files").mkdir()
        (tmp_path / "elsewhere").symlink_to(tmp_path / "files")

        with pytest.raises(keys.KeyStoreError, match="inside"):
            keys.generate(_env(tmp_path / "elsewhere" / "secrets", UPLOADS_DIR="files"))
        assert not (tmp_path / "files" / "secrets").exists()
        assert keys.differing(_env(tmp_path / "elsewhere" / "secrets", UPLOADS_DIR="files")) == []

    def test_a_directory_others_can_write_between_the_mount_and_the_store(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mounts(monkeypatch, tmp_path, (str(tmp_path / "persist"), "ext4"))
        middle = tmp_path / "persist" / "fim"
        middle.mkdir(parents=True)
        store = middle / "secrets"

        middle.chmod(0o777)
        with pytest.raises(keys.KeyStoreError, match="on the way from the mount"):
            keys.generate(_env(store))
        assert not store.exists()
        (tmp_path / "persist").chmod(0o757)
        middle.chmod(0o755)
        with pytest.raises(keys.KeyStoreError, match="on the way from the mount"):
            keys.load(_env(store))
        (tmp_path / "persist").chmod(0o755)

        assert set(keys.generate(_env(store))) == set(keys.KEY_NAMES)

    def test_the_store_as_the_mount_itself(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _store(tmp_path)
        _mounts(monkeypatch, tmp_path, (str(store), "ext4"))
        tmp_path.chmod(0o777)  # above the mount: not the store's to answer for
        try:
            assert set(keys.generate(_env(store))) == set(keys.KEY_NAMES)
        finally:
            tmp_path.chmod(0o700)


class TestAKeyThatDiffersFromTheStoredOne:
    def test_it_is_named_never_shown(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        store = _store(tmp_path, f"JWT_SECRET_KEY={JWT}\nCREDENTIAL_ENCRYPTION_KEY={ENC}\n")
        other = "c" * 64
        monkeypatch.setenv("FIM_SECRETS_DIR", str(store))
        monkeypatch.setenv("JWT_SECRET_KEY", JWT)
        monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", other)

        assert keys.main(["x", "load"]) == 0

        out = capsys.readouterr()
        assert "WARNING: CREDENTIAL_ENCRYPTION_KEY" in out.err and "JWT_SECRET_KEY" not in out.err
        assert ENC not in out.err + out.out and other not in out.err + out.out
        assert keys.differing({"FIM_SECRETS_DIR": str(store), "JWT_SECRET_KEY": JWT}) == []


class TestTheDatabaseIsWaitedFor:
    def test_a_database_that_answers_late(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[float] = []

        def look(_url: str, timeout: float) -> bool:
            calls.append(timeout)
            if len(calls) < 3:
                raise ConnectionRefusedError("not yet")
            return True

        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://db/fim")
        monkeypatch.setattr(keys, "_has_schema", look)
        monkeypatch.setattr(keys.time, "sleep", lambda _seconds: None)

        assert keys._db_state() == "existing"
        assert calls == [keys._DB_CONNECT_TIMEOUT] * 3

    def test_a_database_that_never_answers_is_an_error_not_a_state(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def look(_url: str, _timeout: float) -> bool:
            raise TimeoutError

        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://db/fim")
        monkeypatch.setattr(keys, "_has_schema", look)
        monkeypatch.setattr(keys.time, "sleep", lambda _seconds: None)

        assert keys.main(["x", "db-state"]) != 0
        out = capsys.readouterr()
        assert out.out == "" and out.err.count("asking again") == keys._DB_ATTEMPTS - 1


class TestAKeyStoreThatIsNotAsExpected:
    """It is left exactly as it is, and the start is refused."""

    def _snapshot(self, root: Path) -> list[tuple[str, bool, str]]:
        return sorted(
            (str(p.relative_to(root)), p.is_symlink(), p.read_text() if p.is_file() else "")
            for p in root.rglob("*")
        )

    def test_writable_by_others(self, tmp_path: Path) -> None:
        store = _store(tmp_path, f"CREDENTIAL_ENCRYPTION_KEY={ENC}\n")
        store.chmod(0o777)
        before = self._snapshot(tmp_path)

        for call in (keys.load, keys.generate):
            with pytest.raises(keys.KeyStoreError):
                call(_env(store))
        assert self._snapshot(tmp_path) == before

    def test_a_symlink_in_its_place(self, tmp_path: Path) -> None:
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "keys.env").write_text(f"JWT_SECRET_KEY={JWT}\n")
        store = tmp_path / "secrets"
        store.symlink_to(elsewhere)
        before = self._snapshot(tmp_path)

        for call in (keys.load, keys.generate):
            with pytest.raises(keys.KeyStoreError):
                call(_env(store))
        assert self._snapshot(tmp_path) == before

    @pytest.mark.parametrize(
        "content",
        ["JWT_SECRET_KEY=short\n", "SOMETHING_ELSE=" + "c" * 64 + "\n", "not a key file\n"],
    )
    def test_a_file_that_is_not_a_key_file(self, tmp_path: Path, content: str) -> None:
        store = _store(tmp_path, content)

        for call in (keys.load, keys.generate):
            with pytest.raises(keys.KeyStoreError):
                call(_env(store))
        assert (store / "keys.env").read_text() == content

    def test_a_key_file_others_can_write(self, tmp_path: Path) -> None:
        store = _store(tmp_path, f"JWT_SECRET_KEY={JWT}\n")
        (store / "keys.env").chmod(0o666)

        with pytest.raises(keys.KeyStoreError):
            keys.load(_env(store))
        assert (store / "keys.env").read_text() == f"JWT_SECRET_KEY={JWT}\n"

    def test_the_command_exits_non_zero_and_prints_no_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        store = _store(tmp_path, f"CREDENTIAL_ENCRYPTION_KEY={ENC}\n")
        store.chmod(0o777)
        monkeypatch.setenv("FIM_SECRETS_DIR", str(store))
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY", raising=False)

        codes = [keys.main(["x", "load"]), keys.main(["x", "generate"])]

        out = capsys.readouterr()
        assert all(code != 0 for code in codes)
        assert "export" not in out.out and ENC not in out.out + out.err


class TestTheCommands:
    def test_load_prints_exports_and_what_is_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        store = _store(tmp_path, f"JWT_SECRET_KEY={JWT}\n")
        monkeypatch.setenv("FIM_SECRETS_DIR", str(store))
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY", raising=False)

        assert keys.main(["x", "load"]) == 0

        assert capsys.readouterr().out == (
            f"export JWT_SECRET_KEY={JWT}\nFIM_MISSING_KEYS=CREDENTIAL_ENCRYPTION_KEY\n"
        )

    async def test_a_database_without_a_schema_is_a_first_start(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio
        import sqlite3

        path = tmp_path / "fim.db"
        monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{path}")

        async def state() -> str:
            # The command runs an event loop of its own: off this one.
            return await asyncio.to_thread(keys._db_state)

        assert await state() == "empty" and not path.exists()
        sqlite3.connect(path).close()
        assert await state() == "empty"
        with sqlite3.connect(path) as db:
            db.execute("create table alembic_version (version_num varchar(32))")
        assert await state() == "existing"


REPO = Path(__file__).resolve().parents[1]
ENTRYPOINT = (REPO / "docker-entrypoint.sh").read_text()

_STUBS = {
    # Root, and the application user's id; the data directories are already its own.
    "id": 'if [ "$1" = "-u" ] && [ -z "$2" ]; then echo "$STUB_UID"; else echo 10001; fi\n',
    "stat": "echo 10001\n",
    "chown": "exit 0\n",
    # Drops nothing here: runs what follows its options.
    "setpriv": 'while [ "${1#--}" != "$1" ]; do shift; done\nexec "$@"\n',
    "python": """case "$1" in
  */container_keys.py)
    if [ "$2" = db-state ]; then
      env > "$STUB_LOG/db-state.env"
      [ "$STUB_DB_STATE" = fail ] && exit 3
      echo "$STUB_DB_STATE"
      exit 0
    fi
    exec "$REAL_PYTHON" -s "$@" ;;
  -c)
    case "$2" in *uvicorn*) env > "$STUB_LOG/api.env" ;; esac
    exit 0 ;;
esac
exit 99
""",
    "alembic": 'env > "$STUB_LOG/alembic.env"\n',
    "node": 'env > "$STUB_LOG/node.env"\n',
}


class _Started:
    def __init__(self, code: int, err: str, log: Path) -> None:
        self.code, self.err, self._log = code, err, log

    def env(self, step: str) -> dict[str, str] | None:
        path = self._log / f"{step}.env"
        if not path.exists():
            return None
        return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)


def _start(
    tmp_path: Path,
    store: Path,
    *,
    uid: str = "0",
    db_state: str = "empty",
    mounts: tuple[tuple[str, str], ...] = (),
    **env: str,
) -> _Started:
    """Run ``docker-entrypoint.sh`` under ``sh`` with stand-ins for what the image provides."""
    import subprocess
    import sys

    app, bin_dir, log = tmp_path / "app", tmp_path / "bin", tmp_path / "log"
    for directory in (app / "frontend", bin_dir, log):
        directory.mkdir(parents=True)
    (app / "container_keys.py").write_text(
        (REPO / "src/fim_one/core/security/container_keys.py").read_text()
    )
    script = tmp_path / "entrypoint.sh"
    script.write_text(ENTRYPOINT.replace("/app", str(app)))
    for name, body in _STUBS.items():
        # The log's path is written in: the frontend starts with an empty environment.
        (bin_dir / name).write_text("#!/bin/sh\n" + body.replace("$STUB_LOG", str(log)))
        (bin_dir / name).chmod(0o755)
    # The host's own mounts do not decide: only ``/`` is mounted, unless a
    # test says otherwise.
    table = _mount_table(tmp_path / "mountinfo", *mounts)
    done = subprocess.run(
        ["/bin/sh", str(script)],
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "FIM_MOUNTINFO_PATH": str(table),
            "STUB_UID": uid,
            "STUB_DB_STATE": db_state,
            "STUB_LOG": str(log),
            "REAL_PYTHON": sys.executable,
            "FIM_SECRETS_DIR": str(store),
            "CODE_EXEC_BACKEND": "local",
            **env,
        },
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return _Started(done.returncode, done.stderr, log)


class TestTheEntrypoint:
    """The entrypoint run under ``sh``, with the image's commands stubbed."""

    def test_a_first_start_without_a_volume_for_the_key_store_is_refused(
        self, tmp_path: Path
    ) -> None:
        store = _store(tmp_path)

        started = _start(tmp_path, store)

        assert started.code != 0
        assert f"{store} is not on a mounted volume" in started.err
        assert "FIM_ALLOW_UNMOUNTED_KEY_STORE=true" in started.err
        assert "JWT_SECRET_KEY CREDENTIAL_ENCRYPTION_KEY" in started.err
        assert list(store.iterdir()) == []
        assert started.env("alembic") is None and started.env("node") is None

    def test_a_first_start_with_a_volume_for_the_key_store(self, tmp_path: Path) -> None:
        store = _store(tmp_path)

        started = _start(tmp_path, store, mounts=((str(store), "ext4"),))

        assert started.code == 0, started.err
        assert sorted(p.name for p in store.iterdir()) == ["keys.env"]

    def test_a_first_start_with_the_override(self, tmp_path: Path) -> None:
        store = _store(tmp_path)

        started = _start(
            tmp_path,
            store,
            FIM_ALLOW_UNMOUNTED_KEY_STORE="true",
            DATABASE_URL="postgresql+asyncpg://db/fim",
            KEEP_ALIVE_TIMEOUT="65000",
            NODE_OPTIONS="--max-old-space-size=512",
            NEXT_PUBLIC_X="1",
        )

        assert started.code == 0, started.err
        stored = dict(line.split("=") for line in (store / "keys.env").read_text().splitlines())
        assert set(stored) == set(keys.KEY_NAMES)
        for step in ("db-state", "alembic", "api"):
            env = started.env(step)
            assert env is not None and env["PYTHONNOUSERSITE"] == "1", step
            assert env["HOME"] == "/home/fim", step
        api = started.env("api")
        assert api is not None
        assert {name: api[name] for name in keys.KEY_NAMES} == stored
        web = started.env("node")
        assert web is not None
        assert not set(web) & {*keys.KEY_NAMES, "DATABASE_URL", "FIM_SECRETS_DIR"}
        assert web["KEEP_ALIVE_TIMEOUT"] == "65000"
        assert web["NODE_OPTIONS"] == "--max-old-space-size=512"
        assert web["NEXT_PUBLIC_X"] == "1"

    def test_existing_data_without_keys_is_refused_unless_overridden(self, tmp_path: Path) -> None:
        store = _store(tmp_path)

        started = _start(tmp_path, store, db_state="existing", FIM_ALLOW_UNMOUNTED_KEY_STORE="true")

        assert started.code != 0
        assert "FIM_ALLOW_NEW_KEYS=true" in started.err
        assert "JWT_SECRET_KEY CREDENTIAL_ENCRYPTION_KEY" in started.err
        assert list(store.iterdir()) == [] and started.env("alembic") is None

    def test_existing_data_with_stored_keys_starts_without_asking_the_database(
        self, tmp_path: Path
    ) -> None:
        store = _store(tmp_path, f"JWT_SECRET_KEY={JWT}\nCREDENTIAL_ENCRYPTION_KEY={ENC}\n")

        started = _start(tmp_path, store, db_state="fail")

        assert started.code == 0, started.err
        assert started.env("db-state") is None
        api = started.env("api")
        assert api is not None and api["CREDENTIAL_ENCRYPTION_KEY"] == ENC

    def test_a_database_that_cannot_be_asked_generates_nothing(self, tmp_path: Path) -> None:
        store = _store(tmp_path)

        started = _start(tmp_path, store, db_state="fail", FIM_ALLOW_UNMOUNTED_KEY_STORE="true")

        assert started.code != 0 and "no key is generated" in started.err
        assert list(store.iterdir()) == [] and started.env("alembic") is None

    def test_a_key_store_that_cannot_be_read_stops_the_start(self, tmp_path: Path) -> None:
        store = _store(tmp_path, f"JWT_SECRET_KEY={JWT}\n")
        store.chmod(0o777)

        started = _start(tmp_path, store, FIM_ALLOW_UNMOUNTED_KEY_STORE="true")

        assert started.code != 0 and "key store could not be read" in started.err
        assert (store / "keys.env").read_text() == f"JWT_SECRET_KEY={JWT}\n"
        assert started.env("alembic") is None

    def test_another_user_needs_the_keys_in_the_environment(self, tmp_path: Path) -> None:
        store = _store(tmp_path)

        refused = _start(tmp_path / "a", store, uid="1234")
        started = _start(
            tmp_path / "b", store, uid="1234", JWT_SECRET_KEY=JWT, CREDENTIAL_ENCRYPTION_KEY=ENC
        )

        assert refused.code != 0 and "running as uid 1234" in refused.err
        assert started.code == 0, started.err
        for step in ("alembic", "api"):
            env = started.env(step)
            assert env is not None and env["PYTHONNOUSERSITE"] == "1", step

    def test_an_environment_name_that_is_not_a_plain_name_is_not_evaluated(
        self, tmp_path: Path
    ) -> None:
        store = _store(tmp_path, f"JWT_SECRET_KEY={JWT}\nCREDENTIAL_ENCRYPTION_KEY={ENC}\n")
        marker = tmp_path / "evaluated"

        started = _start(tmp_path, store, **{f"NEXT_X$(touch {marker})": "1"})

        assert started.code == 0, started.err
        assert not marker.exists()
        web = started.env("node")
        assert web is not None and not [name for name in web if name.startswith("NEXT_X")]

    def test_it_deletes_and_moves_nothing(self) -> None:
        code = "\n".join(
            line for line in ENTRYPOINT.splitlines() if not line.lstrip().startswith("#")
        )
        assert not re.search(r"\b(rm|mv|rmdir|unlink|rmtree)\b", code)
