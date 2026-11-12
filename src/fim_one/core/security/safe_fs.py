"""File access inside a directory that sandboxed code can write to.

With the ``docker`` code-execution backend a conversation's workspace is
mounted into sandbox containers. The application reads, lists and writes
the same directory from the outside (tool outputs, artifacts, rendered
templates, the file tool) and treats its content as untrusted: it never
follows a symlink found there.

Everything here therefore:

- starts from a *root* the sandbox cannot replace (the workspace directory
  itself, whose parent is never mounted; see :func:`anchor_of`);
- walks every further component with ``openat(..., O_NOFOLLOW)``, so a
  symlink anywhere below the root is refused rather than followed;
- decides on the opened file (``fstat`` of the descriptor), never on a path
  checked earlier: there is no window between the check and the use.

Only regular files are read, written or listed as files.

On a platform without ``openat`` semantics (Windows) the same functions
fall back to resolving the path and checking it stays under the root.
"""

from __future__ import annotations

import contextlib
import os
import stat as stat_module
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "Entry",
    "UnsafePathError",
    "anchor_of",
    "copy_to",
    "listdir",
    "lstat",
    "mkdir",
    "read_bytes",
    "read_text",
    "rmtree",
    "unlink",
    "write_bytes",
    "write_text",
]

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
# A FIFO left where a file is expected would block the open for ever.
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_OPENAT = bool(_NOFOLLOW) and os.open in os.supports_dir_fd and os.scandir in os.supports_fd


class UnsafePathError(OSError):
    """The path leaves its root, crosses a symlink, or is not a regular file."""


@dataclass(frozen=True)
class Entry:
    """One directory entry, described without following it."""

    name: str
    is_dir: bool
    is_file: bool
    is_symlink: bool
    size: int
    mtime: float


def anchor_of(directory: Path, *, base: Path | None = None) -> tuple[Path, tuple[str, ...]]:
    """Split *directory* into the root sandboxed code cannot replace, and the rest.

    For ``<sandbox base>/<id>/workspace[/sub/dir]`` the root is the
    workspace: it is what containers mount, so from inside it is the mount
    point (it cannot be renamed or replaced there), and its parent is never
    mounted. Everything below it is the sandbox's to rearrange.

    A directory elsewhere (an artifacts directory, a test's scratch
    directory) is its own root. *base* is the sandbox base when it is not
    the application's own.
    """
    if base is None:
        from fim_one.core.assembly.sandbox import sandbox_base

        base = sandbox_base()
    try:
        relative = directory.relative_to(base)
    except ValueError:
        return directory, ()
    parts = relative.parts
    if len(parts) >= 2 and parts[1] == "workspace":
        return base / parts[0] / "workspace", tuple(parts[2:])
    return directory, ()


def _checked(parts: Sequence[str]) -> tuple[str, ...]:
    for part in parts:
        if not part or part in (".", "..") or "/" in part or "\\" in part or "\x00" in part:
            raise UnsafePathError(f"Not a plain path component: {part!r}")
    return tuple(parts)


def _is_link(name: str, dir_fd: int) -> bool:
    """Whether the entry *name* is a symlink (for the message; the open already refused)."""
    try:
        return stat_module.S_ISLNK(os.lstat(name, dir_fd=dir_fd).st_mode)
    except OSError:
        return False


@contextlib.contextmanager
def _open_dir(root: Path, parts: Sequence[str], *, create: bool = False) -> Iterator[int]:
    """A descriptor of ``root/parts...``, reached without following a symlink below *root*."""
    if create:
        root.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC
    try:
        fd = os.open(root, flags)
    except OSError as exc:
        if root.is_symlink():
            raise UnsafePathError(f"{root} is a symlink") from exc
        raise
    try:
        for part in _checked(parts):
            if create:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(part, 0o755, dir_fd=fd)
            try:
                deeper = os.open(part, flags, dir_fd=fd)
            except OSError as exc:
                if _is_link(part, fd):
                    raise UnsafePathError(f"{part!r} is a symlink") from exc
                raise
            os.close(fd)
            fd = deeper
        yield fd
    finally:
        os.close(fd)


def _open_file(dir_fd: int, name: str, flags: int, mode: int = 0o644) -> int:
    """Open *name* in *dir_fd* without following it; only a regular file comes back."""
    try:
        fd = os.open(name, flags | _NOFOLLOW | _CLOEXEC | _NONBLOCK, mode, dir_fd=dir_fd)
    except OSError as exc:
        if _is_link(name, dir_fd):
            raise UnsafePathError(f"{name!r} is a symlink") from exc
        raise
    if not stat_module.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise UnsafePathError(f"{name!r} is not a regular file")
    return fd


def _fallback_path(root: Path, parts: Sequence[str]) -> Path:
    """Where ``root/parts`` is, on a platform without ``openat``: resolved, inside, not a link."""
    target = root.joinpath(*_checked(parts))
    resolved_root = root.resolve()
    walked = root
    for part in parts:
        walked = walked / part
        if walked.is_symlink():
            raise UnsafePathError(f"{part!r} is a symlink")
    if not target.resolve().is_relative_to(resolved_root):
        raise UnsafePathError(f"{target} leaves {root}")
    return target


def _split(parts: Sequence[str]) -> tuple[tuple[str, ...], str]:
    checked = _checked(parts)
    if not checked:
        raise UnsafePathError("No file name given")
    return checked[:-1], checked[-1]


