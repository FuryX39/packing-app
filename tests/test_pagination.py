"""Пагинация номенклатуры и списков FBS: по 50 строк на страницу."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paging import (
    PAGE_SIZE,
    catalog_matches,
    clamp_page,
    filter_catalog,
    format_fbs_picked_text,
    job_line_matches,
    page_count,
    page_range_label,
    remaining_group_matches,
    slice_page,
)


def _products(count: int) -> list[dict]:
    return [{"id": i, "sku": f"SKU-{i:04d}", "name": f"Товар {i}"} for i in range(count)]


def _jobs(count: int) -> list[dict]:
    return [{"id": i + 1, "status": "open", "line_total": 10} for i in range(count)]


class PaginationMathTest(unittest.TestCase):
    def test_page_size_is_fifty(self) -> None:
        self.assertEqual(PAGE_SIZE, 50)

    def test_empty_list(self) -> None:
        visible, page = slice_page([], 3)
        self.assertEqual(visible, [])
        self.assertEqual(page, 0)
        self.assertEqual(page_range_label(0, 0), "0 из 0")

    def test_short_list_stays_on_one_page(self) -> None:
        items = list(range(12))
        visible, page = slice_page(items, 0)
        self.assertEqual(visible, items)
        self.assertEqual(page, 0)
        self.assertEqual(page_count(12), 1)
        self.assertEqual(page_range_label(12, 0), "1–12 из 12")

    def test_catalog_first_page_does_not_dump_all_products(self) -> None:
        items = _products(123)
        visible, page = slice_page(items, 0)
        self.assertEqual(page, 0)
        self.assertEqual(len(visible), 50)
        self.assertEqual(visible[0]["id"], 0)
        self.assertEqual(visible[-1]["id"], 49)

    def test_catalog_last_partial_page(self) -> None:
        items = _products(123)
        visible, page = slice_page(items, 2)
        self.assertEqual(page, 2)
        self.assertEqual([p["id"] for p in visible], list(range(100, 123)))
        self.assertEqual(page_range_label(123, 2), "101–123 из 123")

    def test_page_past_the_end_clamps(self) -> None:
        items = list(range(51))
        visible, page = slice_page(items, 99)
        self.assertEqual(page, 1)
        self.assertEqual(visible, [50])
        self.assertEqual(clamp_page(-4, 51), 0)

    def test_fbs_jobs_paginated_like_catalog(self) -> None:
        jobs = _jobs(51)
        first, _ = slice_page(jobs, 0)
        second, _ = slice_page(jobs, 1)
        self.assertEqual(len(first), 50)
        self.assertEqual([j["id"] for j in second], [51])
        self.assertEqual(page_count(51), 2)

    def test_fbs_lines_jump_to_active_index(self) -> None:
        lines = [{"id": i} for i in range(80)]
        page = 67 // PAGE_SIZE
        visible, page = slice_page(lines, page)
        self.assertEqual(page, 1)
        self.assertIn({"id": 67}, visible)
        self.assertEqual([line["id"] for line in visible], list(range(50, 80)))


class CatalogFilterThenPageTest(unittest.TestCase):
    def test_search_finds_product_beyond_first_page_then_shows_it(self) -> None:
        products = _products(120)
        products[80]["sku"] = "NEEDLE-80"
        filtered = filter_catalog(products, "needle")
        visible, page = slice_page(filtered, 0)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(page, 0)
        self.assertEqual(visible[0]["id"], 80)

    def test_empty_query_keeps_full_list_but_only_one_page_is_shown(self) -> None:
        products = _products(60)
        filtered = filter_catalog(products, "  ")
        visible, _ = slice_page(filtered, 0)
        self.assertEqual(len(filtered), 60)
        self.assertEqual(len(visible), 50)

    def test_catalog_matches_sku_and_name(self) -> None:
        product = {"sku": "ABC-1", "name": "Синий плед", "code": "0001"}
        self.assertTrue(catalog_matches(product, "плед"))
        self.assertTrue(catalog_matches(product, "abc"))
        self.assertFalse(catalog_matches(product, "красный"))

    def test_catalog_matches_added_barcode(self) -> None:
        product = {
            "sku": "ABC-1",
            "name": "Плед",
            "barcodes": [{"barcode": "2000000000016"}],
        }
        self.assertTrue(catalog_matches(product, "2000000000016"))


class FbsSearchAndQtyTest(unittest.TestCase):
    def test_skip_mp_batch_shows_order_count(self) -> None:
        lines = [
            {"id": 1, "sku": "A-1", "product_name": "Плед", "order_display": "100"},
            {"id": 2, "sku": "A-1", "product_name": "Плед", "order_display": "101"},
            {"id": 3, "sku": "A-1", "product_name": "Плед", "order_id": "102"},
        ]
        text = format_fbs_picked_text(lines, skip_mp=True)
        self.assertIn("3 шт.", text)
        self.assertIn("100", text)
        self.assertIn("101", text)
        self.assertIn("102", text)
        self.assertNotIn("пропикайте", text)

    def test_job_search_matches_sku_name_order(self) -> None:
        line = {"sku": "ABC-9", "product_name": "Синий плед", "order_display": "Y-55"}
        self.assertTrue(job_line_matches(line, "плед"))
        self.assertTrue(job_line_matches(line, "abc-9"))
        self.assertTrue(job_line_matches(line, "y-55"))
        self.assertFalse(job_line_matches(line, "красный"))
        self.assertTrue(remaining_group_matches({"sku": "ABC-9", "barcode": "2000001"}, "2000001"))


class PageBarWidgetTest(unittest.TestCase):
    def test_page_bar_slices_and_steps(self) -> None:
        import tkinter as tk

        from paging import PageBar

        root = tk.Tk()
        root.withdraw()
        seen: list[int] = []
        bar = PageBar(root, on_change=lambda: seen.append(bar.page))
        items = list(range(51))
        try:
            self.assertEqual(bar.slice_items(items), list(range(50)))
            self.assertEqual(str(bar._next.cget("state")), "normal")
            self.assertEqual(str(bar._prev.cget("state")), "disabled")
            self.assertEqual(bar._label.cget("text"), "1–50 из 51")
            bar._step(1)
            self.assertEqual(seen, [1])
            self.assertEqual(bar.slice_items(items), [50])
            self.assertEqual(bar._label.cget("text"), "51–51 из 51")
            self.assertEqual(str(bar._next.cget("state")), "disabled")
            bar.show_index(0)
            self.assertEqual(bar.slice_items(items), list(range(50)))
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
