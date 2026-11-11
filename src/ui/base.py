from abc import ABC, abstractmethod
import queue
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from typing import Any
import threading

from ..models import FileEntry, TorrentShare
from ..storage import DownloadStore


class UIBase(tk.Tk, ABC):
    manifest_name: str
    files: list[FileEntry]
    torrent_share: TorrentShare | None
    store: DownloadStore
    current_magnet_link: str
    current_destination: Path
    download_running: bool
    file_paused: dict[int, bool]
    file_pause_buttons: dict[int, ttk.Button]
    file_vars: dict[int, tk.BooleanVar]
    file_progress_vars: dict[int, tk.StringVar]
    file_progress_values: dict[int, tk.DoubleVar]
    group_expanded: dict[tuple[str, ...], bool]
    group_vars: dict[tuple[str, ...], tk.BooleanVar]
    group_files: dict[tuple[str, ...], list[FileEntry]]
    selected_file_count: int
    file_group_keys: dict[int, tuple[tuple[str, ...], ...]]
    group_selected_counts: dict[tuple[str, ...], int]
    group_selected_sizes: dict[tuple[str, ...], int]
    group_total_sizes: dict[tuple[str, ...], int]
    group_has_known_sizes: dict[tuple[str, ...], bool]
    tree_group_items: dict[tuple[str, ...], str]
    tree_group_labels: dict[str, str]
    tree_group_keys: dict[str, tuple[str, ...]]
    tree_file_keys: dict[str, FileEntry]
    events: queue.Queue[tuple[str, Any]]
    busy: bool
    load_request_id: int
    load_in_progress: bool
    load_progress_percent: int
    load_last_progress_at: float
    load_cancel_event: threading.Event | None
    link_var: tk.StringVar
    destination_var: tk.StringVar
    theme_var: tk.StringVar
    auto_detect_magnets_var: tk.BooleanVar
    last_clipboard_text: str
    clipboard_prompt_active: bool
    status_var: tk.StringVar
    share_title_var: tk.StringVar
    selection_var: tk.StringVar
    total_progress_label_var: tk.StringVar
    share_control_var: tk.StringVar
    progress_var: tk.DoubleVar
    menu_bar: tk.Menu
    file_menu: tk.Menu
    edit_menu: tk.Menu
    resume_menu_index: int
    stop_all_menu_index: int
    resume_all_menu_index: int
    files_tree: ttk.Treeview
    download_button: ttk.Button
    pause_button: ttk.Button

    @abstractmethod
    def _apply_theme(self, theme: str) -> None: ...

    @abstractmethod
    def _on_auto_detect_changed(self) -> None: ...

    @abstractmethod
    def load_link(self) -> None: ...

    @abstractmethod
    def _refresh_resume_button(self) -> None: ...

    @abstractmethod
    def confirm_magnet_download(self, files: list[FileEntry]) -> Path | None: ...

    @abstractmethod
    def _set_files(self, name: str, files: list[FileEntry]) -> None: ...

    @abstractmethod
    def _clear_loaded_share(self) -> None: ...

    @abstractmethod
    def _update_download_controls(self) -> None: ...

    @abstractmethod
    def _update_selection(self, *_args: object) -> None: ...
