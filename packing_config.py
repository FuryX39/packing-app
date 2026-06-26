from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.env"

DEFAULT_CONFIG = {
    "server_url": "",
    "sumatra": r"C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe",
    "printer": "",
    "print_settings": "noscale,portrait,disable-auto-rotation,paper=40mm x 30mm",
    "refresh_seconds": "30",
}

ENV_KEYS = {
    "server_url": "WAREHOUSE_SERVER_URL",
    "sumatra": "BARCODE_PRINT_SUMATRA",
    "printer": "BARCODE_PRINT_PRINTER",
    "print_settings": "BARCODE_PRINT_SETTINGS",
    "refresh_seconds": "REFRESH_SECONDS",
}


def load_config() -> dict[str, str]:
    load_dotenv(CONFIG_PATH, override=True)
    return {
        "server_url": (os.getenv("WAREHOUSE_SERVER_URL") or DEFAULT_CONFIG["server_url"]).strip().rstrip("/"),
        "sumatra": (os.getenv("BARCODE_PRINT_SUMATRA") or DEFAULT_CONFIG["sumatra"]).strip(),
        "printer": (os.getenv("BARCODE_PRINT_PRINTER") or DEFAULT_CONFIG["printer"]).strip(),
        "print_settings": (
            os.getenv("BARCODE_PRINT_SETTINGS")
            or DEFAULT_CONFIG["print_settings"]
        ).strip(),
        "refresh_seconds": (os.getenv("REFRESH_SECONDS") or DEFAULT_CONFIG["refresh_seconds"]).strip(),
    }


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
