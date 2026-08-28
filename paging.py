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


def catalog_matches(product: dict, query: str) -> bool:
    needle = str(query or "").strip().casefold()
    if not needle:
        return True
    fields = (
        product.get("name", ""),
        product.get("sku", ""),
        product.get("code", ""),
        product.get("external_code", ""),
        product.get("group_name", ""),
    )
    return any(needle in str(field or "").casefold() for field in fields)


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
