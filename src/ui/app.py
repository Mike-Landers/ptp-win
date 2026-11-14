import queue
import tkinter as tk
from tkinter import ttk
from typing import Any

from ..storage import DownloadStore
from .base import UIBase
from .clipboard import ClipboardMixin
from .constants import APP_TITLE, DEFAULT_DOWNLOAD_DIR
from .dialogs import DialogMixin
from .downloads import DownloadsMixin
from .selection import ShareSelectionWindow
from .theme import THEMES, ThemeMixin


class DropLinkApp(ThemeMixin, DialogMixin, ClipboardMixin, DownloadsMixin, UIBase):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1200x700")
        self.minsize(720, 560)
        self.configure(bg="#f5f7fb")

        self.store = DownloadStore()
        self.download_running = False
        self.download_jobs: dict[int, Any] = {}
        self.download_request_id = 0
        self.current_download_id = None
        self.selection_windows: dict[str, ShareSelectionWindow] = {}
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.busy = False
        self.link_var = tk.StringVar()
        self.destination_var = tk.StringVar(value=str(DEFAULT_DOWNLOAD_DIR))
        self.theme_var = tk.StringVar(value="standard_light")
        self.auto_detect_magnets_var = tk.BooleanVar(value=True)
        self.last_clipboard_text = ""
        self.clipboard_prompt_active = False
        self.status_var = tk.StringVar(value="Use Download New Link to start a download")
        self.total_progress_label_var = tk.StringVar(value="No active downloads")
        self.share_control_var = tk.StringVar(value="Pause all")
        self.progress_var = tk.DoubleVar(value=0)

        self._configure_style()
        self._build_ui()
        self.after(100, self._process_events)
        self.after(500, self._poll_clipboard)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, style="App.TFrame", padding=(34, 28, 34, 24))
        root.pack(fill="both", expand=True)
        self.menu_bar = tk.Menu(self)
        self.file_menu = tk.Menu(self.menu_bar, tearoff=False)
        self.file_menu.add_command(label="Download New Link...", command=self.show_load_dialog)
        self.file_menu.add_separator()
        self.resume_menu_index = int(self.file_menu.index("end") or -1) + 1
        self.file_menu.add_command(label="Resume saved (0)", command=self.show_saved_shares, state="disabled")
        self.file_menu.add_separator()
        self.stop_all_menu_index = int(self.file_menu.index("end") or -1) + 1
        self.file_menu.add_command(label="Stop all Downloads", command=self.stop_all_downloads, state="disabled")
        self.resume_all_menu_index = int(self.file_menu.index("end") or -1) + 1
        self.file_menu.add_command(label="Resume all Downloads", command=self.resume_all_downloads, state="disabled")
        self.menu_bar.add_cascade(label="File", menu=self.file_menu)
        self.edit_menu = tk.Menu(self.menu_bar, tearoff=False)
        self.edit_menu.add_command(label="Settings...", command=self.show_settings_dialog)
        self.menu_bar.add_cascade(label="Edit", menu=self.edit_menu)
        self.configure(menu=self.menu_bar)
        self._configure_menu_colors(THEMES[self.theme_var.get()])
        self._refresh_resume_button()

        downloads_card = ttk.Frame(root, style="Card.TFrame", padding=20)
        downloads_card.pack(fill="both", expand=True, pady=(0, 14))
        ttk.Label(downloads_card, text="Active downloads", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(
            downloads_card,
            text="Each link is prepared and selected in its own window.",
            style="Body.TLabel",
        ).pack(anchor="w", pady=(3, 10))
        self.downloads_tree = ttk.Treeview(
            downloads_card,
            columns=("files", "size", "status"),
            show=("tree", "headings"),
            style="Files.Treeview",
            selectmode="browse",
        )
        self.downloads_tree.heading("#0", text="Share", anchor="w")
        self.downloads_tree.heading("files", text="Files", anchor="center")
        self.downloads_tree.heading("size", text="Size", anchor="center")
        self.downloads_tree.heading("status", text="Status", anchor="w")
        self.downloads_tree.column("#0", width=420, minwidth=180, stretch=True, anchor="w")
        self.downloads_tree.column("files", width=80, minwidth=60, stretch=False, anchor="center")
        self.downloads_tree.column("size", width=100, minwidth=80, stretch=False, anchor="center")
        self.downloads_tree.column("status", width=300, minwidth=160, stretch=True, anchor="w")
        scrollbar = ttk.Scrollbar(downloads_card, orient="vertical", command=self.downloads_tree.yview)
        self.downloads_tree.configure(yscrollcommand=scrollbar.set)
        self.downloads_tree.pack(side="left", fill="both", expand=True, pady=(10, 0))
        scrollbar.pack(side="right", fill="y", pady=(10, 0))
        self.downloads_tree.insert("", "end", iid="empty", text="No active downloads", values=("", "", ""))

        footer = ttk.Frame(root, style="App.TFrame")
        footer.pack(fill="x")
        ttk.Label(footer, textvariable=self.status_var, style="Status.TLabel", padding=(10, 8)).pack(side="left", fill="x", expand=True)
        ttk.Label(footer, textvariable=self.total_progress_label_var, style="Status.TLabel", padding=(10, 8)).pack(side="right")
        self.pause_button = ttk.Button(footer, textvariable=self.share_control_var, command=self.toggle_share_pause, style="Secondary.TButton", state="disabled")
        self.pause_button.pack(side="right", padx=(12, 0))
        self.progress = ttk.Progressbar(footer, variable=self.progress_var, maximum=100, style="Download.Horizontal.TProgressbar", length=120)
        self.progress.pack(side="right", padx=(12, 0))
