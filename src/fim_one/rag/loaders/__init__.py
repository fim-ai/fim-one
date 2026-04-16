"""Document loaders with factory function."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BaseLoader, LoadedDocument
from .csv import CSVLoader
from .docx import DOCXLoader
from .html import HTMLLoader
from .markdown import MarkdownLoader
from .markitdown_loader import MarkItDownLoader
from .pdf import PDFLoader
from .text import TextLoader

if TYPE_CHECKING:
    from fim_one.core.model.openai_compatible import OpenAICompatibleLLM

__all__ = [
    "BaseLoader",
    "CSVLoader",
    "DOCXLoader",
    "HTMLLoader",
    "LoadedDocument",
    "MarkdownLoader",
    "MarkItDownLoader",
    "PDFLoader",
    "TextLoader",
    "loader_for_extension",
]

_EXTENSION_MAP: dict[str, type[BaseLoader]] = {
    ".pdf": MarkItDownLoader,  # MarkItDown's PDF path handles both text-rich and scanned (via markitdown-ocr)
    ".docx": MarkItDownLoader,
    ".xlsx": MarkItDownLoader,
    ".xls": MarkItDownLoader,
    ".pptx": MarkItDownLoader,
    ".msg": MarkItDownLoader,  # Outlook email
    ".epub": MarkItDownLoader,
    ".mp3": MarkItDownLoader,  # audio transcription via markitdown[audio-transcription]
    ".wav": MarkItDownLoader,
    ".m4a": MarkItDownLoader,
    ".md": MarkdownLoader,
    ".markdown": MarkdownLoader,
    ".html": HTMLLoader,
    ".htm": HTMLLoader,
    ".csv": CSVLoader,
    ".txt": TextLoader,
}


def loader_for_extension(
    extension: str,
    *,
    vision_llm: "OpenAICompatibleLLM | None" = None,
) -> BaseLoader:
    """Return the appropriate loader for a file extension.

    Args:
        extension: File extension including the dot (e.g. ".pdf").
        vision_llm: Optional vision-capable LLM threaded through to
            loaders that can use it. Currently only
            :class:`MarkItDownLoader` consumes it (for OCR on embedded
            images and scanned PDFs). Other loaders ignore it.

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
    if loader_cls is MarkItDownLoader:
        # Only MarkItDownLoader accepts vision injection today. Using an
        # explicit branch instead of **kwargs keeps the contract honest —
        # a future loader with the same need can add its own branch.
        return MarkItDownLoader(vision_llm=vision_llm)
    return loader_cls()
