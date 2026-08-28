"""Печать без win32ui: MFC на складах часто не стоит."""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from local_print import (
    _dest_rect,
    _hdc_int,
    _parse_print_settings,
    bootstrap_print_modules,
)
from PIL import Image


def _source() -> str:
    return (ROOT / "local_print.py").read_text(encoding="utf-8")


class PrintWithoutWin32uiTest(unittest.TestCase):
    def test_local_print_does_not_import_win32ui(self) -> None:
        tree = ast.parse(_source())
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertNotIn("win32ui", imported)
        self.assertNotIn("import win32ui", _source())

    def test_keeps_add_dll_directory_handles(self) -> None:
        self.assertIn("_DLL_DIR_HANDLES.append", _source())

    def test_hdc_int_from_plain_handle(self) -> None:
        self.assertEqual(_hdc_int(12345), 12345)

    def test_parse_label_paper(self) -> None:
        options = _parse_print_settings("paper=58mm x 40mm,noscale")
        self.assertEqual(options["paper"], "custom")
        self.assertEqual(options["width_mm"], 58.0)
        self.assertTrue(options["noscale"])

    def test_dest_rect_fits_page(self) -> None:
        image = Image.new("RGB", (200, 100), "white")
        rect = _dest_rect(image, (100.0, 50.0), (400, 200), (203, 203), noscale=False)
        x0, y0, x1, y1 = rect
        self.assertGreater(x1 - x0, 0)
        self.assertGreater(y1 - y0, 0)
        self.assertLessEqual(x1, 400)
        self.assertLessEqual(y1, 200)


class BootstrapPrintModulesTest(unittest.TestCase):
    def test_win32print_loads_without_win32ui(self) -> None:
        try:
            bootstrap_print_modules()
        except (ImportError, OSError) as exc:
            self.skipTest(f"pywin32 недоступен в этом Python: {exc}")
        self.assertNotIn("win32ui", sys.modules)


if __name__ == "__main__":
    unittest.main()
