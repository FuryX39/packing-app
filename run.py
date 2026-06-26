from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from api_client import AuthError, WarehouseApiClient
from local_print import barcode_label_pdf, print_pdf
from packing_config import load_config, parse_label_size_mm, save_config


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


class LoginWindow(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Warehouse Packing App — вход")
        self.geometry("480x260")
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

        ttk.Label(root, text="Логин").grid(row=2, column=0, sticky="w", pady=4)
        self.login_var = tk.StringVar()
        login_entry = ttk.Entry(root, textvariable=self.login_var, width=42)
        login_entry.grid(row=2, column=1, sticky="we", pady=4)

        ttk.Label(root, text="Пароль").grid(row=3, column=0, sticky="w", pady=4)
        self.password_var = tk.StringVar()
        ttk.Entry(root, textvariable=self.password_var, show="*", width=42).grid(row=3, column=1, sticky="we", pady=4)

        self.status = ttk.Label(root, text="", foreground="#555")
        self.status.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))

        buttons = ttk.Frame(root)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(16, 0))
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
        self.set_status("Настройки сохранены")

    def do_login(self) -> None:
        server_url = self.server_var.get().strip().rstrip("/")
        login = self.login_var.get().strip()
        password = self.password_var.get()
        if not server_url:
            messagebox.showerror("Ошибка", "Укажите адрес сервера")
            return
        if not login or not password:
            messagebox.showerror("Ошибка", "Введите логин и пароль")
            return
        if server_url != self.config_data.get("server_url", ""):
            save_config({**self.config_data, "server_url": server_url})
            self.config_data = load_config()

        self.set_status("Вход...")
        client = WarehouseApiClient(server_url)
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

        user = session.get("user") or {}
        self.client = client
        self.user_name = str(user.get("display_name") or user.get("login") or login)
        self.destroy()


