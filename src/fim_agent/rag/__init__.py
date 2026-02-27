"""RAG (Retrieval-Augmented Generation) interface."""

from .base import BaseRetriever, Document
from .manager import KnowledgeBaseManager

__all__ = [
    "BaseRetriever",
    "Document",
    "KnowledgeBaseManager",
]
