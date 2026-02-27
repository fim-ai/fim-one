"""Document loaders with factory function."""

from .base import BaseLoader, LoadedDocument
from .csv import CSVLoader
from .docx import DOCXLoader
from .html import HTMLLoader
from .markdown import MarkdownLoader
from .pdf import PDFLoader
from .text import TextLoader

__all__ = [
    "BaseLoader",
    "CSVLoader",
    "DOCXLoader",
    "HTMLLoader",
    "LoadedDocument",
    "MarkdownLoader",
    "PDFLoader",
    "TextLoader",
    "loader_for_extension",
]

_EXTENSION_MAP: dict[str, type[BaseLoader]] = {
    ".pdf": PDFLoader,
    ".docx": DOCXLoader,
    ".md": MarkdownLoader,
    ".markdown": MarkdownLoader,
    ".html": HTMLLoader,
    ".htm": HTMLLoader,
    ".csv": CSVLoader,
    ".txt": TextLoader,
}


def loader_for_extension(extension: str) -> BaseLoader:
    """Return the appropriate loader for a file extension.

    Args:
        extension: File extension including the dot (e.g. ".pdf").

    Returns:
        An instantiated loader.

    Raises:
        ValueError: If the extension is not supported.
    """
    ext = extension.lower()
    loader_cls = _EXTENSION_MAP.get(ext)
    if loader_cls is None:
        supported = ", ".join(sorted(_EXTENSION_MAP.keys()))
        raise ValueError(f"Unsupported file extension: {ext}. Supported: {supported}")
    return loader_cls()