class PackingApp(tk.Tk):
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
        self.catalog_row_qty: dict[str, str] = {}
        self._catalog_qty_entry: ttk.Entry | None = None
        self._catalog_qty_edit_item: str | None = None
        self._catalog_loading = False
        self._catalog_search_job: str | None = None
        self._print_in_progress = False
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._configure_catalog_styles()
        self._build_ui()
        self.after(100, self.load_supplies)

    def _configure_catalog_styles(self) -> None:
        style = ttk.Style(self)
        style.configure("CatalogPrint.Treeview", rowheight=28)
        style.configure("CatalogPrintHeading.Treeview.Heading", font=("Segoe UI", 9, "bold"))

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill=tk.BOTH, expand=True)

        toolbar = ttk.Frame(root)
        toolbar.pack(fill=tk.X)
        ttk.Button(toolbar, text="Настройки", command=self.open_settings).pack(side=tk.RIGHT)
        ttk.Button(toolbar, text="Выйти", command=self.on_close).pack(side=tk.RIGHT, padx=6)

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill=tk.BOTH, expand=True, pady=10)

        self._build_fbo_tab()
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

    def _build_catalog_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text="Номенклатура")

        qty_row = ttk.Frame(tab)
        qty_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(qty_row, text="Кол-во по умолчанию").pack(side=tk.LEFT)
        self.catalog_default_qty_var = tk.StringVar(value="1")
        ttk.Entry(qty_row, textvariable=self.catalog_default_qty_var, width=8).pack(side=tk.LEFT, padx=8)
        ttk.Label(qty_row, text="(используется, если в строке товара кол-во не указано)", foreground="#666").pack(
            side=tk.LEFT
        )

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

        tree_wrap = ttk.Frame(tab)
        tree_wrap.pack(fill=tk.BOTH, expand=True)
        columns = ("code", "sku", "name", "group", "unit", "qty", "print")
        self.catalog_tree = ttk.Treeview(
            tree_wrap,
            columns=columns,
            show="headings",
            style="CatalogPrint.Treeview",
        )
        self.catalog_tree.heading("code", text="Код")
        self.catalog_tree.heading("sku", text="Артикул")
        self.catalog_tree.heading("name", text="Наименование")
        self.catalog_tree.heading("group", text="Группа")
        self.catalog_tree.heading("unit", text="Ед. изм.")
        self.catalog_tree.heading("qty", text="Кол-во")
        self.catalog_tree.heading("print", text="Печать")
        self.catalog_tree.column("code", width=80, stretch=False)
        self.catalog_tree.column("sku", width=100, stretch=False)
        self.catalog_tree.column("name", width=240)
        self.catalog_tree.column("group", width=120)
        self.catalog_tree.column("unit", width=70, stretch=False)
        self.catalog_tree.column("qty", width=60, stretch=False, anchor=tk.CENTER)
        self.catalog_tree.column("print", width=120, stretch=False, anchor=tk.CENTER)
        self.catalog_tree.bind("<Button-1>", self._on_catalog_tree_click)
        self.catalog_tree.bind("<Motion>", self._on_catalog_tree_motion)
        scrollbar = ttk.Scrollbar(tree_wrap, orient=tk.VERTICAL, command=self.catalog_tree.yview)
        self.catalog_tree.configure(yscrollcommand=scrollbar.set)
        self.catalog_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def set_status(self, text: str) -> None:
        self.status.config(text=text)
        self.update_idletasks()

    def _label_size_mm(self) -> tuple[float, float]:
        return parse_label_size_mm(self.config_data.get("print_settings", ""))

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
        self.client.logout()
        self.destroy()

    def open_settings(self) -> None:
        SettingsWindow(self, self.config_data, self.apply_settings)

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
            print_pdf(
                pdf,
                sumatra=self.config_data["sumatra"],
                printer=self.config_data["printer"],
                print_settings=self.config_data["print_settings"],
            )

        self._run_in_background("Скачивание и печать PDF...", worker, lambda _r: self.set_status("PDF отправлен на печать"))

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

    def _catalog_qty_display(self, product_id: str) -> str:
        return self.catalog_row_qty.get(str(product_id), "")

    def _resolve_catalog_copies(self, product_id: str) -> int:
        raw = self.catalog_row_qty.get(str(product_id), "").strip()
        if not raw:
            raw = self.catalog_default_qty_var.get().strip()
        try:
            copies = int(raw or "1")
        except ValueError:
            copies = 1
        return max(1, min(9999, copies))

    def _update_catalog_row_qty_display(self, item_id: str) -> None:
        values = list(self.catalog_tree.item(item_id, "values"))
        if len(values) >= 6:
            values[5] = self._catalog_qty_display(item_id)
            self.catalog_tree.item(item_id, values=values)

    def _finish_catalog_qty_edit(self, item_id: str, value: str) -> None:
        if self._catalog_qty_entry is not None:
            self._catalog_qty_entry.destroy()
            self._catalog_qty_entry = None
        self._catalog_qty_edit_item = None
        text = value.strip()
        if text:
            try:
                copies = int(text)
                if copies < 1 or copies > 9999:
                    raise ValueError
                self.catalog_row_qty[item_id] = str(copies)
            except ValueError:
                messagebox.showwarning("Ошибка", "Кол-во должно быть целым числом от 1 до 9999")
        else:
            self.catalog_row_qty.pop(item_id, None)
        self._update_catalog_row_qty_display(item_id)

    def _begin_catalog_qty_edit(self, item_id: str) -> None:
        if self._catalog_qty_entry is not None and self._catalog_qty_edit_item:
            self._finish_catalog_qty_edit(self._catalog_qty_edit_item, self._catalog_qty_entry.get())
        bbox = self.catalog_tree.bbox(item_id, column="#6")
        if not bbox:
            return
        x, y, width, height = bbox
        var = tk.StringVar(value=self._catalog_qty_display(item_id))
        entry = ttk.Entry(self.catalog_tree, textvariable=var, width=6, justify=tk.CENTER)
        entry.place(x=x, y=y, width=width, height=height)
        entry.focus_set()
        entry.select_range(0, tk.END)
        self._catalog_qty_entry = entry
        self._catalog_qty_edit_item = item_id

        def commit(_event=None, iid=item_id, widget=entry) -> None:
            if self._catalog_qty_entry is not widget:
                return
            self._finish_catalog_qty_edit(iid, var.get())

        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)

    def _on_catalog_tree_motion(self, event) -> None:
        column = self.catalog_tree.identify_column(event.x)
        if column == "#7":
            self.catalog_tree.config(cursor="hand2")
        elif column == "#6":
            self.catalog_tree.config(cursor="xterm")
        else:
            self.catalog_tree.config(cursor="")

    def _on_catalog_tree_click(self, event) -> None:
        column = self.catalog_tree.identify_column(event.x)
        item = self.catalog_tree.identify_row(event.y)
        if not item:
            return
        if column == "#6":
            self._begin_catalog_qty_edit(item)
            return
        if column == "#7":
            self.print_catalog_barcode_for_product(int(item), event.x_root, event.y_root)

    def _render_catalog_tree(self, products: list[dict]) -> None:
        self.catalog_tree.delete(*self.catalog_tree.get_children())
        for product in products:
            kind = "комплект" if product.get("is_kit") else ""
            name = str(product.get("name") or "")
            if kind:
                name = f"{name} ({kind})"
            self.catalog_tree.insert(
                "",
                tk.END,
                iid=str(product.get("id")),
                values=(
                    product.get("code", ""),
                    product.get("sku", ""),
                    name,
                    product.get("group_name", ""),
                    product.get("unit_name", ""),
                    self._catalog_qty_display(str(product.get("id"))),
                    "▼ Печать ШК",
                ),
            )

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
            self.set_status(f"Товаров: {len(self.catalog_products)}")
            self._show_barcode_pick_menu(product, barcodes, x_root, y_root)

        def on_error(exc: Exception) -> None:
            self._background_error(exc)

        self.set_status("Загрузка штрихкодов...")
        self._run_task(worker, on_barcodes, on_error)

    def _apply_catalog_filter(self, query: str) -> None:
        if query:
            self.catalog_products = [p for p in self.catalog_products_all if self._catalog_matches(p, query)]
            status = f"Найдено: {len(self.catalog_products)} из {len(self.catalog_products_all)}"
        else:
            self.catalog_products = list(self.catalog_products_all)
            status = f"Товаров: {len(self.catalog_products)}"
        self._render_catalog_tree(self.catalog_products)
        self.set_status(status)

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
        if self.notebook.tab(tab_id, "text") == "Номенклатура":
            self.load_catalog()

    def _on_catalog_search_key(self, _event=None) -> None:
        if self._catalog_search_job is not None:
            self.after_cancel(self._catalog_search_job)
        self._catalog_search_job = self.after(400, self.load_catalog)

    def _catalog_matches(self, product: dict, query: str) -> bool:
        fields = (
            product.get("name", ""),
            product.get("sku", ""),
            product.get("code", ""),
            product.get("external_code", ""),
            product.get("group_name", ""),
        )
        return any(query in str(field or "").casefold() for field in fields)

    def _print_catalog_barcode_item(self, product: dict, barcode_item: dict[str, str]) -> None:
        barcode = str(barcode_item.get("barcode") or "").strip()
        print_name = barcode_print_name(product, barcode_item)
        sku = str(product.get("sku") or barcode)
        copies = self._resolve_catalog_copies(str(product.get("id") or ""))
        label_w, label_h = self._label_size_mm()

        def worker():
            pdf = barcode_label_pdf(
                barcode,
                sku=sku,
                name=print_name,
                width_mm=label_w,
                height_mm=label_h,
            )
            print_pdf(
                pdf,
                sumatra=self.config_data["sumatra"],
                printer=self.config_data["printer"],
                print_settings=self.config_data["print_settings"],
                copies=copies,
            )
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
            print_pdf(
                pdf,
                sumatra=self.config_data["sumatra"],
                printer=self.config_data["printer"],
                print_settings=self.config_data["print_settings"],
            )
            return barcode

        self._run_in_background(
            "Печать штрихкода...",
            worker,
            lambda b: self.set_status(f"ШК {b} отправлен на печать"),
        )


