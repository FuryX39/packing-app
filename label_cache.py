"""Локальный кэш PDF-ярлыков FBS, чтобы печатать без ожидания API."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from packing_config import ROOT

CACHE_ROOT = ROOT / "cache" / "fbs_labels"


def line_pdf_path(job_id: int, line_id: int) -> Path:
    return CACHE_ROOT / str(int(job_id)) / f"{int(line_id)}.pdf"


def has_line_pdf(job_id: int, line_id: int) -> bool:
    path = line_pdf_path(job_id, line_id)
    return path.is_file() and path.stat().st_size > 4


def get_line_pdf(job_id: int, line_id: int) -> bytes | None:
    path = line_pdf_path(job_id, line_id)
    if not path.is_file():
        return None
    data = path.read_bytes()
    if not data.startswith(b"%PDF"):
        return None
    return data


def put_line_pdf(job_id: int, line_id: int, data: bytes) -> None:
    if not data or not data.startswith(b"%PDF"):
        return
    path = line_pdf_path(job_id, line_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def job_line_ids(job: dict | None) -> list[int]:
    ids: list[int] = []
    for line in (job or {}).get("lines") or []:
        raw = line.get("id") if isinstance(line, dict) else None
        if raw in (None, ""):
            continue
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    return ids


def cached_count(job_id: int, line_ids: list[int]) -> tuple[int, int]:
    have = sum(1 for line_id in line_ids if has_line_pdf(job_id, line_id))
    return have, len(line_ids)


def job_labels_ready(job: dict | None) -> bool:
    if not job or not job.get("id"):
        return False
    ids = job_line_ids(job)
    if not ids:
        return False
    return all(has_line_pdf(int(job["id"]), line_id) for line_id in ids)


def save_zip(job_id: int, zip_bytes: bytes) -> int:
    saved = 0
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = Path(info.filename).name
            if not name.lower().endswith(".pdf"):
                continue
            try:
                line_id = int(name[:-4])
            except ValueError:
                continue
            data = archive.read(info)
            put_line_pdf(job_id, line_id, data)
            saved += 1
    return saved
