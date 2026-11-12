"""The container's key store: where generated keys live, and when one may be generated.

``JWT_SECRET_KEY`` signs sessions and ``CREDENTIAL_ENCRYPTION_KEY`` encrypts
stored credentials. Losing the second makes every stored credential
unreadable, so the rules are strict:

- A key set in the environment is used as it is and never written anywhere.
- A key that is not set is read from the key store, a directory that
  belongs to root and that the application's user can neither read nor
  replace (``/var/lib/fim-one/secrets`` by default, a volume of its own in
  the bundled compose file). A store at or under the application's data or
  uploads directory (``data/``, ``UPLOADS_DIR``; by real path), which the
  application's user owns, is refused, and so is one below a directory,
  from its mount down, that is not root's or that others can write to.
- A key that is in neither place is generated only on a first start, that
  is while the database has no schema yet, or when the operator says so
  (``FIM_ALLOW_NEW_KEYS=true``). On an instance that already has data the
  container refuses to start and says which key to supply.
- A key is generated only into a key store on a mount of its own (a volume
  or a bind mount, at the store or at a directory above it; not tmpfs): one
  in the container's own filesystem is gone when the container is recreated.
  ``FIM_ALLOW_UNMOUNTED_KEY_STORE=true`` allows it anyway.
- A key set in the environment that differs from the stored one is used,
  with a warning: what was encrypted with the stored one cannot be read.
- Nothing here ever deletes, moves or overwrites key material. A key store
  that does not look as it should (not root's, writable by others, a
  symlink) stops the start with a message; the operator decides.
- A new key is written and synced to disk before it is handed out.

Run by ``docker-entrypoint.sh`` as root, before privileges are dropped:

``load``      print ``export NAME='value'`` for stored keys the environment
              lacks, and ``FIM_MISSING_KEYS='A B'`` for those nobody has.
``generate``  generate the missing keys, persist them, print the exports.
``db-state``  print ``empty`` or ``existing`` (run as the application's user). A database
              that cannot be reached yet is asked again a few times, with a short
              connect timeout; when it still cannot, the command fails (no key is
              generated on an unknown state).

Standard library only, except ``db-state`` (SQLAlchemy, from the image).
"""

from __future__ import annotations

import contextlib
import os
import re
import secrets
import shlex
import stat
import sys
import time
from collections.abc import Mapping

__all__ = ["KEY_NAMES", "KeyStoreError", "differing", "generate", "load", "main"]

KEY_NAMES = ("JWT_SECRET_KEY", "CREDENTIAL_ENCRYPTION_KEY")
DEFAULT_DIR = "/var/lib/fim-one/secrets"
FILE_NAME = "keys.env"
_LINE = re.compile(r"([A-Z_]+)=([0-9a-f]{64})")
ALLOW_UNMOUNTED = "FIM_ALLOW_UNMOUNTED_KEY_STORE"
# db-state: how many times, how long each connect may take, the pause between.
_DB_ATTEMPTS = 5
_DB_CONNECT_TIMEOUT = 5.0
_DB_PAUSE = 3.0


class KeyStoreError(Exception):
    """The key store cannot be used as it is; nothing was changed."""


def _directory() -> str:
    return os.environ.get("FIM_SECRETS_DIR") or DEFAULT_DIR


_MOUNTINFO = "/proc/self/mountinfo"
# Another mount table to read instead (only read, never written): for tests.
# Whoever sets the container's environment can mount volumes anyway.
_MOUNTINFO_ENV = "FIM_MOUNTINFO_PATH"
# Mounted, but held in memory: gone with the container like its own filesystem.
_MEMORY_FILESYSTEMS = frozenset({"tmpfs", "ramfs"})


def _mount_types() -> dict[str, str] | None:
    """Mount point -> filesystem type, from the kernel's table; ``None`` where there is none."""
    try:
        path = os.environ.get(_MOUNTINFO_ENV) or _MOUNTINFO
        with open(path, encoding="utf-8", errors="surrogateescape") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return None
    found: dict[str, str] = {}
    for line in lines:
        fields, _, rest = line.partition(" - ")
        parts = fields.split(" ")
        if len(parts) < 5 or not rest:
            continue
        # Spaces and the like in a path are written as octal escapes (\040).
        point = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), parts[4])
        found[point] = rest.split(" ", 1)[0]  # a later mount on the same point hides the earlier
    return found


def _mount_point(directory: str) -> str | None:
    """The mount that *directory* lives on, when it is a volume or a bind mount.

    The nearest mount point at or above the directory's real path, short of
    ``/``: a key store nested in a mounted persistent directory counts. ``/``
    itself is the container's own filesystem and does not. Where the kernel's
    mount table can be read, a mount held in memory (tmpfs, ramfs) does not
    count either; without it (not Linux) every mount point does.
    """
    types = _mount_types()
    path = os.path.realpath(directory)
    while path != os.path.dirname(path):  # stops at "/", which is not checked
        if types is not None:
            if path in types:
                return path if types[path] not in _MEMORY_FILESYSTEMS else None
        elif os.path.ismount(path):
            return path
        path = os.path.dirname(path)
    return None


