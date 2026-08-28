from __future__ import annotations

import threading
from datetime import date, timedelta
import tkinter as tk
from tkinter import messagebox, ttk
import os
import ctypes

from api_client import AuthError, WarehouseApiClient
from fbs_packing_ui import FbsPackingMixin, _FBS_LIST_IMG, _TreeHoverTip
from paging import PageBar, catalog_matches
from label_cache import cache_summary, clear_except, format_cache_size
from local_print import barcode_label_pdf, print_pdf
from packing_config import load_config, parse_label_size_mm, save_config


def _configure_windows_dpi_awareness() -> None:
    """Make Tkinter render crisp UI (avoid bitmap scaling) on Windows."""
    if os.name != "nt":
        return
    try:
        # 2 = PER_MONITOR_DPI_AWARE
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        # Fallback for older Windows.
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def normalize_barcodes(raw) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in raw or []:
        if isinstance(item, dict):
            code = str(item.get("barcode") or "").strip()
            label = str(item.get("label") or "").strip()
            group = str(item.get("group") or "").strip()
        else:
            code = str(item or "").strip()
            label = ""
            group = ""
        if code:
            out.append({"barcode": code, "label": label, "group": group})
    return out


def barcode_print_name(product: dict, _barcode_item: dict) -> str:
    return str(product.get("name") or "").strip()


def barcode_pick_title(barcode_item: dict) -> str:
    label = str(barcode_item.get("label") or "").strip()
    if label:
        return label
    group = str(barcode_item.get("group") or "").strip()
    if group:
        return group
    return str(barcode_item.get("barcode") or "")


def barcode_combo_label(barcode_item: dict) -> str:
    title = barcode_pick_title(barcode_item)
    code = str(barcode_item.get("barcode") or "").strip()
    if title and code and title != code:
        return f"{title} ({code})"
    return title or code


def format_task_day(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "—"
    parts = raw.split("-")
    if len(parts) == 3:
        return f"{parts[2]}.{parts[1]}.{parts[0]}"
    return raw


def parse_task_day(value: str) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    parts = raw.split("-")
    if len(parts) != 3:
        return None
    try:
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def ship_date_tag(end_date: str, *, today: date | None = None) -> str:
    ship = parse_task_day(end_date)
    if ship is None:
        return ""
    current = today or date.today()
    if ship == current:
        return "ship_today"
    if ship == current + timedelta(days=1):
        return "ship_tomorrow"
    return ""


class LoginWindow(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Warehouse Packing App — вход")
        self.geometry("520x320")
        self.resizable(False, False)
        self.config_data = load_config()
        self.client: WarehouseApiClient | None = None
        self.user_name = ""
        self._build_ui()

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=16)
        root.pack(fill=tk.BOTH, expand=True)

        ttk.Label(root, text="Вход в панель склада", font=("", 12, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 12)
        )

        ttk.Label(root, text="Адрес сервера").grid(row=1, column=0, sticky="w", pady=4)
        self.server_var = tk.StringVar(value=self.config_data.get("server_url", ""))
        ttk.Entry(root, textvariable=self.server_var, width=42).grid(row=1, column=1, sticky="we", pady=4)

        ttk.Label(root, text="Адрес API").grid(row=2, column=0, sticky="w", pady=4)
        self.api_var = tk.StringVar(value=self.config_data.get("api_url", ""))
        api_entry = ttk.Entry(root, textvariable=self.api_var, width=42)
        api_entry.grid(row=2, column=1, sticky="we", pady=4)
        ttk.Label(root, text="пусто = тот же хост :8766 (run_api.py)", foreground="#666").grid(
            row=3, column=1, sticky="w"
        )

        ttk.Label(root, text="Логин").grid(row=4, column=0, sticky="w", pady=4)
        self.login_var = tk.StringVar()
        login_entry = ttk.Entry(root, textvariable=self.login_var, width=42)
        login_entry.grid(row=4, column=1, sticky="we", pady=4)

        ttk.Label(root, text="Пароль").grid(row=5, column=0, sticky="w", pady=4)
        self.password_var = tk.StringVar()
        ttk.Entry(root, textvariable=self.password_var, show="*", width=42).grid(row=5, column=1, sticky="we", pady=4)

        self.status = ttk.Label(root, text="", foreground="#555")
        self.status.grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))

        buttons = ttk.Frame(root)
        buttons.grid(row=7, column=0, columnspan=2, sticky="e", pady=(16, 0))
        ttk.Button(buttons, text="Настройки", command=self.open_settings).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Войти", command=self.do_login).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Выход", command=self.destroy).pack(side=tk.RIGHT, padx=8)

        root.columnconfigure(1, weight=1)
        self.bind("<Return>", lambda _e: self.do_login())
        login_entry.focus_set()

    def set_status(self, text: str) -> None:
        self.status.config(text=text)
        self.update_idletasks()

    def open_settings(self) -> None:
        SettingsWindow(self, self.config_data, self.apply_settings)

    def apply_settings(self, config: dict[str, str]) -> None:
        save_config(config)
        self.config_data = load_config()
        self.server_var.set(self.config_data.get("server_url", ""))
        self.api_var.set(self.config_data.get("api_url", ""))
        self.set_status("Настройки сохранены")

    def do_login(self) -> None:
        server_url = self.server_var.get().strip().rstrip("/")
        api_url = self.api_var.get().strip().rstrip("/")
        login = self.login_var.get().strip()
        password = self.password_var.get()
        if not server_url:
            messagebox.showerror("Ошибка", "Укажите адрес сервера")
            return
        if not login or not password:
            messagebox.showerror("Ошибка", "Введите логин и пароль")
            return
        changed = (
            server_url != self.config_data.get("server_url", "")
            or api_url != self.config_data.get("api_url", "")
        )
        if changed:
            save_config({**self.config_data, "server_url": server_url, "api_url": api_url})
            self.config_data = load_config()

        self.set_status("Вход...")
        client = WarehouseApiClient(server_url, api_url)
        try:
            session = client.login(login, password)
        except AuthError as exc:
            messagebox.showerror("Ошибка входа", str(exc))
            self.set_status("")
            return
        except Exception as exc:
            messagebox.showerror("Ошибка входа", f"Не удалось подключиться к серверу:\n{exc}")
            self.set_status("")
            return

        if not client.api_ok:
            messagebox.showwarning(
                "API упаковщиков",
                (client.api_error or "Нет сессии API")
                + "\n\nЗадания и номенклатура работают. "
                "Вкладка «Упаковка FBS» нужна после запуска run_api.py.",
            )

        user = session.get("user") or {}
        self.client = client
        self.user_name = str(user.get("display_name") or user.get("login") or login)
        self.destroy()


