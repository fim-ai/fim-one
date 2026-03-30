"""Tests for the vision-aware document processing module."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fim_one.core.document.processor import (
    DocumentProcessor,
    DocumentResult,
    _extract_docx_with_images,
    _extract_pptx_with_images,
    _extract_text_sync,
    _extract_with_images_sync,
    _get_doc_processing_mode,
    _get_doc_vision_dpi,
    _get_doc_vision_max_pages,
)


# ---------------------------------------------------------------------------
# DocumentResult dataclass
# ---------------------------------------------------------------------------


class TestDocumentResult:
    """Tests for the DocumentResult dataclass."""

    def test_defaults(self) -> None:
        result = DocumentResult(text="hello")
        assert result.text == "hello"
        assert result.page_images == []
        assert result.mode_used == "text"
        assert result.page_count == 0

    def test_vision_result(self) -> None:
        result = DocumentResult(
            text="content",
            page_images=["data:image/png;base64,abc"],
            mode_used="vision",
            page_count=1,
        )
        assert result.mode_used == "vision"
        assert len(result.page_images) == 1
        assert result.page_count == 1

    def test_none_text(self) -> None:
        result = DocumentResult(text=None)
        assert result.text is None


# ---------------------------------------------------------------------------
# Environment configuration helpers
# ---------------------------------------------------------------------------


class TestEnvConfig:
    """Tests for environment variable configuration helpers."""

    def test_default_mode(self) -> None:
        assert _get_doc_processing_mode() == "auto"

    def test_custom_mode(self) -> None:
        with patch.dict("os.environ", {"DOCUMENT_PROCESSING_MODE": "vision"}):
            assert _get_doc_processing_mode() == "vision"

    def test_default_dpi(self) -> None:
        assert _get_doc_vision_dpi() == 150

    def test_custom_dpi(self) -> None:
        with patch.dict("os.environ", {"DOCUMENT_VISION_DPI": "300"}):
            assert _get_doc_vision_dpi() == 300

    def test_default_max_pages(self) -> None:
        assert _get_doc_vision_max_pages() == 20

    def test_custom_max_pages(self) -> None:
        with patch.dict("os.environ", {"DOCUMENT_VISION_MAX_PAGES": "10"}):
            assert _get_doc_vision_max_pages() == 10


# ---------------------------------------------------------------------------
# _extract_text_sync
# ---------------------------------------------------------------------------


class TestExtractTextSync:
    """Tests for the synchronous text extraction function."""

    def test_plain_text(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("Hello world", encoding="utf-8")
        assert _extract_text_sync(f) == "Hello world"

    def test_markdown(self, tmp_path: Path) -> None:
        f = tmp_path / "readme.md"
        f.write_text("# Title", encoding="utf-8")
        assert _extract_text_sync(f) == "# Title"

    def test_json_pretty_prints(self, tmp_path: Path) -> None:
        f = tmp_path / "data.json"
        f.write_text('{"key":"value"}', encoding="utf-8")
        result = _extract_text_sync(f)
        assert result is not None
        assert json.loads(result) == {"key": "value"}
        assert "\n" in result  # pretty-printed

    def test_json_malformed_returns_raw(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.json"
        f.write_text("{broken", encoding="utf-8")
        assert _extract_text_sync(f) == "{broken"

    def test_csv(self, tmp_path: Path) -> None:
        f = tmp_path / "data.csv"
        f.write_text("a,b\n1,2\n", encoding="utf-8")
        assert _extract_text_sync(f) == "a,b\n1,2\n"

    def test_image_returns_none(self, tmp_path: Path) -> None:
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"\xff\xd8\xff\xe0")
        assert _extract_text_sync(f) is None

    def test_png_returns_none(self, tmp_path: Path) -> None:
        f = tmp_path / "img.png"
        f.write_bytes(b"\x89PNG")
        assert _extract_text_sync(f) is None

    def test_unsupported_extension_returns_none(self, tmp_path: Path) -> None:
        f = tmp_path / "binary.dat"
        f.write_bytes(b"\x00\x01\x02")
        assert _extract_text_sync(f) is None

    def test_pdf_with_pdfplumber(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4")

        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Page 1 text"
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)

        with patch.dict("sys.modules", {"pdfplumber": MagicMock()}):
            import sys

            sys.modules["pdfplumber"].open.return_value = mock_pdf
            result = _extract_text_sync(f)
            assert result == "Page 1 text"

    def test_pdf_without_pdfplumber(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4")

        with patch.dict("sys.modules", {"pdfplumber": None}):
            result = _extract_text_sync(f)
            assert result is not None
            assert "pdfplumber" in result

    def test_docx_with_markitdown(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.docx"
        f.write_bytes(b"PK")

        mock_result = MagicMock()
        mock_result.text_content = "Document content"
        mock_converter = MagicMock()
        mock_converter.convert.return_value = mock_result

        mock_module = MagicMock()
        mock_module.MarkItDown.return_value = mock_converter

        with patch.dict("sys.modules", {"markitdown": mock_module}):
            result = _extract_text_sync(f)
            assert result == "Document content"

    def test_docx_without_markitdown(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.docx"
        f.write_bytes(b"PK")

        with patch.dict("sys.modules", {"markitdown": None}):
            result = _extract_text_sync(f)
            assert result is not None
            assert "markitdown" in result

    def test_html_extraction(self, tmp_path: Path) -> None:
        f = tmp_path / "page.html"
        f.write_text("<html><body>Hello</body></html>", encoding="utf-8")
        result = _extract_text_sync(f)
        assert result is not None
        assert "Hello" in result

    def test_python_extraction(self, tmp_path: Path) -> None:
        f = tmp_path / "script.py"
        f.write_text("print('hello')", encoding="utf-8")
        assert _extract_text_sync(f) == "print('hello')"


# ---------------------------------------------------------------------------
# DocumentProcessor.extract_text (async wrapper)
# ---------------------------------------------------------------------------


class TestExtractTextAsync:
    """Tests for the async extract_text method."""

    @pytest.mark.asyncio
    async def test_delegates_to_sync(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("async test", encoding="utf-8")
        result = await DocumentProcessor.extract_text(f)
        assert result == "async test"


# ---------------------------------------------------------------------------
# DocumentProcessor.render_pdf_pages
# ---------------------------------------------------------------------------


class TestRenderPdfPages:
    """Tests for PDF page rendering using PyMuPDF."""

    @pytest.mark.asyncio
    async def test_renders_pages(self) -> None:
        fake_png = b"\x89PNG\r\n\x1a\nfake_image_data"

        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = fake_png

        mock_page = MagicMock()
        mock_page.get_pixmap.return_value = mock_pix

        mock_doc = MagicMock()
        mock_doc.__iter__ = MagicMock(return_value=iter([mock_page, mock_page]))
        mock_doc.close = MagicMock()

        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc
        mock_fitz.Matrix.return_value = MagicMock()

        with patch.dict("sys.modules", {"fitz": mock_fitz}):
            result = await DocumentProcessor.render_pdf_pages(
                Path("/fake/doc.pdf"), dpi=150, max_pages=20
            )
            assert len(result) == 2
            assert result[0] == fake_png

    @pytest.mark.asyncio
    async def test_respects_max_pages(self) -> None:
        fake_png = b"PNG"

        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = fake_png

        pages = []
        for _ in range(5):
            p = MagicMock()
            p.get_pixmap.return_value = mock_pix
            pages.append(p)

        mock_doc = MagicMock()
        mock_doc.__iter__ = MagicMock(return_value=iter(pages))
        mock_doc.close = MagicMock()

        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc
        mock_fitz.Matrix.return_value = MagicMock()

        with patch.dict("sys.modules", {"fitz": mock_fitz}):
            result = await DocumentProcessor.render_pdf_pages(
                Path("/fake/doc.pdf"), dpi=150, max_pages=2
            )
            assert len(result) == 2

    @pytest.mark.asyncio
    async def test_import_error_propagates(self) -> None:
        """When fitz is not installed, ImportError should propagate."""
        with patch.dict("sys.modules", {"fitz": None}):
            # The import inside the thread will fail
            with pytest.raises(Exception):
                await DocumentProcessor.render_pdf_pages(
                    Path("/fake/doc.pdf"), dpi=150, max_pages=20
                )


# ---------------------------------------------------------------------------
# DocumentProcessor.process_document
# ---------------------------------------------------------------------------


class TestProcessDocument:
    """Tests for the main process_document method."""

    @pytest.mark.asyncio
    async def test_text_mode_for_non_pdf(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("hello", encoding="utf-8")

        result = await DocumentProcessor.process_document(
            f, mode="auto", supports_vision=True
        )
        assert result.mode_used == "text"
        assert result.text == "hello"
        assert result.page_images == []

    @pytest.mark.asyncio
    async def test_text_mode_explicit(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4")

        with patch.object(
            DocumentProcessor, "extract_text", return_value="PDF text"
        ):
            result = await DocumentProcessor.process_document(
                f, mode="text", supports_vision=True
            )
            assert result.mode_used == "text"
            assert result.text == "PDF text"
            assert result.page_images == []

    @pytest.mark.asyncio
    async def test_vision_mode_for_pdf(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4")

        fake_png = b"\x89PNGfake"
        b64_png = base64.b64encode(fake_png).decode("ascii")

        with (
            patch.object(
                DocumentProcessor, "extract_text", return_value="PDF text"
            ),
            patch.object(
                DocumentProcessor,
                "render_pdf_pages",
                return_value=[fake_png, fake_png],
            ),
        ):
            result = await DocumentProcessor.process_document(
                f, mode="auto", supports_vision=True, max_pages=20, dpi=150
            )
            assert result.mode_used == "vision"
            assert result.text == "PDF text"
            assert len(result.page_images) == 2
            assert result.page_count == 2
            assert f"data:image/png;base64,{b64_png}" in result.page_images[0]

    @pytest.mark.asyncio
    async def test_auto_mode_no_vision_support(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4")

        with patch.object(
            DocumentProcessor, "extract_text", return_value="PDF text"
        ):
            result = await DocumentProcessor.process_document(
                f, mode="auto", supports_vision=False
            )
            assert result.mode_used == "text"

    @pytest.mark.asyncio
    async def test_vision_fallback_on_render_error(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4")

        with (
            patch.object(
                DocumentProcessor, "extract_text", return_value="PDF text"
            ),
            patch.object(
                DocumentProcessor,
                "render_pdf_pages",
                side_effect=RuntimeError("render failed"),
            ),
        ):
            result = await DocumentProcessor.process_document(
                f, mode="vision", supports_vision=True
            )
            assert result.mode_used == "text"
            assert result.text == "PDF text"

    @pytest.mark.asyncio
    async def test_vision_fallback_on_import_error(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4")

        with (
            patch.object(
                DocumentProcessor, "extract_text", return_value="PDF text"
            ),
            patch.object(
                DocumentProcessor,
                "render_pdf_pages",
                side_effect=ImportError("No fitz"),
            ),
        ):
            result = await DocumentProcessor.process_document(
                f, mode="vision", supports_vision=True
            )
            assert result.mode_used == "text"

    @pytest.mark.asyncio
    async def test_vision_mode_non_pdf_falls_to_text(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.docx"
        f.write_bytes(b"PK")

        with patch.object(
            DocumentProcessor, "extract_text", return_value="docx text"
        ):
            result = await DocumentProcessor.process_document(
                f, mode="vision", supports_vision=True
            )
            # Vision mode only applies to PDFs; others fall back to text
            assert result.mode_used == "text"

    @pytest.mark.asyncio
    async def test_env_defaults_used(self, tmp_path: Path) -> None:
        """When max_pages and dpi are None, env defaults are used."""
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4")

        with (
            patch.object(
                DocumentProcessor, "extract_text", return_value="text"
            ),
            patch.object(
                DocumentProcessor, "render_pdf_pages", return_value=[b"PNG"]
            ) as mock_render,
            patch(
                "fim_one.core.document.processor._get_doc_vision_dpi",
                return_value=200,
            ),
            patch(
                "fim_one.core.document.processor._get_doc_vision_max_pages",
                return_value=5,
            ),
        ):
            await DocumentProcessor.process_document(
                f, mode="vision", supports_vision=True
            )
            mock_render.assert_called_once_with(f, 200, 5)


# ---------------------------------------------------------------------------
# DocumentProcessor.get_or_create_cached_pages
# ---------------------------------------------------------------------------


class TestCachedPages:
    """Tests for the page caching mechanism."""

    @pytest.mark.asyncio
    async def test_creates_cache(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4")

        fake_png = b"\x89PNGdata"
        with patch.object(
            DocumentProcessor,
            "render_pdf_pages",
            return_value=[fake_png],
        ):
            urls = await DocumentProcessor.get_or_create_cached_pages(
                f, dpi=150, max_pages=20
            )
            assert len(urls) == 1
            assert urls[0].startswith("data:image/png;base64,")

            # Verify cache files were created
            pages_dir = tmp_path / ".pages" / "doc"
            assert pages_dir.exists()
            cached = list(pages_dir.glob("page_*.png"))
            assert len(cached) == 1

    @pytest.mark.asyncio
    async def test_uses_existing_cache(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4")

        # Pre-create cache
        pages_dir = tmp_path / ".pages" / "doc"
        pages_dir.mkdir(parents=True)
        cached_png = b"\x89PNGcached"
        (pages_dir / "page_0000.png").write_bytes(cached_png)

        with patch.object(
            DocumentProcessor, "render_pdf_pages"
        ) as mock_render:
            urls = await DocumentProcessor.get_or_create_cached_pages(
                f, dpi=150, max_pages=20
            )
            # Should NOT call render since cache exists
            mock_render.assert_not_called()
            assert len(urls) == 1
            b64 = base64.b64encode(cached_png).decode("ascii")
            assert urls[0] == f"data:image/png;base64,{b64}"

    @pytest.mark.asyncio
    async def test_cache_respects_max_pages(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4")

        # Pre-create cache with 5 pages
        pages_dir = tmp_path / ".pages" / "doc"
        pages_dir.mkdir(parents=True)
        for i in range(5):
            (pages_dir / f"page_{i:04d}.png").write_bytes(b"PNG")

        urls = await DocumentProcessor.get_or_create_cached_pages(
            f, dpi=150, max_pages=3
        )
        assert len(urls) == 3

    @pytest.mark.asyncio
    async def test_render_failure_returns_empty(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4")

        with patch.object(
            DocumentProcessor,
            "render_pdf_pages",
            side_effect=RuntimeError("render failed"),
        ):
            urls = await DocumentProcessor.get_or_create_cached_pages(
                f, dpi=150, max_pages=20
            )
            assert urls == []

    @pytest.mark.asyncio
    async def test_import_error_returns_empty(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4")

        with patch.object(
            DocumentProcessor,
            "render_pdf_pages",
            side_effect=ImportError("No fitz"),
        ):
            urls = await DocumentProcessor.get_or_create_cached_pages(
                f, dpi=150, max_pages=20
            )
            assert urls == []


# ---------------------------------------------------------------------------
# Files.py delegation
# ---------------------------------------------------------------------------


class TestFilesExtractContent:
    """Test that files.py _extract_content delegates to DocumentProcessor."""

    def test_delegates_to_processor(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("delegated", encoding="utf-8")

        from fim_one.web.api.files import _extract_content

        result = _extract_content(f)
        assert result == "delegated"


# ---------------------------------------------------------------------------
# _extract_docx_with_images
# ---------------------------------------------------------------------------


class TestExtractDocxWithImages:
    """Tests for DOCX image extraction with positional markers."""

    def _make_docx_mocks(self) -> tuple[MagicMock, MagicMock, MagicMock]:
        """Build mock docx module, Document class, Paragraph class, Table class."""
        mock_docx_mod = MagicMock()
        return mock_docx_mod, mock_docx_mod.Document, mock_docx_mod.text.paragraph.Paragraph

    def test_extracts_text_and_images(self) -> None:
        """Paragraphs with embedded images produce [Figure N] markers."""
        fake_image_bytes = b"\x89PNGfake_docx_image"

        # Build a mock python-docx Document
        mock_blip = MagicMock()
        mock_blip.get.return_value = "rId1"

        mock_target_part = MagicMock()
        mock_target_part.blob = fake_image_bytes
        mock_rel = MagicMock()
        mock_rel.target_part = mock_target_part

        # Paragraph element with blip
        mock_para_element = MagicMock()
        mock_para_element.tag = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"
        mock_para_element.findall.return_value = [mock_blip]

        # Plain paragraph element (no images)
        mock_plain_element = MagicMock()
        mock_plain_element.tag = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"
        mock_plain_element.findall.return_value = []

        mock_doc = MagicMock()
        mock_doc.element.body = [mock_para_element, mock_plain_element]
        mock_doc.part.rels = {"rId1": mock_rel}

        # Mock Paragraph to return controlled text
        call_count = [0]
        def make_para(el: Any, doc: Any) -> MagicMock:
            call_count[0] += 1
            p = MagicMock()
            p.text = MagicMock()
            p.text.strip = MagicMock(return_value=f"Paragraph {call_count[0]}" if call_count[0] > 1 else "")
            return p

        mock_docx_mod = MagicMock()
        mock_docx_mod.Document.return_value = mock_doc
        mock_para_mod = MagicMock()
        mock_para_mod.Paragraph.side_effect = make_para
        mock_table_mod = MagicMock()

        with patch.dict("sys.modules", {
            "docx": mock_docx_mod,
            "docx.table": mock_table_mod,
            "docx.text": MagicMock(),
            "docx.text.paragraph": mock_para_mod,
        }):
            text, images = _extract_docx_with_images(Path("/fake/doc.docx"))

        assert len(images) == 1
        assert images[0] == fake_image_bytes
        assert "[Figure 1]" in text

    def test_no_images_returns_text_only(self) -> None:
        """DOCX without images returns text with empty image list."""
        mock_element = MagicMock()
        mock_element.tag = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"
        mock_element.findall.return_value = []

        mock_doc = MagicMock()
        mock_doc.element.body = [mock_element]
        mock_doc.part.rels = {}

        para = MagicMock()
        para.text = MagicMock()
        para.text.strip = MagicMock(return_value="Hello world")

        mock_docx_mod = MagicMock()
        mock_docx_mod.Document.return_value = mock_doc
        mock_para_mod = MagicMock()
        mock_para_mod.Paragraph.return_value = para
        mock_table_mod = MagicMock()

        with patch.dict("sys.modules", {
            "docx": mock_docx_mod,
            "docx.table": mock_table_mod,
            "docx.text": MagicMock(),
            "docx.text.paragraph": mock_para_mod,
        }):
            text, images = _extract_docx_with_images(Path("/fake/doc.docx"))

        assert images == []
        assert "Hello world" in text

    def test_table_extraction(self) -> None:
        """Tables are extracted as pipe-delimited rows."""
        mock_tbl_element = MagicMock()
        mock_tbl_element.tag = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tbl"

        mock_cell1 = MagicMock()
        mock_cell1.text = MagicMock()
        mock_cell1.text.strip = MagicMock(return_value="A")
        mock_cell2 = MagicMock()
        mock_cell2.text = MagicMock()
        mock_cell2.text.strip = MagicMock(return_value="B")
        mock_row = MagicMock()
        mock_row.cells = [mock_cell1, mock_cell2]

        tbl = MagicMock()
        tbl.rows = [mock_row]

        mock_doc = MagicMock()
        mock_doc.element.body = [mock_tbl_element]

        mock_docx_mod = MagicMock()
        mock_docx_mod.Document.return_value = mock_doc
        mock_para_mod = MagicMock()
        mock_table_mod = MagicMock()
        mock_table_mod.Table.return_value = tbl

        with patch.dict("sys.modules", {
            "docx": mock_docx_mod,
            "docx.table": mock_table_mod,
            "docx.text": MagicMock(),
            "docx.text.paragraph": mock_para_mod,
        }):
            text, images = _extract_docx_with_images(Path("/fake/doc.docx"))

        assert images == []
        assert "A | B" in text


# ---------------------------------------------------------------------------
# _extract_pptx_with_images
# ---------------------------------------------------------------------------


class TestExtractPptxWithImages:
    """Tests for PPTX image extraction with positional markers."""

    def test_extracts_slides_with_images(self) -> None:
        """Slides with picture shapes produce [Figure N] markers."""
        # Use integer sentinel for PICTURE type — the real enum is
        # MSO_SHAPE_TYPE.PICTURE (13).
        PICTURE = 13
        TEXT_BOX = 17

        fake_image_bytes = b"\xff\xd8\xff\xe0fake_pptx_image"

        mock_text_frame = MagicMock()
        mock_text_frame.text = MagicMock()
        mock_text_frame.text.strip = MagicMock(return_value="Slide title")

        # Text shape
        text_shape = MagicMock()
        text_shape.has_text_frame = True
        text_shape.text_frame = mock_text_frame
        text_shape.shape_type = TEXT_BOX

        # Picture shape
        pic_shape = MagicMock()
        pic_shape.has_text_frame = False
        pic_shape.shape_type = PICTURE
        pic_shape.image.blob = fake_image_bytes

        mock_slide = MagicMock()
        mock_slide.shapes = [text_shape, pic_shape]

        mock_prs = MagicMock()
        mock_prs.slides = [mock_slide]

        # Build mock pptx modules
        mock_pptx_mod = MagicMock()
        mock_pptx_mod.Presentation.return_value = mock_prs
        mock_enum_mod = MagicMock()
        mock_enum_mod.MSO_SHAPE_TYPE.PICTURE = PICTURE
        mock_enum_mod.MSO_SHAPE_TYPE.GROUP = 6

        with patch.dict("sys.modules", {
            "pptx": mock_pptx_mod,
            "pptx.enum": MagicMock(),
            "pptx.enum.shapes": mock_enum_mod,
        }):
            text, images = _extract_pptx_with_images(Path("/fake/slides.pptx"))

        assert len(images) == 1
        assert images[0] == fake_image_bytes
        assert "--- Slide 1 ---" in text
        assert "[Figure 1]" in text
        assert "Slide title" in text

    def test_no_images(self) -> None:
        """PPTX without images returns text with empty image list."""
        mock_text_frame = MagicMock()
        mock_text_frame.text = MagicMock()
        mock_text_frame.text.strip = MagicMock(return_value="Just text")

        text_shape = MagicMock()
        text_shape.has_text_frame = True
        text_shape.text_frame = mock_text_frame
        text_shape.shape_type = 1  # Not PICTURE or GROUP

        mock_slide = MagicMock()
        mock_slide.shapes = [text_shape]

        mock_prs = MagicMock()
        mock_prs.slides = [mock_slide]

        mock_pptx_mod = MagicMock()
        mock_pptx_mod.Presentation.return_value = mock_prs
        mock_enum_mod = MagicMock()
        mock_enum_mod.MSO_SHAPE_TYPE.PICTURE = 13
        mock_enum_mod.MSO_SHAPE_TYPE.GROUP = 6

        with patch.dict("sys.modules", {
            "pptx": mock_pptx_mod,
            "pptx.enum": MagicMock(),
            "pptx.enum.shapes": mock_enum_mod,
        }):
            text, images = _extract_pptx_with_images(Path("/fake/slides.pptx"))

        assert images == []
        assert "Just text" in text

    def test_grouped_shapes_with_images(self) -> None:
        """Grouped shapes with nested images are extracted."""
        GROUP = 6
        fake_image = b"\x89PNGgrouped_image"

        child = MagicMock()
        child.image.blob = fake_image

        group_shape = MagicMock()
        group_shape.has_text_frame = False
        group_shape.shape_type = GROUP
        group_shape.shapes = [child]

        mock_slide = MagicMock()
        mock_slide.shapes = [group_shape]

        mock_prs = MagicMock()
        mock_prs.slides = [mock_slide]

        mock_pptx_mod = MagicMock()
        mock_pptx_mod.Presentation.return_value = mock_prs
        mock_enum_mod = MagicMock()
        mock_enum_mod.MSO_SHAPE_TYPE.PICTURE = 13
        mock_enum_mod.MSO_SHAPE_TYPE.GROUP = GROUP

        with patch.dict("sys.modules", {
            "pptx": mock_pptx_mod,
            "pptx.enum": MagicMock(),
            "pptx.enum.shapes": mock_enum_mod,
        }):
            text, images = _extract_pptx_with_images(Path("/fake/slides.pptx"))

        assert len(images) == 1
        assert "[Figure 1]" in text


# ---------------------------------------------------------------------------
# _extract_with_images_sync
# ---------------------------------------------------------------------------


class TestExtractWithImagesSync:
    """Tests for the unified image extraction dispatcher."""

    def test_docx_dispatches_to_docx_extractor(self) -> None:
        """DOCX files dispatch to _extract_docx_with_images."""
        with patch(
            "fim_one.core.document.processor._extract_docx_with_images",
            return_value=("docx text [Figure 1]", [b"img"]),
        ) as mock_docx:
            text, images = _extract_with_images_sync(Path("/fake/doc.docx"))
            mock_docx.assert_called_once()
            assert text == "docx text [Figure 1]"
            assert images == [b"img"]

    def test_pptx_dispatches_to_pptx_extractor(self) -> None:
        """PPTX files dispatch to _extract_pptx_with_images."""
        with patch(
            "fim_one.core.document.processor._extract_pptx_with_images",
            return_value=("slide text [Figure 1]", [b"img"]),
        ) as mock_pptx:
            text, images = _extract_with_images_sync(Path("/fake/slides.pptx"))
            mock_pptx.assert_called_once()
            assert text == "slide text [Figure 1]"
            assert images == [b"img"]

    def test_doc_dispatches_to_docx_extractor(self) -> None:
        """Legacy .doc files also route to DOCX extractor."""
        with patch(
            "fim_one.core.document.processor._extract_docx_with_images",
            return_value=("doc text", []),
        ) as mock_docx:
            text, images = _extract_with_images_sync(Path("/fake/legacy.doc"))
            mock_docx.assert_called_once()
            assert text == "doc text"

    def test_ppt_dispatches_to_pptx_extractor(self) -> None:
        """Legacy .ppt files also route to PPTX extractor."""
        with patch(
            "fim_one.core.document.processor._extract_pptx_with_images",
            return_value=("ppt text", []),
        ) as mock_pptx:
            text, images = _extract_with_images_sync(Path("/fake/legacy.ppt"))
            mock_pptx.assert_called_once()
            assert text == "ppt text"

    def test_docx_fallback_on_error(self) -> None:
        """DOCX extraction failure falls back to text-only."""
        with (
            patch(
                "fim_one.core.document.processor._extract_docx_with_images",
                side_effect=RuntimeError("docx parse error"),
            ),
            patch(
                "fim_one.core.document.processor._extract_text_sync",
                return_value="fallback text",
            ) as mock_text,
        ):
            text, images = _extract_with_images_sync(Path("/fake/doc.docx"))
            mock_text.assert_called_once()
            assert text == "fallback text"
            assert images == []

    def test_pptx_fallback_on_error(self) -> None:
        """PPTX extraction failure falls back to text-only."""
        with (
            patch(
                "fim_one.core.document.processor._extract_pptx_with_images",
                side_effect=RuntimeError("pptx parse error"),
            ),
            patch(
                "fim_one.core.document.processor._extract_text_sync",
                return_value="fallback text",
            ) as mock_text,
        ):
            text, images = _extract_with_images_sync(Path("/fake/slides.pptx"))
            mock_text.assert_called_once()
            assert text == "fallback text"
            assert images == []

    def test_other_formats_return_text_only(self, tmp_path: Path) -> None:
        """Non-DOCX/PPTX files return text with empty image list."""
        f = tmp_path / "test.txt"
        f.write_text("plain text", encoding="utf-8")
        text, images = _extract_with_images_sync(f)
        assert text == "plain text"
        assert images == []

    def test_pdf_returns_text_only(self, tmp_path: Path) -> None:
        """PDF files use text-only extraction (vision handled separately)."""
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4")

        mock_page = MagicMock()
        mock_page.extract_text.return_value = "PDF text"
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)

        with patch.dict("sys.modules", {"pdfplumber": MagicMock()}):
            import sys
            sys.modules["pdfplumber"].open.return_value = mock_pdf
            text, images = _extract_with_images_sync(f)
            assert text == "PDF text"
            assert images == []


# ---------------------------------------------------------------------------
# DocumentProcessor.extract_with_images (async wrapper)
# ---------------------------------------------------------------------------


class TestExtractWithImagesAsync:
    """Tests for the async extract_with_images method."""

    @pytest.mark.asyncio
    async def test_delegates_to_sync(self) -> None:
        with patch(
            "fim_one.core.document.processor._extract_with_images_sync",
            return_value=("async text", [b"img"]),
        ):
            text, images = await DocumentProcessor.extract_with_images(Path("/fake/doc.docx"))
            assert text == "async text"
            assert images == [b"img"]


# ---------------------------------------------------------------------------
# Allowed extensions (.doc, .ppt additions)
# ---------------------------------------------------------------------------


class TestAllowedExtensions:
    """Test that .doc and .ppt are in the upload whitelist."""

    def test_doc_in_allowed(self) -> None:
        from fim_one.web.api.files import ALLOWED_EXTENSIONS
        assert ".doc" in ALLOWED_EXTENSIONS

    def test_ppt_in_allowed(self) -> None:
        from fim_one.web.api.files import ALLOWED_EXTENSIONS
        assert ".ppt" in ALLOWED_EXTENSIONS

    def test_doc_in_text_extraction(self) -> None:
        """The .doc suffix is handled by markitdown path."""
        f = Path("/fake/legacy.doc")
        mock_result = MagicMock()
        mock_result.text_content = "Legacy doc"
        mock_converter = MagicMock()
        mock_converter.convert.return_value = mock_result
        mock_module = MagicMock()
        mock_module.MarkItDown.return_value = mock_converter

        with patch.dict("sys.modules", {"markitdown": mock_module}):
            result = _extract_text_sync(f)
            assert result == "Legacy doc"

    def test_ppt_in_text_extraction(self) -> None:
        """The .ppt suffix is handled by markitdown path."""
        f = Path("/fake/legacy.ppt")
        mock_result = MagicMock()
        mock_result.text_content = "Legacy ppt"
        mock_converter = MagicMock()
        mock_converter.convert.return_value = mock_result
        mock_module = MagicMock()
        mock_module.MarkItDown.return_value = mock_converter

        with patch.dict("sys.modules", {"markitdown": mock_module}):
            result = _extract_text_sync(f)
            assert result == "Legacy ppt"
