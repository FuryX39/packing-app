from __future__ import annotations

import base64
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable

from PIL import Image, ImageTk

from api_client import ApiError, AuthError
from local_print import print_pdf, render_code128_image


class FbsPackingMixin:
    """Вкладка «Упаковка FBS» для PackingApp."""

    def _init_fbs_state(self) -> None:
        self.fbs_jobs: list[dict] = []
        self.fbs_job: dict | None = None
        self.fbs_busy = False
        self._fbs_barcode_photo: ImageTk.PhotoImage | None = None
        self._fbs_selected_group: dict | None = None

    def _build_fbs_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text="Упаковка FBS")

        toolbar = ttk.Frame(tab)
        toolbar.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(toolbar, text="Обновить", command=self.load_fbs_jobs).pack(side=tk.LEFT)
        self.fbs_api_hint = ttk.Label(toolbar, text="", foreground="#a33")
        self.fbs_api_hint.pack(side=tk.LEFT, padx=12)

        panes = ttk.PanedWindow(tab, orient=tk.HORIZONTAL)
        panes.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(panes)
        right = ttk.Frame(panes)
        panes.add(left, weight=1)
        panes.add(right, weight=3)

        ttk.Label(left, text="Мои задания FBS").pack(anchor=tk.W)
        self.fbs_jobs_tree = ttk.Treeview(
            left,
            columns=("id", "status", "progress"),
            show="headings",
            height=16,
        )
        self.fbs_jobs_tree.heading("id", text="№")
        self.fbs_jobs_tree.heading("status", text="Статус")
        self.fbs_jobs_tree.heading("progress", text="Готово")
        self.fbs_jobs_tree.column("id", width=50, stretch=False)
        self.fbs_jobs_tree.column("status", width=90, stretch=False)
        self.fbs_jobs_tree.column("progress", width=90, stretch=False)
        self.fbs_jobs_tree.pack(fill=tk.BOTH, expand=True)
        self.fbs_jobs_tree.bind("<<TreeviewSelect>>", self.on_fbs_job_select)

        header = ttk.Frame(right)
        header.pack(fill=tk.X)
        self.fbs_job_title = ttk.Label(header, text="Выберите задание", font=("", 11, "bold"))
        self.fbs_job_title.pack(side=tk.LEFT)
        self.fbs_job_stats = ttk.Label(header, text="")
        self.fbs_job_stats.pack(side=tk.RIGHT)

        active = ttk.LabelFrame(right, text="Активная строка (наклеить ярлык заказа)", padding=8)
        active.pack(fill=tk.X, pady=(8, 8))
        self.fbs_active_var = tk.StringVar(value="Нет активной строки — пикните товар")
        ttk.Label(active, textvariable=self.fbs_active_var, wraplength=520).pack(anchor=tk.W)
        active_btns = ttk.Frame(active)
        active_btns.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(active_btns, text="Перепечатать ярлык", command=self.fbs_reprint_active).pack(side=tk.LEFT)
        ttk.Button(active_btns, text="Закрыть без скана", command=self.fbs_close_active).pack(side=tk.LEFT, padx=6)
        ttk.Button(active_btns, text="Отменить печать", command=self.fbs_cancel_active).pack(side=tk.LEFT)

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

        self.fbs_manual_frame = ttk.Frame(right)
        # packed when manual on

        remaining_wrap = ttk.Frame(self.fbs_manual_frame)
        remaining_wrap.pack(fill=tk.BOTH, expand=True)
        left_rem = ttk.Frame(remaining_wrap)
        left_rem.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ttk.Label(left_rem, text="Остаток (pending)").pack(anchor=tk.W)
        self.fbs_remaining_tree = ttk.Treeview(
            left_rem,
            columns=("sku", "name", "qty", "barcode"),
            show="headings",
            height=10,
        )
        self.fbs_remaining_tree.heading("sku", text="Артикул")
        self.fbs_remaining_tree.heading("name", text="Название")
        self.fbs_remaining_tree.heading("qty", text="Ост.")
        self.fbs_remaining_tree.heading("barcode", text="ШК")
        self.fbs_remaining_tree.column("sku", width=100, stretch=False)
        self.fbs_remaining_tree.column("name", width=200)
        self.fbs_remaining_tree.column("qty", width=50, stretch=False)
        self.fbs_remaining_tree.column("barcode", width=120, stretch=False)
        self.fbs_remaining_tree.pack(fill=tk.BOTH, expand=True)
        self.fbs_remaining_tree.bind("<<TreeviewSelect>>", self.on_fbs_remaining_select)
        self.fbs_remaining_tree.bind("<Double-1>", self.on_fbs_remaining_pick)

        right_rem = ttk.Frame(remaining_wrap)
        right_rem.pack(side=tk.LEFT, fill=tk.Y, padx=(10, 0))
        ttk.Label(right_rem, text="ШК для сканера").pack(anchor=tk.W)
        self.fbs_barcode_canvas = tk.Label(right_rem, background="#fff", relief=tk.SOLID, borderwidth=1)
        self.fbs_barcode_canvas.pack(pady=6)
        ttk.Button(right_rem, text="Взять (тап)", command=self.on_fbs_remaining_pick).pack(fill=tk.X)

        self.fbs_lines_frame = ttk.LabelFrame(right, text="Строки задания", padding=6)
        self.fbs_lines_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.fbs_lines_tree = ttk.Treeview(
            self.fbs_lines_frame,
            columns=("seq", "sku", "order", "status"),
            show="headings",
            height=8,
        )
        self.fbs_lines_tree.heading("seq", text="#")
        self.fbs_lines_tree.heading("sku", text="Артикул")
        self.fbs_lines_tree.heading("order", text="Заказ")
        self.fbs_lines_tree.heading("status", text="Статус")
        self.fbs_lines_tree.column("seq", width=40, stretch=False)
        self.fbs_lines_tree.column("sku", width=120, stretch=False)
        self.fbs_lines_tree.column("order", width=140)
        self.fbs_lines_tree.column("status", width=90, stretch=False)
        self.fbs_lines_tree.pack(fill=tk.BOTH, expand=True)

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
            selected = None
            if self.fbs_job:
                selected = str(self.fbs_job.get("id") or "")
            self.fbs_jobs_tree.delete(*self.fbs_jobs_tree.get_children())
            for job in jobs:
                jid = str(job.get("id") or "")
                done = job.get("line_done", 0)
                total = job.get("line_total", 0)
                self.fbs_jobs_tree.insert(
                    "",
                    tk.END,
                    iid=jid,
                    values=(jid, job.get("status") or "", f"{done}/{total}"),
                )
            if selected and self.fbs_jobs_tree.exists(selected):
                self.fbs_jobs_tree.selection_set(selected)
                self.fbs_jobs_tree.focus(selected)
            self.set_status(f"FBS заданий: {len(jobs)}")
            self._fbs_focus_scan()

        self.set_status("Загрузка FBS...")
        self._run_task(worker, on_ok)

    def on_fbs_job_select(self, _event=None) -> None:
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

        active = job.get("active_line")
        active_lines = job.get("active_lines") or ([] if not active else [active])
        if active_lines:
            first = active_lines[0]
            if len(active_lines) == 1:
                self.fbs_active_var.set(
                    f"SKU {first.get('sku')} · {first.get('product_name') or ''} · "
                    f"заказ {first.get('order_display') or first.get('order_id')} · "
                    f"строка #{first.get('id')}"
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
        else:
            self.fbs_active_var.set("Нет активной строки — пикните товар")

        self.fbs_lines_tree.delete(*self.fbs_lines_tree.get_children())
        for line in job.get("lines") or []:
            self.fbs_lines_tree.insert(
                "",
                tk.END,
                iid=str(line.get("id")),
                values=(
                    line.get("seq", ""),
                    line.get("sku", ""),
                    line.get("order_display") or line.get("order_id") or "",
                    line.get("status", ""),
                ),
            )
        if self.fbs_manual_var.get():
            self._fbs_render_remaining()

    def _fbs_render_remaining(self) -> None:
        job = self.fbs_job or {}
        groups = job.get("remaining_groups") or []
        self.fbs_remaining_tree.delete(*self.fbs_remaining_tree.get_children())
        for idx, group in enumerate(groups):
            self.fbs_remaining_tree.insert(
                "",
                tk.END,
                iid=str(idx),
                values=(
                    group.get("sku") or "",
                    group.get("name") or "",
                    group.get("quantity") or 0,
                    group.get("barcode") or "",
                ),
            )
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
        if not code:
            self.fbs_barcode_canvas.config(image="", text="нет ШК\n(возьмите тапом)")
            self._fbs_barcode_photo = None
            return
        try:
            img = render_code128_image(code)
            # Enlarge for handheld scanner from monitor
            w, h = img.size
            scale = max(2, min(4, 360 // max(w, 1)))
            img = img.resize((w * scale, h * scale))
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
        batch = bool(self.fbs_batch_var.get())

        def worker():
            return self.client.fbs_pick_sku(job_id, sku=sku, product_id=product_id, batch=batch)

        self._fbs_handle_allocate(worker, status="Выделение SKU...")

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
        active = job.get("active_line") or (job.get("active_lines") or [None])[0]

        if active:
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

        batch = bool(self.fbs_batch_var.get())

        def worker_product():
            return self.client.fbs_scan_product(job_id, code, batch=batch)

        self._fbs_handle_allocate(worker_product, status="Пик товара...")

    def _fbs_handle_allocate(self, worker: Callable[[], dict], *, status: str) -> None:
        def on_ok(payload: dict) -> None:
            self.fbs_job = payload.get("job") or self.fbs_job
            pdfs = payload.get("pdfs_base64") or []
            if not pdfs and payload.get("pdf_base64"):
                pdfs = [payload.get("pdf_base64")]
            lines = payload.get("lines") or ([payload.get("line")] if payload.get("line") else [])
            self._fbs_render_job()
            printed = 0
            try:
                for pdf_b64 in pdfs:
                    print_pdf(base64.b64decode(pdf_b64), **self._label_print_profile())
                    printed += 1
            except Exception as exc:
                messagebox.showerror("Печать", str(exc))
                self.set_status(
                    f"Получено ярлыков: {len(pdfs)}, напечатано: {printed}. Можно Перепечатать"
                )
                self._fbs_focus_scan()
                return
            sku = (lines[0] or {}).get("sku") if lines else ""
            if printed > 1:
                self.set_status(f"Напечатано ярлыков: {printed} · SKU {sku} · пропикайте ярлыки подряд")
            elif printed == 1:
                line = lines[0] if lines else {}
                self.set_status(f"Напечатан ярлык · SKU {line.get('sku')} · заказ {line.get('order_id')}")
            else:
                self.set_status("Строка выделена")
            self._fbs_focus_scan()

        self._fbs_run(worker, on_ok, status=status)

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
            return [self.client.fbs_download_line_pdf(job_id, line_id) for line_id in line_ids]

        def on_ok(pdfs: list[bytes]) -> None:
            for pdf in pdfs:
                print_pdf(pdf, **self._label_print_profile())
            self.set_status(f"На повторную печать: {len(pdfs)} ярл.")
            self._fbs_focus_scan()

        self._fbs_run(worker, on_ok, status="Перепечатка...")

    def fbs_close_active(self) -> None:
        job = self.fbs_job
        active = self._fbs_active_line()
        if not job or not active:
            messagebox.showwarning("FBS", "Нет активной строки")
            return
        job_id = int(job["id"])
        line_id = int(active["id"])

        def worker():
            return self.client.fbs_close_line(job_id, line_id)

        def on_ok(payload: dict) -> None:
            self.fbs_job = payload.get("job") or self.fbs_job
            self._fbs_render_job()
            left = len(self._fbs_active_lines())
            if left:
                self.set_status(f"Строка закрыта вручную · осталось в печати: {left}")
            else:
                self.set_status("Строка закрыта вручную")
            self._fbs_focus_scan()

        self._fbs_run(worker, on_ok, status="Закрытие...")

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
            self.set_status("Печать отменена, строки снова pending")
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
