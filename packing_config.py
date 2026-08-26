from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.env"

DEFAULT_CONFIG = {
    "server_url": "",
    "api_url": "",
    "sumatra": r"C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe",
    "printer": "",
    "print_settings": "noscale,portrait,disable-auto-rotation,paper=47mm x 25mm",
    "printer_a4": "",
    "print_settings_a4": "paper=A4,portrait",
    "printer_label": "",
    "print_settings_label": "noscale,portrait,disable-auto-rotation,paper=47mm x 25mm",
    "refresh_seconds": "30",
}

ENV_KEYS = {
    "server_url": "WAREHOUSE_SERVER_URL",
    "api_url": "WAREHOUSE_API_URL",
    "sumatra": "BARCODE_PRINT_SUMATRA",
    "printer": "BARCODE_PRINT_PRINTER",
    "print_settings": "BARCODE_PRINT_SETTINGS",
    "printer_a4": "BARCODE_PRINT_PRINTER_A4",
    "print_settings_a4": "BARCODE_PRINT_SETTINGS_A4",
    "printer_label": "BARCODE_PRINT_PRINTER_LABEL",
    "print_settings_label": "BARCODE_PRINT_SETTINGS_LABEL",
    "refresh_seconds": "REFRESH_SECONDS",
}


def load_config() -> dict[str, str]:
    load_dotenv(CONFIG_PATH, override=True)
    printer = (os.getenv("BARCODE_PRINT_PRINTER") or DEFAULT_CONFIG["printer"]).strip()
    print_settings = (
        os.getenv("BARCODE_PRINT_SETTINGS") or DEFAULT_CONFIG["print_settings"]
    ).strip()
    printer_label = (os.getenv("BARCODE_PRINT_PRINTER_LABEL") or printer).strip()
    print_settings_label = (
        os.getenv("BARCODE_PRINT_SETTINGS_LABEL") or print_settings
    ).strip()
    return {
        "server_url": (os.getenv("WAREHOUSE_SERVER_URL") or DEFAULT_CONFIG["server_url"]).strip().rstrip("/"),
        "api_url": (os.getenv("WAREHOUSE_API_URL") or DEFAULT_CONFIG["api_url"]).strip().rstrip("/"),
        "sumatra": (os.getenv("BARCODE_PRINT_SUMATRA") or DEFAULT_CONFIG["sumatra"]).strip(),
        "printer": printer,
        "print_settings": print_settings,
        "printer_a4": (os.getenv("BARCODE_PRINT_PRINTER_A4") or DEFAULT_CONFIG["printer_a4"]).strip(),
        "print_settings_a4": (
            os.getenv("BARCODE_PRINT_SETTINGS_A4") or DEFAULT_CONFIG["print_settings_a4"]
        ).strip(),
        "printer_label": printer_label,
        "print_settings_label": print_settings_label,
        "refresh_seconds": (os.getenv("REFRESH_SECONDS") or DEFAULT_CONFIG["refresh_seconds"]).strip(),
    }


_LABEL_SIZE_RE = re.compile(
    r"paper\s*=\s*([\d.]+)\s*mm\s*x\s*([\d.]+)\s*mm",
    re.IGNORECASE,
)
DEFAULT_LABEL_WIDTH_MM = 47.0
DEFAULT_LABEL_HEIGHT_MM = 25.0


def parse_label_size_mm(print_settings: str) -> tuple[float, float]:
    """Размер этикетки из параметра paper=… в настройках печати."""
    match = _LABEL_SIZE_RE.search(str(print_settings or ""))
    if not match:
        return DEFAULT_LABEL_WIDTH_MM, DEFAULT_LABEL_HEIGHT_MM
    try:
        width = float(match.group(1))
        height = float(match.group(2))
    except ValueError:
        return DEFAULT_LABEL_WIDTH_MM, DEFAULT_LABEL_HEIGHT_MM
    if width <= 0 or height <= 0:
        return DEFAULT_LABEL_WIDTH_MM, DEFAULT_LABEL_HEIGHT_MM
    return width, height


def save_config(config: dict[str, str]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {**DEFAULT_CONFIG, **{k: str(v or "") for k, v in config.items()}}
    lines = [
        "# Warehouse Packing App config",
        "# Saved from application settings.",
        "",
    ]
    for internal_key, env_key in ENV_KEYS.items():
        value = data.get(internal_key, "")
        lines.append(f"{env_key}={_quote_value(value)}")
    CONFIG_PATH.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    for internal_key, env_key in ENV_KEYS.items():
        os.environ[env_key] = data.get(internal_key, "")


def _quote_value(value: str) -> str:
    s = str(value or "")
    if not s:
        return ""
    if any(ch in s for ch in ('"', "#", "\n", "\r")) or s.startswith(" ") or s.endswith(" "):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "") + '"'
    return s
