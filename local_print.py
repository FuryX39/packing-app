from __future__ import annotations

import io
import os
import subprocess
import tempfile
import threading
from pathlib import Path

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


def _multiply_pdf_pages(pdf_bytes: bytes, copies: int) -> bytes:
    """Один PDF с N одинаковыми страницами — одно задание на принтер."""
    if copies <= 1:
        return pdf_bytes
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(io.BytesIO(pdf_bytes))
    if not reader.pages:
        raise ValueError("Empty PDF")
    page = reader.pages[0]
    writer = PdfWriter()
    for _ in range(copies):
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def print_pdf(
    pdf_bytes: bytes,
    *,
    sumatra: str,
    printer: str = "",
    print_settings: str = "",
    copies: int = 1,
) -> None:
    if not pdf_bytes:
        raise ValueError("Empty PDF")
    count = max(1, min(9999, int(copies)))
    exe = Path(sumatra)
    if not exe.is_file():
        raise FileNotFoundError(f"SumatraPDF not found: {sumatra}")
    payload = _multiply_pdf_pages(pdf_bytes, count)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(payload)
        path = Path(tmp.name)
    cmd = [str(exe), "-silent", "-exit-when-done"]
    if printer:
        cmd.extend(["-print-to", printer])
    else:
        cmd.append("-print-to-default")
    if print_settings:
        cmd.extend(["-print-settings", print_settings])
    cmd.append(str(path))

    popen_kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    proc = subprocess.Popen(cmd, **popen_kwargs)

    def _wait_and_cleanup(process=proc, file_path=path) -> None:
        try:
            process.wait(timeout=max(90, count * 2))
        except subprocess.TimeoutExpired:
            process.kill()
        finally:
            file_path.unlink(missing_ok=True)

    threading.Thread(target=_wait_and_cleanup, daemon=True).start()


def _trim_barcode_whitespace(img: Image.Image) -> Image.Image:
    gray = img.convert("L")
    bbox = gray.getbbox()
    if not bbox:
        return img
    return img.crop(bbox)


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
