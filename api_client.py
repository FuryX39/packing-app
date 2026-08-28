from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests


class AuthError(Exception):
    """Ошибка входа или истёкшая сессия."""


class ApiError(Exception):
    """Ошибка API с текстом detail от сервера."""

    def __init__(self, message: str, *, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


def derive_api_url(server_url: str, api_url: str = "") -> str:
    """Адрес run_api.py. Если пусто — тот же хост, порт 8766."""
    explicit = (api_url or "").strip().rstrip("/")
    if explicit:
        return explicit
    raw = (server_url or "").strip().rstrip("/")
    if not raw:
        return ""
    parts = urlsplit(raw if "://" in raw else f"http://{raw}")
    host = parts.hostname or "127.0.0.1"
    scheme = parts.scheme or "http"
    userinfo = ""
    if parts.username:
        userinfo = parts.username
        if parts.password:
            userinfo += f":{parts.password}"
        userinfo += "@"
    netloc = f"{userinfo}{host}:8766"
    return urlunsplit((scheme, netloc, "", "", "")).rstrip("/")


def _detail_from_response(resp: requests.Response) -> str:
    try:
        data = resp.json()
    except Exception:
        text = (resp.text or "").strip()
        return text or f"HTTP {resp.status_code}"
    detail = data.get("detail") if isinstance(data, dict) else None
    if isinstance(detail, list):
        parts: list[str] = []
        for item in detail:
            if isinstance(item, dict):
                parts.append(str(item.get("msg") or item))
            else:
                parts.append(str(item))
        return "; ".join(parts) if parts else f"HTTP {resp.status_code}"
    if detail not in (None, ""):
        return str(detail)
    return f"HTTP {resp.status_code}"


class WarehouseApiClient:
    """Веб-панель (/api/warehouse) + десктоп API (/api/v1) с разными cookie."""

    def __init__(self, server_url: str, api_url: str = "") -> None:
        self.server_url = server_url.rstrip("/")
        self.api_url = derive_api_url(self.server_url, api_url)
        self.session = requests.Session()
        self.api_session = requests.Session()
        self.api_ok = False
        self.api_error = ""

    def _url(self, path: str) -> str:
        return self.server_url + path

    def _api_url(self, path: str) -> str:
        return self.api_url + path

    def login(self, login: str, password: str) -> dict:
        resp = self.session.post(
            self._url("/api/warehouse/login"),
            data={"login": login.strip(), "password": password},
            timeout=20,
        )
        if resp.status_code == 401:
            raise AuthError("Неверный логин или пароль")
        if resp.status_code == 400:
            detail = _detail_from_response(resp)
            raise AuthError(detail or "Введите логин и пароль")
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            raise AuthError(str(exc)) from exc
        session = self.get_session()
        self._try_login_api(login, password)
        return session

    def _try_login_api(self, login: str, password: str) -> None:
        self.api_ok = False
        self.api_error = ""
        if not self.api_url:
            self.api_error = "Не задан адрес API (WAREHOUSE_API_URL / порт 8766)"
            return
        try:
            resp = self.api_session.post(
                self._api_url("/api/v1/login"),
                json={"login": login.strip(), "password": password},
                timeout=20,
            )
        except requests.RequestException as exc:
            self.api_error = (
                f"Не удалось подключиться к API ({self.api_url}): {exc}. "
                "Запустите python run_api.py (порт 8766)."
            )
            return
        if resp.status_code in (400, 401):
            self.api_error = _detail_from_response(resp) or "Ошибка входа в API"
            return
        if resp.status_code >= 400:
            self.api_error = _detail_from_response(resp)
            return
        self.api_ok = True

    def logout(self) -> None:
        try:
            self.session.post(self._url("/api/warehouse/logout"), timeout=10)
        except requests.RequestException:
            pass
        try:
            if self.api_url:
                self.api_session.post(self._api_url("/api/v1/logout"), timeout=10)
        except requests.RequestException:
            pass
        self.session.cookies.clear()
        self.api_session.cookies.clear()
        self.api_ok = False

    def get_session(self) -> dict:
        resp = self.session.get(self._url("/api/warehouse/session"), timeout=20)
        if resp.status_code == 401:
            raise AuthError("Требуется вход")
        resp.raise_for_status()
        return resp.json()

    def _raise_http(self, resp: requests.Response) -> None:
        if resp.status_code == 401:
            raise AuthError("Сессия истекла — войдите снова")
        if resp.status_code >= 400:
            raise ApiError(_detail_from_response(resp), status_code=resp.status_code)
        resp.raise_for_status()

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        resp = self.session.request(method, self._url(path), timeout=kwargs.pop("timeout", 30), **kwargs)
        self._raise_http(resp)
        return resp

    def _api_request(self, method: str, path: str, **kwargs) -> requests.Response:
        if not self.api_url:
            raise ApiError("Не задан адрес API (run_api.py)")
        if not self.api_ok:
            raise ApiError(self.api_error or "Нет сессии API — войдите снова")
        resp = self.api_session.request(
            method,
            self._api_url(path),
            timeout=kwargs.pop("timeout", 30),
            **kwargs,
        )
        self._raise_http(resp)
        return resp

    def get_my_fbo_supplies(self) -> list[dict]:
        resp = self._request("GET", "/api/warehouse/marketplaces/ozon-fbo/my-supplies", timeout=20)
        return resp.json().get("supplies") or []

    def get_fbo_supply(self, supply_id: int) -> dict:
        resp = self._request("GET", f"/api/warehouse/marketplaces/ozon-fbo/supplies/{int(supply_id)}", timeout=20)
        return resp.json().get("supply") or {}

    def save_cargoes(self, supply_id: int, cargoes: list[dict]) -> dict:
        resp = self._request(
            "PUT",
            f"/api/warehouse/marketplaces/ozon-fbo/supplies/{int(supply_id)}/cargoes",
            json={"cargoes": cargoes},
            timeout=30,
        )
        return resp.json().get("supply") or {}

    def download_labels_pdf(self, supply_id: int) -> bytes:
        resp = self._request(
            "GET",
            f"/api/warehouse/marketplaces/ozon-fbo/supplies/{int(supply_id)}/labels.pdf",
            timeout=120,
        )
        return resp.content

    def get_my_tasks(self) -> list[dict]:
        resp = self._request("GET", "/api/warehouse/tasks/my", timeout=30)
        return resp.json().get("tasks") or []

    def get_task(self, task_id: int) -> dict:
        resp = self._request("GET", f"/api/warehouse/tasks/{int(task_id)}", timeout=30)
        return resp.json().get("task") or {}

    def get_task_statuses(self) -> list[dict]:
        resp = self._request("GET", "/api/warehouse/tasks/statuses", timeout=20)
        return resp.json().get("task_statuses") or []

    def patch_task(self, task_id: int, body: dict) -> dict:
        resp = self._request(
            "PATCH",
            f"/api/warehouse/tasks/{int(task_id)}",
            json=body,
            timeout=30,
        )
        return resp.json().get("task") or {}

    def download_task_attachment(self, task_id: int, attachment_id: int) -> bytes:
        resp = self._request(
            "GET",
            f"/api/warehouse/tasks/{int(task_id)}/attachments/{int(attachment_id)}",
            timeout=120,
        )
        return resp.content

    def search_catalog_products(self, q: str | None = None) -> list[dict]:
        params: dict[str, str] = {}
        if q is not None and q.strip():
            params["q"] = q.strip()
        resp = self._request(
            "GET",
            "/api/warehouse/catalog/products",
            params=params,
            timeout=60,
        )
        return resp.json().get("products") or []

    def get_catalog_product(self, product_id: int) -> dict:
        resp = self._request(
            "GET",
            f"/api/warehouse/catalog/products/{int(product_id)}",
            timeout=20,
        )
        return resp.json().get("product") or {}

    def add_catalog_barcode(
        self,
        product_id: int,
        barcode: str,
        *,
        label: str = "",
        group: str = "",
    ) -> dict:
        resp = self._request(
            "POST",
            f"/api/warehouse/catalog/products/{int(product_id)}/barcodes",
            json={
                "barcode": str(barcode or "").strip(),
                "label": str(label or "").strip(),
                "group": str(group or "").strip(),
            },
            timeout=20,
        )
        return resp.json()

    # --- FBS packing (desktop API) ---

    def fbs_my_jobs(self) -> list[dict]:
        resp = self._api_request("GET", "/api/v1/fbs-packing/my", timeout=30)
        return resp.json().get("jobs") or []

    def fbs_open_job(self, job_id: int) -> dict:
        resp = self._api_request("GET", f"/api/v1/fbs-packing/jobs/{int(job_id)}/pack", timeout=30)
        return resp.json().get("job") or {}

    def fbs_scan_product(
        self,
        job_id: int,
        barcode: str,
        *,
        batch: bool = False,
        include_pdf: bool = True,
        auto_close: bool = False,
    ) -> dict[str, Any]:
        resp = self._api_request(
            "POST",
            f"/api/v1/fbs-packing/jobs/{int(job_id)}/scan-product",
            json={
                "barcode": barcode,
                "batch": bool(batch),
                "include_pdf": bool(include_pdf),
                "auto_close": bool(auto_close),
            },
            timeout=60,
        )
        return resp.json()

    def fbs_pick_sku(
        self,
        job_id: int,
        *,
        sku: str = "",
        product_id: int | None = None,
        batch: bool = False,
        include_pdf: bool = True,
        auto_close: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "sku": sku,
            "batch": bool(batch),
            "include_pdf": bool(include_pdf),
            "auto_close": bool(auto_close),
        }
        if product_id is not None:
            body["product_id"] = int(product_id)
        resp = self._api_request(
            "POST",
            f"/api/v1/fbs-packing/jobs/{int(job_id)}/pick-sku",
            json=body,
            timeout=60,
        )
        return resp.json()

    def fbs_scan_label(self, job_id: int, barcode: str) -> dict[str, Any]:
        resp = self._api_request(
            "POST",
            f"/api/v1/fbs-packing/jobs/{int(job_id)}/scan-label",
            json={"barcode": barcode},
            timeout=30,
        )
        return resp.json()

    def fbs_close_line(self, job_id: int, line_id: int) -> dict[str, Any]:
        resp = self._api_request(
            "POST",
            f"/api/v1/fbs-packing/jobs/{int(job_id)}/lines/{int(line_id)}/close",
            timeout=30,
        )
        return resp.json()

    def fbs_cancel_print(self, job_id: int, line_id: int) -> dict[str, Any]:
        resp = self._api_request(
            "POST",
            f"/api/v1/fbs-packing/jobs/{int(job_id)}/lines/{int(line_id)}/cancel-print",
            timeout=30,
        )
        return resp.json()

    def fbs_set_line_status(self, job_id: int, line_id: int, status: str) -> dict[str, Any]:
        resp = self._api_request(
            "POST",
            f"/api/v1/fbs-packing/jobs/{int(job_id)}/lines/{int(line_id)}/set-status",
            json={"status": str(status or "").strip()},
            timeout=30,
        )
        return resp.json()

    def fbs_download_line_pdf(self, job_id: int, line_id: int) -> bytes:
        resp = self._api_request(
            "GET",
            f"/api/v1/fbs-packing/jobs/{int(job_id)}/lines/{int(line_id)}/label",
            timeout=60,
        )
        return resp.content

    def fbs_download_line_labels_zip(self, job_id: int) -> bytes:
        resp = self._api_request(
            "GET",
            f"/api/v1/fbs-packing/jobs/{int(job_id)}/line-labels.zip",
            timeout=180,
        )
        return resp.content
