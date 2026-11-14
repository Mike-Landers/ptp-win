from __future__ import annotations

import json
import queue
import threading
import time
import urllib.parse
import urllib.error
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

from ..models import FileEntry, TorrentShare, is_magnet_link, is_text_file, sort_share_files
from ..storage import DownloadStore
from ..transfer import LinkClient
from .constants import APP_TITLE


LOAD_STALL_TIMEOUT_SECONDS = 120


def metadata_load_timed_out(
    load_started_at: float,
    current_time: float,
    metadata_loaded: bool = False,
) -> bool:
    return not metadata_loaded and current_time - load_started_at >= LOAD_STALL_TIMEOUT_SECONDS


def magnet_link_name(link: str) -> str | None:
    if not link.strip().lower().startswith("magnet:?"):
        return None
    names = urllib.parse.parse_qs(urllib.parse.urlparse(link.strip()).query).get("dn")
    return names[0] if names and names[0] else None


class ShareSelectionWindow(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Tk,
        link: str,
        destination: Path,
        saved_record: dict | None,
        store: DownloadStore,
        on_download: Callable[[str, str, list[FileEntry], TorrentShare | None, Path], None],
        on_close: Callable[[str, ShareSelectionWindow], None],
    ) -> None:
        super().__init__(parent)
        self.link = link
        self.destination = tk.StringVar(value=str(destination))
        self.store = store
        self.on_download = on_download
        self.on_close = on_close
        self.saved_record = saved_record
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.initial_destination = destination
        self.cancel_event = threading.Event()
        self.state_lock = threading.Lock()
        self.closed = False
        self.files: list[FileEntry] = []
        self.file_vars: dict[int, tk.BooleanVar] = {}
        self.row_files: dict[str, FileEntry] = {}
        self.name = "Loading share"
        self.magnet_name = magnet_link_name(link)
        self.torrent_share: TorrentShare | None = None
        self.load_progress_percent = 0
        self.load_started_at = time.monotonic()
        self.metadata_loaded = False

        self.title(self.magnet_name or "Load and select files")
        self.geometry("760x800")
        self.minsize(620, 420)
        self.protocol("WM_DELETE_WINDOW", self.close)

        frame = ttk.Frame(self, style="App.TFrame", padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Loading share metadata", style="CardTitle.TLabel").pack(anchor="w")
        self.name_var = tk.StringVar(value="Connecting to peer...")
        ttk.Label(frame, textvariable=self.name_var, style="Subtitle.TLabel").pack(anchor="w", pady=(3, 10))
        self.progress_var = tk.DoubleVar(value=0)
        progress = ttk.Progressbar(
            frame,
            variable=self.progress_var,
            maximum=100,
            style="Download.Horizontal.TProgressbar",
        )
        progress.pack(fill="x", pady=(0, 8))
        self.status_var = tk.StringVar(value="Connecting to peer...")
        ttk.Label(frame, textvariable=self.status_var, style="Body.TLabel").pack(anchor="w")
        if is_magnet_link(link):
            ttk.Label(
                frame,
                text="Peer count is live, not cumulative. Peers can disconnect or connections can be dropped while metadata loads.",
                style="Body.TLabel",
                wraplength=680,
            ).pack(anchor="w", pady=(4, 0))

        self.list_frame = ttk.Frame(frame, style="Card.TFrame", padding=12)
        self.list_frame.pack(fill="both", expand=True, pady=(16, 10))
        self.selection_var = tk.StringVar(value="0 files selected")
        ttk.Label(self.list_frame, textvariable=self.selection_var, style="CardTitle.TLabel").pack(anchor="w")
        self.files_tree = ttk.Treeview(
            self.list_frame,
            columns=("select", "size"),
            show=("tree", "headings"),
            style="Files.Treeview",
            selectmode="none",
        )
        self.files_tree.heading("#0", text="File", anchor="w")
        self.files_tree.heading("select", text="Select", anchor="center")
        self.files_tree.heading("size", text="Size", anchor="e")
        self.files_tree.column("#0", width=400, minwidth=160, stretch=True, anchor="w")
        self.files_tree.column("select", width=75, minwidth=65, stretch=False, anchor="center")
        self.files_tree.column("size", width=110, minwidth=85, stretch=False, anchor="e")
        self.files_tree.bind("<Button-1>", self._toggle_file)
        scrollbar = ttk.Scrollbar(self.list_frame, orient="vertical", command=self.files_tree.yview)
        self.files_tree.configure(yscrollcommand=scrollbar.set)
        self.files_tree.pack(side="left", fill="both", expand=True, pady=(10, 0))
        scrollbar.pack(side="right", fill="y", pady=(10, 0))

        destination_frame = ttk.Frame(frame, style="App.TFrame")
        destination_frame.pack(fill="x")
        ttk.Label(destination_frame, text="Download location", style="Body.TLabel").pack(anchor="w", pady=(0, 4))
        destination_row = ttk.Frame(destination_frame, style="App.TFrame")
        destination_row.pack(fill="x")
        ttk.Entry(destination_row, textvariable=self.destination, style="Folder.TEntry").pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(
            destination_row,
            text="Browse",
            command=self._choose_folder,
            style="Secondary.TButton",
        ).pack(side="left", padx=(8, 0))

        actions = ttk.Frame(frame, style="App.TFrame")
        actions.pack(fill="x", pady=(14, 0))
        ttk.Button(actions, text="Cancel", command=self.close, style="Secondary.TButton").pack(side="right")
        self.download_button = ttk.Button(
            actions,
            text="Download selected",
            command=self._start_download,
            style="Primary.TButton",
            state="disabled",
        )
        self.download_button.pack(side="right", padx=(0, 8))

        self.after(100, self._process_events)
        threading.Thread(
            target=self._load_worker,
            args=(saved_record,),
            daemon=True,
        ).start()

    def _load_worker(self, saved_record: dict | None) -> None:
        try:
            result = LinkClient.fetch_link(
                self.link,
                self.initial_destination,
                saved_record,
                progress=lambda status, percent: self.events.put(("progress", (status, percent))),
                cancel_event=self.cancel_event,
            )
            name, files, share = result
            with self.state_lock:
                if self.closed or self.cancel_event.is_set():
                    if share:
                        share.handle.pause()
                        share.session.remove_torrent(share.handle)
                    return
                self.events.put(("loaded", (name, files, share)))
        except (ValueError, json.JSONDecodeError, urllib.error.URLError, TimeoutError, OSError, RuntimeError) as error:
            self.events.put(("error", str(error)))

    def _process_events(self) -> None:
        if self.closed:
            return
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "progress":
                    status, percentage = payload
                    self.load_progress_percent = max(
                        self.load_progress_percent,
                        max(0, min(100, int(percentage))),
                    )
                    self.status_var.set(status)
                    self.progress_var.set(self.load_progress_percent)
                elif event == "loaded":
                    self.metadata_loaded = True
                    self.name, self.files, self.torrent_share = payload
                    self._populate_files()
                    self.title(self.magnet_name or f"Select files - {self.name}")
                    self.name_var.set(self.name)
                    self.status_var.set("Choose the files to download.")
                    self.download_button.configure(state="normal")
                elif event == "error":
                    messagebox.showerror("Failed to load", str(payload), parent=self)
                    self.close()
                    return
        except queue.Empty:
            pass

        if not is_magnet_link(self.link) and metadata_load_timed_out(
            self.load_started_at, time.monotonic(), self.metadata_loaded
        ):
            self.cancel_event.set()
            messagebox.showerror(
                "Failed to load",
                "Share metadata did not load within 2 minutes.",
                parent=self,
            )
            self.close()
            return
        self.after(100, self._process_events)

    def _populate_files(self) -> None:
        saved_files = {
            int(item["index"]): item
            for item in (self.saved_record or {}).get("files", [])
            if "index" in item
        }
        self.files = sort_share_files(self.files)
        self.file_vars = {}
        self.row_files = {}
        self.files_tree.delete(*self.files_tree.get_children())
        for index, file in enumerate(self.files):
            saved_file = saved_files.get(file.torrent_index, {}) if file.torrent_index is not None else {}
            selected = saved_file.get("selected", not is_text_file(file.name))
            file.selected = selected
            self.file_vars[id(file)] = tk.BooleanVar(master=self, value=selected)
            row = f"file-{index}"
            self.row_files[row] = file
            self.files_tree.insert(
                "",
                "end",
                iid=row,
                text=file.name,
                values=("☑" if selected else "☐", file.size or "Size unknown"),
            )
        self._refresh_selection()

    def _toggle_file(self, event: tk.Event) -> str | None:
        row = self.files_tree.identify_row(event.y)
        if row not in self.row_files:
            return None
        file = self.row_files[row]
        selected = not self.file_vars[id(file)].get()
        self.file_vars[id(file)].set(selected)
        file.selected = selected
        self.files_tree.set(row, "select", "☑" if selected else "☐")
        self._refresh_selection()
        return "break"

    def _refresh_selection(self) -> None:
        selected_count = sum(variable.get() for variable in self.file_vars.values())
        self.selection_var.set(f"{selected_count} of {len(self.files)} selected")
        self.download_button.configure(
            state="normal" if self.files and selected_count else "disabled"
        )

    def _choose_folder(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.destination.get(), parent=self)
        if chosen:
            self.destination.set(chosen)

    def _start_download(self) -> None:
        selected = [file for file in self.files if self.file_vars[id(file)].get()]
        destination = self.destination.get().strip()
        if not selected:
            messagebox.showinfo(APP_TITLE, "Select at least one file to download.", parent=self)
            return
        if not destination:
            messagebox.showinfo(APP_TITLE, "Choose a download location first.", parent=self)
            return

        if self.torrent_share:
            self.store.save({
                "link": self.link,
                "name": self.name,
                "destination": str(Path(destination).expanduser()),
                "files": [
                    {
                        "index": file.torrent_index,
                        "name": file.name,
                        "size": file.size,
                        "selected": self.file_vars[id(file)].get(),
                        "completed": False,
                    }
                    for file in self.files
                ],
            })
        self.on_download(
            self.link,
            self.name,
            selected,
            self.torrent_share,
            Path(destination).expanduser(),
        )
        self.close(cancel=False)

    def close(self, cancel: bool = True) -> None:
        if self.closed:
            return
        with self.state_lock:
            self.closed = True
            if cancel:
                self.cancel_event.set()
        if cancel:
            self._discard_queued_share()
            if self.torrent_share:
                self.torrent_share.handle.pause()
                self.torrent_share.session.remove_torrent(self.torrent_share.handle)
        self.on_close(self.link, self)
        self.destroy()

    def _discard_queued_share(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "loaded":
                    _, _, share = payload
                    if share:
                        share.handle.pause()
                        share.session.remove_torrent(share.handle)
        except queue.Empty:
            pass
