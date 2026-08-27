"""Фоновый кэш картинок каталога для FBS. UI не ждёт загрузку."""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from urllib.request import Request, urlopen

from packing_config import ROOT

CACHE_ROOT = ROOT / "cache" / "fbs_images"
TIMEOUT_SEC = 8
MAX_BYTES = 2_000_000

_lock = threading.Lock()
_inflight: set[str] = set()


def _key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8", "replace")).hexdigest()


def cache_path(url: str) -> Path:
    return CACHE_ROOT / _key(url)


def get_cached_bytes(url: str) -> bytes | None:
    raw = str(url or "").strip()
    if not raw:
        return None
    path = cache_path(raw)
    if not path.is_file():
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < 24:
        return None
    return data


def begin_fetch(url: str) -> bool:
    """True, если этот поток должен качать URL."""
    raw = str(url or "").strip()
    if not raw.startswith(("http://", "https://")):
        return False
    if get_cached_bytes(raw):
        return False
    with _lock:
        if raw in _inflight:
            return False
        _inflight.add(raw)
    return True


def finish_fetch(url: str) -> None:
    with _lock:
        _inflight.discard(str(url or "").strip())


def fetch_url(url: str) -> bytes | None:
    raw = str(url or "").strip()
    cached = get_cached_bytes(raw)
    if cached:
        return cached
    if not raw.startswith(("http://", "https://")):
        return None
    try:
        request = Request(raw, headers={"User-Agent": "warehouse-packing-app"})
        with urlopen(request, timeout=TIMEOUT_SEC) as resp:
            data = resp.read(MAX_BYTES + 1)
        if not data or len(data) > MAX_BYTES:
            return None
        CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        path = cache_path(raw)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        return data
    except Exception:
        return None
    finally:
        finish_fetch(raw)
