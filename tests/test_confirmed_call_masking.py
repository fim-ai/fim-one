"""What an approver is shown of a call's arguments stays readable.

Every env var value used to be replaced wherever it occurred, in any order:
``RETRIES=1`` turned ``1000`` into ``{{env.RETRIES}}000``, and a later
replacement could match inside an earlier placeholder.
"""

from __future__ import annotations

from fim_one.core.assembly.confirmed_call import masked_env


def test_a_long_value_is_masked_wherever_it_occurs() -> None:
    shown = masked_env(
        {"url": "https://api.example.com/?key=sk-0123456789", "n": 3},
        {"API_KEY": "sk-0123456789"},
    )
    assert shown == {"url": "https://api.example.com/?key={{env.API_KEY}}", "n": 3}


def test_a_short_value_is_masked_only_as_a_whole_argument() -> None:
    env = {"RETRIES": "1", "MODE": "e"}
    shown = masked_env(
        {"amount": "1000", "who": "attacker-9911", "retries": "1", "mode": "e", "count": 1},
        env,
    )
    assert shown == {
        "amount": "1000",
        "who": "attacker-9911",
        "retries": "{{env.RETRIES}}",
        "mode": "{{env.MODE}}",
        "count": 1,
    }


def test_a_placeholder_is_never_rewritten_by_another_value() -> None:
    env = {"A": "env.B}}xx", "B": "attacker-9911"}
    shown = masked_env({"who": "attacker-9911", "odd": "env.B}}xx"}, env)
    assert shown == {"who": "{{env.B}}", "odd": "{{env.A}}"}


def test_the_longest_value_wins_where_two_overlap() -> None:
    env = {"SHORT": "secret-token", "LONG": "secret-token-extended"}
    assert masked_env("x secret-token-extended y", env) == "x {{env.LONG}} y"


def test_numbers_booleans_and_none_are_shown_as_they_are() -> None:
    env = {"PORT": "54321234"}  # long enough to be masked in text
    assert masked_env({"port": 54321234, "on": True, "none": None}, env) == {
        "port": 54321234,
        "on": True,
        "none": None,
    }
