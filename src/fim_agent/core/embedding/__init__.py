"""Embedding model abstractions."""

from .base import BaseEmbedding
from .openai_compatible import OpenAICompatibleEmbedding

__all__ = ["BaseEmbedding", "OpenAICompatibleEmbedding"]
