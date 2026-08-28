from __future__ import annotations

import ctypes
import io
import os
import re
import shutil
import sys
import sysconfig
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Any

from barcode import Code128
from barcode.writer import ImageWriter
from PIL import Image
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from pdf_fonts import get_pdf_label_fonts

BARCODE_SIDE_MARGIN_MM = 0.6
TEXT_SIDE_MARGIN_MM = 1.2
NAME_MAX_LINES = 2


_PRINT_LOCK = threading.Lock()
_PAPER_MM_RE = re.compile(
    r"paper\s*=\s*([\d.]+)\s*mm\s*x\s*([\d.]+)\s*mm",
    re.IGNORECASE,
)
_HORZRES = 8
_VERTRES = 10
_LOGPIXELSX = 88
_LOGPIXELSY = 90
_PYWIN32_READY = False
# os.add_dll_directory возвращает handle: если его выбросить, Python 3.8+
# снова перестаёт искать DLL в этой папке (GC закрывает cookie).
_DLL_DIR_HANDLES: list[object] = []


class _DOCINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_int),
        ("lpszDocName", wintypes.LPCWSTR),
        ("lpszOutput", wintypes.LPCWSTR),
        ("lpszDatatype", wintypes.LPCWSTR),
        ("fwType", wintypes.DWORD),
    ]


def _pywin32_folders() -> list[Path]:
    sites: list[Path] = []
    platlib = sysconfig.get_path("platlib")
    if platlib:
        sites.append(Path(platlib))
    for root in (Path(sys.prefix), Path(getattr(sys, "base_prefix", sys.prefix) or sys.prefix)):
        sites.append(root / "Lib" / "site-packages")
        sites.append(root / "lib" / "site-packages")
    out: list[Path] = []
    seen: set[str] = set()
    for site in sites:
        for rel in ("pywin32_system32", "win32", "win32/lib", "Pythonwin"):
            folder = site / rel
            key = str(folder).casefold()
            if key in seen or not folder.is_dir():
                continue
            seen.add(key)
            out.append(folder)
    return out


def _copy_pywin32_dlls() -> None:
    """Кладём pywintypes/pythoncom рядом с pythonw.exe — так ищет загрузчик Windows."""
    dests = [Path(sys.executable).resolve().parent, Path(sys.prefix)]
    for folder in _pywin32_folders():
        if folder.name.casefold() != "pywin32_system32":
            continue
        for dll in folder.glob("*.dll"):
            for dest in dests:
                target = dest / dll.name
                try:
                    if target.exists() and target.stat().st_size == dll.stat().st_size:
                        continue
                    shutil.copy2(dll, target)
                except OSError:
                    continue


def _ensure_pywin32() -> None:
    """pywin32 часто стоит в pip, но DLL не рядом с pythonw (venv без postinstall)."""
    global _PYWIN32_READY
    if _PYWIN32_READY:
        return
    folders = _pywin32_folders()
    path_parts = os.environ.get("PATH", "").split(os.pathsep)
    add = getattr(os, "add_dll_directory", None)
    for folder in folders:
        folder_s = str(folder)
        if folder_s not in path_parts:
            os.environ["PATH"] = folder_s + os.pathsep + os.environ.get("PATH", "")
            path_parts.insert(0, folder_s)
        if add is not None:
            try:
                handle = add(folder_s)
            except OSError:
                handle = None
            if handle is not None:
                _DLL_DIR_HANDLES.append(handle)
    _copy_pywin32_dlls()
    try:
        import pywin32_bootstrap  # noqa: F401
    except ImportError:
        pass
    _PYWIN32_READY = True


def bootstrap_print_modules() -> None:
    """Проверка для start.bat: печать идёт через win32print/win32gui, без win32ui/MFC."""
    _ensure_pywin32()
    import win32gui  # noqa: F401
    import win32print  # noqa: F401


def _pywin32_error(exc: BaseException) -> RuntimeError:
    return RuntimeError(
        "Не удалось загрузить модули печати Windows (pywin32).\n"
        f"Этот Python: {sys.executable}\n"
        f"Ошибка: {exc}\n"
        "Запускайте start.bat setup (переустановка .venv).\n"
        "Печать не требует win32ui. Если ошибка всё ещё про DLL — "
        "поставьте Microsoft Visual C++ Redistributable x64."
    )


def _gdi32():
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    gdi32.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]
    gdi32.GetDeviceCaps.restype = ctypes.c_int
    gdi32.StartDocW.argtypes = [wintypes.HDC, ctypes.POINTER(_DOCINFOW)]
    gdi32.StartDocW.restype = ctypes.c_int
    gdi32.StartPage.argtypes = [wintypes.HDC]
    gdi32.StartPage.restype = ctypes.c_int
    gdi32.EndPage.argtypes = [wintypes.HDC]
    gdi32.EndPage.restype = ctypes.c_int
    gdi32.EndDoc.argtypes = [wintypes.HDC]
    gdi32.EndDoc.restype = ctypes.c_int
    return gdi32