class SettingsWindow(tk.Toplevel):
    def __init__(self, parent, config: dict[str, str], on_save) -> None:
        super().__init__(parent)
        self.title("Настройки")
        self.geometry("720x380")
        self.resizable(True, False)
        self.on_save = on_save
        self.vars = {
            "server_url": tk.StringVar(value=config.get("server_url", "")),
            "sumatra": tk.StringVar(value=config.get("sumatra", "")),
            "printer": tk.StringVar(value=config.get("printer", "")),
            "print_settings": tk.StringVar(value=config.get("print_settings", "")),
            "refresh_seconds": tk.StringVar(value=config.get("refresh_seconds", "30")),
        }
        self._build_ui()
        self.transient(parent)
        self.grab_set()

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill=tk.BOTH, expand=True)

        self._row(root, 0, "Адрес сервера", "server_url", "https://example.com")
        self._row(root, 1, "SumatraPDF.exe", "sumatra", r"C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe", browse=True)
        self._row(root, 2, "Имя принтера", "printer", "Если пусто, принтер по умолчанию")
        self._row(root, 3, "Параметры печати", "print_settings", "noscale,portrait,disable-auto-rotation,paper=47mm x 25mm")
        self._row(root, 4, "Автообновление, сек", "refresh_seconds", "30")

        hint = ttk.Label(
            root,
            text=(
                "Настройки сохраняются в config.env рядом с приложением. "
                "Размер этикетки задаётся в paper=ШИРИНАmm x ВЫСОТАmm (например paper=47mm x 25mm). "
                "Для печати PDF нужен SumatraPDF. Вход выполняется логином и паролем от панели склада."
            ),
            wraplength=660,
            foreground="#555",
        )
        hint.grid(row=5, column=0, columnspan=3, sticky="we", pady=(12, 8))

        buttons = ttk.Frame(root)
        buttons.grid(row=6, column=0, columnspan=3, sticky="e")
        ttk.Button(buttons, text="Отмена", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Сохранить", command=self.save).pack(side=tk.RIGHT, padx=8)

        root.columnconfigure(1, weight=1)

    def _row(self, root, row: int, label: str, key: str, placeholder: str = "", *, browse: bool = False) -> None:
        ttk.Label(root, text=label).grid(row=row, column=0, sticky="w", pady=5, padx=(0, 8))
        entry = ttk.Entry(root, textvariable=self.vars[key])
        entry.grid(row=row, column=1, sticky="we", pady=5)
        if browse:
            ttk.Button(root, text="Выбрать…", command=lambda: self.browse_file(key)).grid(row=row, column=2, padx=(8, 0))
        else:
            ttk.Label(root, text="").grid(row=row, column=2)

    def browse_file(self, key: str) -> None:
        path = filedialog.askopenfilename(
            title="Выберите SumatraPDF.exe",
            filetypes=[("Executable", "*.exe"), ("All files", "*.*")],
        )
        if path:
            self.vars[key].set(path)

    def save(self) -> None:
        config = {key: var.get().strip() for key, var in self.vars.items()}
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
    login = LoginWindow()
    login.mainloop()
    if login.client is None:
        return
    app = PackingApp(login.config_data, login.client, login.user_name)
    app.mainloop()


if __name__ == "__main__":
    main()
