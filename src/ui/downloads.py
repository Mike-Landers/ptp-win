import queue
import threading
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox

from ..models import FileEntry, TorrentShare, format_bytes
from ..transfer import LinkClient
from .base import UIBase
from .constants import APP_TITLE
from .selection import (
    ShareSelectionWindow,
    metadata_load_timed_out as _metadata_load_timed_out,
    magnet_link_name,
)


def metadata_load_timed_out(
    load_started_at: float,
    current_time: float,
    metadata_loaded: bool = False,
) -> bool:
    return _metadata_load_timed_out(load_started_at, current_time, metadata_loaded)


@dataclass
class DownloadJob:
    files: list[FileEntry]
    destination: Path
    torrent_share: TorrentShare | None
    link: str
    share_name: str
    selected_size: str
    progress: float = 0
    status: str = "Preparing download"
    downloaded_bytes: int = 0
    progress_reported: bool = False


class DownloadsMixin(UIBase):
    def _set_global_download_state(self, paused: bool) -> None:
        shares = [
            job.torrent_share
            for job in self.download_jobs.values()
            if job.torrent_share is not None
        ]
        if not shares:
            return
        for share in shares:
            with share.lock:
                share.paused = paused
            if paused:
                share.handle.pause()
            else:
                share.handle.resume()
        if paused:
            self.status_var.set("All files paused. Resume when you are ready.")
        else:
            self.status_var.set("All selected files are downloading...")
        for request_id, job in self.download_jobs.items():
            if job.torrent_share:
                job.status = "Paused" if paused else "Downloading"
                self._refresh_download_row(request_id, job)
        self._update_download_controls()

    def stop_all_downloads(self) -> None:
        self._set_global_download_state(True)

    def resume_all_downloads(self) -> None:
        self._set_global_download_state(False)

    def load_link(self) -> None:
        link = self.link_var.get().strip()
        if not link:
            messagebox.showinfo(APP_TITLE, "Paste a peer link first.")
            return
        if link in self.selection_windows:
            self.selection_windows[link].lift()
            return
        if any(job.link == link for job in self.download_jobs.values()):
            messagebox.showinfo(APP_TITLE, "This link is already downloading.")
            return
        saved = self.store.get(link)

        saved_destination = saved.get("destination") if saved else None
        destination = Path(saved_destination or self.destination_var.get()).expanduser()
        window = ShareSelectionWindow(
            self,
            link,
            destination,
            saved,
            self.store,
            self._start_download,
            self._selection_window_closed,
        )
        self.selection_windows[link] = window

    def toggle_share_pause(self) -> None:
        shares = [
            job.torrent_share
            for job in self.download_jobs.values()
            if job.torrent_share is not None
        ]
        if not shares:
            return
        self._set_global_download_state(not all(share.paused for share in shares))

    def _save_torrent_progress(self, job: DownloadJob, snapshots: dict[int, tuple[int, int]]) -> None:
        if not job.torrent_share:
            return
        previous = self.store.get(job.link) or {}
        previous_files = {int(item["index"]): item for item in previous.get("files", []) if "index" in item}
        for index, (current, total) in snapshots.items():
            item = previous_files.setdefault(index, {"index": index})
            item["completed"] = current >= total
        selected_indexes = {
            file.torrent_index
            for file in job.files
            if file.torrent_index is not None
        }
        for index, item in previous_files.items():
            item["selected"] = index in selected_indexes
        previous.update({
            "link": job.link,
            "name": job.torrent_share.name,
            "destination": str(job.destination),
            "files": list(previous_files.values()),
        })
        self.store.save(previous)

    def _remove_completed_record(self, link: str) -> None:
        if link:
            self.store.remove(link)
            self._refresh_resume_button()

    def _selection_window_closed(self, link: str, window: ShareSelectionWindow) -> None:
        if self.selection_windows.get(link) is window:
            del self.selection_windows[link]

    def _start_download(
        self,
        link: str,
        share_name: str,
        selected: list[FileEntry],
        torrent_share: TorrentShare | None,
        destination: Path,
    ) -> None:
        if any(job.link == link for job in self.download_jobs.values()):
            messagebox.showinfo(APP_TITLE, "This link is already downloading.")
            if torrent_share:
                torrent_share.handle.pause()
                torrent_share.session.remove_torrent(torrent_share.handle)
            return

        known_sizes = [file.size_bytes for file in selected]
        selected_size = (
            format_bytes(sum(size for size in known_sizes if size is not None))
            if all(size is not None for size in known_sizes)
            else "Size unknown"
        )
        if torrent_share:
            torrent_share.paused = False
            self._refresh_resume_button()
        self.destination_var.set(str(destination))
        self.download_request_id += 1
        request_id = self.download_request_id
        self.current_download_id = request_id
        job = DownloadJob(
            files=selected,
            destination=destination,
            torrent_share=torrent_share,
            link=link,
            share_name=share_name,
            selected_size=selected_size,
        )
        self.download_jobs[request_id] = job
        self.download_running = True
        if self.downloads_tree.exists("empty"):
            self.downloads_tree.delete("empty")
        self.downloads_tree.insert(
            "",
            "end",
            iid=f"download-{request_id}",
            text=magnet_link_name(link) or share_name,
            values=(len(selected), job.selected_size, job.status),
        )
        self._refresh_download_summary()
        self._update_download_controls()
        self.status_var.set(f"Preparing {share_name}...")
        threading.Thread(
            target=self._download_worker,
            args=(request_id, job),
            daemon=True,
        ).start()

    def _refresh_download_row(self, request_id: int, job: DownloadJob) -> None:
        item = f"download-{request_id}"
        if hasattr(self, "downloads_tree") and self.downloads_tree.exists(item):
            status = (
                f"{job.downloaded_bytes / (1024 * 1024):.1f}MB / {job.progress:.0f}%"
                if job.progress_reported
                else job.status
            )
            self.downloads_tree.item(
                item,
                text=magnet_link_name(job.link) or job.share_name,
                values=(len(job.files), job.selected_size, status),
            )

    def _refresh_download_summary(self) -> None:
        count = len(self.download_jobs)
        average = (
            sum(job.progress for job in self.download_jobs.values()) / count
            if count
            else 0
        )
        if hasattr(self, "progress_var"):
            self.progress_var.set(average)
        if hasattr(self, "total_progress_label_var"):
            self.total_progress_label_var.set(
                f"Downloads: {count} active | {average:.0f}% average"
                if count
                else "No active downloads"
            )

    def _download_worker(
        self,
        request_id: int,
        job: DownloadJob,
    ) -> None:
        completed = 0
        downloaded_bytes = 0
        failures: list[str] = []
        if job.torrent_share:
            try:
                self.events.put(("status", (request_id, "Downloading selected torrent files...")))
                LinkClient.download_torrent(
                    job.files,
                    job.torrent_share,
                    job.destination,
                    lambda snapshots, copied, total: self.events.put(
                        ("torrent_progress", (request_id, snapshots, copied, total))
                    ),
                )
                completed = len(job.files)
            except (OSError, RuntimeError) as error:
                failures.append(f"Torrent download: {error}")
        else:
            for file in job.files:
                file_downloaded = 0

                def report_progress(copied: int, total_bytes: int) -> None:
                    nonlocal file_downloaded
                    file_downloaded = copied
                    self.events.put(
                        (
                            "progress",
                            (
                                request_id,
                                completed,
                                len(job.files),
                                copied,
                                total_bytes,
                                downloaded_bytes + copied,
                            ),
                        )
                    )

                try:
                    self.events.put(("status", (request_id, f"Downloading {file.name}...")))
                    LinkClient.download(
                        file,
                        job.destination,
                        report_progress,
                    )
                    downloaded_bytes += file_downloaded
                    completed += 1
                except (urllib.error.URLError, OSError, ValueError) as error:
                    failures.append(f"{file.name}: {error}")
        if not job.torrent_share:
            try:
                LinkClient.cleanup_temporary_downloads(job.files, job.destination)
            except OSError as error:
                failures.append(f"Temporary file cleanup: {error}")
        self.events.put(("finished", (request_id, completed, len(job.files), failures)))

    def _process_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "status":
                    request_id, status = payload
                    job = self.download_jobs.get(request_id)
                    if job:
                        job.status = (
                            "Paused"
                            if job.torrent_share and job.torrent_share.paused
                            else status
                        )
                        self._refresh_download_row(request_id, job)
                    if request_id == self.current_download_id:
                        self.status_var.set(status)
                elif event == "progress":
                    request_id, completed, total_files, copied, total_bytes, downloaded_bytes = payload
                    job = self.download_jobs.get(request_id)
                    if not job:
                        continue
                    current = (copied / total_bytes * 100) if total_bytes else 0
                    job.progress = ((completed + current / 100) / total_files) * 100
                    job.downloaded_bytes = downloaded_bytes
                    job.progress_reported = True
                    self._refresh_download_row(request_id, job)
                    self._refresh_download_summary()
                elif event == "torrent_progress":
                    request_id, snapshots, copied, total = payload
                    job = self.download_jobs.get(request_id)
                    if job and job.torrent_share:
                        self._save_torrent_progress(job, snapshots)
                    if not job:
                        continue
                    job.progress = (copied / total * 100) if total else 100
                    job.downloaded_bytes = copied
                    job.progress_reported = True
                    self._refresh_download_row(request_id, job)
                    self._refresh_download_summary()
                elif event == "finished":
                    request_id, completed, total_files, failures = payload
                    job = self.download_jobs.pop(request_id, None)
                    if not job:
                        continue
                    is_current = request_id == self.current_download_id
                    was_complete = completed == total_files and not failures
                    if is_current:
                        self.current_download_id = max(self.download_jobs, default=None)
                    self.download_running = bool(self.download_jobs)
                    row = f"download-{request_id}"
                    if hasattr(self, "downloads_tree") and self.downloads_tree.exists(row):
                        self.downloads_tree.delete(row)
                    if hasattr(self, "downloads_tree") and not self.download_jobs:
                        self.downloads_tree.insert(
                            "",
                            "end",
                            iid="empty",
                            text="No active downloads",
                            values=("", "", ""),
                        )
                    if was_complete:
                        if job.torrent_share:
                            self._remove_completed_record(job.link)
                        self._update_download_controls()
                        self._refresh_download_summary()
                        if self.download_jobs:
                            self.status_var.set(f"{job.share_name} finished; other downloads are still active.")
                        else:
                            self.status_var.set(f"Download finished for {job.share_name}.")
                        messagebox.showinfo(
                            APP_TITLE,
                            f"Download {job.selected_size} finished for {job.share_name}",
                        )
                        continue
                    self._update_download_controls()
                    self._refresh_download_summary()
                    self.status_var.set(
                        f"{completed} of {total_files} file(s) downloaded for {job.share_name}."
                    )
                    if failures:
                        messagebox.showwarning(
                            APP_TITLE,
                            f"Some files for {job.share_name} could not be downloaded:\n\n"
                            + "\n".join(failures),
                        )
                    else:
                        messagebox.showwarning(
                            APP_TITLE,
                            f"The selected files for {job.share_name} were not fully downloaded.",
                        )
        except queue.Empty:
            pass
        self.after(100, self._process_events)

    def _update_download_controls(self) -> None:
        active_shares = [
            job.torrent_share
            for job in self.download_jobs.values()
            if job.torrent_share is not None
        ]
        global_state = "normal" if active_shares else "disabled"
        if hasattr(self, "pause_button"):
            self.pause_button.configure(state=global_state)
        if active_shares and all(share.paused for share in active_shares):
            if hasattr(self, "share_control_var"):
                self.share_control_var.set("Resume all")
        else:
            if hasattr(self, "share_control_var"):
                self.share_control_var.set("Pause all")
        if hasattr(self, "file_menu"):
            all_paused = bool(active_shares) and all(share.paused for share in active_shares)
            self.file_menu.entryconfigure(
                self.stop_all_menu_index,
                state="normal" if active_shares and not all_paused else "disabled",
            )
            self.file_menu.entryconfigure(
                self.resume_all_menu_index,
                state="normal" if active_shares and all_paused else "disabled",
            )
