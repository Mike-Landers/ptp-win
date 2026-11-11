import json
import queue
import threading
import time
import urllib.error
import urllib.parse
from pathlib import Path
from tkinter import messagebox

from ..models import FileEntry, TorrentShare, format_bytes, is_magnet_link, normalize_link
from ..transfer import LinkClient
from .base import UIBase
from .constants import APP_TITLE


LOAD_STALL_TIMEOUT_SECONDS = 60


def load_progress_timed_out(last_progress_at: float, current_time: float) -> bool:
    return current_time - last_progress_at >= LOAD_STALL_TIMEOUT_SECONDS


class DownloadsMixin(UIBase):
    def _set_global_download_state(self, paused: bool) -> None:
        if not self.torrent_share or not self.download_running:
            return
        with self.torrent_share.lock:
            self.torrent_share.paused = paused
        if paused:
            self.torrent_share.handle.pause()
            self.share_control_var.set("Resume all")
            self.status_var.set("All files paused. Resume when you are ready.")
        else:
            self.torrent_share.handle.resume()
            self.share_control_var.set("Pause all")
            self.status_var.set("All selected files are downloading...")
        self._update_download_controls()

    def stop_all_downloads(self) -> None:
        self._set_global_download_state(True)

    def resume_all_downloads(self) -> None:
        self._set_global_download_state(False)

    def load_demo(self) -> None:
        self.link_var.set("ptp://demo.local/share/aurora")
        self.torrent_share = None
        self.current_magnet_link = ""
        self.file_paused = {}
        self._set_files("Aurora design archive", [
            FileEntry("aurora-brand-kit.zip", "https://example.com/aurora-brand-kit.zip", "48.2 MB", "ZIP"),
            FileEntry("release-notes.pdf", "https://example.com/release-notes.pdf", "1.4 MB", "PDF"),
            FileEntry("product-preview.mp4", "https://example.com/product-preview.mp4", "126 MB", "MP4", False),
        ])
        self.status_var.set("Demo share loaded. Review the selection before downloading.")

    def load_link(self) -> None:
        link = self.link_var.get().strip()
        if not link:
            messagebox.showinfo(APP_TITLE, "Paste a peer link first.")
            return
        if self.busy:
            return
        saved = self.store.get(link)
        if saved and saved.get("destination"):
            self.destination_var.set(saved["destination"])
        destination = Path(self.destination_var.get()).expanduser()
        self.load_request_id += 1
        request_id = self.load_request_id
        cancel_event = threading.Event()
        self.load_cancel_event = cancel_event
        self.load_in_progress = True
        self.load_progress_percent = 0
        self.load_last_progress_at = time.monotonic()
        share_name = self._loading_share_name(link, saved)
        self.share_title_var.set(f"Loading share [{share_name}]")
        self.total_progress_label_var.set("Loading: 0% complete")
        self.progress_var.set(0)
        self._set_busy(True)
        self.status_var.set("Connecting to peer...")
        threading.Thread(
            target=self._load_worker,
            args=(link, request_id, destination, saved, cancel_event),
            daemon=True,
        ).start()

    @staticmethod
    def _loading_share_name(link: str, saved: dict | None) -> str:
        if saved and saved.get("name"):
            return str(saved["name"])
        if is_magnet_link(link):
            display_name = urllib.parse.parse_qs(urllib.parse.urlparse(link).query).get("dn")
            return display_name[0] if display_name else "Unnamed share"
        parsed = urllib.parse.urlparse(normalize_link(link))
        path_name = Path(urllib.parse.unquote(parsed.path)).stem
        return path_name or parsed.hostname or "Unknown share"

    def _load_worker(
        self,
        link: str,
        request_id: int,
        destination: Path,
        saved: dict | None,
        cancel_event: threading.Event,
    ) -> None:
        try:
            result = LinkClient.fetch_link(
                link,
                destination,
                saved,
                progress=lambda status, percentage: self.events.put(
                    ("load_progress", (request_id, status, percentage, time.monotonic()))
                ),
                cancel_event=cancel_event,
            )
            self.events.put(("loaded", (request_id, result, destination)))
        except (ValueError, json.JSONDecodeError, urllib.error.URLError, TimeoutError, OSError, RuntimeError) as error:
            self.events.put(("load_error", (request_id, str(error))))

    def toggle_file_pause(self, file: FileEntry) -> None:
        if not self.torrent_share or file.torrent_index is None:
            return
        file_key = id(file)
        paused = not self.file_paused.get(file_key, False)
        self.file_paused[file_key] = paused
        with self.torrent_share.lock:
            if paused:
                self.torrent_share.paused_indexes.add(file.torrent_index)
            else:
                self.torrent_share.paused_indexes.discard(file.torrent_index)
        if self.download_running:
            self.torrent_share.handle.file_priority(file.torrent_index, 0 if paused else 1)
        button = self.file_pause_buttons.get(file_key)
        if button:
            button.configure(text="Resume" if paused else "Pause")

    def toggle_share_pause(self) -> None:
        if not self.torrent_share or not self.download_running:
            return
        with self.torrent_share.lock:
            paused = not self.torrent_share.paused
        self._set_global_download_state(paused)

    def _save_current_record(self, destination: Path) -> None:
        if not self.torrent_share or not self.current_magnet_link:
            return
        previous = self.store.get(self.current_magnet_link)
        previous_files = {int(item["index"]): item for item in (previous or {}).get("files", []) if "index" in item}
        files = []
        for file in self.files:
            file_key = id(file)
            old = previous_files.get(file.torrent_index, {}) if file.torrent_index is not None else {}
            files.append({
                "index": file.torrent_index,
                "name": file.name,
                "size": file.size,
                "selected": self.file_vars[file_key].get(),
                "completed": old.get("completed", False) or self.file_progress_values[file_key].get() >= 100,
            })
        self.store.save({
            "link": self.current_magnet_link,
            "name": self.torrent_share.name,
            "destination": str(destination),
            "files": files,
        })
        self._refresh_resume_button()

    def _save_torrent_progress(self, snapshots: dict[int, tuple[int, int]]) -> None:
        if not self.torrent_share:
            return
        previous = self.store.get(self.current_magnet_link) or {}
        previous_files = {int(item["index"]): item for item in previous.get("files", []) if "index" in item}
        for index, (current, total) in snapshots.items():
            item = previous_files.setdefault(index, {"index": index})
            item["completed"] = current >= total
        for file in self.files:
            if file.torrent_index in previous_files:
                previous_files[file.torrent_index]["selected"] = self.file_vars[id(file)].get()
        previous.update({"link": self.current_magnet_link, "name": self.torrent_share.name, "destination": str(self.current_destination), "files": list(previous_files.values())})
        self.store.save(previous)

    def _remove_completed_record(self) -> None:
        if self.current_magnet_link:
            self.store.remove(self.current_magnet_link)
            self._refresh_resume_button()

    def download_selected(self) -> None:
        selected = [file for file in self.files if self.file_vars[id(file)].get()]
        if not selected:
            messagebox.showinfo(APP_TITLE, "Select at least one file to download.")
            return
        if not self.destination_var.get().strip():
            messagebox.showinfo(APP_TITLE, "Choose a download location first.")
            return
        if self.torrent_share:
            destination = self.confirm_magnet_download(selected)
            if destination is None:
                return
        else:
            destination = Path(self.destination_var.get()).expanduser()
        self._set_busy(True)
        self.progress_var.set(0)
        self.total_progress_label_var.set("All selected: 0%")
        self.current_destination = destination
        self.download_running = True
        share_name = self.manifest_name or self.share_title_var.get()
        known_sizes = [file.size_bytes for file in selected]
        selected_size = (
            format_bytes(sum(size for size in known_sizes if size is not None))
            if all(size is not None for size in known_sizes)
            else "Size unknown"
        )
        if self.torrent_share:
            self.torrent_share.paused = False
            self._save_current_record(destination)
            self.share_control_var.set("Pause all")
        self._update_download_controls()
        self.status_var.set(f"Preparing {len(selected)} file(s)...")
        threading.Thread(
            target=self._download_worker,
            args=(selected, destination, self.torrent_share, share_name, selected_size),
            daemon=True,
        ).start()

    def _download_worker(
        self,
        files: list[FileEntry],
        destination: Path,
        torrent_share: TorrentShare | None,
        share_name: str,
        selected_size: str,
    ) -> None:
        completed = 0
        failures: list[str] = []
        if torrent_share:
            try:
                self.events.put(("status", "Downloading selected torrent files..."))
                LinkClient.download_torrent(
                    files,
                    torrent_share,
                    destination,
                    lambda snapshots, copied, total: self.events.put(("torrent_progress", (snapshots, copied, total))),
                )
                completed = len(files)
            except (OSError, RuntimeError) as error:
                failures.append(f"Torrent download: {error}")
        else:
            for file in files:
                try:
                    self.events.put(("status", f"Downloading {file.name}..."))
                    LinkClient.download(file, destination, lambda copied, total: self.events.put(("progress", (completed, len(files), copied, total))))
                    completed += 1
                except (urllib.error.URLError, OSError, ValueError) as error:
                    failures.append(f"{file.name}: {error}")
        try:
            LinkClient.cleanup_temporary_downloads(files, destination)
        except OSError as error:
            failures.append(f"Temporary file cleanup: {error}")
        self.events.put(("finished", (completed, len(files), failures, destination, share_name, selected_size)))

    def _process_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "load_progress":
                    request_id, status, percentage, reported_at = payload
                    if request_id != self.load_request_id or not self.load_in_progress:
                        continue
                    self.load_last_progress_at = reported_at
                    self.load_progress_percent = max(
                        self.load_progress_percent,
                        max(0, min(100, int(percentage))),
                    )
                    self.status_var.set(status)
                    self.total_progress_label_var.set(
                        f"Loading: {self.load_progress_percent}% complete"
                    )
                    self.progress_var.set(self.load_progress_percent)
                elif event == "loaded":
                    request_id, result, destination = payload
                    if request_id != self.load_request_id or not self.load_in_progress:
                        continue
                    name, files, torrent_share = result
                    self.load_in_progress = False
                    self.load_cancel_event = None
                    self.torrent_share = torrent_share
                    self.current_magnet_link = torrent_share.link if torrent_share else ""
                    self.current_destination = destination
                    self._set_files(name, files)
                    if torrent_share:
                        self._save_current_record(self.current_destination)
                    self._set_busy(False)
                    self.status_var.set(f"{name} loaded. Confirm the files you want to download.")
                elif event == "load_error":
                    request_id, error = payload
                    if request_id != self.load_request_id or not self.load_in_progress:
                        continue
                    self._fail_loading_share(error)
                elif event == "status":
                    self.status_var.set(payload)
                elif event == "progress":
                    completed, total_files, copied, total_bytes = payload
                    current = (copied / total_bytes * 100) if total_bytes else 0
                    self.progress_var.set(((completed + current / 100) / total_files) * 100)
                elif event == "torrent_progress":
                    snapshots, copied, total = payload
                    self._save_torrent_progress(snapshots)
                    for file in self.files:
                        if file.torrent_index not in snapshots:
                            continue
                        current, file_total = snapshots[file.torrent_index]
                        percentage = (current / file_total * 100) if file_total else 100
                        file_key = id(file)
                        self.file_progress_values[file_key].set(percentage)
                        self.file_progress_vars[file_key].set(f"{percentage:.0f}%")
                    overall = (copied / total * 100) if total else 0
                    self.progress_var.set(overall)
                    self.total_progress_label_var.set(f"All selected: {overall:.0f}%")
                elif event == "finished":
                    completed, total_files, failures, destination, share_name, selected_size = payload
                    was_complete = completed == total_files and not failures
                    self._set_busy(False)
                    self.download_running = False
                    self.share_control_var.set("Pause all")
                    if was_complete:
                        if self.torrent_share:
                            self._remove_completed_record()
                        self._clear_loaded_share()
                        self._set_busy(False)
                        self.status_var.set("Use Load files to choose a peer link")
                        messagebox.showinfo(APP_TITLE, f"Download {selected_size} finished for {share_name}")
                        continue
                    self._update_download_controls()
                    self.progress_var.set(0)
                    self.total_progress_label_var.set("All selected: 0%")
                    self.status_var.set(f"{completed} of {total_files} file(s) downloaded to {destination}")
                    if failures:
                        messagebox.showwarning(APP_TITLE, "Some files could not be downloaded:\n\n" + "\n".join(failures))
                    else:
                        messagebox.showwarning(APP_TITLE, "The selected files were not fully downloaded.")
        except queue.Empty:
            pass
        if (
            self.load_in_progress
            and load_progress_timed_out(self.load_last_progress_at, time.monotonic())
        ):
            if self.load_cancel_event:
                self.load_cancel_event.set()
            self._fail_loading_share("No loading progress was made for 1 minute.")
        self.after(100, self._process_events)

    def _fail_loading_share(self, error: str) -> None:
        if self.load_cancel_event:
            self.load_cancel_event.set()
        self.load_request_id += 1
        self.load_in_progress = False
        self.load_cancel_event = None
        self._clear_loaded_share()
        self._set_busy(False)
        self.status_var.set("Could not load this link.")
        messagebox.showerror("Failed to load", error)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.file_menu.entryconfigure(0, state="disabled" if busy else "normal")
        self._update_selection()

    def _update_download_controls(self) -> None:
        state = "normal" if self.download_running and self.torrent_share else "disabled"
        self.pause_button.configure(state=state)
        for button in self.file_pause_buttons.values():
            button.configure(state=state)
        if hasattr(self, "file_menu"):
            paused = bool(self.torrent_share and self.torrent_share.paused)
            self.file_menu.entryconfigure(self.stop_all_menu_index, state="normal" if state == "normal" and not paused else "disabled")
            self.file_menu.entryconfigure(self.resume_all_menu_index, state="normal" if state == "normal" and paused else "disabled")
