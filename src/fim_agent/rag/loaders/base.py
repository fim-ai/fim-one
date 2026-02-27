"""Base loader interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class LoadedDocument:
    """A document loaded from a file.

    Attributes:
        content: The extracted text content.
        metadata: Metadata about the source (filename, page number, etc.).
    """

    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseLoader(ABC):
    """Abstract interface for document loaders."""

    @abstractmethod
    async def load(self, path: Path) -> list[LoadedDocument]:
        """Load a file and return extracted documents.

        Args:
            path: Path to the file to load.

        Returns:
            List of LoadedDocument objects. PDF loaders may return one per page.
        """
        ...
