"""Регистрация TTF-шрифтов с кириллицей для ReportLab (PDF)."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_ASSETS = Path(__file__).resolve().parent / "assets" / "fonts"
_FONT_REGULAR_NAME = "PackingLabel"
_FONT_BOLD_NAME = "PackingLabel-Bold"

_REGULAR_CANDIDATES = (
    _ASSETS / "DejaVuSans.ttf",
    _ASSETS / "Arial.ttf",
    _ASSETS / "LiberationSans-Regular.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/TTF/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("C:/Windows/Fonts/Arial.ttf"),
)

_BOLD_CANDIDATES = (
    _ASSETS / "DejaVuSans-Bold.ttf",
    _ASSETS / "Arial-Bold.ttf",
    _ASSETS / "LiberationSans-Bold.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    Path("C:/Windows/Fonts/arialbd.ttf"),
    Path("C:/Windows/Fonts/Arialbd.ttf"),
)


def _first_existing(paths: tuple[Path, ...]) -> Path | None:
    for path in paths:
        if path.is_file():
            return path
    return None


@lru_cache(maxsize=1)
def get_pdf_label_fonts() -> tuple[str, str]:
    """Имена зарегистрированных шрифтов (regular, bold) для canvas.setFont."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    regular_path = _first_existing(_REGULAR_CANDIDATES)
    if regular_path is None:
        logger.warning(
            "TTF с кириллицей не найден; в PDF русский текст может отображаться квадратами. "
            "Положите DejaVuSans.ttf в assets/fonts/ или используйте Windows с Arial."
        )
        return "Helvetica", "Helvetica-Bold"

    bold_path = _first_existing(_BOLD_CANDIDATES) or regular_path

    if _FONT_REGULAR_NAME not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(_FONT_REGULAR_NAME, str(regular_path)))
    if _FONT_BOLD_NAME not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(_FONT_BOLD_NAME, str(bold_path)))

    return _FONT_REGULAR_NAME, _FONT_BOLD_NAME
