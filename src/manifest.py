from __future__ import annotations

import urllib.parse
from pathlib import Path

from .models import FileEntry, format_bytes


def parse_manifest(payload: object, source_url: str = "") -> tuple[str, list[FileEntry]]:
    if isinstance(payload, list):
        manifest_name = "Shared files"
        raw_files = payload
    elif isinstance(payload, dict):
        manifest_name = str(payload.get("name") or payload.get("title") or "Shared files")
        raw_files = payload.get("files", [])
    else:
        raise ValueError("The link did not contain a valid file manifest.")

    if not isinstance(raw_files, list):
        raise ValueError("The manifest's 'files' value must be a list.")

    files: list[FileEntry] = []
    for item in raw_files:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("download") or "").strip()
        name = str(item.get("name") or item.get("filename") or "").strip()
        if not url or not name:
            continue
        full_url = urllib.parse.urljoin(source_url, url)
        kind = str(item.get("type") or Path(name).suffix.lstrip(".").upper() or "File")
        size = format_bytes(item.get("size")) if item.get("size") is not None else ""
        files.append(FileEntry(name=name, url=full_url, size=size, kind=kind))

    if not files:
        raise ValueError("No downloadable files were found in this link.")
    return manifest_name, files
