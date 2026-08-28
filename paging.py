from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Sequence, TypeVar

PAGE_SIZE = 50

T = TypeVar("T")


def page_count(total: int, page_size: int = PAGE_SIZE) -> int:
    if total <= 0:
        return 1
    size = max(1, int(page_size))
    return (int(total) + size - 1) // size


def clamp_page(page: int, total: int, page_size: int = PAGE_SIZE) -> int:
    pages = page_count(total, page_size)
    return max(0, min(int(page), pages - 1))


def slice_page(items: Sequence[T], page: int, page_size: int = PAGE_SIZE) -> tuple[list[T], int]:
    total = len(items)
    size = max(1, int(page_size))
    page = clamp_page(page, total, size)
    start = page * size
    return list(items[start : start + size]), page


def page_range_label(total: int, page: int, page_size: int = PAGE_SIZE) -> str:
    if total <= 0:
        return "0 из 0"
    size = max(1, int(page_size))
    page = clamp_page(page, total, size)
    start = page * size + 1
    end = min(total, (page + 1) * size)
    return f"{start}–{end} из {total}"


def _barcode_texts(raw) -> list[str]:
    out: list[str] = []
    for item in raw or []:
        if isinstance(item, dict):
            code = str(item.get("barcode") or "").strip()
        else:
            code = str(item or "").strip()
        if code:
            out.append(code)
    return out


def text_matches(query: str, *fields: object) -> bool:
    needle = str(query or "").strip().casefold()
    if not needle:
        return True
    return any(needle in str(field or "").casefold() for field in fields)


def catalog_matches(product: dict, query: str) -> bool:
    return text_matches(
        query,
        product.get("name", ""),
        product.get("sku", ""),
        product.get("code", ""),
        product.get("external_code", ""),
        product.get("group_name", ""),
        *_barcode_texts(product.get("barcodes")),
    )


def job_line_matches(line: dict, query: str, *extra: object) -> bool:
    return text_matches(
        query,
        line.get("sku", ""),
        line.get("product_name", ""),
        line.get("name", ""),
        line.get("order_id", ""),
        line.get("order_display", ""),
        line.get("barcode", ""),
        *extra,
    )


def remaining_group_matches(group: dict, query: str) -> bool:
    return text_matches(
        query,
        group.get("sku", ""),
        group.get("name", ""),
        group.get("barcode", ""),
    )


def format_fbs_picked_text(lines: list[dict], *, skip_mp: bool = False) -> str:
    if not lines:
        return ""
    first = lines[0]
    cis_note = " · КИЗ" if first.get("has_cis") else ""
    sku = first.get("sku") or ""
    name = first.get("product_name") or first.get("name") or ""
    if len(lines) == 1:
        return (
            f"SKU {sku} · {name} · "
            f"заказ {first.get('order_display') or first.get('order_id')} · "
            f"строка #{first.get('id')}{cis_note}"
        )
    orders = ", ".join(
        str(item.get("order_display") or item.get("order_id") or "?") for item in lines
    )
    confirm = "" if skip_mp else " · пропикайте ярлыки подряд"
    return f"SKU {sku} · {name} · {len(lines)} шт. · заказы: {orders}{confirm}{cis_note}"


def filter_catalog(products: Sequence[dict], query: str) -> list[dict]:
    needle = str(query or "").strip().casefold()
    if not needle:
        return list(products)
    return [item for item in products if catalog_matches(item, needle)]


class PageBar(ttk.Frame):
    """Назад / 1–50 из N / Вперёд — список не рисуется целиком."""

    def __init__(self, master, *, on_change: Callable[[], None], page_size: int = PAGE_SIZE) -> None:
        super().__init__(master)
        self._on_change = on_change
        self.page_size = max(1, int(page_size))
        self.page = 0
        self.total = 0
        self._prev = ttk.Button(self, text="Назад", command=lambda: self._step(-1), width=8)
        self._prev.pack(side=tk.LEFT)
        self._label = ttk.Label(self, text="0 из 0")
        self._label.pack(side=tk.LEFT, padx=8)
        self._next = ttk.Button(self, text="Вперёд", command=lambda: self._step(1), width=8)
        self._next.pack(side=tk.LEFT)

    def reset(self) -> None:
        self.page = 0

    def show_index(self, index: int) -> None:
        if index < 0:
            return
        self.page = int(index) // self.page_size

    def slice_items(self, items: Sequence[T]) -> list[T]:
        visible, self.page = slice_page(items, self.page, self.page_size)
        self.total = len(items)
        self._refresh_controls()
        return visible

    def _pages(self) -> int:
        return page_count(self.total, self.page_size)

    def _refresh_controls(self) -> None:
        self._label.config(text=page_range_label(self.total, self.page, self.page_size))
        self._prev.configure(state="normal" if self.page > 0 else "disabled")
        self._next.configure(state="normal" if self.page + 1 < self._pages() and self.total else "disabled")

    def _step(self, delta: int) -> None:
        new_page = self.page + delta
        if new_page < 0 or new_page >= self._pages():
            return
        self.page = new_page
        self._on_change()
