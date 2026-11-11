"""A one-time code that is still valid when the server checks it.

The server verifies a code against the 30-second step it receives it in
(``pyotp.TOTP.verify``, no window). A code taken in the last moment of a
step reaches the server in the next one and is refused, which made tests
that sign in with a code fail now and then.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import pyotp

# Longer than any request in a test takes.
MARGIN_SECONDS = 3.0


def current_code(
    secret: str,
    *,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """The code for *secret*, taken where the step has time left."""
    totp = pyotp.TOTP(secret)
    left = totp.interval - clock() % totp.interval
    if left < MARGIN_SECONDS:
        sleep(left + 0.05)
    return totp.now()