def _parse_print_settings(print_settings: str) -> dict[str, Any]:
    raw = str(print_settings or "")
    folded = raw.casefold()
    landscape = "landscape" in folded
    noscale = "noscale" in folded
    paper = "default"
    width_mm: float | None = None
    height_mm: float | None = None
    if re.search(r"paper\s*=\s*a4\b", folded):
        paper = "a4"
        width_mm, height_mm = 210.0, 297.0
    else:
        match = _PAPER_MM_RE.search(raw)
        if match:
            paper = "custom"
            width_mm = float(match.group(1))
            height_mm = float(match.group(2))
    return {
        "landscape": landscape,
        "noscale": noscale,
        "paper": paper,
        "width_mm": width_mm,
        "height_mm": height_mm,
    }


def _rasterize_pdf(pdf_bytes: bytes, *, dpi: int) -> list[tuple[Image.Image, tuple[float, float]]]:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        if len(doc) == 0:
            raise ValueError("Empty PDF")
        scale = max(72, int(dpi)) / 72.0
        pages: list[tuple[Image.Image, tuple[float, float]]] = []
        for index in range(len(doc)):
            page = doc[index]
            width_pt, height_pt = page.get_size()
            bitmap = page.render(scale=scale, rotation=0)
            image = bitmap.to_pil().convert("RGB")
            pages.append((image, (float(width_pt), float(height_pt))))
        return pages
    finally:
        doc.close()


def _resolve_printer(name: str) -> str:
    _ensure_pywin32()
    import win32print

    printers = [
        item[2]
        for item in win32print.EnumPrinters(
            win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        )
    ]
    wanted = (name or "").strip()
    if not wanted:
        return win32print.GetDefaultPrinter()
    for item in printers:
        if item.casefold() == wanted.casefold():
            return item
    raise FileNotFoundError(f"Принтер не найден: {name}")


def _apply_devmode(devmode, options: dict[str, Any]) -> None:
    _ensure_pywin32()
    import win32con

    if options["landscape"]:
        devmode.Orientation = win32con.DMORIENT_LANDSCAPE
    else:
        devmode.Orientation = win32con.DMORIENT_PORTRAIT
    paper = options["paper"]
    width_mm = options["width_mm"]
    height_mm = options["height_mm"]
    if paper == "a4":
        devmode.PaperSize = win32con.DMPAPER_A4
        return
    if paper == "custom" and width_mm and height_mm:
        w_tenths = max(1, int(round(float(width_mm) * 10)))
        h_tenths = max(1, int(round(float(height_mm) * 10)))
        if options["landscape"] and h_tenths > w_tenths:
            w_tenths, h_tenths = h_tenths, w_tenths
        devmode.PaperSize = getattr(win32con, "DMPAPER_USER", 256)
        devmode.PaperWidth = w_tenths
        devmode.PaperLength = h_tenths