def read_bytes(root: Path, parts: Sequence[str], *, max_bytes: int | None = None) -> bytes:
    """The content of the regular file ``root/parts``; at most *max_bytes* of it."""
    parents, name = _split(parts)
    if not _OPENAT:
        with _fallback_path(root, parts).open("rb") as handle:
            return handle.read() if max_bytes is None else handle.read(max_bytes)
    with _open_dir(root, parents) as dir_fd:
        fd = _open_file(dir_fd, name, os.O_RDONLY)
    with os.fdopen(fd, "rb") as handle:
        return handle.read() if max_bytes is None else handle.read(max_bytes)


def read_text(root: Path, parts: Sequence[str], *, encoding: str = "utf-8") -> str:
    return read_bytes(root, parts).decode(encoding)


def write_bytes(
    root: Path,
    parts: Sequence[str],
    data: bytes,
    *,
    append: bool = False,
    exclusive: bool = False,
) -> None:
    """Write *data* to the regular file ``root/parts``, creating its directories.

    An existing symlink at that name is refused, not written through.
    """
    parents, name = _split(parts)
    if not _OPENAT:
        target = _fallback_path(root, parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("ab" if append else ("xb" if exclusive else "wb")) as handle:
            handle.write(data)
        return
    flags = os.O_WRONLY | os.O_CREAT
    flags |= os.O_APPEND if append else (os.O_EXCL if exclusive else 0)
    with _open_dir(root, parents, create=True) as dir_fd:
        fd = _open_file(dir_fd, name, flags)
    with os.fdopen(fd, "ab" if append else "wb") as handle:
        if not append:
            # After the open, on the descriptor: O_TRUNC would have emptied
            # whatever the name led to before it was known to be a file.
            handle.truncate(0)
        handle.write(data)


def write_text(
    root: Path,
    parts: Sequence[str],
    text: str,
    *,
    encoding: str = "utf-8",
    append: bool = False,
    exclusive: bool = False,
) -> None:
    write_bytes(root, parts, text.encode(encoding), append=append, exclusive=exclusive)


def lstat(root: Path, parts: Sequence[str]) -> os.stat_result:
    """``lstat`` of ``root/parts`` (the entry itself, never what it points at)."""
    if not parts:
        return os.lstat(root)
    parents, name = _split(parts)
    if not _OPENAT:
        return os.lstat(_fallback_path(root, parts))
    with _open_dir(root, parents) as dir_fd:
        return os.lstat(name, dir_fd=dir_fd)


def listdir(root: Path, parts: Sequence[str] = ()) -> list[Entry]:
    """The entries of the directory ``root/parts``, none of them followed."""

    def describe(scan: Iterator[os.DirEntry[str]]) -> list[Entry]:
        entries: list[Entry] = []
        for entry in scan:
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            entries.append(
                Entry(
                    name=entry.name,
                    is_dir=stat_module.S_ISDIR(info.st_mode),
                    is_file=stat_module.S_ISREG(info.st_mode),
                    is_symlink=stat_module.S_ISLNK(info.st_mode),
                    size=info.st_size,
                    mtime=info.st_mtime,
                )
            )
        return entries

    if not _OPENAT:
        with os.scandir(_fallback_path(root, parts) if parts else root) as scan:
            return describe(scan)
    with _open_dir(root, _checked(parts)) as dir_fd, os.scandir(dir_fd) as scan:
        return describe(scan)


def mkdir(root: Path, parts: Sequence[str]) -> None:
    """Create the directory ``root/parts`` and its parents, crossing no symlink."""
    if not _OPENAT:
        _fallback_path(root, parts).mkdir(parents=True, exist_ok=True)
        return
    with _open_dir(root, _checked(parts), create=True):
        pass


def unlink(root: Path, parts: Sequence[str]) -> None:
    """Remove the entry ``root/parts`` itself (a symlink is removed, not its target)."""
    parents, name = _split(parts)
    if not _OPENAT:
        _fallback_path(root, parents).joinpath(name).unlink()
        return
    with _open_dir(root, parents) as dir_fd:
        os.unlink(name, dir_fd=dir_fd)


def rmtree(root: Path, parts: Sequence[str]) -> None:
    """Remove the directory tree ``root/parts``; a symlink there is refused, none is followed."""
    import shutil

    parents, name = _split(parts)
    if not _OPENAT:
        shutil.rmtree(_fallback_path(root, parts))
        return
    with _open_dir(root, parents) as dir_fd:
        shutil.rmtree(name, dir_fd=dir_fd)


def copy_to(root: Path, parts: Sequence[str], dest: Path, *, max_bytes: int) -> int | None:
    """Copy the regular file ``root/parts`` to the new file *dest*; its size, or ``None``.

    ``None`` when the file is larger than *max_bytes* (nothing is written).
    The size is the opened file's, and no more than that is copied: a file
    swapped or grown after a listing is still bounded.
    """
    parents, name = _split(parts)
    if not _OPENAT:
        source = _fallback_path(root, parts)
        if not source.is_file():
            raise UnsafePathError(f"{name!r} is not a regular file")
        if source.stat().st_size > max_bytes:
            return None
        data = source.read_bytes()[: max_bytes + 1]
    else:
        with _open_dir(root, parents) as dir_fd:
            fd = _open_file(dir_fd, name, os.O_RDONLY)
        with os.fdopen(fd, "rb") as handle:
            if os.fstat(handle.fileno()).st_size > max_bytes:
                return None
            data = handle.read(max_bytes + 1)
    if len(data) > max_bytes:
        return None
    out = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC, 0o644)
    with os.fdopen(out, "wb") as handle:
        handle.write(data)
    return len(data)
