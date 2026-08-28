from __future__ import annotations

import base64
import io
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable

from PIL import Image, ImageTk

from api_client import ApiError, AuthError
from image_cache import begin_fetch, fetch_url, get_cached_bytes
from label_cache import (
    cached_count,
    get_line_pdf,
    job_labels_ready,
    job_line_ids,
    put_line_pdf,
    save_zip,
)
from local_print import print_pdf, render_code128_image
from packing_config import load_config, save_config
from paging import PageBar

_FBS_JOB_STATUS_RU = {
    "open": "Открыто",
    "in_progress": "В работе",
    "done": "Готово",
    "cancelled": "Отменено",
}
_FBS_LINE_STATUS_RU = {
    "pending": "В сборке",
    "printed": "Печать",
    "done": "Готово",
}
_FBS_JOBS_MIN_PX = 240
_FBS_BARCODE_W = 180
_FBS_BARCODE_H = 80
_FBS_ACTIVE_IMG = 110
_FBS_LIST_IMG = 32


def _fbs_job_status_ru(value: object) -> str:
    raw = str(value or "").strip()
    return _FBS_JOB_STATUS_RU.get(raw, raw or "—")


def _fbs_line_status_ru(value: object) -> str:
    raw = str(value or "").strip()
    return _FBS_LINE_STATUS_RU.get(raw, raw or "—")


class _TreeHoverTip:
    """Подсказка с полным текстом ячейки при удержании курсора."""

    def __init__(self, tree: ttk.Treeview, text_fn: Callable[[str, str], str]) -> None:
        self.tree = tree
        self.text_fn = text_fn
        self._after: str | None = None
        self._tip: tk.Toplevel | None = None
        self._last = ""
        tree.bind("<Motion>", self._on_motion, add="+")
        tree.bind("<Leave>", self._on_leave, add="+")
        tree.bind("<Button>", self._on_leave, add="+")
        tree.bind("<MouseWheel>", self._on_leave, add="+")

    def _on_leave(self, _event=None) -> None:
        self._cancel()
        self._hide()
        self._last = ""

    def _on_motion(self, event) -> None:
        row = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        key = f"{row}:{col}"
        if key != self._last:
            self._hide()
            self._last = key
        self._cancel()
        if not row:
            return
        self._after = self.tree.after(
            400, lambda r=row, c=col, x=event.x_root, y=event.y_root: self._show(r, c, x, y)
        )

    def _cancel(self) -> None:
        if self._after:
            try:
                self.tree.after_cancel(self._after)
            except Exception:
                pass
            self._after = None

    def _hide(self) -> None:
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None

    def _show(self, row: str, col: str, x: int, y: int) -> None:
        self._after = None
        text = (self.text_fn(row, col) or "").strip()
        if not text:
            return
        self._hide()
        tip = tk.Toplevel(self.tree)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x + 12}+{y + 16}")
        label = tk.Label(
            tip,
            text=text,
            justify=tk.LEFT,
            background="#ffffe0",
            relief=tk.SOLID,
            borderwidth=1,
            padx=6,
            pady=4,
            wraplength=420,
        )
        label.pack()
        self._tip = tip