class PackingApp(FbsPackingMixin, tk.Tk):
    def __init__(self, config_data: dict[str, str], client: WarehouseApiClient, user_name: str) -> None:
        super().__init__()
        self.title(f"Warehouse Packing App — {user_name}")
        self.geometry("980x640")
        self.config_data = config_data
        self.client = client
        self.supplies: list[dict] = []
        self.current_supply: dict | None = None
        self.catalog_products: list[dict] = []
        self.catalog_products_all: list[dict] = []
        self.catalog_barcode_cache: dict[int, list[dict[str, str]]] = {}
        self._catalog_image_urls: dict[str, str] = {}
        self._catalog_loading = False
        self._catalog_search_job: str | None = None
        self._print_in_progress = False
        self.tasks: list[dict] = []
        self.current_task: dict | None = None
        self.task_statuses: list[dict] = []
        self._status_id_by_name: dict[str, int] = {}
        self._task_status_silent = False
        self._tasks_refresh_job: str | None = None
        self._init_fbs_state()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._configure_catalog_styles()
        self._configure_tasks_styles()
        self._build_ui()
        # Maximize window without enabling fullscreen mode.
        # On Windows Tkinter "zoomed" typically expands to available screen area (not exclusive fullscreen).
        try:
            self.state("zoomed")
        except Exception:
            pass
        self.after(200, self.load_tasks)

    def _configure_catalog_styles(self) -> None:
        style = ttk.Style(self)
        style.configure("CatalogPrint.Treeview", rowheight=36, indent=0)
        style.configure("CatalogPrintHeading.Treeview.Heading", font=("Segoe UI", 9, "bold"))

    def _configure_tasks_styles(self) -> None:
        self.tasks_tree_tags_ready = False

    def _ensure_tasks_tree_tags(self) -> None:
        if getattr(self, "tasks_tree_tags_ready", False):
            return
        if not hasattr(self, "tasks_tree"):
            return
        self.tasks_tree.tag_configure("ship_today", background="#ffcdd2")
        self.tasks_tree.tag_configure("ship_tomorrow", background="#fff9c4")
        self.tasks_tree_tags_ready = True

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill=tk.BOTH, expand=True)

        toolbar = ttk.Frame(root)
        toolbar.pack(fill=tk.X)
        ttk.Button(toolbar, text="Настройки", command=self.open_settings).pack(side=tk.RIGHT)
        ttk.Button(toolbar, text="Выйти", command=self.on_close).pack(side=tk.RIGHT, padx=6)

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill=tk.BOTH, expand=True, pady=10)

        self._build_tasks_tab()
        self._build_fbs_tab()
        self._build_catalog_tab()
        self.notebook.bind("<<NotebookTabChanged>>", self._on_notebook_tab_changed)

        self.status = ttk.Label(root, text="Готово")
        self.status.pack(anchor=tk.W)

    def _build_fbo_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text="FBO-задания")

        fbo_toolbar = ttk.Frame(tab)
        fbo_toolbar.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(fbo_toolbar, text="Обновить задания", command=self.load_supplies).pack(side=tk.LEFT)
        ttk.Button(fbo_toolbar, text="Печать PDF этикеток", command=self.print_labels).pack(side=tk.LEFT, padx=6)
        ttk.Button(fbo_toolbar, text="Печать ШК выбранного товара", command=self.print_selected_barcode).pack(side=tk.LEFT)

        panes = ttk.PanedWindow(tab, orient=tk.HORIZONTAL)
        panes.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(panes)
        right = ttk.Frame(panes)
        panes.add(left, weight=1)
        panes.add(right, weight=2)

        ttk.Label(left, text="Мои FBO-задания").pack(anchor=tk.W)
        self.supply_list = tk.Listbox(left)
        self.supply_list.pack(fill=tk.BOTH, expand=True)
        self.supply_list.bind("<<ListboxSelect>>", self.on_supply_select)

        ttk.Label(right, text="Состав задания").pack(anchor=tk.W)
        self.items = ttk.Treeview(right, columns=("sku", "qty"), show="headings")
        self.items.heading("sku", text="SKU")
        self.items.heading("qty", text="Кол-во")
        self.items.pack(fill=tk.BOTH, expand=True)

    def _build_tasks_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text="Задания")

        toolbar = ttk.Frame(tab)
        toolbar.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(toolbar, text="Обновить", command=self.load_tasks).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Печать все А4", command=lambda: self.print_task_attachments("a4")).pack(
            side=tk.LEFT, padx=6
        )
        ttk.Button(toolbar, text="Печать все этикетки", command=lambda: self.print_task_attachments("label")).pack(
            side=tk.LEFT
        )
        ttk.Button(toolbar, text="Печать всё", command=self.print_all_task_attachments).pack(side=tk.LEFT, padx=6)

        panes = ttk.PanedWindow(tab, orient=tk.HORIZONTAL)
        panes.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(panes)
        right = ttk.Frame(panes)
        panes.add(left, weight=1)
        panes.add(right, weight=2)

        ttk.Label(left, text="Мои задания").pack(anchor=tk.W)
        self.tasks_tree = ttk.Treeview(
            left,
            columns=("assembly", "marketplace", "ship"),
            show="headings",
            height=12,
        )
        self.tasks_tree.heading("assembly", text="Дата сборки")
        self.tasks_tree.heading("marketplace", text="Маркетплейс")
        self.tasks_tree.heading("ship", text="Дата отгрузки")
        self.tasks_tree.column("assembly", width=100, stretch=False)
        self.tasks_tree.column("marketplace", width=160)
        self.tasks_tree.column("ship", width=100, stretch=False)
        self.tasks_tree.pack(fill=tk.BOTH, expand=True)
        self.tasks_tree.bind("<<TreeviewSelect>>", self.on_task_select)
        self._ensure_tasks_tree_tags()

        ttk.Label(right, text="Детали задания").pack(anchor=tk.W)
        status_row = ttk.Frame(right)
        status_row.pack(fill=tk.X, pady=(4, 8))
        ttk.Label(status_row, text="Статус").pack(side=tk.LEFT)
        self.task_status_var = tk.StringVar()
        self.task_status_combo = ttk.Combobox(
            status_row,
            textvariable=self.task_status_var,
            state="readonly",
            width=28,
        )
        self.task_status_combo.pack(side=tk.LEFT, padx=(8, 0))
        self.task_status_combo.bind("<<ComboboxSelected>>", self.on_task_status_change)

        self.task_description = tk.Text(right, height=10, wrap=tk.WORD, state=tk.DISABLED)
        self.task_description.pack(fill=tk.BOTH, expand=True, pady=(4, 8))

        files_frame = ttk.LabelFrame(right, text="Файлы для печати", padding=8)
        files_frame.pack(fill=tk.BOTH, expand=True)
        self.task_files = ttk.Treeview(
            files_frame,
            columns=("kind", "filename", "action"),
            show="headings",
            height=8,
        )
        self.task_files.heading("kind", text="Тип")
        self.task_files.heading("filename", text="Файл")
        self.task_files.heading("action", text="")
        self.task_files.column("kind", width=90, stretch=False)
        self.task_files.column("filename", width=260)
        self.task_files.column("action", width=90, stretch=False, anchor=tk.CENTER)
        self.task_files.pack(fill=tk.BOTH, expand=True)
        self.task_files.bind("<Button-1>", self._on_task_files_click)

    def _build_catalog_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text="Номенклатура")

        qty_row = ttk.Frame(tab)
        qty_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(qty_row, text="Кол-во этикеток").pack(side=tk.LEFT)
        self.catalog_default_qty_var = tk.StringVar(value="1")
        ttk.Entry(qty_row, textvariable=self.catalog_default_qty_var, width=8).pack(side=tk.LEFT, padx=8)
        ttk.Label(qty_row, text="для кнопки «Печать ШК»", foreground="#666").pack(side=tk.LEFT)

        search_row = ttk.Frame(tab)
        search_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(search_row, text="Поиск").pack(side=tk.LEFT)
        self.catalog_search_var = tk.StringVar()
        search_entry = ttk.Entry(search_row, textvariable=self.catalog_search_var)
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        search_entry.bind("<Return>", lambda _e: self.load_catalog())
        search_entry.bind("<KeyRelease>", self._on_catalog_search_key)
        ttk.Button(search_row, text="Найти", command=self.load_catalog).pack(side=tk.LEFT)
        ttk.Button(search_row, text="Обновить", command=lambda: self.load_catalog(force_reload=True)).pack(side=tk.LEFT, padx=6)

        self.catalog_pager = PageBar(tab, on_change=self._render_catalog_page)
        self.catalog_pager.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 0))

        tree_wrap = ttk.Frame(tab)
        tree_wrap.pack(fill=tk.BOTH, expand=True)
        self.catalog_tree = ttk.Treeview(
            tree_wrap,
            columns=("sku", "name", "print"),
            show="tree headings",
            style="CatalogPrint.Treeview",
        )
        self.catalog_tree.heading("#0", text="Фото")
        self.catalog_tree.heading("sku", text="Артикул")
        self.catalog_tree.heading("name", text="Название")
        self.catalog_tree.heading("print", text="Печать ШК")
        self.catalog_tree.column("#0", width=52, stretch=False, minwidth=52)
        self.catalog_tree.column("sku", width=120, stretch=False, minwidth=80)
        self.catalog_tree.column("name", width=360, minwidth=160)
        self.catalog_tree.column("print", width=120, stretch=False, minwidth=100, anchor=tk.CENTER)
        self.catalog_tree.bind("<Button-1>", self._on_catalog_tree_click)
        self.catalog_tree.bind("<Motion>", self._on_catalog_tree_motion)
        self.catalog_tree.bind("<Leave>", lambda _e: self.catalog_tree.config(cursor=""))
        _TreeHoverTip(self.catalog_tree, self._catalog_tip)
        scrollbar = ttk.Scrollbar(tree_wrap, orient=tk.VERTICAL, command=self.catalog_tree.yview)
        self.catalog_tree.configure(yscrollcommand=scrollbar.set)
        self.catalog_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def set_status(self, text: str) -> None:
        self.status.config(text=text)
        self.update_idletasks()

    def _label_print_profile(self) -> dict[str, str]:
        return {
            "printer": self.config_data.get("printer_label") or self.config_data.get("printer", ""),
            "print_settings": self.config_data.get("print_settings_label")
            or self.config_data.get("print_settings", ""),
        }

    def _a4_print_profile(self) -> dict[str, str]:
        return {
            "printer": self.config_data.get("printer_a4", ""),
            "print_settings": self.config_data.get("print_settings_a4", "paper=A4,portrait"),
        }

    def _label_size_mm(self) -> tuple[float, float]:
        return parse_label_size_mm(self._label_print_profile()["print_settings"])

    def _run_task(self, worker, on_success, on_error=None) -> None:
        def runner() -> None:
            try:
                result = worker()
            except Exception as exc:
                if on_error:
                    self.after(0, lambda e=exc: on_error(e))
                else:
                    self.after(0, lambda e=exc: self._background_error(e))
            else:
                self.after(0, lambda r=result: on_success(r))

        threading.Thread(target=runner, daemon=True).start()

    def _run_in_background(self, status: str, worker, on_success) -> None:
        if self._print_in_progress:
            return
        self._print_in_progress = True
        self.set_status(status)

        def runner() -> None:
            try:
                result = worker()
            except Exception as exc:
                self.after(0, lambda e=exc: self._background_error(e))
            else:
                self.after(0, lambda r=result: on_success(r))
            finally:
                self.after(0, lambda: setattr(self, "_print_in_progress", False))

        threading.Thread(target=runner, daemon=True).start()

    def _background_error(self, exc: Exception) -> None:
        if isinstance(exc, AuthError):
            messagebox.showerror("Сессия", str(exc))
            self.on_close()
            return
        if isinstance(exc, ValueError):
            messagebox.showwarning("Внимание", str(exc))
            self.set_status("Ошибка")
            return
        messagebox.showerror("Ошибка", str(exc))
        self.set_status("Ошибка")

    def on_close(self) -> None:
        self._cancel_tasks_refresh()
        self.client.logout()
        self.destroy()

    def open_settings(self) -> None:
        SettingsWindow(self, self.config_data, self.apply_settings, client=self.client)

    def apply_settings(self, config: dict[str, str]) -> None:
        save_config(config)
        self.config_data = load_config()
        self.set_status("Настройки сохранены")

    def load_supplies(self) -> None:
        try:
            self.set_status("Загрузка заданий...")
            self.supplies = self.client.get_my_fbo_supplies()
        except AuthError as exc:
            messagebox.showerror("Сессия", str(exc))
            self.on_close()
            return
        except Exception as exc:
            messagebox.showerror("Ошибка загрузки", str(exc))
            self.set_status("Ошибка")
            return
        self.supply_list.delete(0, tk.END)
        for supply in self.supplies:
            self.supply_list.insert(
                tk.END,
                f"#{supply.get('id')} {supply.get('title')} [{supply.get('status_label')}]",
            )
        self.set_status(f"Заданий: {len(self.supplies)}")

    def on_supply_select(self, _event=None) -> None:
        selection = self.supply_list.curselection()
        if not selection:
            return
        supply_id = int(self.supplies[selection[0]]["id"])
        try:
            self.set_status("Загрузка карточки...")
            self.current_supply = self.client.get_fbo_supply(supply_id)
        except AuthError as exc:
            messagebox.showerror("Сессия", str(exc))
            self.on_close()
            return
        except Exception as exc:
            messagebox.showerror("Ошибка", str(exc))
            self.set_status("Ошибка")
            return
        self.render_supply()

    def render_supply(self) -> None:
        self.items.delete(*self.items.get_children())
        supply = self.current_supply or {}
        for item in supply.get("items") or []:
            self.items.insert(
                "",
                tk.END,
                values=(item.get("sku", ""), item.get("quantity", "")),
                text=item.get("name", ""),
            )
        self.set_status(f"Открыто: {supply.get('title', '')}")

    def print_labels(self) -> None:
        supply = self.current_supply
        if not supply:
            messagebox.showwarning("Нет задания", "Выберите задание")
            return
        supply_id = int(supply["id"])

        def worker():
            pdf = self.client.download_labels_pdf(supply_id)
            profile = self._label_print_profile()
            print_pdf(pdf, **profile)

        self._run_in_background("Скачивание и печать PDF...", worker, lambda _r: self.set_status("PDF отправлен на печать"))

    def _ship_date_tag(self, end_date: str) -> str:
        return ship_date_tag(end_date)

    def _ensure_task_statuses_loaded(self) -> None:
        if self.task_statuses:
            return
        try:
            self.task_statuses = self.client.get_task_statuses()
        except Exception:
            self.task_statuses = []
        self._status_id_by_name = {
            str(item.get("name") or ""): int(item["id"])
            for item in self.task_statuses
            if item.get("id") is not None
        }

    def _task_tree_values(self, task: dict) -> tuple[str, str, str]:
        return (
            format_task_day(task.get("start_date", "")),
            task.get("counterparty_name") or "—",
            format_task_day(task.get("end_date", "")),
        )

    def _apply_task_tree_row(self, task: dict) -> None:
        self._ensure_tasks_tree_tags()
        task_id = str(task.get("id"))
        tag = self._ship_date_tag(str(task.get("end_date") or ""))
        tags = (tag,) if tag else ()
        values = self._task_tree_values(task)
        if self.tasks_tree.exists(task_id):
            self.tasks_tree.item(task_id, values=values, tags=tags)
        else:
            self.tasks_tree.insert("", tk.END, iid=task_id, values=values, tags=tags)

    def _sync_task_cache(self, task: dict) -> None:
        task_id = int(task.get("id") or 0)
        if not task_id:
            return
        for idx, item in enumerate(self.tasks):
            if int(item.get("id") or 0) == task_id:
                self.tasks[idx] = task
                return
        self.tasks.append(task)

    def _render_task_status(self, task: dict) -> None:
        self._ensure_task_statuses_loaded()
        names = [str(item.get("name") or "") for item in self.task_statuses if item.get("name")]
        self.task_status_combo["values"] = names
        if not task:
            self._task_status_silent = True
            self.task_status_var.set("")
            self._task_status_silent = False
            return
        current = str(task.get("status_name") or "")
        self._task_status_silent = True
        self.task_status_var.set(current if current in names else (names[0] if names else ""))
        self._task_status_silent = False

    def on_task_status_change(self, _event=None) -> None:
        if self._task_status_silent:
            return
        task = self.current_task
        if not task:
            return
        name = self.task_status_var.get().strip()
        status_id = self._status_id_by_name.get(name)
        if status_id is None or int(task.get("status_id") or 0) == status_id:
            return
        task_id = int(task["id"])

        def worker():
            return self.client.patch_task(task_id, {"status_id": status_id})

        def on_updated(updated: dict) -> None:
            self.current_task = updated
            self._sync_task_cache(updated)
            self._apply_task_tree_row(updated)
            self._render_task_status(updated)
            self.set_status(f"Статус: {updated.get('status_name', name)}")

        def on_error(exc: Exception) -> None:
            self._render_task_status(task)
            self._background_error(exc)

        self.set_status("Сохранение статуса...")
        self._run_task(worker, on_updated, on_error)

    def _maybe_mark_task_in_progress(self) -> None:
        task = self.current_task
        if not task:
            return
        if str(task.get("status_name") or "") != "Новый":
            return
        self._ensure_task_statuses_loaded()
        status_id = self._status_id_by_name.get("В работе")
        if status_id is None:
            return
        task_id = int(task["id"])

        def worker():
            return self.client.patch_task(task_id, {"status_id": status_id})

        def on_updated(updated: dict) -> None:
            self.current_task = updated
            self._sync_task_cache(updated)
            self._render_task_status(updated)
            self.set_status(f"Статус изменён: {updated.get('status_name', 'В работе')}")

        def on_error(exc: Exception) -> None:
            if isinstance(exc, AuthError):
                messagebox.showerror("Сессия", str(exc))
                self.on_close()
                return
            self.set_status("Не удалось обновить статус задачи")

        self._run_task(worker, on_updated, on_error)

    def _on_task_print_done(self, message: str) -> None:
        self.set_status(message)
        self._maybe_mark_task_in_progress()

    def _tasks_refresh_interval_ms(self) -> int:
        try:
            seconds = int(self.config_data.get("refresh_seconds") or "30")
        except ValueError:
            seconds = 30
        return max(5, seconds) * 1000

    def _cancel_tasks_refresh(self) -> None:
        if self._tasks_refresh_job is not None:
            self.after_cancel(self._tasks_refresh_job)
            self._tasks_refresh_job = None

    def _schedule_tasks_refresh(self) -> None:
        self._cancel_tasks_refresh()
        self._tasks_refresh_job = self.after(self._tasks_refresh_interval_ms(), self._tasks_auto_refresh)

    def _tasks_auto_refresh(self) -> None:
        self._tasks_refresh_job = None
        tab_id = self.notebook.select()
        if self.notebook.tab(tab_id, "text") != "Задания":
            return
        self.load_tasks(silent=True)

    def load_tasks(self, *, silent: bool = False) -> None:
        if not silent:
            self.set_status("Загрузка заданий...")
        self._ensure_task_statuses_loaded()
        try:
            self.tasks = self.client.get_my_tasks()
        except AuthError as exc:
            messagebox.showerror("Сессия", str(exc))
            self.on_close()
            return
        except Exception as exc:
            messagebox.showerror("Ошибка загрузки", str(exc))
            self.set_status("Ошибка")
            return

        selected_id: int | None = None
        if self.current_task:
            selected_id = int(self.current_task.get("id") or 0) or None
        self.tasks_tree.delete(*self.tasks_tree.get_children())
        self._ensure_tasks_tree_tags()
        for task in self.tasks:
            self._apply_task_tree_row(task)
        if selected_id is not None and self.tasks_tree.exists(str(selected_id)):
            self.tasks_tree.selection_set(str(selected_id))
            self.tasks_tree.focus(str(selected_id))
            self.on_task_select()
        elif self.current_task:
            self.current_task = None
            self.render_task()
        self.set_status(f"Заданий: {len(self.tasks)}")
        tab_id = self.notebook.select()
        if self.notebook.tab(tab_id, "text") == "Задания":
            self._schedule_tasks_refresh()

    def on_task_select(self, _event=None) -> None:
        selected = self.tasks_tree.selection()
        if not selected:
            return
        task_id = int(selected[0])
        try:
            self.set_status("Загрузка задания...")
            self.current_task = self.client.get_task(task_id)
        except AuthError as exc:
            messagebox.showerror("Сессия", str(exc))
            self.on_close()
            return
        except Exception as exc:
            messagebox.showerror("Ошибка", str(exc))
            self.set_status("Ошибка")
            return
        self.render_task()

    def render_task(self) -> None:
        task = self.current_task or {}
        self._render_task_status(task)
        self.task_description.config(state=tk.NORMAL)
        self.task_description.delete("1.0", tk.END)
        description = str(task.get("description") or "").strip()
        self.task_description.insert("1.0", description or "—")
        self.task_description.config(state=tk.DISABLED)

        self.task_files.delete(*self.task_files.get_children())
        for attachment in task.get("attachments") or []:
            kind = str(attachment.get("kind") or "")
            kind_label = "А4" if kind == "a4" else "Этикетки" if kind == "label" else kind
            self.task_files.insert(
                "",
                tk.END,
                iid=str(attachment.get("id")),
                values=(
                    kind_label,
                    attachment.get("filename") or "file.pdf",
                    "Печать",
                ),
            )
        title = task.get("counterparty_name") or f"Задание #{task.get('id', '')}"
        self.set_status(f"Открыто: {title}")

    def _on_task_files_click(self, event) -> None:
        column = self.task_files.identify_column(event.x)
        item = self.task_files.identify_row(event.y)
        if not item or column != "#3":
            return
        self.print_task_attachment(int(item))

    def _print_profile_for_kind(self, kind: str) -> dict[str, str]:
        if kind == "a4":
            return self._a4_print_profile()
        return self._label_print_profile()

    def _task_attachments(self, kind: str | None = None) -> list[dict]:
        task = self.current_task
        if not task:
            return []
        attachments = list(task.get("attachments") or [])
        if kind is None:
            return attachments
        return [item for item in attachments if str(item.get("kind") or "") == kind]

    def print_task_attachment(self, attachment_id: int) -> None:
        task = self.current_task
        if not task:
            messagebox.showwarning("Нет задания", "Выберите задание")
            return
        attachment = next(
            (item for item in (task.get("attachments") or []) if int(item.get("id") or 0) == attachment_id),
            None,
        )
        if attachment is None:
            messagebox.showwarning("Нет файла", "Файл не найден")
            return
        kind = str(attachment.get("kind") or "")
        profile = self._print_profile_for_kind(kind)
        task_id = int(task["id"])

        def worker():
            pdf = self.client.download_task_attachment(task_id, attachment_id)
            print_pdf(pdf, **profile)
            return attachment.get("filename") or "file.pdf"

        self._run_in_background(
            "Печать файла...",
            worker,
            lambda name: self._on_task_print_done(f"Файл «{name}» отправлен на печать"),
        )

    def print_task_attachments(self, kind: str) -> None:
        task = self.current_task
        if not task:
            messagebox.showwarning("Нет задания", "Выберите задание")
            return
        attachments = self._task_attachments(kind)
        if not attachments:
            label = "А4" if kind == "a4" else "этикетки"
            messagebox.showwarning("Нет файлов", f"У задания нет PDF для печати ({label})")
            return
        profile = self._print_profile_for_kind(kind)
        task_id = int(task["id"])

        def worker():
            for attachment in attachments:
                pdf = self.client.download_task_attachment(task_id, int(attachment["id"]))
                print_pdf(pdf, **profile)
            return len(attachments)

        kind_label = "А4" if kind == "a4" else "этикетки"
        self._run_in_background(
            f"Печать {kind_label}...",
            worker,
            lambda count: self._on_task_print_done(f"Напечатано файлов ({kind_label}): {count}"),
        )

    def print_all_task_attachments(self) -> None:
        task = self.current_task
        if not task:
            messagebox.showwarning("Нет задания", "Выберите задание")
            return
        a4_items = self._task_attachments("a4")
        label_items = self._task_attachments("label")
        if not a4_items and not label_items:
            messagebox.showwarning("Нет файлов", "У задания нет PDF для печати")
            return
        task_id = int(task["id"])
        a4_profile = self._a4_print_profile()
        label_profile = self._label_print_profile()

        def worker():
            for attachment in a4_items:
                pdf = self.client.download_task_attachment(task_id, int(attachment["id"]))
                print_pdf(pdf, **a4_profile)
            for attachment in label_items:
                pdf = self.client.download_task_attachment(task_id, int(attachment["id"]))
                print_pdf(pdf, **label_profile)
            return len(a4_items), len(label_items)

        self._run_in_background(
            "Печать всех файлов...",
            worker,
            lambda counts: self._on_task_print_done(
                f"Напечатано: А4 — {counts[0]}, этикетки — {counts[1]}"
            ),
        )

    def print_selected_barcode(self) -> None:
        selected = self.items.selection()
        if not selected:
            messagebox.showwarning("Нет товара", "Выберите товар в составе")
            return
        sku, _qty = self.items.item(selected[0], "values")
        if not sku:
            return
        name = self.items.item(selected[0], "text")
        self._print_barcode_label(sku, name)

    def _resolve_catalog_copies(self) -> int:
        raw = self.catalog_default_qty_var.get().strip()
        try:
            copies = int(raw or "1")
        except ValueError:
            copies = 1
        return max(1, min(9999, copies))

    def _catalog_tip(self, row: str, col: str) -> str:
        values = self.catalog_tree.item(row, "values") or ()
        mapping = {"#1": 0, "#2": 1, "#3": 2}
        idx = mapping.get(col)
        if idx is None:
            sku = str(values[0] if values else "")
            name = str(values[1] if len(values) > 1 else "")
            return " · ".join(part for part in (sku, name) if part)
        if idx >= len(values):
            return ""
        return str(values[idx] or "")

    def _on_catalog_tree_motion(self, event) -> None:
        column = self.catalog_tree.identify_column(event.x)
        if column == "#3":
            self.catalog_tree.config(cursor="hand2")
        else:
            self.catalog_tree.config(cursor="")

    def _on_catalog_tree_click(self, event) -> None:
        if self.catalog_tree.identify_column(event.x) != "#3":
            return
        item = self.catalog_tree.identify_row(event.y)
        if not item:
            return
        self.print_catalog_barcode_for_product(int(item), event.x_root, event.y_root)

    def _render_catalog_tree(self, products: list[dict]) -> None:
        self.catalog_tree.delete(*self.catalog_tree.get_children())
        self._catalog_image_urls = {}
        for product in products:
            iid = str(product.get("id") or "")
            if not iid:
                continue
            kind = "комплект" if product.get("is_kit") else ""
            name = str(product.get("name") or "")
            if kind:
                name = f"{name} ({kind})"
            sku = str(product.get("sku") or "")
            url = str(product.get("image_url") or "").strip()
            self._catalog_image_urls[iid] = url
            if url:
                self._fbs_ensure_image(url)
            kwargs: dict = {
                "iid": iid,
                "text": "",
                "values": (sku, name, "Печать ШК"),
            }
            photo = self._fbs_thumb_photo(url, _FBS_LIST_IMG) if url else None
            if photo is not None:
                kwargs["image"] = photo
            self.catalog_tree.insert("", tk.END, **kwargs)

    def _show_barcode_pick_menu(
        self,
        product: dict,
        barcodes: list[dict[str, str]],
        x_root: int,
        y_root: int,
    ) -> None:
        if len(barcodes) == 1:
            self._print_catalog_barcode_item(product, barcodes[0])
            return

        menu = tk.Menu(self, tearoff=0)
        for bc in barcodes:
            menu.add_command(
                label=barcode_combo_label(bc),
                command=lambda item=bc, prod=product: self._print_catalog_barcode_item(prod, item),
            )
        try:
            menu.tk_popup(x_root, y_root)
        finally:
            menu.grab_release()

    def print_catalog_barcode_for_product(self, product_id: int, x_root: int, y_root: int) -> None:
        product = next((p for p in self.catalog_products if int(p.get("id") or 0) == product_id), None)
        if product is None:
            product = next(
                (p for p in self.catalog_products_all if int(p.get("id") or 0) == product_id),
                None,
            )
        if product is None:
            messagebox.showwarning("Нет товара", "Товар не найден — обновите список")
            return

        cached = self.catalog_barcode_cache.get(product_id)
        if cached is not None:
            self._show_barcode_pick_menu(product, cached, x_root, y_root)
            return

        def worker():
            details = self.client.get_catalog_product(product_id)
            barcodes = normalize_barcodes(details.get("barcodes"))
            if not barcodes:
                sku = str(product.get("sku") or "").strip()
                if not sku:
                    raise ValueError("У товара нет штрихкода и артикула")
                barcodes = [{"barcode": sku, "label": "", "group": ""}]
            return barcodes

        def on_barcodes(barcodes: list[dict[str, str]]) -> None:
            self.catalog_barcode_cache[product_id] = barcodes
            self.set_status(self._catalog_status_text())
            self._show_barcode_pick_menu(product, barcodes, x_root, y_root)

        def on_error(exc: Exception) -> None:
            self._background_error(exc)

        self.set_status("Загрузка штрихкодов...")
        self._run_task(worker, on_barcodes, on_error)

    def _catalog_status_text(self) -> str:
        total = len(self.catalog_products)
        all_count = len(self.catalog_products_all)
        query = self.catalog_search_var.get().strip()
        if query:
            prefix = f"Найдено: {total} из {all_count}"
        else:
            prefix = f"Товаров: {total}"
        if total <= 0:
            return prefix
        start = self.catalog_pager.page * self.catalog_pager.page_size + 1
        end = min(total, (self.catalog_pager.page + 1) * self.catalog_pager.page_size)
        return f"{prefix} · {start}–{end}"

    def _render_catalog_page(self) -> None:
        visible = self.catalog_pager.slice_items(self.catalog_products)
        self._render_catalog_tree(visible)
        self.set_status(self._catalog_status_text())

    def _apply_catalog_filter(self, query: str) -> None:
        if query:
            self.catalog_products = [p for p in self.catalog_products_all if self._catalog_matches(p, query)]
        else:
            self.catalog_products = list(self.catalog_products_all)
        self.catalog_pager.reset()
        self._render_catalog_page()

    def load_catalog(self, *, force_reload: bool = False) -> None:
        if self._catalog_search_job is not None:
            self.after_cancel(self._catalog_search_job)
            self._catalog_search_job = None
        query = self.catalog_search_var.get().strip().casefold()
        if not force_reload and self.catalog_products_all:
            self._apply_catalog_filter(query)
            return
        if self._catalog_loading:
            return
        self._catalog_loading = True
        self.set_status("Загрузка номенклатуры...")

        def worker():
            return self.client.search_catalog_products()

        def on_products(products: list[dict]) -> None:
            self._catalog_loading = False
            self.catalog_products_all = products
            if force_reload:
                self.catalog_barcode_cache.clear()
            self._apply_catalog_filter(query)

        def on_error(exc: Exception) -> None:
            self._catalog_loading = False
            if isinstance(exc, AuthError):
                messagebox.showerror("Сессия", str(exc))
                self.on_close()
                return
            messagebox.showerror("Ошибка загрузки", str(exc))
            self.set_status("Ошибка загрузки")

        self._run_task(worker, on_products, on_error)

    def _on_notebook_tab_changed(self, _event=None) -> None:
        tab_id = self.notebook.select()
        tab_text = self.notebook.tab(tab_id, "text")
        if tab_text == "Номенклатура":
            self._cancel_tasks_refresh()
            self.load_catalog()
        elif tab_text == "Задания":
            self.load_tasks()
        elif tab_text == "Упаковка FBS":
            self._cancel_tasks_refresh()
            self.load_fbs_jobs()
        else:
            self._cancel_tasks_refresh()

    def _on_catalog_search_key(self, _event=None) -> None:
        if self._catalog_search_job is not None:
            self.after_cancel(self._catalog_search_job)
        self._catalog_search_job = self.after(400, self.load_catalog)

    def _catalog_matches(self, product: dict, query: str) -> bool:
        return catalog_matches(product, query)

    def _print_catalog_barcode_item(self, product: dict, barcode_item: dict[str, str]) -> None:
        barcode = str(barcode_item.get("barcode") or "").strip()
        print_name = barcode_print_name(product, barcode_item)
        sku = str(product.get("sku") or barcode)
        copies = self._resolve_catalog_copies()
        label_w, label_h = self._label_size_mm()

        def worker():
            pdf = barcode_label_pdf(
                barcode,
                sku=sku,
                name=print_name,
                width_mm=label_w,
                height_mm=label_h,
            )
            print_pdf(pdf, **self._label_print_profile(), copies=copies)
            return barcode, copies

        self._run_in_background(
            "Печать штрихкода...",
            worker,
            lambda result: self.set_status(f"ШК {result[0]}: напечатано {result[1]} шт."),
        )

    def _print_barcode_label(self, barcode: str, name: str) -> None:
        label_w, label_h = self._label_size_mm()

        def worker():
            pdf = barcode_label_pdf(
                barcode,
                sku=barcode,
                name=name,
                width_mm=label_w,
                height_mm=label_h,
            )
            print_pdf(pdf, **self._label_print_profile())
            return barcode

        self._run_in_background(
            "Печать штрихкода...",
            worker,
            lambda b: self.set_status(f"ШК {b} отправлен на печать"),
        )


