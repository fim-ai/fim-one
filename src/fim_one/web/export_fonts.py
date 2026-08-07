"""Font resolution for conversation exports (PDF and DOCX).

**PDF.** ReportLab ships the Adobe CID font ``STSong-Light``, which is
*referenced* rather than embedded: the reader substitutes whatever CJK font
it has, Latin advance widths come out wrong (words run together, spaces
collapse), punctuation such as ``U+2022`` maps to the wrong glyph, and there
is no bold companion so ``<b>`` silently does nothing.  This module instead
locates a real TrueType family, registers regular + bold with ReportLab and
lets it embed a subset.  ReportLab cannot read PostScript/CFF outlines, so
OpenType-flavoured families (Noto Sans CJK ``.otc``, Hiragino, PingFang) are
skipped automatically by the load probe.

Search order: an explicitly configured directory (``EXPORT_FONT_DIR``), the
image-bundled directory the Dockerfile populates, the repository's
``assets/fonts/``, then well-known system paths.  ``STSong-Light`` remains
the last-resort fallback so export never fails outright.

**DOCX.** Word resolves fonts by *name*, and a run carries separate slots for
Latin (``w:ascii``/``w:hAnsi``) and East Asian (``w:eastAsia``) text.
python-docx only writes the Latin slots, so CJK characters fall through to
the theme default — which is why bold runs render half-bold: the East Asian
fallback has no bold face.  This module supplies the name pair; the writer in
``api/export.py`` stamps both slots onto every style.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: Adobe CID font bundled with ReportLab. Not embedded in the output, so it is
#: only used when no TrueType family can be found.
CID_FALLBACK_FONT = "STSong-Light"

#: Names the resolved faces are registered under with ReportLab.
_FAMILY = "FIMExportSans"
_FAMILY_BOLD = "FIMExportSans-Bold"


@dataclass(frozen=True)
class PdfFonts:
    """Font names to hand to ReportLab styles.

    ``regular`` doubles as the family name, so ``<b>`` inside a Paragraph
    resolves through ``registerFontFamily`` to ``bold``.
    """

    regular: str
    bold: str
    #: Monospace face for code blocks. Latin-only — callers must fall back to
    #: ``regular`` for code that contains CJK.
    mono: str = "Courier"
    #: ``True`` when a real TrueType file is embedded in the PDF.
    embedded: bool = True

    @property
    def mono_bold(self) -> str:
        return "Courier-Bold" if self.mono == "Courier" else self.bold


#: Filenames looked for inside the bundle directories, best first.
_BUNDLED_PAIRS: tuple[tuple[str, str], ...] = (
    ("NotoSansSC-Regular.ttf", "NotoSansSC-Bold.ttf"),
    ("NotoSansCJKsc-Regular.ttf", "NotoSansCJKsc-Bold.ttf"),
)

#: ``(regular, bold)`` where each entry is ``(path, subfont_index)``.  A
#: ``None`` bold means the family has no bold face; emphasis then degrades to
#: the regular weight rather than failing.
_SYSTEM_PAIRS: tuple[tuple[tuple[str, int], tuple[str, int] | None], ...] = (
    # Linux — TrueType-flavoured CJK families. Debian's fonts-noto-cjk is CFF
    # and is rejected by the load probe, so it is deliberately not listed.
    (("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 0), None),
    (("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0), None),
    (("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf", 0), None),
    (("/usr/share/fonts/truetype/arphic/uming.ttc", 0), None),
    # macOS — Heiti SC (index 1 is the Simplified face; index 0 is Traditional)
    (
        ("/System/Library/Fonts/STHeiti Light.ttc", 1),
        ("/System/Library/Fonts/STHeiti Medium.ttc", 1),
    ),
    (("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0), None),
    # Windows
    (("C:/Windows/Fonts/msyh.ttc", 0), ("C:/Windows/Fonts/msyhbd.ttc", 0)),
    (("C:/Windows/Fonts/simhei.ttf", 0), None),
)

_cached_fonts: PdfFonts | None = None


def _bundle_dirs() -> list[Path]:
    """Directories that may hold a bundled export font, best first."""
    dirs: list[Path] = []
    configured = os.environ.get("EXPORT_FONT_DIR", "").strip()
    if configured:
        dirs.append(Path(configured))
    # Populated by the Dockerfile.
    dirs.append(Path("/usr/share/fonts/truetype/fim-one"))
    # Repository checkout: src/fim_one/web/export_fonts.py -> <repo>/assets/fonts
    dirs.append(Path(__file__).resolve().parents[3] / "assets" / "fonts")
    return dirs


def _candidate_pairs() -> list[tuple[tuple[str, int], tuple[str, int] | None]]:
    pairs: list[tuple[tuple[str, int], tuple[str, int] | None]] = []
    for directory in _bundle_dirs():
        for regular_name, bold_name in _BUNDLED_PAIRS:
            regular = directory / regular_name
            if regular.is_file():
                bold = directory / bold_name
                pairs.append(
                    ((str(regular), 0), (str(bold), 0) if bold.is_file() else None)
                )
    pairs.extend(_SYSTEM_PAIRS)
    return pairs


def _register(name: str, spec: tuple[str, int]) -> bool:
    """Register one TrueType face; return ``False`` if it is unusable."""
    path, index = spec
    if not Path(path).is_file():
        return False
    try:
        from reportlab.pdfbase import pdfmetrics  # type: ignore[import-untyped]
        from reportlab.pdfbase.ttfonts import TTFont  # type: ignore[import-untyped]

        pdfmetrics.registerFont(TTFont(name, path, subfontIndex=index))
        return True
    except Exception as exc:  # noqa: BLE001 — any parse failure means "try the next one"
        logger.debug("Export font %s is not usable by ReportLab: %s", path, exc)
        return False


def resolve_pdf_fonts() -> PdfFonts:
    """Register and return the PDF export fonts, memoised per process."""
    global _cached_fonts
    if _cached_fonts is not None:
        return _cached_fonts

    for regular_spec, bold_spec in _candidate_pairs():
        if not _register(_FAMILY, regular_spec):
            continue

        bold_name = _FAMILY
        if bold_spec is not None and _register(_FAMILY_BOLD, bold_spec):
            bold_name = _FAMILY_BOLD
        else:
            logger.info(
                "Export font %s has no bold face; emphasis falls back to regular",
                regular_spec[0],
            )

        from reportlab.pdfbase import pdfmetrics

        pdfmetrics.registerFontFamily(
            _FAMILY, normal=_FAMILY, bold=bold_name, italic=_FAMILY, boldItalic=bold_name
        )
        logger.info("PDF export using embedded font %s", regular_spec[0])
        _cached_fonts = PdfFonts(regular=_FAMILY, bold=bold_name)
        return _cached_fonts

    logger.warning(
        "No embeddable TrueType CJK font found — PDF export falls back to the "
        "non-embedded %s CID font. Set EXPORT_FONT_DIR or install a TrueType "
        "CJK family for correct spacing and bold text.",
        CID_FALLBACK_FONT,
    )
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont  # type: ignore[import-untyped]

        pdfmetrics.registerFont(UnicodeCIDFont(CID_FALLBACK_FONT))
        _cached_fonts = PdfFonts(
            regular=CID_FALLBACK_FONT, bold=CID_FALLBACK_FONT, embedded=False
        )
    except Exception:  # noqa: BLE001
        logger.warning("CJK font %s unavailable, falling back to Helvetica", CID_FALLBACK_FONT)
        _cached_fonts = PdfFonts(
            regular="Helvetica", bold="Helvetica-Bold", embedded=False
        )
    return _cached_fonts


def reset_pdf_font_cache() -> None:
    """Drop the memoised resolution. Test-only hook."""
    global _cached_fonts
    _cached_fonts = None


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------

#: Latin face for DOCX body and headings. Ships with Office on every platform.
DOCX_LATIN_FONT = "Calibri"

#: Monospace face for code runs.
DOCX_MONO_FONT = "Courier New"

#: East Asian face per language. Word picks this slot for CJK codepoints; the
#: chosen families all carry a real bold weight, which is what keeps mixed
#: Latin/CJK bold runs from rendering half-bold.
_DOCX_EAST_ASIAN: dict[str, str] = {
    "zh": "Microsoft YaHei",
    "ja": "Yu Gothic",
    "ko": "Malgun Gothic",
}

#: Used for locales with no East Asian script of their own — exported content
#: can still contain Chinese, so the slot is never left empty.
_DOCX_EAST_ASIAN_DEFAULT = "Microsoft YaHei"


def docx_east_asian_font(locale: str) -> str:
    """Return the ``w:eastAsia`` font name for an export locale."""
    return _DOCX_EAST_ASIAN.get(locale.split("-")[0].lower(), _DOCX_EAST_ASIAN_DEFAULT)