def _is_mount_point(directory: str) -> bool:
    """Whether *directory* lives on a mount of its own (see ``_mount_point``)."""
    return _mount_point(directory) is not None


def _app_directories(env: Mapping[str, str]) -> list[str]:
    """The real paths of the directories the application's user owns: data and uploads."""
    return [os.path.realpath(path) for path in ("data", env.get("UPLOADS_DIR") or "uploads")]


def _check_location(directory: str, env: Mapping[str, str]) -> None:
    """Raise when the key store sits where the application's user could replace it.

    Not at or under the application's data or uploads directory (by real
    path, so a symlink does not hide it), and every directory from the mount
    it lives on down to it belongs to this user (root) and is not writable
    by group or others. Without a mount the store is in the image's own
    directories, which are root's.
    """
    real = os.path.realpath(directory)
    for owned in _app_directories(env):
        if real == owned or real.startswith(owned.rstrip(os.sep) + os.sep):
            raise KeyStoreError(
                f"{directory} is inside {owned}, which the application's user owns and "
                "could replace. It is left as it is: keep the key store outside the data "
                "and uploads directories (the default is /var/lib/fim-one/secrets)."
            )
    mount = _mount_point(directory)
    if mount is None:
        return
    # The store itself is checked by ``_check_directory``; these are above it.
    above: list[str] = []
    relative = os.path.relpath(real, mount)
    if relative != os.curdir:
        path = mount
        above.append(path)
        for part in relative.split(os.sep)[:-1]:
            path = os.path.join(path, part)
            above.append(path)
    for path in above:
        try:
            info = os.stat(path)
        except FileNotFoundError:
            return  # created below, by this user
        if not stat.S_ISDIR(info.st_mode) or not _owner_ok(info) or info.st_mode & 0o022:
            raise KeyStoreError(
                f"{path}, on the way from the mount {mount} to {directory}, does not "
                "belong to the user this script runs as, or others can write to it, so "
                "the key store could be replaced from there. It is left as it is: make "
                "every directory from the mount down root's and not writable by others "
                "(chown root:root, chmod go-w), or point FIM_SECRETS_DIR elsewhere."
            )


def _owner_ok(info: os.stat_result) -> bool:
    return info.st_uid == os.geteuid()


def _check_directory(directory: str, *, create: bool) -> bool:
    """Whether the key store exists and is private to this user. Raises when it is not ours."""
    try:
        info = os.lstat(directory)
    except FileNotFoundError:
        if not create:
            return False
        os.makedirs(directory, mode=0o700)
        return True
    if not stat.S_ISDIR(info.st_mode):
        raise KeyStoreError(f"{directory} is not a directory. It is left as it is.")
    if not _owner_ok(info) or info.st_mode & 0o022:
        raise KeyStoreError(
            f"{directory} does not belong to the user this script runs as, or others can "
            "write to it. It is left as it is: check where it came from, then make it "
            "root's and private (chown root:root, chmod 700) or point FIM_SECRETS_DIR elsewhere."
        )
    if info.st_mode & 0o077:
        os.chmod(directory, 0o700)
    return True


def _read(directory: str) -> dict[str, str]:
    """The keys in the store. Raises when the file is not a private regular file of ours."""
    path = os.path.join(directory, FILE_NAME)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise KeyStoreError(
            f"{path} cannot be read ({exc.strerror}). It is left as it is."
        ) from exc
    with os.fdopen(fd, encoding="utf-8") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or not _owner_ok(info) or info.st_mode & 0o022:
            raise KeyStoreError(
                f"{path} is not a private regular file of the user this script runs as. "
                "It is left as it is."
            )
        stored: dict[str, str] = {}
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            match = _LINE.fullmatch(line)
            if match is None or match.group(1) not in KEY_NAMES:
                raise KeyStoreError(f"{path} has a line that is not a key. It is left as it is.")
            stored[match.group(1)] = match.group(2)
    return stored


def _write(directory: str, stored: dict[str, str]) -> None:
    """Replace the key file atomically; on disk before this returns."""
    path = os.path.join(directory, FILE_NAME)
    temporary = f"{path}.{os.getpid()}.new"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.writelines(f"{name}={value}\n" for name, value in stored.items())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise
    directory_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def load(environ: dict[str, str] | None = None) -> tuple[dict[str, str], list[str]]:
    """Stored keys the environment lacks, and the names nobody has."""
    env = os.environ if environ is None else environ
    directory = env.get("FIM_SECRETS_DIR") or DEFAULT_DIR
    _check_location(directory, env)
    stored = _read(directory) if _check_directory(directory, create=False) else {}
    found = {name: stored[name] for name in KEY_NAMES if not env.get(name) and name in stored}
    missing = [name for name in KEY_NAMES if not env.get(name) and name not in stored]
    return found, missing


