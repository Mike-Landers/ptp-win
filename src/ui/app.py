import queue
import tkinter as tk
from tkinter import ttk
from typing import Any
import threading

from ..models import FileEntry, TorrentShare
from ..storage import DownloadStore
from .base import UIBase
from .clipboard import ClipboardMixin
from .constants import APP_TITLE, DEFAULT_DOWNLOAD_DIR
from .dialogs import DialogMixin
from .downloads import DownloadsMixin
from .file_list import FileListMixin
from .theme import THEMES, ThemeMixin


class DropLinkApp(ThemeMixin, FileListMixin, DialogMixin, ClipboardMixin, DownloadsMixin, UIBase):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("900x700")
        self.minsize(720, 560)
        self.configure(bg="#f5f7fb")

        self.manifest_name = ""
        self.files: list[FileEntry] = []
        self.torrent_share: TorrentShare | None = None
        self.store = DownloadStore()
        self.current_magnet_link = ""
        self.current_destination = DEFAULT_DOWNLOAD_DIR
        self.download_running = False
        self.file_paused: dict[int, bool] = {}
        self.file_pause_buttons: dict[int, ttk.Button] = {}
        self.file_vars: dict[int, tk.BooleanVar] = {}
        self.file_progress_vars: dict[int, tk.StringVar] = {}
        self.file_progress_values: dict[int, tk.DoubleVar] = {}
        self.group_expanded: dict[tuple[bool, str], bool] = {}
        self.group_vars: dict[tuple[bool, str], tk.BooleanVar] = {}
        self.group_files: dict[tuple[bool, str], list[FileEntry]] = {}
        self.tree_group_keys: dict[str, tuple[bool, str]] = {}
        self.tree_file_keys: dict[str, FileEntry] = {}
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.busy = False
        self.load_request_id = 0
        self.load_in_progress = False
        self.load_progress_percent = 0
        self.load_last_progress_at = 0.0
        self.load_cancel_event: threading.Event | None = None
        self.link_var = tk.StringVar()
        self.destination_var = tk.StringVar(value=str(DEFAULT_DOWNLOAD_DIR))
        self.theme_var = tk.StringVar(value="standard_light")
        self.auto_detect_magnets_var = tk.BooleanVar(value=True)
        self.last_clipboard_text = ""
        self.clipboard_prompt_active = False
        self.status_var = tk.StringVar(value="Use Load files to choose a peer link")
        self.share_title_var = tk.StringVar(value="No share loaded")
        self.selection_var = tk.StringVar(value="0 files selected")
        self.total_progress_label_var = tk.StringVar(value="All selected: 0%")
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

        content_pane = ttk.PanedWindow(root, orient="vertical")
        content_pane.pack(fill="both", expand=True, pady=(0, 14))

        list_card = ttk.Frame(content_pane, style="Card.TFrame", padding=20)
        list_header = ttk.Frame(list_card, style="Card.TFrame")
        list_header.pack(fill="x")
        share_heading = ttk.Frame(list_header, style="Card.TFrame")
        share_heading.pack(side="left", fill="x", expand=True)
        ttk.Label(share_heading, textvariable=self.share_title_var, style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(share_heading, text="Files in this share", style="Body.TLabel").pack(anchor="w", pady=(2, 0))
        ttk.Label(list_header, textvariable=self.selection_var, style="Body.TLabel").pack(side="right")
        self.files_tree = ttk.Treeview(
            list_card,
            columns=("select", "size"),
            show=("tree", "headings"),
            style="Files.Treeview",
            selectmode="browse",
        )
        self.files_tree.heading("#0", text="File", anchor="w")
        self.files_tree.heading("select", text="Select", anchor="center")
        self.files_tree.heading("size", text="Size", anchor="e")
        self.files_tree.column("#0", width=80, minwidth=1, stretch=False, anchor="w")
        self.files_tree.column("select", width=10, minwidth=1, stretch=False, anchor="center")
        self.files_tree.column("size", width=10, minwidth=1, stretch=False, anchor="e")
        self.files_tree.bind("<ButtonPress-1>", self._block_column_resize)
        self.files_tree.bind("<Button-1>", self._on_file_tree_click)
        self.files_tree.bind("<Configure>", self._fit_columns_to_table)
        scrollbar = ttk.Scrollbar(list_card, orient="vertical", command=self.files_tree.yview)
        self.files_tree.configure(yscrollcommand=scrollbar.set)
        self.files_tree.pack(side="left", fill="both", expand=True, pady=(16, 0))
        scrollbar.pack(side="right", fill="y", pady=(16, 0))
        self._show_empty_state()
        self.after_idle(self._fit_columns_to_table)
        content_pane.add(list_card, weight=4)

        footer = ttk.Frame(root, style="App.TFrame")
        footer.pack(fill="x")
        ttk.Label(footer, textvariable=self.status_var, style="Status.TLabel", padding=(10, 8)).pack(side="left", fill="x", expand=True)
        ttk.Label(footer, textvariable=self.total_progress_label_var, style="Status.TLabel", padding=(10, 8)).pack(side="right")
        self.download_button = ttk.Button(footer, text="Download selected", command=self.download_selected, style="Primary.TButton", state="disabled")
        self.download_button.pack(side="right", padx=(12, 0))
        self.pause_button = ttk.Button(footer, textvariable=self.share_control_var, command=self.toggle_share_pause, style="Secondary.TButton", state="disabled")
        self.pause_button.pack(side="right", padx=(12, 0))
        self.progress = ttk.Progressbar(footer, variable=self.progress_var, maximum=100, style="Download.Horizontal.TProgressbar", length=120)
        self.progress.pack(side="right", padx=(12, 0))