class SettingsWindow(tk.Toplevel):
    def __init__(self, parent, config: dict[str, str], on_save, client=None) -> None:
        super().__init__(parent)
        self.title("Настройки")
        self.geometry("760x640")
        self.resizable(True, False)
        self.on_save = on_save
        self.client = client
        label_printer = config.get("printer_label") or config.get("printer", "")
        label_settings = config.get("print_settings_label") or config.get("print_settings", "")
        self.vars = {
            "server_url": tk.StringVar(value=config.get("server_url", "")),
            "api_url": tk.StringVar(value=config.get("api_url", "")),
            "printer_a4": tk.StringVar(value=config.get("printer_a4", "")),
            "print_settings_a4": tk.StringVar(value=config.get("print_settings_a4", "paper=A4,portrait")),
            "printer_label": tk.StringVar(value=label_printer),
            "print_settings_label": tk.StringVar(value=label_settings),
            "refresh_seconds": tk.StringVar(value=config.get("refresh_seconds", "30")),
        }
        self._build_ui()
        self.transient(parent)
        self.grab_set()

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill=tk.BOTH, expand=True)

        self._row(root, 0, "Адрес сервера (веб)", "server_url", "http://127.0.0.1:8765")
        self._row(root, 1, "Адрес API (упаковка)", "api_url", "пусто = тот же хост :8766")
        self._row(root, 2, "Принтер А4", "printer_a4", "Если пусто — принтер по умолчанию")
        self._row(root, 3, "Параметры А4", "print_settings_a4", "paper=A4,portrait")
        self._row(root, 4, "Принтер этикеток", "printer_label", "Если пусто — принтер по умолчанию")
        self._row(root, 5, "Параметры этикеток", "print_settings_label", "noscale,portrait,disable-auto-rotation,paper=47mm x 25mm")
        self._row(root, 6, "Автообновление, сек", "refresh_seconds", "30")

        hint = ttk.Label(
            root,
            text=(
                "Настройки сохраняются в config.env рядом с приложением. "
                "Веб — run_web.py (задачи, каталог). API — run_api.py (FBS-упаковка). "
                "Печать тихая через Windows, SumatraPDF не нужен. "
                "Размер этикетки: paper=ШИРИНАmm x ВЫСОТАmm."
            ),
            wraplength=700,
            foreground="#555",
        )
        hint.grid(row=7, column=0, columnspan=3, sticky="we", pady=(12, 8))

        cache_frame = ttk.LabelFrame(root, text="Кэш ярлыков FBS", padding=8)
        cache_frame.grid(row=8, column=0, columnspan=3, sticky="we", pady=(0, 8))
        self.cache_summary_label = ttk.Label(cache_frame, text=self._cache_summary_text())
        self.cache_summary_label.pack(anchor="w")
        ttk.Label(
            cache_frame,
            text="Удаляет скачанные PDF у заданий «Готово» и «Отменено». Задания в работе не трогает.",
            wraplength=700,
            foreground="#555",
        ).pack(anchor="w", pady=(4, 6))
        ttk.Button(
            cache_frame,
            text="Очистить кэш выполненных заданий",
            command=self.clear_done_job_cache,
        ).pack(anchor="w")

        buttons = ttk.Frame(root)
        buttons.grid(row=9, column=0, columnspan=3, sticky="e")
        ttk.Button(buttons, text="Отмена", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Сохранить", command=self.save).pack(side=tk.RIGHT, padx=8)

        root.columnconfigure(1, weight=1)
        self.geometry("760x640")

    def _row(self, root, row: int, label: str, key: str, placeholder: str = "") -> None:
        ttk.Label(root, text=label).grid(row=row, column=0, sticky="w", pady=5, padx=(0, 8))
        ttk.Entry(root, textvariable=self.vars[key]).grid(row=row, column=1, sticky="we", pady=5)
        ttk.Label(root, text="").grid(row=row, column=2)

    def _cache_summary_text(self) -> str:
        summary = cache_summary()
        return (
            f"На диске: {summary['jobs']} заданий, {summary['files']} ярлыков, "
            f"{format_cache_size(summary['bytes'])}."
        )

    def _refresh_cache_summary(self) -> None:
        self.cache_summary_label.config(text=self._cache_summary_text())
        hint_fn = getattr(self.master, "_fbs_update_cache_hint", None)
        if callable(hint_fn):
            try:
                hint_fn()
            except Exception:
                pass

    def clear_done_job_cache(self) -> None:
        client = self.client
        if client is None or not getattr(client, "api_ok", False):
            messagebox.showinfo(
                "Кэш ярлыков",
                "Войдите в приложение и подключитесь к API, чтобы удалить кэш только у выполненных заданий.",
                parent=self,
            )
            return
        if not messagebox.askyesno(
            "Очистить кэш",
            "Удалить скачанные ярлыки по выполненным и отменённым заданиям?\n"
            "Задания в работе останутся.",
            parent=self,
        ):
            return
        try:
            jobs = client.fbs_my_jobs()
            keep: set[int] = set()
            for job in jobs:
                raw = job.get("id") if isinstance(job, dict) else None
                if raw in (None, ""):
                    continue
                try:
                    keep.add(int(raw))
                except (TypeError, ValueError):
                    continue
            result = clear_except(keep)
        except AuthError as exc:
            messagebox.showerror("Сессия", str(exc), parent=self)
            return
        except Exception as exc:
            messagebox.showerror("Ошибка", str(exc), parent=self)
            return
        self._refresh_cache_summary()
        if result["jobs"] <= 0:
            messagebox.showinfo(
                "Кэш ярлыков",
                "Нечего удалять: кэш выполненных заданий пуст.",
                parent=self,
            )
            return
        messagebox.showinfo(
            "Кэш ярлыков",
            f"Удалено заданий: {result['jobs']} ({format_cache_size(result['bytes'])}).",
            parent=self,
        )

    def save(self) -> None:
        config = {key: var.get().strip() for key, var in self.vars.items()}
        config["printer"] = config.get("printer_label", "")
        config["print_settings"] = config.get("print_settings_label", "")
        try:
            seconds = int(config.get("refresh_seconds") or "30")
            if seconds < 5:
                raise ValueError
            config["refresh_seconds"] = str(seconds)
        except ValueError:
            messagebox.showerror("Ошибка", "Интервал автообновления должен быть числом не меньше 5")
            return
        try:
            self.on_save(config)
        except Exception as exc:
            messagebox.showerror("Ошибка сохранения", str(exc))
            return
        self.destroy()


def main() -> None:
    _configure_windows_dpi_awareness()
    login = LoginWindow()
    login.mainloop()
    if login.client is None:
        return
    app = PackingApp(login.config_data, login.client, login.user_name)
    app.mainloop()


if __name__ == "__main__":
    main()
