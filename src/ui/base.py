from abc import ABC, abstractmethod
import queue
import tkinter as tk
from tkinter import ttk
from typing import Any

from ..storage import DownloadStore


class UIBase(tk.Tk, ABC):
    store: DownloadStore
    download_running: bool
    download_jobs: dict[int, Any]
    download_request_id: int
    current_download_id: int | None
    selection_windows: dict[str, Any]
    events: queue.Queue[tuple[str, Any]]
    busy: bool
    link_var: tk.StringVar
    destination_var: tk.StringVar
    theme_var: tk.StringVar
    auto_detect_magnets_var: tk.BooleanVar
    last_clipboard_text: str
    clipboard_prompt_active: bool
    status_var: tk.StringVar
    total_progress_label_var: tk.StringVar
    share_control_var: tk.StringVar
    progress_var: tk.DoubleVar
    menu_bar: tk.Menu
    file_menu: tk.Menu
    edit_menu: tk.Menu
    resume_menu_index: int
    stop_all_menu_index: int
    resume_all_menu_index: int
    downloads_tree: ttk.Treeview
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
    def _update_download_controls(self) -> None: ...
