from __future__ import annotations

import requests


class AuthError(Exception):
    """Ошибка входа или истёкшая сессия."""


class WarehouseApiClient:
    def __init__(self, server_url: str) -> None:
        self.server_url = server_url.rstrip("/")
        self.session = requests.Session()

    def _url(self, path: str) -> str:
        return self.server_url + path

    def login(self, login: str, password: str) -> dict:
        resp = self.session.post(
            self._url("/api/warehouse/login"),
            data={"login": login.strip(), "password": password},
            timeout=20,
        )
        if resp.status_code == 401:
            raise AuthError("Неверный логин или пароль")
        if resp.status_code == 400:
            detail = resp.json().get("detail") if resp.headers.get("content-type", "").startswith("application/json") else ""
            raise AuthError(str(detail or "Введите логин и пароль"))
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            raise AuthError(str(exc)) from exc
        return self.get_session()

    def logout(self) -> None:
        try:
            self.session.post(self._url("/api/warehouse/logout"), timeout=10)
        except requests.RequestException:
            pass
        self.session.cookies.clear()

    def get_session(self) -> dict:
        resp = self.session.get(self._url("/api/warehouse/session"), timeout=20)
        if resp.status_code == 401:
            raise AuthError("Требуется вход")
        resp.raise_for_status()
        return resp.json()

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        resp = self.session.request(method, self._url(path), timeout=kwargs.pop("timeout", 30), **kwargs)
        if resp.status_code == 401:
            raise AuthError("Сессия истекла — войдите снова")
        resp.raise_for_status()
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
