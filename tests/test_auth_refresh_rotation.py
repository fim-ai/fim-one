"""Regression test: refresh tokens must be unique per issuance.

``/api/auth/refresh`` rotates the refresh token — it signs a new one and
overwrites ``user.refresh_token`` with its hash, which is what makes the old
token stop working. That guarantee rests entirely on the new token differing
from the old one.

``exp`` is only second-precision, so two refresh tokens signed for the same user
inside the same second used to carry an identical payload and encode to the same
bytes. Rotation then became a no-op: the stored hash matched the *old* token
just as well, and a stolen refresh token could be renewed forever. The ``jti``
claim is what breaks the tie.
"""

from __future__ import annotations

import uuid

import jwt

from fim_one.web.auth import ALGORITHM, SECRET_KEY, create_refresh_token, hash_refresh_token


def test_same_second_refresh_tokens_differ() -> None:
    """Back-to-back issuance (same second) must not produce identical tokens."""
    user_id = str(uuid.uuid4())
    tokens = {create_refresh_token(user_id, "u@example.com") for _ in range(20)}
    assert len(tokens) == 20


def test_rotation_invalidates_the_old_token() -> None:
    """The stored hash of the new token must not validate the old one."""
    user_id = str(uuid.uuid4())
    old = create_refresh_token(user_id, "u@example.com")
    new = create_refresh_token(user_id, "u@example.com")

    # This is the comparison /api/auth/refresh performs after rotation.
    assert hash_refresh_token(old) != hash_refresh_token(new)


def test_refresh_token_carries_jti() -> None:
    payload = jwt.decode(
        create_refresh_token(str(uuid.uuid4()), "u@example.com"),
        SECRET_KEY,
        algorithms=[ALGORITHM],
    )
    assert payload["type"] == "refresh"
    assert payload.get("jti")