def _dest_rect(
    image: Image.Image,
    page_pts: tuple[float, float],
    printable: tuple[int, int],
    dpi: tuple[int, int],
    *,
    noscale: bool,
) -> tuple[int, int, int, int]:
    area_w, area_h = printable
    if area_w <= 0 or area_h <= 0:
        return (0, 0, image.width, image.height)
    img_w, img_h = image.size
    if noscale:
        page_w_pt, page_h_pt = page_pts
        draw_w = int(round(page_w_pt / 72.0 * dpi[0])) if page_w_pt else img_w
        draw_h = int(round(page_h_pt / 72.0 * dpi[1])) if page_h_pt else img_h
    else:
        scale = min(area_w / img_w, area_h / img_h)
        draw_w = max(1, int(round(img_w * scale)))
        draw_h = max(1, int(round(img_h * scale)))
    if draw_w > area_w or draw_h > area_h:
        scale = min(area_w / max(draw_w, 1), area_h / max(draw_h, 1))
        draw_w = max(1, int(round(draw_w * scale)))
        draw_h = max(1, int(round(draw_h * scale)))
    x = max(0, (area_w - draw_w) // 2)
    y = max(0, (area_h - draw_h) // 2)
    return (x, y, x + draw_w, y + draw_h)


def _hdc_int(hdc: object) -> int:
    if hasattr(hdc, "GetHandleOutput"):
        return int(hdc.GetHandleOutput())
    handle = getattr(hdc, "handle", hdc)
    return int(handle)


def _gdi_print_pages(
    pages: list[tuple[Image.Image, tuple[float, float]]],
    *,
    printer: str,
    copies: int,
    options: dict[str, Any],
) -> None:
    # win32ui тянет MFC (mfc140u.dll). На складах его часто нет — печать через gdi32.
    _ensure_pywin32()
    import win32gui
    import win32print
    from PIL import ImageWin

    printer_name = _resolve_printer(printer)
    handle = win32print.OpenPrinter(printer_name)
    try:
        devmode = win32print.GetPrinter(handle, 2).get("pDevMode")
        if devmode is not None:
            _apply_devmode(devmode, options)
            hdc = win32gui.CreateDC("WINSPOOL", printer_name, devmode)
        else:
            hdc = win32gui.CreateDC("WINSPOOL", printer_name, None)
    finally:
        win32print.ClosePrinter(handle)

    hdc_int = _hdc_int(hdc)
    gdi32 = _gdi32()
    try:
        area = (
            int(gdi32.GetDeviceCaps(hdc_int, _HORZRES)),
            int(gdi32.GetDeviceCaps(hdc_int, _VERTRES)),
        )
        dpi = (
            int(gdi32.GetDeviceCaps(hdc_int, _LOGPIXELSX) or 203),
            int(gdi32.GetDeviceCaps(hdc_int, _LOGPIXELSY) or 203),
        )
        docinfo = _DOCINFOW()
        docinfo.cbSize = ctypes.sizeof(_DOCINFOW)
        docinfo.lpszDocName = "Warehouse packing"
        docinfo.lpszOutput = None
        docinfo.lpszDatatype = None
        docinfo.fwType = 0
        if gdi32.StartDocW(hdc_int, ctypes.byref(docinfo)) <= 0:
            err = ctypes.get_last_error()
            raise OSError(err, f"StartDoc failed (Win32 {err})")
        try:
            for _copy in range(copies):
                for image, page_pts in pages:
                    if gdi32.StartPage(hdc_int) <= 0:
                        err = ctypes.get_last_error()
                        raise OSError(err, f"StartPage failed (Win32 {err})")
                    dib = ImageWin.Dib(image)
                    dib.draw(
                        hdc_int,
                        _dest_rect(image, page_pts, area, dpi, noscale=options["noscale"]),
                    )
                    gdi32.EndPage(hdc_int)
        finally:
            gdi32.EndDoc(hdc_int)
    finally:
        win32gui.DeleteDC(hdc)


def print_pdf(
    pdf_bytes: bytes,
    *,
    printer: str = "",
    print_settings: str = "",
    copies: int = 1,
    sumatra: str = "",
    **_unused: Any,
) -> None:
    """Тихая печать PDF через Windows GDI, без SumatraPDF."""
    if not pdf_bytes:
        raise ValueError("Empty PDF")
    count = max(1, min(9999, int(copies)))
    options = _parse_print_settings(print_settings)
    dpi = 200 if options["paper"] == "a4" else 300
    try:
        pages = _rasterize_pdf(pdf_bytes, dpi=dpi)
    except ImportError as exc:
        raise RuntimeError("Установите pypdfium2: pip install pypdfium2") from exc
    try:
        _ensure_pywin32()
        with _PRINT_LOCK:
            _gdi_print_pages(pages, printer=printer, copies=count, options=options)
    except (ImportError, OSError) as exc:
        raise _pywin32_error(exc) from exc


def _trim_barcode_whitespace(img: Image.Image) -> Image.Image:
    gray = img.convert("L")
    bbox = gray.getbbox()
    if not bbox:
        return img
    return img.crop(bbox)


def render_code128_image(barcode_value: str) -> Image.Image:
    """Публичная отрисовка Code128 для экрана ручной сборки."""
    return _render_code128_image(barcode_value)


def _render_code128_image(barcode_value: str) -> Image.Image:
    writer = ImageWriter()
    buf = io.BytesIO()
    Code128(barcode_value, writer=writer).write(
        buf,
        options={
            "module_width": 0.22,
            "module_height": 10.0,
            "quiet_zone": 1.2,
            "font_size": 0,
            "text_distance": 0,
            "write_text": False,
        },
    )
    buf.seek(0)
    img = _trim_barcode_whitespace(Image.open(buf).convert("RGB"))
    w, h = img.size
    if h > w:
        img = img.rotate(90, expand=True)
    return img


def _truncate_to_width(text: str, font_name: str, font_size: float, max_width: float) -> str:
    s = str(text or "").strip()
    if not s:
        return ""
    if pdfmetrics.stringWidth(s, font_name, font_size) <= max_width:
        return s
    ell = "…"
    while len(s) > 1 and pdfmetrics.stringWidth(s + ell, font_name, font_size) > max_width:
        s = s[:-1]
    return s + ell


def _wrap_name_lines(
    name: str,
    font_name: str,
    font_size: float,
    max_width: float,
    *,
    max_lines: int = NAME_MAX_LINES,
) -> list[str]:
    s = " ".join(str(name or "").split())
    if not s:
        return []

    def fits(chunk: str) -> bool:
        return pdfmetrics.stringWidth(chunk, font_name, font_size) <= max_width

    lines: list[str] = []
    words = s.split(" ")
    idx = 0
    while idx < len(words) and len(lines) < max_lines:
        word = words[idx]
        if not fits(word):
            chunk = ""
            for ch in word:
                trial = chunk + ch
                if fits(trial):
                    chunk = trial
                else:
                    if chunk:
                        lines.append(chunk)
                        chunk = ch
                        if len(lines) >= max_lines:
                            break
                    else:
                        lines.append(ch)
                        if len(lines) >= max_lines:
                            break
                        chunk = ""
            if chunk and len(lines) < max_lines:
                lines.append(chunk)
            idx += 1
            continue

        current = word
        idx += 1
        while idx < len(words):
            trial = f"{current} {words[idx]}"
            if fits(trial):
                current = trial
                idx += 1
            else:
                break
        lines.append(current)

    if idx < len(words) and lines:
        rest = " ".join(words[idx:])
        lines[-1] = _truncate_to_width(f"{lines[-1]} {rest}", font_name, font_size, max_width)
    return lines[:max_lines]


def _draw_centered_lines(
    c: canvas.Canvas,
    lines: list[str],
    *,
    cx: float,
    y_top: float,
    font_name: str,
    font_size: float,
    line_step: float,
) -> float:
    if not lines:
        return 0.0
    c.setFont(font_name, font_size)
    for i, line in enumerate(lines):
        c.drawCentredString(cx, y_top - i * line_step, line)
    return (len(lines) - 1) * line_step + font_size * 0.85


def barcode_label_pdf(
    barcode_value: str,
    *,
    sku: str = "",
    name: str = "",
    width_mm: float = 47.0,
    height_mm: float = 25.0,
) -> bytes:
    value = str(barcode_value or "").strip()
    if not value:
        raise ValueError("Barcode is empty")

    sku_s = str(sku or "").strip()
    name_s = str(name or "").strip()

    page_w = width_mm * mm
    page_h = height_mm * mm
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_w, page_h))

    margin_v = 1.0 * mm
    text_w = page_w - 2 * TEXT_SIDE_MARGIN_MM * mm
    barcode_w = page_w - 2 * BARCODE_SIDE_MARGIN_MM * mm
    cx = page_w / 2

    font, font_bold = get_pdf_label_fonts()
    compact = height_mm <= 26
    name_pt = 6.0 if compact else 6.5
    line_pt = 5.5 if compact else 6.0
    name_line_step = 1.9 * mm if compact else 2.15 * mm
    footer_line_step = 2.0 * mm if compact else 2.4 * mm

    c.setFont(font, line_pt)
    y_value = margin_v
    y_sku = margin_v + footer_line_step
    footer_top = y_sku + line_pt * 0.9
    if sku_s:
        c.drawCentredString(cx, y_sku, _truncate_to_width(f"SKU {sku_s}", font, line_pt, text_w))
    c.drawCentredString(cx, y_value, _truncate_to_width(value, font, line_pt, text_w))

    zone_bottom = footer_top + 0.5 * mm
    zone_top = page_h - margin_v

    if name_s:
        name_lines = _wrap_name_lines(name_s, font_bold, name_pt, text_w, max_lines=NAME_MAX_LINES)
        name_block = _draw_centered_lines(
            c,
            name_lines,
            cx=cx,
            y_top=zone_top - name_pt * 0.15,
            font_name=font_bold,
            font_size=name_pt,
            line_step=name_line_step,
        )
        zone_top = zone_top - name_block - 0.4 * mm

    img = _render_code128_image(value)
    img_w_px, img_h_px = img.size
    max_w = barcode_w
    max_h = zone_top - zone_bottom
    if max_h < 2.5 * mm:
        max_h = 2.5 * mm
    draw_w = max_w
    draw_h = img_h_px * (max_w / img_w_px) if img_w_px else max_h
    if draw_h > max_h:
        draw_h = max_h
        draw_w = img_w_px * (max_h / img_h_px) if img_h_px else max_w
    x = (page_w - draw_w) / 2
    img_y = zone_bottom + (max_h - draw_h) / 2
    c.drawImage(
        ImageReader(img),
        x,
        img_y,
        width=draw_w,
        height=draw_h,
        preserveAspectRatio=True,
        mask="auto",
    )

    c.showPage()
    c.save()
    return buf.getvalue()
