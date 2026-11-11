"""The token estimate for a call that ended without the provider's count.

Each sample's reference count was taken once with tiktoken's ``o200k_base``
encoding (GPT-4o family); the test does not need tiktoken. The estimate
should not charge any kind of text much more than the provider would, nor
much less: within 0.8x-1.5x on every sample.
"""

# ruff: noqa: RUF001

from __future__ import annotations

import base64
import hashlib
import json

import pytest

from fim_one.core.model.usage import TextTokenCounter, estimate_tokens


def _bytes(seed: str, n: int) -> bytes:
    out = b""
    block = seed.encode()
    while len(out) < n:
        block = hashlib.sha256(block).digest()
        out += block
    return out[:n]


_ALPHA = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
_CODE = '''
class RateLimiter:
    """Allow at most ``limit`` calls per ``window`` seconds for each key."""

    def __init__(self, limit: int, window: float = 60.0) -> None:
        self._limit = limit
        self._window = window
        self._calls: dict[str, list[float]] = {}

    def allow(self, key: str, now: float) -> bool:
        recent = [t for t in self._calls.get(key, []) if now - t < self._window]
        if len(recent) >= self._limit:
            self._calls[key] = recent
            return False
        recent.append(now)
        self._calls[key] = recent
        return True


async def fetch_all(client, urls: list[str], *, retries: int = 3) -> dict[str, int]:
    results: dict[str, int] = {}
    for url in urls:
        for attempt in range(1, retries + 1):
            try:
                response = await client.get(url, timeout=10)
                results[url] = response.status_code
                break
            except TimeoutError:
                if attempt == retries:
                    results[url] = -1
    return results
'''
_RECORDS = [
    {"id": n, "name": f"Acme {n}", "owner": "li.wei", "stage": "negotiation",
     "amount": n * 1000.5, "created_at": f"2026-10-{1 + n % 28:02d}T08:{n % 60:02d}:00Z",
     "active": n % 2 == 0}
    for n in range(300)
]  # fmt: skip
SAMPLES: dict[str, str] = {
    "English prose": (
        "The committee reviewed the proposal carefully and concluded that the timeline was"
        " realistic, provided that the supplier could deliver the remaining components before"
        " the end of the quarter. "
    )
    * 30,
    "Chinese prose": (
        "本项目采用分层架构设计，前端基于组件化框架实现，后端提供统一的接口服务，"
        "并通过消息队列完成各模块之间的异步解耦，保证系统在高并发场景下的稳定运行。"
    )
    * 30,
    "Python source": _CODE * 10,
    "compact JSON": json.dumps(_RECORDS),
    "indented JSON": json.dumps(_RECORDS, indent=2),
    "Russian": (
        "Комитет внимательно рассмотрел предложение и пришёл к выводу, что сроки реалистичны"
        " при условии своевременной поставки комплектующих. "
    )
    * 30,
    "Arabic": (
        "راجعت اللجنة المقترح بعناية وخلصت إلى أن الجدول الزمني واقعي بشرط أن يسلم المورد"
        " المكونات المتبقية. "
    )
    * 30,
    "Greek": (
        "Η επιτροπή εξέτασε προσεκτικά την πρόταση και κατέληξε ότι το χρονοδιάγραμμα"
        " είναι ρεαλιστικό. "
    )
    * 30,
    "Hebrew": (
        "הוועדה בחנה בקפידה את ההצעה והגיעה למסקנה שלוח הזמנים מציאותי,"
        " בתנאי שהספק יספק את הרכיבים בזמן. "
    )
    * 30,
    "Thai": "คณะกรรมการได้พิจารณาข้อเสนออย่างรอบคอบและสรุปว่ากำหนดเวลานั้นเป็นไปได้จริง " * 30,
    "Hindi": "समिति ने प्रस्ताव की सावधानीपूर्वक समीक्षा की और निष्कर्ष निकाला कि समय-सीमा यथार्थवादी है। "
    * 30,
    "emoji": "🚀🔥✅🙂👍🏽🇨🇳" * 300,
    "whitespace": " \n\t \n" * 2000,
    "base64": base64.b64encode(_bytes("base64", 15000)).decode(),
    "hex": _bytes("hex", 10000).hex(),
    "random identifiers": " ".join(
        "".join(_ALPHA[b % 52] for b in _bytes(f"id{i}", 24)) for i in range(500)
    ),
    "digit run": "".join(str(b % 10) for b in _bytes("digits", 20000)),
    "markdown table": "\n".join(
        f"| item {i} | {(i * 37 % 10000) / 100:.2f} | 2026-11-{1 + i % 28:02d} | ok |"
        for i in range(400)
    ),
    "YAML": "\n".join(" " * (2 * (i % 12)) + f"key_{i}: value_{i}" for i in range(800)),
}

# ``len(tiktoken.get_encoding("o200k_base").encode(SAMPLES[name]))``
REFERENCE = {
    "English prose": 901,
    "Chinese prose": 1621,
    "Python source": 2591,
    "compact JSON": 19200,
    "indented JSON": 22501,
    "Russian": 901,
    "Arabic": 960,
    "Greek": 991,
    "Hebrew": 1171,
    "Thai": 991,
    "Hindi": 812,
    "emoji": 3600,
    "whitespace": 4000,
    "base64": 13658,
    "hex": 11397,
    "random identifiers": 7432,
    "digit run": 6667,
    "markdown table": 8000,
    "YAML": 7132,
}


@pytest.mark.parametrize("name", list(SAMPLES))
def test_it_comes_within_reach_of_the_real_count(name: str) -> None:
    ratio = estimate_tokens(SAMPLES[name]) / REFERENCE[name]

    assert 0.8 <= ratio <= 1.5, f"{name}: {ratio:.2f}x"


@pytest.mark.parametrize("name", ["English prose", "Python source", "random identifiers", "hex"])
def test_text_counted_in_pieces_costs_what_it_does_whole(name: str) -> None:
    """A stream adds its text a few characters at a time."""
    text = SAMPLES[name]
    counter = TextTokenCounter()
    for start in range(0, len(text), 3):
        counter.add(text[start : start + 3])

    assert counter.tokens() == estimate_tokens(text)


def test_whitespace_is_not_free() -> None:
    assert estimate_tokens("\n" * 100) >= 100
    # The single space between two words is part of the next word's token.
    assert estimate_tokens("one two") == estimate_tokens("onetwo")
