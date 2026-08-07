"""Tests for export font resolution (`fim_one.web.export_fonts`)."""

from __future__ import annotations

from pathlib import Path

import pytest

from fim_one.web import export_fonts


@pytest.fixture(autouse=True)
def _clear_font_cache():
    export_fonts.reset_pdf_font_cache()
    yield
    export_fonts.reset_pdf_font_cache()


# ---------------------------------------------------------------------------
# PDF font resolution
# ---------------------------------------------------------------------------


class TestResolvePdfFonts:
    def test_returns_registered_font_names(self):
        from reportlab.pdfbase import pdfmetrics

        fonts = export_fonts.resolve_pdf_fonts()

        # Whatever was picked must actually be registered with ReportLab.
        assert pdfmetrics.getFont(fonts.regular) is not None
        assert pdfmetrics.getFont(fonts.bold) is not None

    def test_result_is_memoised(self):
        first = export_fonts.resolve_pdf_fonts()
        assert export_fonts.resolve_pdf_fonts() is first

    def test_bold_resolves_through_the_family_map(self):
        """`<b>` inside a Paragraph must map to the bold face."""
        from reportlab.lib.fonts import tt2ps

        fonts = export_fonts.resolve_pdf_fonts()
        if not fonts.embedded:
            pytest.skip("no embeddable TrueType CJK font on this machine")

        assert tt2ps(fonts.regular, 1, 0) == fonts.bold

    def test_falls_back_to_cid_font_without_candidates(self, monkeypatch):
        monkeypatch.setattr(export_fonts, "_candidate_pairs", lambda: [])

        fonts = export_fonts.resolve_pdf_fonts()

        assert fonts.embedded is False
        assert fonts.regular in (export_fonts.CID_FALLBACK_FONT, "Helvetica")

    def test_unusable_candidate_is_skipped(self, monkeypatch, tmp_path):
        """A file ReportLab cannot parse must not abort resolution."""
        broken = tmp_path / "broken.ttf"
        broken.write_bytes(b"not a font")
        monkeypatch.setattr(
            export_fonts, "_candidate_pairs", lambda: [((str(broken), 0), None)]
        )

        fonts = export_fonts.resolve_pdf_fonts()

        assert fonts.embedded is False


class TestRegister:
    def test_missing_file_returns_false(self, tmp_path):
        assert export_fonts._register("x", (str(tmp_path / "nope.ttf"), 0)) is False


class TestBundleDirs:
    def test_env_var_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("EXPORT_FONT_DIR", "/custom/fonts")
        assert export_fonts._bundle_dirs()[0] == Path("/custom/fonts")

    def test_blank_env_var_ignored(self, monkeypatch):
        monkeypatch.setenv("EXPORT_FONT_DIR", "   ")
        assert Path("/custom/fonts") not in export_fonts._bundle_dirs()

    def test_bundled_pair_is_preferred(self, monkeypatch, tmp_path):
        regular = tmp_path / "NotoSansSC-Regular.ttf"
        bold = tmp_path / "NotoSansSC-Bold.ttf"
        regular.write_bytes(b"stub")
        bold.write_bytes(b"stub")
        monkeypatch.setenv("EXPORT_FONT_DIR", str(tmp_path))

        first = export_fonts._candidate_pairs()[0]

        assert first == ((str(regular), 0), (str(bold), 0))

    def test_bundled_regular_without_bold(self, monkeypatch, tmp_path):
        (tmp_path / "NotoSansSC-Regular.ttf").write_bytes(b"stub")
        monkeypatch.setenv("EXPORT_FONT_DIR", str(tmp_path))

        assert export_fonts._candidate_pairs()[0][1] is None


# ---------------------------------------------------------------------------
# DOCX font names
# ---------------------------------------------------------------------------


class TestDocxEastAsianFont:
    @pytest.mark.parametrize(
        ("locale", "expected"),
        [
            ("zh", "Microsoft YaHei"),
            ("zh-CN", "Microsoft YaHei"),
            ("ja", "Yu Gothic"),
            ("ko", "Malgun Gothic"),
        ],
    )
    def test_known_locales(self, locale, expected):
        assert export_fonts.docx_east_asian_font(locale) == expected

    def test_unknown_locale_still_gets_a_cjk_font(self):
        """Exports in any locale can contain Chinese, so the slot is never empty."""
        assert export_fonts.docx_east_asian_font("fr") == "Microsoft YaHei"
