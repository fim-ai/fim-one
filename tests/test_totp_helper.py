"""``tests.totp.current_code`` never hands out a code about to expire."""

from __future__ import annotations

import datetime

import pyotp

from tests.totp import MARGIN_SECONDS, current_code


def test_a_code_taken_at_the_end_of_a_step_is_refused_a_moment_later() -> None:
    """Why the helper exists: no window, so the next step refuses it."""
    totp = pyotp.TOTP(pyotp.random_base32())
    late = datetime.datetime(2026, 11, 11, 12, 0, 29, 900000)
    assert not totp.verify(totp.at(late), for_time=late + datetime.timedelta(seconds=0.2))


def test_near_the_end_of_a_step_it_waits_for_the_next() -> None:
    waited: list[float] = []

    current_code(pyotp.random_base32(), clock=lambda: 1_800_000_029.5, sleep=waited.append)

    assert len(waited) == 1 and 0.5 <= waited[0] < MARGIN_SECONDS + 0.1


def test_with_time_left_it_does_not_wait() -> None:
    waited: list[float] = []

    current_code(pyotp.random_base32(), clock=lambda: 1_800_000_010.0, sleep=waited.append)

    assert waited == []