class _FbsLinesTable(ttk.Frame):
    """Строки FBS: отдельные колонки №, фото, артикул, название, заказ, статус."""

    _MIN = (40, 40, 90, 180, 120, 90)
    _STRETCH_COL = 3

    def __init__(self, master, *, on_context) -> None:
        super().__init__(master)
        self._on_context = on_context
        self._rows: dict[str, dict[str, Any]] = {}
        self._selected = ""
        self._order: list[str] = []

        header = ttk.Frame(self)
        header.pack(fill=tk.X)
        for i, title in enumerate(("№", "Фото", "Артикул", "Название", "Заказ", "Статус")):
            header.grid_columnconfigure(i, minsize=self._MIN[i], weight=1 if i == self._STRETCH_COL else 0)
            ttk.Label(header, text=title, anchor="w").grid(row=0, column=i, sticky="ew", padx=4, pady=2)

        body = ttk.Frame(self)
        body.pack(fill=tk.BOTH, expand=True)
        self._canvas = tk.Canvas(body, highlightthickness=0, background="#fff")
        scroll = ttk.Scrollbar(body, orient=tk.VERTICAL, command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._inner = ttk.Frame(self._canvas)
        self._win = self._canvas.create_window((0, 0), window=self._inner, anchor="nw")
        for i, width in enumerate(self._MIN):
            self._inner.grid_columnconfigure(i, minsize=width, weight=1 if i == self._STRETCH_COL else 0)
        self._inner.bind("<Configure>", self._on_inner_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self._canvas.bind("<MouseWheel>", self._on_wheel)

    def _on_inner_configure(self, _event=None) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        self._canvas.itemconfigure(self._win, width=event.width)

    def _on_wheel(self, event) -> None:
        self._canvas.yview_scroll(int(-event.delta / 120), "units")
        return "break"

    def clear(self) -> None:
        for child in self._inner.winfo_children():
            child.destroy()
        self._rows = {}
        self._order = []
        self._selected = ""

    def exists(self, iid: str) -> bool:
        return str(iid) in self._rows

    def add_row(
        self,
        iid: str,
        *,
        seq: object,
        sku: str,
        name: str,
        order: str,
        status: str,
        photo: ImageTk.PhotoImage | None,
        tip: str,
    ) -> None:
        iid = str(iid)
        row = len(self._order)
        bg = "#ffffff" if row % 2 == 0 else "#f4f4f4"
        widgets: list[tk.Widget] = []
        seq_l = tk.Label(self._inner, text=str(seq or ""), anchor="center", background=bg)
        seq_l.grid(row=row, column=0, sticky="nsew", padx=4, pady=3)
        widgets.append(seq_l)
        img_hold = tk.Frame(self._inner, width=36, height=36, background=bg)
        img_hold.grid(row=row, column=1, sticky="nsew", padx=4, pady=3)
        img_hold.grid_propagate(False)
        img_l = tk.Label(img_hold, background=bg)
        img_l.place(relx=0.5, rely=0.5, anchor="center")
        if photo is not None:
            img_l.config(image=photo)
        widgets.append(img_hold)
        widgets.append(img_l)
        sku_l = tk.Label(self._inner, text=sku, anchor="w", background=bg)
        sku_l.grid(row=row, column=2, sticky="nsew", padx=4, pady=3)
        widgets.append(sku_l)
        name_l = tk.Label(self._inner, text=name, anchor="w", background=bg)
        name_l.grid(row=row, column=3, sticky="nsew", padx=4, pady=3)
        widgets.append(name_l)
        order_l = tk.Label(self._inner, text=order, anchor="w", background=bg)
        order_l.grid(row=row, column=4, sticky="nsew", padx=4, pady=3)
        widgets.append(order_l)
        status_l = tk.Label(self._inner, text=status, anchor="w", background=bg)
        status_l.grid(row=row, column=5, sticky="nsew", padx=4, pady=3)
        widgets.append(status_l)
        for widget in widgets:
            widget.bind("<Button-1>", lambda _e, key=iid: self.select(key))
            widget.bind("<Button-3>", lambda e, key=iid: self._context(e, key))
            widget.bind("<MouseWheel>", self._on_wheel)
        if tip:
            for widget in (sku_l, name_l, order_l, seq_l):
                self._bind_tip(widget, tip)
        self._rows[iid] = {
            "bg": bg,
            "widgets": widgets,
            "photo": img_l,
        }
        self._order.append(iid)

    def _bind_tip(self, widget: tk.Widget, text: str) -> None:
        state = {"after": None, "tip": None}

        def hide(_event=None) -> None:
            if state["after"]:
                try:
                    widget.after_cancel(state["after"])
                except Exception:
                    pass
                state["after"] = None
            if state["tip"] is not None:
                try:
                    state["tip"].destroy()
                except Exception:
                    pass
                state["tip"] = None

        def show() -> None:
            state["after"] = None
            if state["tip"] is not None:
                return
            tip = tk.Toplevel(widget)
            tip.wm_overrideredirect(True)
            tip.wm_geometry(f"+{widget.winfo_pointerx() + 12}+{widget.winfo_pointery() + 16}")
            tk.Label(
                tip,
                text=text,
                justify=tk.LEFT,
                background="#ffffe0",
                relief=tk.SOLID,
                borderwidth=1,
                padx=6,
                pady=4,
                wraplength=420,
            ).pack()
            state["tip"] = tip

        def schedule(_event=None) -> None:
            hide()
            state["after"] = widget.after(400, show)

        widget.bind("<Enter>", schedule, add="+")
        widget.bind("<Leave>", hide, add="+")

    def select(self, iid: str) -> None:
        iid = str(iid)
        self._selected = iid
        for key, row in self._rows.items():
            color = "#cde8ff" if key == iid else row["bg"]
            for widget in row["widgets"]:
                try:
                    widget.configure(background=color)
                except tk.TclError:
                    pass

    def _context(self, event, iid: str) -> None:
        self.select(iid)
        self._on_context(event, iid)

    def set_photo(self, iid: str, photo: ImageTk.PhotoImage | None) -> None:
        row = self._rows.get(str(iid))
        if not row:
            return
        label: tk.Label = row["photo"]
        if photo is None:
            label.config(image="", text="")
        else:
            label.config(image=photo, text="")


class FbsPackingMixin:
    """Вкладка «Упаковка FBS» для PackingApp."""

    def _init_fbs_state(self) -> None:
        self.fbs_jobs: list[dict] = []
        self.fbs_job: dict | None = None
        self.fbs_busy = False
        self._fbs_barcode_photo: ImageTk.PhotoImage | None = None
        self._fbs_active_photo: ImageTk.PhotoImage | None = None
        self._fbs_photo_cache: dict[str, ImageTk.PhotoImage] = {}
        self._fbs_line_urls: dict[str, str] = {}
        self._fbs_remaining_urls: dict[str, str] = {}
        self._fbs_selected_group: dict | None = None
        self._fbs_last_scan_code = ""
        self._fbs_last_scan_sku = ""
        self._fbs_last_scan_line_ids: list[int] = []
        self._fbs_prefetching_job_id: int | None = None
        self._fbs_jobs_filling = False
        self._fbs_paged_job_id: object | None = None
        cfg = load_config()
        skip_raw = str(cfg.get("fbs_skip_mp_confirm") or "").strip().lower()
        self.fbs_skip_mp_var = tk.BooleanVar(value=skip_raw in {"1", "true", "yes", "on"})

    def _build_fbs_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text="Упаковка FBS")
        style = ttk.Style(self)
        style.configure("FbsThumb.Treeview", rowheight=36, indent=0)

        toolbar = ttk.Frame(tab)
        toolbar.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(toolbar, text="Обновить", command=self.load_fbs_jobs).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Скачать ярлыки", command=self.download_fbs_labels).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        self.fbs_cache_hint = ttk.Label(toolbar, text="", foreground="#555")
        self.fbs_cache_hint.pack(side=tk.LEFT, padx=12)
        self.fbs_api_hint = ttk.Label(toolbar, text="", foreground="#a33")
        self.fbs_api_hint.pack(side=tk.LEFT, padx=12)

        panes = ttk.PanedWindow(tab, orient=tk.HORIZONTAL)
        panes.pack(fill=tk.BOTH, expand=True)
        self.fbs_panes = panes

        left = ttk.Frame(panes, width=_FBS_JOBS_MIN_PX)
        right = ttk.Frame(panes)
        panes.add(left, weight=1)
        panes.add(right, weight=5)
        try:
            panes.pane(left, minsize=_FBS_JOBS_MIN_PX, weight=1)
            panes.pane(right, minsize=480, weight=5)
        except tk.TclError:
            pass

        ttk.Label(left, text="Мои задания FBS").pack(anchor=tk.W)
        self.fbs_jobs_pager = PageBar(left, on_change=self._fbs_render_jobs_tree)
        self.fbs_jobs_pager.pack(side=tk.BOTTOM, fill=tk.X, pady=(4, 0))
        self.fbs_jobs_tree = ttk.Treeview(
            left,
            columns=("id", "status", "progress"),
            show="headings",
            height=16,
        )
        self.fbs_jobs_tree.heading("id", text="№")
        self.fbs_jobs_tree.heading("status", text="Статус")
        self.fbs_jobs_tree.heading("progress", text="Готово")
        self.fbs_jobs_tree.column("id", width=44, stretch=False, minwidth=36)
        self.fbs_jobs_tree.column("status", width=90, stretch=True, minwidth=72)
        self.fbs_jobs_tree.column("progress", width=64, stretch=False, minwidth=52)
        self.fbs_jobs_tree.pack(fill=tk.BOTH, expand=True)
        self.fbs_jobs_tree.bind("<<TreeviewSelect>>", self.on_fbs_job_select)
        _TreeHoverTip(self.fbs_jobs_tree, self._fbs_jobs_tip)

        header = ttk.Frame(right)
        header.pack(fill=tk.X)
        self.fbs_job_title = ttk.Label(header, text="Выберите задание", font=("", 11, "bold"))
        self.fbs_job_title.pack(side=tk.LEFT)
        self.fbs_job_stats = ttk.Label(header, text="")
        self.fbs_job_stats.pack(side=tk.RIGHT)

        active = ttk.LabelFrame(right, text="Активная строка (наклеить ярлык заказа)", padding=8)
        active.pack(fill=tk.X, pady=(8, 8))
        active_inner = ttk.Frame(active)
        active_inner.pack(fill=tk.X)
        self.fbs_active_image_frame = tk.Frame(
            active_inner,
            width=_FBS_ACTIVE_IMG,
            height=_FBS_ACTIVE_IMG,
            background="#f3f3f3",
            highlightthickness=1,
            highlightbackground="#ccc",
        )
        self.fbs_active_image_frame.pack(side=tk.LEFT, padx=(0, 10))
        self.fbs_active_image_frame.pack_propagate(False)
        self.fbs_active_image = tk.Label(self.fbs_active_image_frame, background="#f3f3f3")
        self.fbs_active_image.pack(fill=tk.BOTH, expand=True)
        active_text = ttk.Frame(active_inner)
        active_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.fbs_active_var = tk.StringVar(value="Нет активной строки — пикните товар")
        ttk.Label(active_text, textvariable=self.fbs_active_var, wraplength=420).pack(anchor=tk.W)
        active_btns = ttk.Frame(active_text)
        active_btns.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(active_btns, text="Перепечатать ярлык", command=self.fbs_reprint_active).pack(side=tk.LEFT)
        ttk.Button(active_btns, text="Отменить печать", command=self.fbs_cancel_active).pack(
            side=tk.LEFT, padx=6
        )

        self.fbs_cis_hint = ttk.Label(right, text="", foreground="#a33", wraplength=520)
        self.fbs_cis_hint.pack(anchor=tk.W, pady=(0, 4))

        scan_row = ttk.Frame(right)
        scan_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(scan_row, text="Сканер").pack(side=tk.LEFT)
        self.fbs_scan_var = tk.StringVar()
        self.fbs_scan_entry = ttk.Entry(scan_row, textvariable=self.fbs_scan_var)
        self.fbs_scan_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        self.fbs_scan_entry.bind("<Return>", self.on_fbs_scan_enter)
        ttk.Button(scan_row, text="OK", command=self.on_fbs_scan_enter).pack(side=tk.LEFT)

        mode_row = ttk.Frame(right)
        mode_row.pack(fill=tk.X, pady=(0, 4))
        self.fbs_manual_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            mode_row,
            text="Ручная сборка (остаток + ШК с экрана)",
            variable=self.fbs_manual_var,
            command=self._fbs_toggle_manual,
        ).pack(side=tk.LEFT)
        self.fbs_batch_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            mode_row,
            text="Печатать все ярлыки SKU сразу",
            variable=self.fbs_batch_var,
        ).pack(side=tk.LEFT, padx=(16, 0))
        ttk.Checkbutton(
            mode_row,
            text="Без подтверждения ШК с МП",
            variable=self.fbs_skip_mp_var,
            command=self._fbs_save_skip_mp_setting,
        ).pack(side=tk.LEFT, padx=(16, 0))

        self.fbs_manual_frame = ttk.Frame(right)
        # packed when manual on

        remaining_wrap = ttk.Frame(self.fbs_manual_frame)
        remaining_wrap.pack(fill=tk.BOTH, expand=True)
        left_rem = ttk.Frame(remaining_wrap)
        left_rem.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ttk.Label(left_rem, text="Остаток к сборке").pack(anchor=tk.W)
        self.fbs_remaining_tree = ttk.Treeview(
            left_rem,
            columns=("sku", "name", "qty", "barcode"),
            show="tree headings",
            height=10,
            style="FbsThumb.Treeview",
        )
        self.fbs_remaining_tree.heading("#0", text="Фото")
        self.fbs_remaining_tree.heading("sku", text="Артикул")
        self.fbs_remaining_tree.heading("name", text="Название")
        self.fbs_remaining_tree.heading("qty", text="Ост.")
        self.fbs_remaining_tree.heading("barcode", text="ШК")
        self.fbs_remaining_tree.column("#0", width=44, stretch=False, minwidth=44)
        self.fbs_remaining_tree.column("sku", width=90, stretch=False)
        self.fbs_remaining_tree.column("name", width=180)
        self.fbs_remaining_tree.column("qty", width=46, stretch=False)
        self.fbs_remaining_tree.column("barcode", width=110, stretch=False)
        self.fbs_remaining_tree.pack(fill=tk.BOTH, expand=True)
        self.fbs_remaining_tree.bind("<<TreeviewSelect>>", self.on_fbs_remaining_select)
        self.fbs_remaining_tree.bind("<Double-1>", self.on_fbs_remaining_pick)
        _TreeHoverTip(self.fbs_remaining_tree, self._fbs_remaining_tip)

        right_rem = ttk.Frame(remaining_wrap)
        right_rem.pack(side=tk.LEFT, fill=tk.Y, padx=(10, 0))
        ttk.Label(right_rem, text="ШК для сканера").pack(anchor=tk.W)
        barcode_box = tk.Frame(
            right_rem,
            width=_FBS_BARCODE_W,
            height=_FBS_BARCODE_H,
            background="#fff",
            highlightthickness=1,
            highlightbackground="#ccc",
        )
        barcode_box.pack(pady=6)
        barcode_box.pack_propagate(False)
        self.fbs_barcode_canvas = tk.Label(barcode_box, background="#fff")
        self.fbs_barcode_canvas.pack(fill=tk.BOTH, expand=True)
        ttk.Button(right_rem, text="Взять (тап)", command=self.on_fbs_remaining_pick).pack(fill=tk.X)

        self.fbs_lines_frame = ttk.LabelFrame(right, text="Строки задания", padding=6)
        self.fbs_lines_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.fbs_lines_pager = PageBar(self.fbs_lines_frame, on_change=self._fbs_fill_lines_table)
        self.fbs_lines_pager.pack(side=tk.BOTTOM, fill=tk.X, pady=(4, 0))
        self.fbs_lines_table = _FbsLinesTable(
            self.fbs_lines_frame,
            on_context=self._fbs_lines_context_menu,
        )
        self.fbs_lines_table.pack(fill=tk.BOTH, expand=True)

    def _fbs_save_skip_mp_setting(self) -> None:
        cfg = load_config()
        cfg["fbs_skip_mp_confirm"] = "1" if self.fbs_skip_mp_var.get() else "0"
        save_config(cfg)
        if self.fbs_job:
            self._fbs_render_job()

    def _fbs_jobs_tip(self, row: str, col: str) -> str:
        values = self.fbs_jobs_tree.item(row, "values") or ()
        if not values:
            return ""
        mapping = {"#1": 0, "#2": 1, "#3": 2}
        idx = mapping.get(col)
        if idx is None or idx >= len(values):
            return " · ".join(str(v) for v in values)
        return str(values[idx] or "")

    def _fbs_remaining_tip(self, row: str, col: str) -> str:
        values = self.fbs_remaining_tree.item(row, "values") or ()
        if not values:
            return ""
        mapping = {"#1": 0, "#2": 1, "#3": 2, "#4": 3}
        idx = mapping.get(col)
        if idx is None:
            return " · ".join(str(v) for v in values if v)
        if idx >= len(values):
            return ""
        return str(values[idx] or "")

    def _fbs_thumb_pil(self, url: str, size: int) -> Image.Image | None:
        raw = str(url or "").strip()
        if not raw:
            return None
        data = get_cached_bytes(raw)
        if not data:
            self._fbs_ensure_image(raw)
            return None
        try:
            img = Image.open(io.BytesIO(data))
            img.thumbnail((size, size))
            return img.convert("RGB")
        except Exception:
            return None

    def _fbs_thumb_photo(self, url: str, size: int) -> ImageTk.PhotoImage | None:
        raw = str(url or "").strip()
        if not raw:
            return None
        key = f"{size}:{raw}"
        cached = self._fbs_photo_cache.get(key)
        if cached is not None:
            return cached
        img = self._fbs_thumb_pil(raw, size)
        if img is None:
            return None
        photo = ImageTk.PhotoImage(img)
        self._fbs_photo_cache[key] = photo
        return photo

    def _fbs_ensure_image(self, url: str) -> None:
        raw = str(url or "").strip()
        if not begin_fetch(raw):
            return

        def worker():
            return raw, fetch_url(raw)

        def on_ok(result: tuple[str, bytes | None]) -> None:
            _url, data = result
            if data:
                self._fbs_apply_loaded_images()

        self._run_task(worker, on_ok, lambda _exc: None)

    def _fbs_apply_loaded_images(self) -> None:
        for iid, url in list(self._fbs_line_urls.items()):
            if not self.fbs_lines_table.exists(iid):
                continue
            self.fbs_lines_table.set_photo(iid, self._fbs_thumb_photo(url, _FBS_LIST_IMG))
        for iid, url in list(self._fbs_remaining_urls.items()):
            if not self.fbs_remaining_tree.exists(iid):
                continue
            photo = self._fbs_thumb_photo(url, _FBS_LIST_IMG)
            if photo is not None:
                self.fbs_remaining_tree.item(iid, image=photo)
        self._fbs_refresh_active_image()
        catalog_urls = getattr(self, "_catalog_image_urls", None)
        catalog_tree = getattr(self, "catalog_tree", None)
        if isinstance(catalog_urls, dict) and catalog_tree is not None:
            for iid, url in list(catalog_urls.items()):
                if not catalog_tree.exists(iid):
                    continue
                photo = self._fbs_thumb_photo(url, _FBS_LIST_IMG)
                if photo is not None:
                    catalog_tree.item(iid, image=photo)

    def _fbs_set_active_image(self, url: str) -> None:
        photo = self._fbs_thumb_photo(url, _FBS_ACTIVE_IMG) if url else None
        self._fbs_active_photo = photo
        if photo is not None:
            self.fbs_active_image.config(image=photo, text="")
        else:
            self.fbs_active_image.config(image="", text="")
        if url and photo is None:
            self._fbs_ensure_image(url)

    def _fbs_refresh_active_image(self) -> None:
        job = self.fbs_job or {}
        active_lines = job.get("active_lines") or ([] if not job.get("active_line") else [job.get("active_line")])
        if active_lines:
            self._fbs_set_active_image(str(active_lines[0].get("image_url") or ""))
            return
        last = self._fbs_last_picked_line()
        if self._fbs_skip_mp_enabled() and last:
            self._fbs_set_active_image(str(last.get("image_url") or ""))
            return
        self._fbs_set_active_image("")

    def _fbs_last_picked_line(self) -> dict | None:
        job = self.fbs_job or {}
        ids = {int(x) for x in self._fbs_last_scan_line_ids if str(x).strip()}
        if not ids:
            return None
        for line in job.get("lines") or []:
            try:
                if int(line.get("id")) in ids:
                    return line
            except (TypeError, ValueError):
                continue
        return None

    def _fbs_lines_context_menu(self, event, iid: str) -> None:
        job = self.fbs_job or {}
        line = next((item for item in (job.get("lines") or []) if str(item.get("id")) == str(iid)), None)
        if line is None:
            return
        status = str(line.get("status") or "")
        menu = tk.Menu(self, tearoff=0)
        if status in {"printed", "done"}:
            menu.add_command(
                label="Статус: в сборке",
                command=lambda: self._fbs_set_line_status(int(iid), "pending"),
            )
        else:
            menu.add_command(
                label="Статус: готово",
                command=lambda: self._fbs_set_line_status(int(iid), "done"),
            )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _fbs_set_line_status(self, line_id: int, status: str) -> None:
        job = self.fbs_job
        if not job:
            return
        if not self._fbs_require_api():
            return
        job_id = int(job["id"])

        def worker():
            return self.client.fbs_set_line_status(job_id, line_id, status)

        def on_ok(payload: dict) -> None:
            self.fbs_job = payload.get("job") or self.fbs_job
            self._fbs_render_job()
            self.set_status(f"Статус строки: {_fbs_line_status_ru(status)}")
            self._fbs_focus_scan()

        self._fbs_run(worker, on_ok, status="Смена статуса...")

    def _fbs_scan_key(self, code: str) -> str:
        return str(code or "").strip().casefold()

    def _fbs_skip_mp_enabled(self) -> bool:
        return bool(self.fbs_skip_mp_var.get())

    def _fbs_update_cache_hint(self) -> None:
        job = self.fbs_job or {}
        jid = job.get("id")
        ids = job_line_ids(job)
        if not jid or not ids:
            self.fbs_cache_hint.config(text="")
            return
        have, total = cached_count(int(jid), ids)
        if have >= total:
            self.fbs_cache_hint.config(text=f"Ярлыки: {have}/{total} на диске", foreground="#2a7")
        elif have:
            self.fbs_cache_hint.config(text=f"Ярлыки: {have}/{total} на диске", foreground="#a80")
        else:
            self.fbs_cache_hint.config(text="Ярлыки не скачаны", foreground="#a33")

    def _fbs_resolve_line_pdfs(
        self,
        job_id: int,
        line_ids: list[int],
        payload_pdfs: list[str] | None = None,
    ) -> list[bytes]:
        pdfs: list[bytes] = []
        for index, line_id in enumerate(line_ids):
            cached = get_line_pdf(job_id, line_id)
            if cached:
                pdfs.append(cached)
                continue
            raw = ""
            if payload_pdfs and index < len(payload_pdfs):
                raw = str(payload_pdfs[index] or "")
            if raw:
                pdf = base64.b64decode(raw)
                put_line_pdf(job_id, line_id, pdf)
                pdfs.append(pdf)
                continue
            pdf = self.client.fbs_download_line_pdf(job_id, line_id)
            put_line_pdf(job_id, line_id, pdf)
            pdfs.append(pdf)
        return pdfs

    def _fbs_prefetch_labels(self, job: dict, *, interactive: bool) -> None:
        job_id = int(job.get("id") or 0)
        if not job_id:
            return
        if self._fbs_prefetching_job_id == job_id and not interactive:
            return
        if job_labels_ready(job) and not interactive:
            self._fbs_update_cache_hint()
            return
        if interactive:
            if self.fbs_busy:
                return
            self.fbs_busy = True
            self.set_status("Скачивание ярлыков...")

        self._fbs_prefetching_job_id = job_id

        def worker():
            zip_bytes = self.client.fbs_download_line_labels_zip(job_id)
            return save_zip(job_id, zip_bytes)

        def on_ok(saved: int) -> None:
            if interactive:
                self.fbs_busy = False
            if self._fbs_prefetching_job_id == job_id:
                self._fbs_prefetching_job_id = None
            self._fbs_update_cache_hint()
            self.set_status(f"Ярлыки сохранены локально: {saved}")
            if interactive:
                self._fbs_focus_scan()

        def on_err(exc: Exception) -> None:
            if interactive:
                self.fbs_busy = False
            if self._fbs_prefetching_job_id == job_id:
                self._fbs_prefetching_job_id = None
            if interactive:
                if isinstance(exc, AuthError):
                    messagebox.showerror("Сессия", str(exc))
                    self.on_close()
                    return
                if isinstance(exc, ApiError):
                    messagebox.showwarning("FBS", str(exc))
                    self.set_status(str(exc))
                    self._fbs_focus_scan()
                    return
                self._background_error(exc)
                self._fbs_focus_scan()
            else:
                self.fbs_cache_hint.config(text="Не удалось скачать ярлыки", foreground="#a33")

        self._run_task(worker, on_ok, on_err)

    def download_fbs_labels(self) -> None:
        job = self.fbs_job
        if not job:
            messagebox.showwarning("FBS", "Сначала откройте задание")
            return
        if not self._fbs_require_api():
            return
        self._fbs_prefetch_labels(job, interactive=True)

    def _fbs_reprint_last_line(self, job_id: int) -> None:
        line_ids = list(self._fbs_last_scan_line_ids)
        if not line_ids:
            messagebox.showwarning("FBS", "Нет последней строки для перепечатки")
            return

        def worker():
            return self._fbs_resolve_line_pdfs(job_id, [int(line_id) for line_id in line_ids])

        def on_ok(pdfs: list[bytes]) -> None:
            for pdf in pdfs:
                print_pdf(pdf, **self._label_print_profile())
            self.set_status(f"Ярлык перепечатан ({len(pdfs)})")
            self._fbs_focus_scan()

        self._fbs_run(worker, on_ok, status="Перепечатка...")

    def _fbs_sku_has_pending(self, sku: str) -> bool:
        want = str(sku or "").strip().casefold()
        if not want:
            return False
        job = self.fbs_job or {}
        for line in job.get("lines") or []:
            if str(line.get("sku") or "").strip().casefold() != want:
                continue
            if str(line.get("status") or "") == "pending":
                return True
        for group in job.get("remaining_groups") or []:
            if str(group.get("sku") or "").strip().casefold() != want:
                continue
            try:
                qty = int(group.get("quantity") or 0)
            except (TypeError, ValueError):
                qty = 0
            if qty > 0:
                return True
        return False

    def _fbs_auto_close_lines(self, job_id: int, lines: list[dict]) -> dict:
        payload: dict | None = None
        for line in lines:
            if not line or not line.get("id"):
                continue
            payload = self.client.fbs_close_line(job_id, int(line["id"]))
        return payload or {}

    def _fbs_toggle_manual(self) -> None:
        if self.fbs_manual_var.get():
            self.fbs_manual_frame.pack(fill=tk.BOTH, expand=True, before=self.fbs_lines_frame)
            self._fbs_render_remaining()
        else:
            self.fbs_manual_frame.pack_forget()
        self._fbs_focus_scan()

    def _fbs_focus_scan(self) -> None:
        try:
            self.fbs_scan_entry.focus_set()
        except Exception:
            pass

    def _fbs_require_api(self) -> bool:
        if self.client.api_ok:
            self.fbs_api_hint.config(text=f"API: {self.client.api_url}", foreground="#2a7")
            return True
        self.fbs_api_hint.config(
            text=self.client.api_error or "Нет сессии API — нужен run_api.py",
            foreground="#a33",
        )
        return False

    def load_fbs_jobs(self) -> None:
        if not self._fbs_require_api():
            messagebox.showwarning(
                "API упаковщиков",
                self.client.api_error
                or "Запустите python run_api.py (порт 8766) и укажите адрес API в настройках.",
            )
            return

        def worker():
            return self.client.fbs_my_jobs()

        def on_ok(jobs: list[dict]) -> None:
            self.fbs_jobs = jobs
            selected = str((self.fbs_job or {}).get("id") or "")
            if selected:
                for index, job in enumerate(jobs):
                    if str(job.get("id") or "") == selected:
                        self.fbs_jobs_pager.show_index(index)
                        break
            self._fbs_render_jobs_tree()
            self.set_status(f"FBS заданий: {len(jobs)}")
            self._fbs_focus_scan()

        self.set_status("Загрузка FBS...")
        self._run_task(worker, on_ok)

    def _fbs_render_jobs_tree(self) -> None:
        jobs = self.fbs_jobs
        selected = str((self.fbs_job or {}).get("id") or "")
        visible = self.fbs_jobs_pager.slice_items(jobs)
        self._fbs_jobs_filling = True
        try:
            self.fbs_jobs_tree.delete(*self.fbs_jobs_tree.get_children())
            for job in visible:
                jid = str(job.get("id") or "")
                done = job.get("line_done", 0)
                total = job.get("line_total", 0)
                self.fbs_jobs_tree.insert(
                    "",
                    tk.END,
                    iid=jid,
                    values=(jid, _fbs_job_status_ru(job.get("status")), f"{done}/{total}"),
                )
            if selected and self.fbs_jobs_tree.exists(selected):
                self.fbs_jobs_tree.selection_set(selected)
                self.fbs_jobs_tree.focus(selected)
            else:
                for item in self.fbs_jobs_tree.selection():
                    self.fbs_jobs_tree.selection_remove(item)
        finally:
            self._fbs_jobs_filling = False

    def on_fbs_job_select(self, _event=None) -> None:
        if self._fbs_jobs_filling:
            return
        selected = self.fbs_jobs_tree.selection()
        if not selected:
            return
        job_id = int(selected[0])

        def worker():
            return self.client.fbs_open_job(job_id)

        def on_ok(job: dict) -> None:
            self.fbs_job = job
            self._fbs_render_job()
            self.set_status(f"FBS задание #{job.get('id')}")
            self._fbs_focus_scan()
            self._fbs_prefetch_labels(job, interactive=False)

        self.set_status("Открытие FBS...")
        self._run_task(worker, on_ok)

    def _fbs_render_job(self) -> None:
        job = self.fbs_job or {}
        jid = job.get("id")
        self.fbs_job_title.config(text=f"Задание #{jid}" if jid else "Выберите задание")
        done = job.get("line_done", 0)
        total = job.get("line_total", 0)
        pending = job.get("line_pending", job.get("remaining", 0))
        printed = job.get("line_printed", 0)
        self.fbs_job_stats.config(text=f"готово {done}/{total} · осталось {pending} · в печати {printed}")
        self._fbs_update_cache_hint()
        if job.get("require_cis"):
            self.fbs_cis_hint.config(
                text="Обязательная маркировка ЧЗ: для товаров с GTIN пикайте КИЗ (Data Matrix), не обычный ШК."
            )
        else:
            self.fbs_cis_hint.config(text="")

        active = job.get("active_line")
        active_lines = job.get("active_lines") or ([] if not active else [active])
        if active_lines:
            first = active_lines[0]
            cis_note = " · КИЗ" if first.get("has_cis") else ""
            if len(active_lines) == 1:
                self.fbs_active_var.set(
                    f"SKU {first.get('sku')} · {first.get('product_name') or ''} · "
                    f"заказ {first.get('order_display') or first.get('order_id')} · "
                    f"строка #{first.get('id')}{cis_note}"
                )
            else:
                orders = ", ".join(
                    str(item.get("order_display") or item.get("order_id") or "?")
                    for item in active_lines
                )
                self.fbs_active_var.set(
                    f"SKU {first.get('sku')} · {first.get('product_name') or ''} · "
                    f"в печати {len(active_lines)} шт. · заказы: {orders} · "
                    f"пропикайте ярлыки подряд"
                )
            self._fbs_set_active_image(str(first.get("image_url") or ""))
        else:
            last = self._fbs_last_picked_line()
            if self._fbs_skip_mp_enabled() and last:
                cis_note = " · КИЗ" if last.get("has_cis") else ""
                self.fbs_active_var.set(
                    f"SKU {last.get('sku')} · {last.get('product_name') or ''} · "
                    f"заказ {last.get('order_display') or last.get('order_id')} · "
                    f"строка #{last.get('id')}{cis_note}"
                )
                self._fbs_set_active_image(str(last.get("image_url") or ""))
            else:
                hint = "пикните КИЗ или товар" if job.get("require_cis") else "пикните товар"
                if self._fbs_skip_mp_enabled():
                    hint += " (без подтверждения ШК МП)"
                self.fbs_active_var.set(f"Нет активной строки — {hint}")
                self._fbs_set_active_image("")

        if getattr(self, "_fbs_paged_job_id", None) != jid:
            self.fbs_lines_pager.reset()
            self._fbs_paged_job_id = jid
        if active_lines:
            active_id = str(active_lines[0].get("id") or "")
            for index, line in enumerate(job.get("lines") or []):
                if str(line.get("id") or "") == active_id:
                    self.fbs_lines_pager.show_index(index)
                    break
        self._fbs_fill_lines_table()
        if self.fbs_manual_var.get():
            self._fbs_render_remaining()

    def _fbs_fill_lines_table(self) -> None:
        job = self.fbs_job or {}
        lines = list(job.get("lines") or [])
        visible = self.fbs_lines_pager.slice_items(lines)
        self.fbs_lines_table.clear()
        self._fbs_line_urls = {}
        for line in visible:
            iid = str(line.get("id"))
            url = str(line.get("image_url") or "")
            self._fbs_line_urls[iid] = url
            sku = str(line.get("sku") or "")
            name = str(line.get("product_name") or "")
            order = str(line.get("order_display") or line.get("order_id") or "")
            tip = " · ".join(part for part in (sku, name, order) if part)
            self.fbs_lines_table.add_row(
                iid,
                seq=line.get("seq", ""),
                sku=sku,
                name=name,
                order=order,
                status=_fbs_line_status_ru(line.get("status")),
                photo=self._fbs_thumb_photo(url, _FBS_LIST_IMG) if url else None,
                tip=tip,
            )

    def _fbs_render_remaining(self) -> None:
        job = self.fbs_job or {}
        groups = job.get("remaining_groups") or []
        self.fbs_remaining_tree.delete(*self.fbs_remaining_tree.get_children())
        self._fbs_remaining_urls = {}
        for idx, group in enumerate(groups):
            iid = str(idx)
            url = str(group.get("image_url") or "")
            self._fbs_remaining_urls[iid] = url
            photo = self._fbs_thumb_photo(url, _FBS_LIST_IMG) if url else None
            kwargs: dict[str, Any] = {
                "iid": iid,
                "values": (
                    group.get("sku") or "",
                    group.get("name") or "",
                    group.get("quantity") or 0,
                    group.get("barcode") or "",
                ),
            }
            if photo is not None:
                kwargs["image"] = photo
            self.fbs_remaining_tree.insert("", tk.END, **kwargs)
        if groups:
            self.fbs_remaining_tree.selection_set("0")
            self.fbs_remaining_tree.focus("0")
            self.on_fbs_remaining_select()
        else:
            self._fbs_selected_group = None
            self._fbs_show_barcode_image("")

    def on_fbs_remaining_select(self, _event=None) -> None:
        selected = self.fbs_remaining_tree.selection()
        job = self.fbs_job or {}
        groups = job.get("remaining_groups") or []
        if not selected:
            self._fbs_selected_group = None
            self._fbs_show_barcode_image("")
            return
        try:
            idx = int(selected[0])
        except ValueError:
            return
        if idx < 0 or idx >= len(groups):
            return
        group = groups[idx]
        self._fbs_selected_group = group
        self._fbs_show_barcode_image(str(group.get("barcode") or ""))

    def _fbs_show_barcode_image(self, code: str) -> None:
        code = (code or "").strip()
        box_w = max(8, _FBS_BARCODE_W - 10)
        box_h = max(8, _FBS_BARCODE_H - 10)
        if not code:
            self.fbs_barcode_canvas.config(image="", text="нет ШК")
            self._fbs_barcode_photo = None
            return
        try:
            img = render_code128_image(code)
            img.thumbnail((box_w, box_h))
            photo = ImageTk.PhotoImage(img)
            self._fbs_barcode_photo = photo
            self.fbs_barcode_canvas.config(image=photo, text="")
        except Exception:
            self.fbs_barcode_canvas.config(image="", text=code)
            self._fbs_barcode_photo = None

    def on_fbs_remaining_pick(self, _event=None) -> None:
        group = self._fbs_selected_group
        job = self.fbs_job
        if not job or not group:
            return
        job_id = int(job["id"])
        sku = str(group.get("sku") or "")
        raw_pid = group.get("product_id")
        product_id = int(raw_pid) if raw_pid not in (None, "") else None
        include_pdf = not job_labels_ready(job)
        batch = bool(self.fbs_batch_var.get())
        auto_close = self._fbs_skip_mp_enabled()

        def worker():
            return self.client.fbs_pick_sku(
                job_id,
                sku=sku,
                product_id=product_id,
                batch=batch,
                include_pdf=include_pdf,
                auto_close=auto_close,
            )

        scan_key = sku.casefold() if sku else str(product_id or "")
        self._fbs_handle_allocate(worker, status="Выделение SKU...", scan_code=scan_key)

    def on_fbs_scan_enter(self, _event=None) -> None:
        code = self.fbs_scan_var.get().strip()
        self.fbs_scan_var.set("")
        if not code:
            return
        job = self.fbs_job
        if not job:
            messagebox.showwarning("FBS", "Сначала откройте задание")
            return
        job_id = int(job["id"])
        scan_key = self._fbs_scan_key(code)
        active = job.get("active_line") or (job.get("active_lines") or [None])[0]

        if active and not self._fbs_skip_mp_enabled():
            def worker():
                return self.client.fbs_scan_label(job_id, code)

            def on_ok(payload: dict) -> None:
                self.fbs_job = payload.get("job") or self.fbs_job
                self._fbs_render_job()
                left = len((self.fbs_job or {}).get("active_lines") or [])
                if left:
                    self.set_status(f"Ярлык принят · осталось пропикать: {left}")
                else:
                    self.set_status("Ярлык принят, строка закрыта")
                self._fbs_focus_scan()

            self._fbs_run(worker, on_ok, status="Сверка ярлыка...")
            return

        if (
            self._fbs_skip_mp_enabled()
            and scan_key
            and scan_key == self._fbs_last_scan_code
            and self._fbs_last_scan_line_ids
            and not self._fbs_sku_has_pending(self._fbs_last_scan_sku)
        ):
            if messagebox.askyesno("FBS", "Распечатать заново?"):
                self._fbs_reprint_last_line(job_id)
            self._fbs_focus_scan()
            return

        batch = bool(self.fbs_batch_var.get())
        include_pdf = not job_labels_ready(job)
        auto_close = self._fbs_skip_mp_enabled()

        def worker_product():
            return self.client.fbs_scan_product(
                job_id,
                code,
                batch=batch,
                include_pdf=include_pdf,
                auto_close=auto_close,
            )

        self._fbs_handle_allocate(worker_product, status="Пик товара...", scan_code=scan_key)

    def _fbs_handle_allocate(
        self,
        worker: Callable[[], dict],
        *,
        status: str,
        scan_code: str = "",
    ) -> None:
        job = self.fbs_job or {}
        job_id = int(job["id"]) if job.get("id") else 0
        skip_mp = self._fbs_skip_mp_enabled()
        print_profile = self._label_print_profile()

        def wrapped_worker() -> tuple[dict, list[dict], int, Exception | None, Exception | None]:
            payload = worker()
            lines = payload.get("lines") or ([payload.get("line")] if payload.get("line") else [])
            payload_pdfs = list(payload.get("pdfs_base64") or [])
            if not payload_pdfs and payload.get("pdf_base64"):
                payload_pdfs = [payload.get("pdf_base64")]
            line_ids = [int(item["id"]) for item in lines if item and item.get("id")]
            pdfs = self._fbs_resolve_line_pdfs(job_id, line_ids, payload_pdfs) if job_id else []
            printed = 0
            print_error: Exception | None = None
            close_error: Exception | None = None
            try:
                for pdf in pdfs:
                    print_pdf(pdf, **print_profile)
                    printed += 1
            except Exception as exc:
                print_error = exc
            need_close = [
                item
                for item in lines
                if item and item.get("id") and str(item.get("status") or "") != "done"
            ]
            if skip_mp and job_id and need_close:
                try:
                    payload = self._fbs_auto_close_lines(job_id, need_close) or payload
                except Exception as exc:
                    close_error = exc
            return payload, lines, printed, print_error, close_error

        def on_ok(
            result: tuple[dict, list[dict], int, Exception | None, Exception | None],
        ) -> None:
            payload, lines, printed, print_error, close_error = result
            allocate_line = lines[0] if lines else {}
            self.fbs_job = payload.get("job") or self.fbs_job
            closed_lines = payload.get("lines") or (
                [payload.get("line")] if payload.get("line") else []
            )
            line = (closed_lines[0] if closed_lines else None) or allocate_line or {}
            if skip_mp:
                printed_ids = [
                    int(item["id"])
                    for item in (lines or [])
                    if item and item.get("id")
                ]
                if printed_ids:
                    self._fbs_last_scan_code = scan_code or self._fbs_scan_key(str((line or {}).get("sku") or ""))
                    self._fbs_last_scan_sku = str((line or {}).get("sku") or "")
                    self._fbs_last_scan_line_ids = printed_ids
            self._fbs_render_job()
            if print_error is not None:
                messagebox.showerror("Печать", str(print_error))
                self.set_status(
                    f"Получено ярлыков: {printed}. Можно Перепечатать"
                )
                self._fbs_focus_scan()
                return
            if close_error is not None:
                messagebox.showwarning("FBS", f"Ярлык напечатан, но строка не закрыта: {close_error}")
            sku = line.get("sku") if line else ""
            cis_note = " · КИЗ записан" if line and line.get("has_cis") else ""
            if printed > 1:
                if skip_mp:
                    self.set_status(f"Готово · SKU {sku} · ярлыков {printed} · пикайте следующий")
                else:
                    self.set_status(f"Напечатано ярлыков: {printed} · SKU {sku} · пропикайте ярлыки подряд")
            elif printed == 1:
                if skip_mp:
                    self.set_status(
                        f"Готово · SKU {line.get('sku')} · заказ {line.get('order_id')}{cis_note} · пикайте следующий"
                    )
                else:
                    self.set_status(
                        f"Напечатан ярлык · SKU {line.get('sku')} · заказ {line.get('order_id')}{cis_note}"
                    )
            else:
                self.set_status("Строка выделена")
            self._fbs_focus_scan()

        self._fbs_run(wrapped_worker, on_ok, status=status)

    def _fbs_active_lines(self) -> list[dict]:
        job = self.fbs_job or {}
        lines = job.get("active_lines")
        if isinstance(lines, list) and lines:
            return [item for item in lines if isinstance(item, dict)]
        active = job.get("active_line")
        return [active] if isinstance(active, dict) else []

    def _fbs_active_line(self) -> dict | None:
        lines = self._fbs_active_lines()
        return lines[0] if lines else None

    def fbs_reprint_active(self) -> None:
        job = self.fbs_job
        actives = self._fbs_active_lines()
        if not job or not actives:
            messagebox.showwarning("FBS", "Нет активной строки")
            return
        job_id = int(job["id"])
        line_ids = [int(item["id"]) for item in actives]

        def worker():
            return self._fbs_resolve_line_pdfs(job_id, line_ids)

        def on_ok(pdfs: list[bytes]) -> None:
            for pdf in pdfs:
                print_pdf(pdf, **self._label_print_profile())
            self.set_status(f"На повторную печать: {len(pdfs)} ярл.")
            self._fbs_focus_scan()

        self._fbs_run(worker, on_ok, status="Перепечатка...")

    def fbs_cancel_active(self) -> None:
        job = self.fbs_job
        active = self._fbs_active_line()
        if not job or not active:
            messagebox.showwarning("FBS", "Нет активной строки")
            return
        job_id = int(job["id"])
        line_id = int(active["id"])

        def worker():
            return self.client.fbs_cancel_print(job_id, line_id)

        def on_ok(payload: dict) -> None:
            self.fbs_job = payload.get("job") or self.fbs_job
            self._fbs_render_job()
            self.set_status("Печать отменена, КИЗ сброшен, строки снова в сборке")
            self._fbs_focus_scan()

        self._fbs_run(worker, on_ok, status="Отмена...")

    def _fbs_run(self, worker, on_ok, *, status: str) -> None:
        if self.fbs_busy:
            return
        self.fbs_busy = True
        self.set_status(status)

        def wrapped_ok(result: Any) -> None:
            self.fbs_busy = False
            on_ok(result)

        def wrapped_err(exc: Exception) -> None:
            self.fbs_busy = False
            if isinstance(exc, AuthError):
                messagebox.showerror("Сессия", str(exc))
                self.on_close()
                return
            if isinstance(exc, ApiError):
                messagebox.showwarning("FBS", str(exc))
                self.set_status(str(exc))
                self._fbs_focus_scan()
                return
            self._background_error(exc)
            self._fbs_focus_scan()

        self._run_task(worker, wrapped_ok, wrapped_err)