def differing(environ: dict[str, str] | None = None) -> list[str]:
    """The keys set in the environment whose stored value is another one. Never raises."""
    env = os.environ if environ is None else environ
    directory = env.get("FIM_SECRETS_DIR") or DEFAULT_DIR
    try:
        _check_location(directory, env)
        stored = _read(directory) if _check_directory(directory, create=False) else {}
    except (KeyStoreError, OSError):
        return []
    return [
        name for name in KEY_NAMES if env.get(name) and name in stored and env[name] != stored[name]
    ]


def generate(environ: dict[str, str] | None = None) -> dict[str, str]:
    """Generate and persist every key that is in neither the environment nor the store."""
    env = os.environ if environ is None else environ
    directory = env.get("FIM_SECRETS_DIR") or DEFAULT_DIR
    if (env.get(ALLOW_UNMOUNTED) or "").strip().lower() != "true" and not _is_mount_point(
        directory
    ):
        missing = " ".join(name for name in KEY_NAMES if not env.get(name))
        raise KeyStoreError(
            f"no key is generated: {directory} is not on a mounted volume (neither it nor "
            "a directory above it is a mount point other than the container's root or a "
            "tmpfs), so a key generated into it would be lost when the container is "
            f"recreated. Missing: {missing}. "
            f"Mount a volume at {directory} (the app_secrets volume in the bundled "
            "docker-compose.yml), or set the keys in the environment (.env). To keep "
            f"generated keys in the container anyway, set {ALLOW_UNMOUNTED}=true."
        )
    _check_location(directory, env)
    _check_directory(directory, create=True)
    stored = _read(directory)
    created = {
        name: secrets.token_hex(32)
        for name in KEY_NAMES
        if not env.get(name) and name not in stored
    }
    if created:
        # Existing entries are carried over unchanged; none is ever replaced.
        _write(directory, {**stored, **created})
    return created


def _has_schema(url: str, timeout: float) -> bool:
    """One look: whether the database at *url* has a schema. Raises when it cannot be asked."""
    import asyncio

    from sqlalchemy import inspect
    from sqlalchemy.ext.asyncio import create_async_engine

    async def _look() -> bool:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as connection:
                return bool(
                    await connection.run_sync(
                        lambda sync: inspect(sync).has_table("alembic_version")
                    )
                )
        finally:
            await engine.dispose()

    async def _bounded() -> bool:
        return await asyncio.wait_for(_look(), timeout)

    return asyncio.run(_bounded())


def _db_state() -> str:
    """``existing`` when the database has a schema (it is not a first start), else ``empty``.

    Asked up to ``_DB_ATTEMPTS`` times; raises the last error when no answer came.
    """
    url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./data/fim_one.db")
    if url.startswith("sqlite"):
        # Looking must not create the file.
        path = url.split("///", 1)[-1].split("?", 1)[0]
        if path and path != ":memory:" and not os.path.exists(path):
            return "empty"
    for attempt in range(1, _DB_ATTEMPTS + 1):
        try:
            return "existing" if _has_schema(url, _DB_CONNECT_TIMEOUT) else "empty"
        except Exception as exc:
            if attempt == _DB_ATTEMPTS:
                raise
            print(
                f"Key store: the database cannot be asked yet ({type(exc).__name__}); "
                f"asking again in {_DB_PAUSE:.0f} s ({attempt}/{_DB_ATTEMPTS})",
                file=sys.stderr,
            )
            time.sleep(_DB_PAUSE)
    raise AssertionError("unreachable")


def _exports(keys: dict[str, str]) -> str:
    return "".join(f"export {name}={shlex.quote(value)}\n" for name, value in keys.items())


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else ""
    try:
        if command == "load":
            found, missing = load()
            for name in differing():
                print(
                    f"WARNING: {name} in the environment differs from the one in "
                    f"{os.path.join(_directory(), FILE_NAME)}; the environment's is used. "
                    "What was encrypted or signed with the stored one cannot be read with it.",
                    file=sys.stderr,
                )
            sys.stdout.write(_exports(found))
            sys.stdout.write(f"FIM_MISSING_KEYS={shlex.quote(' '.join(missing))}\n")
            return 0
        if command == "generate":
            created = generate()
            for name in created:
                print(
                    f"{name} was not set: generated one and stored it in "
                    f"{os.path.join(_directory(), FILE_NAME)} (root only). Back it up.",
                    file=sys.stderr,
                )
            sys.stdout.write(_exports(created))
            return 0
        if command == "db-state":
            print(_db_state())
            return 0
    except KeyStoreError as exc:
        print(f"Key store: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Key store: {command} failed ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 3
    print("usage: container_keys.py load | generate | db-state", file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv))
