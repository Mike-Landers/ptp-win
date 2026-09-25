from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .manifest import parse_manifest
from .models import FileEntry, TorrentShare, format_bytes, is_magnet_link, normalize_link, safe_filename


class LinkClient:
    @staticmethod
    def fetch_link(link: str, save_path: Path, saved_record: dict | None = None) -> tuple[str, list[FileEntry], TorrentShare | None]:
        if is_magnet_link(link):
            name, files, share = LinkClient.fetch_magnet(link, save_path, saved_record)
            return name, files, share
        name, files = LinkClient.fetch_manifest(link)
        return name, files, None

    @staticmethod
    def fetch_manifest(link: str) -> tuple[str, list[FileEntry]]:
        normalized = normalize_link(link)
        parsed = urllib.parse.urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Paste an http(s)://, ptp://, or magnet:? link.")
        request = urllib.request.Request(normalized, headers={"User-Agent": "DropLink/1.0"})
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return parse_manifest(payload, normalized)

    @staticmethod
    def fetch_magnet(link: str, save_path: Path, saved_record: dict | None = None) -> tuple[str, list[FileEntry], TorrentShare]:
        try:
            import libtorrent as lt
        except ImportError as error:
            raise ValueError("Magnet links require libtorrent. Install dependencies with: python -m pip install -r requirements.txt") from error

        try:
            params = lt.parse_magnet_uri(link.strip())
        except RuntimeError as error:
            raise ValueError(f"Invalid magnet link: {error}") from error
        params.save_path = str(save_path.expanduser())
        session = lt.session()
        handle = session.add_torrent(params)
        deadline = time.monotonic() + 120
        while not handle.has_metadata():
            if time.monotonic() >= deadline:
                handle.pause()
                raise TimeoutError("Timed out waiting for torrent metadata from peers.")
            time.sleep(0.25)

        torrent = handle.torrent_file()
        files = torrent.files()
        entries = []
        saved_files = {int(item["index"]): item for item in (saved_record or {}).get("files", []) if "index" in item}
        for index in range(files.num_files()):
            name = files.file_path(index)
            size = files.file_size(index)
            kind = Path(name).suffix.lstrip(".").upper() or "File"
            selected = saved_files.get(index, {}).get("selected", True)
            entries.append(FileEntry(name=name, url="", size=format_bytes(size), kind=kind, selected=selected, torrent_index=index, size_bytes=size))
            handle.file_priority(index, 0)
        handle.pause()
        name = torrent.name() or "Magnet share"
        return name, entries, TorrentShare(handle=handle, session=session, link=link.strip(), name=name, paused_indexes=set(), lock=threading.Lock())

    @staticmethod
    def download(file: FileEntry, destination: Path, progress: callable) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / safe_filename(file.name)
        temporary = target.with_name(target.name + ".part")
        request = urllib.request.Request(file.url, headers={"User-Agent": "DropLink/1.0"})
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
            total = int(response.headers.get("Content-Length", "0"))
            copied = 0
            while True:
                block = response.read(1024 * 128)
                if not block:
                    break
                output.write(block)
                copied += len(block)
                progress(copied, total)
        os.replace(temporary, target)

    @staticmethod
    def download_torrent(files: list[FileEntry], share: TorrentShare, destination: Path, progress: callable) -> None:
        handle = share.handle
        handle.move_storage(str(destination))
        selected_indexes = {file.torrent_index for file in files}
        with share.lock:
            paused_indexes = set(share.paused_indexes)
        for index in range(handle.torrent_file().files().num_files()):
            handle.file_priority(index, 1 if index in selected_indexes and index not in paused_indexes else 0)
        handle.resume()
        wanted = {file.torrent_index: file for file in files}
        while True:
            status = handle.status()
            file_progress = handle.file_progress()
            snapshots: dict[int, tuple[int, int]] = {}
            completed = 0
            total = 0
            finished = True
            with share.lock:
                paused_indexes = set(share.paused_indexes)
            for index in selected_indexes:
                handle.file_priority(index, 0 if index in paused_indexes else 1)
            for index, file in wanted.items():
                size = handle.torrent_file().files().file_size(index)
                current = min(file_progress[index], size)
                snapshots[index] = (current, size)
                completed += current
                total += size
                if current < size:
                    finished = False
            progress(snapshots, completed, total)
            if finished:
                handle.pause()
                LinkClient.cleanup_completed_torrent(files, share, destination)
                return
            if status.errc and status.errc.value() != 0:
                handle.pause()
                raise OSError(status.errc.message())
            time.sleep(0.5)

    @staticmethod
    def cleanup_completed_torrent(selected_files: list[FileEntry], share: TorrentShare, destination: Path) -> None:
        handle = share.handle
        torrent_files = handle.torrent_file().files()
        selected_indexes = {file.torrent_index for file in selected_files}
        root = destination.expanduser().resolve()
        for index in range(torrent_files.num_files()):
            if index in selected_indexes:
                continue
            candidate = (root / Path(torrent_files.file_path(index))).resolve()
            if root not in candidate.parents:
                raise OSError(f"Refusing to remove a torrent file outside the download folder: {candidate}")
            if candidate.is_file() or candidate.is_symlink():
                candidate.unlink()
        share.session.remove_torrent(handle)
        with share.lock:
            share.paused_indexes.clear()
