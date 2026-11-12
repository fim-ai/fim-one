"""The one place that decides what a stored avatar value may be.

``users.avatar`` holds either a built-in avatar token
(``builtin:<icon>[:<color>]``) or the relative name of an image the
application itself wrote for that user (``avatars/<user_id>_<ts>.<ext>``).
Nothing else is a valid value, and no code path turns the column into a
filesystem path except :func:`uploaded_avatar_path`, which rebuilds the path
from the validated file name instead of trusting the stored string.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

AVATAR_ALLOWED_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp"})

_BUILTIN_RE = re.compile(r"builtin:[a-z0-9][a-z0-9_-]{0,31}(?::[a-z0-9][a-z0-9_-]{0,31})?")
# ``<user_id>_<unix ts>.<ext>``; the bare ``<user_id>.<ext>`` form predates
# the timestamp and may still exist on disk.
_UPLOADED_NAME_RE = re.compile(r"(?P<owner>[0-9A-Za-z-]{1,64})(?:_\d{1,20})?(?P<ext>\.[a-z0-9]{1,5})")


def avatars_dir() -> Path:
    """Directory uploaded avatar images live in."""
    return Path(os.environ.get("UPLOADS_DIR", "uploads")) / "avatars"


def is_builtin_avatar(value: object) -> bool:
    """Whether *value* is a built-in avatar token."""
    return isinstance(value, str) and _BUILTIN_RE.fullmatch(value) is not None


def uploaded_avatar_name(user_id: str, value: object) -> str | None:
    """The file name inside :func:`avatars_dir` that *value* denotes.

    Returns ``None`` unless *value* is exactly ``avatars/<name>`` where
    ``<name>`` is a file the upload endpoint would have written for
    *user_id*.
    """
    if not isinstance(value, str) or not value.startswith("avatars/"):
        return None
    name = value[len("avatars/"):]
    match = _UPLOADED_NAME_RE.fullmatch(name)
    if match is None:
        return None
    if match.group("owner") != str(user_id):
        return None
    if match.group("ext") not in AVATAR_ALLOWED_EXTENSIONS:
        return None
    return name


def uploaded_avatar_path(user_id: str, value: object) -> Path | None:
    """Resolve a stored avatar value to *user_id*'s own file, or ``None``.

    The returned path is a regular file directly inside
    :func:`avatars_dir` (not a symlink, not a subdirectory entry).
    """
    name = uploaded_avatar_name(user_id, value)
    if name is None:
        return None
    base = avatars_dir()
    candidate = base / name
    try:
        if candidate.is_symlink() or not candidate.is_file():
            return None
        if candidate.resolve().parent != base.resolve():
            return None
    except OSError:
        return None
    return candidate


def public_avatar_value(user_id: str, value: object) -> str | None:
    """The avatar value to show to clients; invalid stored values read as unset."""
    if is_builtin_avatar(value) or uploaded_avatar_name(user_id, value) is not None:
        return str(value)
    return None


def remove_uploaded_avatars(user_id: str) -> None:
    """Delete every avatar image the application wrote for *user_id*."""
    base = avatars_dir()
    if not base.is_dir():
        return
    try:
        entries = list(base.iterdir())
    except OSError:
        return
    owner = str(user_id)
    if not owner or not re.fullmatch(r"[0-9A-Za-z-]{1,64}", owner):
        return
    for entry in entries:
        # Same reach as the names the upload endpoint has ever produced:
        # ``<id>_<anything>`` and the legacy ``<id>.<ext>``.  ``entry`` comes
        # from listing the directory, so it is always a direct child.
        name = entry.name
        if not (name.startswith(f"{owner}_") or name.startswith(f"{owner}.")):
            continue
        try:
            if entry.is_symlink() or entry.is_file():
                entry.unlink(missing_ok=True)
        except OSError:
            continue
