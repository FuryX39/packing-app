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
    for _ in range(count):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
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
                process.wait(timeout=90)
            except subprocess.TimeoutExpired:
                process.kill()
            finally:
                file_path.unlink(missing_ok=True)

        threading.Thread(target=_wait_and_cleanup, daemon=True).start()


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


def barcode_label_pdf(barcode_value: str, *, sku: str = "", name: str = "") -> bytes:
    value = str(barcode_value or "").strip()
    if not value:
        raise ValueError("Barcode is empty")

    page_w = 40 * mm
    page_h = 30 * mm
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_w, page_h))

    writer = ImageWriter()
    img_buf = io.BytesIO()
    Code128(value, writer=writer).write(
        img_buf,
        options={
            "module_width": 0.22,
            "module_height": 10.0,
            "quiet_zone": 1.2,
            "font_size": 0,
            "text_distance": 0,
            "write_text": False,
        },
    )
    img_buf.seek(0)
    img = Image.open(img_buf).convert("RGB")
    max_w = page_w - 2 * mm
    max_h = 13 * mm
    draw_h = max_h
    draw_w = min(max_w, img.size[0] * (draw_h / img.size[1]))
    c.drawImage(ImageReader(img), (page_w - draw_w) / 2, 9 * mm, width=draw_w, height=draw_h)

    font, font_bold = get_pdf_label_fonts()
    text_w = page_w - 2 * mm
    line_pt = 6.0
    if name:
        c.setFont(font_bold, line_pt)
        c.drawCentredString(page_w / 2, 26 * mm, _truncate_to_width(name, font_bold, line_pt, text_w))
    c.setFont(font, line_pt)
    c.drawCentredString(page_w / 2, 5 * mm, _truncate_to_width(f"SKU {sku}", font, line_pt, text_w))
    c.drawCentredString(page_w / 2, 2.5 * mm, _truncate_to_width(value, font, line_pt, text_w))
    c.showPage()
    c.save()
    return buf.getvalue()
