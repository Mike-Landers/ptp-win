from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
import threading


TEXT_EXTENSIONS = {
    ".c", ".cc", ".cpp", ".css", ".csv", ".go", ".h", ".hpp", ".html", ".ini",
    ".java", ".js", ".json", ".jsx", ".log", ".md", ".py", ".rst", ".sql", ".svg",
    ".toml", ".ts", ".tsx", ".txt", ".xml", ".yaml", ".yml",
}


@dataclass
class FileEntry:
    name: str
    url: str
    size: str = ""
    kind: str = "File"
    selected: bool = True
    torrent_index: int | None = None
    size_bytes: int | None = None


@dataclass
class TorrentShare:
    handle: object
    session: object
    link: str
    name: str
    paused_indexes: set[int]
    lock: threading.Lock
    paused: bool = False


def format_bytes(value: object) -> str:
    try:
        amount = int(value)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return ""


def normalize_link(link: str) -> str:
    link = link.strip()
    if link.startswith("ptp://"):
        return "https://" + link[6:]
    return link


def is_magnet_link(link: str) -> bool:
    return link.strip().lower().startswith("magnet:?")


def safe_filename(name: str) -> str:
    clean_name = Path(name).name
    clean_name = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", clean_name).strip(" .")
    return clean_name or "download"


def file_extension(name: str) -> str:
    return Path(name).suffix.lower() or "[no extension]"


def is_text_file(name: str) -> bool:
    return file_extension(name) in TEXT_EXTENSIONS


def sort_share_files(files: list[FileEntry]) -> list[FileEntry]:
    return sorted(files, key=lambda file: (is_text_file(file.name), file_extension(file.name), file.name.casefold()))


def group_share_files(files: list[FileEntry]) -> list[tuple[bool, str, list[FileEntry]]]:
    groups: dict[tuple[bool, str], list[FileEntry]] = {}
    for file in sort_share_files(files):
        key = (is_text_file(file.name), file_extension(file.name))
        groups.setdefault(key, []).append(file)
    return [(is_text, extension, groups[(is_text, extension)]) for is_text, extension in groups]
