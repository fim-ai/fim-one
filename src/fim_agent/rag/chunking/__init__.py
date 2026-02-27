"""Text chunking strategies with factory function."""

from .base import BaseChunker, Chunk
from .fixed import FixedSizeChunker
from .markdown import MarkdownChunker
from .recursive import RecursiveCharacterChunker
from .semantic import SemanticChunker

__all__ = [
    "BaseChunker",
    "Chunk",
    "FixedSizeChunker",
    "MarkdownChunker",
    "RecursiveCharacterChunker",
    "SemanticChunker",
    "get_chunker",
]


def get_chunker(strategy: str, **kwargs: object) -> BaseChunker:
    """Return a chunker by strategy name.

    Args:
        strategy: One of "fixed", "recursive", "semantic", "markdown".
        **kwargs: Forwarded to the chunker constructor.

    Returns:
        An instantiated chunker.

    Raises:
        ValueError: If the strategy is unknown.
    """
    chunkers: dict[str, type[BaseChunker]] = {
        "fixed": FixedSizeChunker,
        "markdown": MarkdownChunker,
        "recursive": RecursiveCharacterChunker,
        "semantic": SemanticChunker,
    }
    cls = chunkers.get(strategy)
    if cls is None:
        raise ValueError(
            f"Unknown chunking strategy: {strategy}. Available: {list(chunkers)}"
        )
    return cls(**kwargs)  # type: ignore[arg-type]
