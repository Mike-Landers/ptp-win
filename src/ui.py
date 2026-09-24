from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


APP_TITLE = "DropLink"
DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads"
TEXT_EXTENSIONS = {
    ".c", ".cc", ".cpp", ".css", ".csv", ".go", ".h", ".hpp", ".html", ".ini",
    ".java", ".js", ".json", ".jsx", ".log", ".md", ".py", ".rst", ".sql", ".svg",
    ".toml", ".ts", ".tsx", ".txt", ".xml", ".yaml", ".yml",
}

THEMES = {
    "standard_light": {
        "app": "#f5f7fb", "card": "#ffffff", "title": "#172033", "body": "#526078",
        "meta": "#8490a5", "group": "#344057", "status": "#eef2f8", "button": "#eef2f8",
        "button_active": "#eef2f8", "primary": "#1d6fe8", "primary_active": "#1d6fe8", "hover": "#ffffff",
        "disabled": "#b8c7dc", "entry": "#ffffff", "trough": "#e7edf5", "progress": "#1d6fe8",
    },
    "high_contrast_light": {
        "app": "#ffffff", "card": "#ffffff", "title": "#000000", "body": "#111111",
        "meta": "#000000", "group": "#000000", "status": "#ffffff", "button": "#000000",
        "button_active": "#000000", "primary": "#000000", "primary_active": "#000000", "hover": "#ffffff",
        "disabled": "#000000", "entry": "#ffffff", "trough": "#ffffff", "progress": "#000000",
    },
    "standard_dark": {
        "app": "#1e232b", "card": "#28303b", "title": "#f5f7fb", "body": "#c5ceda",
        "meta": "#a8b3c2", "group": "#e3e9f2", "status": "#303946", "button": "#ffffff",
        "button_active": "#ffffff", "primary": "#ffffff", "primary_active": "#ffffff", "hover": "#28303b",
        "disabled": "#ffffff", "entry": "#202631", "trough": "#414b59", "progress": "#4da3ff",
    },
    "high_contrast_dark": {
        "app": "#000000", "card": "#0b0b0b", "title": "#ffffff", "body": "#ffffff",
        "meta": "#ffffff", "group": "#ffffff", "status": "#000000", "button": "#ffffff",
        "button_active": "#ffffff", "primary": "#ffffff", "primary_active": "#ffffff", "hover": "#0b0b0b",
        "disabled": "#ffffff", "entry": "#000000", "trough": "#000000", "progress": "#ffffff",
    },
}

THEME_LABELS = {
    "standard_light": "Standard light",
    "high_contrast_light": "High-contrast light",
    "standard_dark": "Standard dark",
    "high_contrast_dark": "High-contrast dark",
}


@dataclass
class FileEntry:
    name: str
    url: str
    size: str = ""
    kind: str = "File"
    selected: bool = True
    torrent_index: int | None = None


@dataclass
class TorrentShare:
    handle: object
    session: object
    link: str
    name: str
    paused_indexes: set[int]
    lock: threading.Lock
    paused: bool = False


def default_store_path() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return root / APP_TITLE / "shares.json"


class DownloadStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_store_path()
        self.lock = threading.RLock()

    def records(self) -> list[dict]:
        with self.lock:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                return []
        shares = payload.get("shares", {}) if isinstance(payload, dict) else {}
        if not isinstance(shares, dict):
            return []
        incomplete = []
        for record in shares.values():
            files = record.get("files", [])
            if files and all(not item.get("selected", True) or item.get("completed", False) for item in files):
                continue
            incomplete.append(record)
        if len(incomplete) != len(shares):
            with self.lock:
                if incomplete:
                    self.path.write_text(json.dumps({"version": 1, "shares": {item["link"]: item for item in incomplete}}, indent=2), encoding="utf-8")
                elif self.path.exists():
                    self.path.unlink()
        return incomplete

    def get(self, link: str) -> dict | None:
        return next((record for record in self.records() if record.get("link") == link), None)

    def save(self, record: dict) -> None:
        with self.lock:
            records = {item.get("link"): item for item in self.records() if item.get("link")}
            records[record["link"]] = record
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"version": 1, "shares": records}, indent=2), encoding="utf-8")
            os.replace(temporary, self.path)

    def remove(self, link: str) -> None:
        with self.lock:
            records = {item.get("link"): item for item in self.records() if item.get("link") and item.get("link") != link}
            if records:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps({"version": 1, "shares": records}, indent=2), encoding="utf-8")
            elif self.path.exists():
                self.path.unlink()


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
            entries.append(FileEntry(name=name, url="", size=format_bytes(size), kind=kind, selected=selected, torrent_index=index))
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
                return
            if status.errc and status.errc.value() != 0:
                handle.pause()
                raise OSError(status.errc.message())
            time.sleep(0.5)


from .models import FileEntry, TorrentShare, group_share_files, is_magnet_link, sort_share_files
from .storage import DownloadStore
from .transfer import LinkClient


class DropLinkApp(tk.Tk):
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
        self.check_vars: list[tk.BooleanVar] = []
        self.file_vars: dict[int, tk.BooleanVar] = {}
        self.file_progress_vars: dict[int, tk.StringVar] = {}
        self.file_progress_values: dict[int, tk.DoubleVar] = {}
        self.group_expanded: dict[tuple[bool, str], bool] = {}
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.busy = False
        self.link_var = tk.StringVar()
        self.destination_var = tk.StringVar(value=str(DEFAULT_DOWNLOAD_DIR))
        self.theme_var = tk.StringVar(value="standard_light")
        self.auto_detect_magnets_var = tk.BooleanVar(value=False)
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

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        colors = THEMES[self.theme_var.get()]
        style.configure("App.TFrame", background=colors["app"])
        style.configure("Card.TFrame", background=colors["card"])
        style.configure("Title.TLabel", background=colors["app"], foreground=colors["title"], font=("Segoe UI Semibold", 25))
        style.configure("Subtitle.TLabel", background=colors["app"], foreground=colors["body"], font=("Segoe UI", 10))
        style.configure("CardTitle.TLabel", background=colors["card"], foreground=colors["title"], font=("Segoe UI Semibold", 13))
        style.configure("Body.TLabel", background=colors["card"], foreground=colors["body"], font=("Segoe UI", 9))
        style.configure("File.TLabel", background=colors["card"], foreground=colors["title"], font=("Segoe UI Semibold", 10))
        style.configure("Meta.TLabel", background=colors["card"], foreground=colors["meta"], font=("Segoe UI", 9))
        style.configure("Group.TLabel", background=colors["card"], foreground=colors["group"], font=("Segoe UI Semibold", 9))
        style.configure("Status.TLabel", background=colors["status"], foreground=colors["body"], font=("Segoe UI", 9))
        high_contrast = self.theme_var.get() in {"high_contrast_light", "high_contrast_dark"}
        button_foreground = ("#ffffff" if self.theme_var.get() == "high_contrast_light" else "#000000") if high_contrast else "#000000" if self.theme_var.get() == "standard_dark" else "#ffffff"
        secondary_foreground = "#ffffff" if self.theme_var.get() == "high_contrast_light" else "#000000" if self.theme_var.get() == "standard_dark" else button_foreground if high_contrast else colors["title"]
        disabled_foreground = "#ffffff" if self.theme_var.get() == "high_contrast_light" else "#000000" if high_contrast or self.theme_var.get() == "standard_dark" else "#526078"
        style.configure("Primary.TButton", background=colors["primary"], foreground=button_foreground, borderwidth=0, padding=(18, 10), font=("Segoe UI Semibold", 10))
        style.map("Primary.TButton", background=[("active", colors["primary_active"]), ("disabled", colors["disabled"])], foreground=[("active", button_foreground), ("disabled", disabled_foreground)])
        style.configure("Secondary.TButton", background=colors["button"], foreground=secondary_foreground, borderwidth=0, padding=(12, 9), font=("Segoe UI Semibold", 9))
        style.map("Secondary.TButton", background=[("active", colors["button_active"])], foreground=[("active", secondary_foreground), ("disabled", disabled_foreground)])
        style.configure("Theme.TRadiobutton", background=colors["app"], foreground=colors["title"], font=("Segoe UI", 10))
        style.map("Theme.TRadiobutton", background=[("active", colors["app"])], foreground=[("active", colors["title"])])
        style.configure("Theme.TCheckbutton", background=colors["app"], foreground=colors["title"], font=("Segoe UI", 10))
        style.map("Theme.TCheckbutton", background=[("active", colors["app"])], foreground=[("active", colors["title"])])
        style.configure("Link.TEntry", fieldbackground=colors["entry"], foreground=colors["title"], padding=10, borderwidth=1)
        style.configure("Folder.TEntry", fieldbackground=colors["entry"], foreground=colors["title"], padding=8, borderwidth=1)
        style.configure("Download.Horizontal.TProgressbar", troughcolor=colors["trough"], background=colors["progress"], borderwidth=0, thickness=8)
        self.configure(bg=colors["app"])
        if hasattr(self, "files_canvas"):
            self.files_canvas.configure(background=colors["card"])
        if hasattr(self, "menu_bar"):
            self._configure_menu_colors(colors)

    def _configure_menu_colors(self, colors: dict[str, str]) -> None:
        menu_background = colors["card"]
        menu_foreground = colors["title"]
        for menu in (self.menu_bar, self.file_menu, self.edit_menu):
            menu.configure(
                background=menu_background,
                foreground=menu_foreground,
                activebackground=menu_background,
                activeforeground=menu_foreground,
                disabledforeground=colors["meta"],
            )

    def _apply_theme(self, theme: str) -> None:
        if theme not in THEMES:
            return
        self.theme_var.set(theme)
        self._configure_style()

    def _build_ui(self) -> None:
        root = ttk.Frame(self, style="App.TFrame", padding=(34, 28, 34, 24))
        root.pack(fill="both", expand=True)
        self.menu_bar = tk.Menu(self)
        self.file_menu = tk.Menu(self.menu_bar, tearoff=False)
        self.file_menu.add_command(label="Download New Link...", command=self.show_load_dialog)
        self.file_menu.add_separator()
        self.resume_menu_index = self.file_menu.index("end") + 1
        self.file_menu.add_command(label="Resume saved (0)", command=self.show_saved_shares, state="disabled")
        self.file_menu.add_separator()
        self.stop_all_menu_index = self.file_menu.index("end") + 1
        self.file_menu.add_command(label="Stop all Downloads", command=self.stop_all_downloads, state="disabled")
        self.resume_all_menu_index = self.file_menu.index("end") + 1
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
        self.files_canvas = tk.Canvas(list_card, background="#ffffff", highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_card, orient="vertical", command=self.files_canvas.yview)
        self.files_frame = ttk.Frame(self.files_canvas, style="Card.TFrame")
        self.files_frame.bind("<Configure>", lambda _event: self.files_canvas.configure(scrollregion=self.files_canvas.bbox("all")))
        self.files_canvas.create_window((0, 0), window=self.files_frame, anchor="nw", tags="files")
        self.files_canvas.configure(yscrollcommand=scrollbar.set)
        self.files_canvas.pack(side="left", fill="both", expand=True, pady=(16, 0))
        scrollbar.pack(side="right", fill="y", pady=(16, 0))
        self.files_canvas.bind("<Configure>", self._resize_file_window)
        self._show_empty_state()
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

    def _resize_file_window(self, event: tk.Event) -> None:
        self.files_canvas.itemconfigure("files", width=event.width)

    def _show_empty_state(self) -> None:
        for child in self.files_frame.winfo_children():
            child.destroy()
        ttk.Label(self.files_frame, text="No files loaded", style="CardTitle.TLabel").pack(pady=(70, 4))
        ttk.Label(self.files_frame, text="Use Load files above to inspect a peer share.", style="Body.TLabel").pack(pady=(0, 70))

    def _refresh_resume_button(self) -> None:
        if hasattr(self, "file_menu"):
            count = len(self.store.records())
            self.file_menu.entryconfigure(self.resume_menu_index, label=f"Resume saved ({count})", state="normal" if count else "disabled")

    def _on_auto_detect_changed(self) -> None:
        if self.auto_detect_magnets_var.get():
            self.last_clipboard_text = ""

    def _poll_clipboard(self) -> None:
        try:
            clipboard_text = self.clipboard_get().strip()
        except tk.TclError:
            clipboard_text = ""
        if clipboard_text != self.last_clipboard_text:
            self.last_clipboard_text = clipboard_text
            if self.auto_detect_magnets_var.get() and is_magnet_link(clipboard_text) and not self.busy and not self.clipboard_prompt_active:
                self.clipboard_prompt_active = True
                try:
                    accepted = messagebox.askyesno(
                        APP_TITLE,
                        f"Download copied magnet link?\n\n{clipboard_text}",
                        parent=self,
                    )
                    if accepted:
                        self.link_var.set(clipboard_text)
                        self.load_link()
                finally:
                    self.clipboard_prompt_active = False
        self.after(500, self._poll_clipboard)

    def show_settings_dialog(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Settings")
        dialog.geometry("680x430")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()
        frame = ttk.Frame(dialog, style="App.TFrame", padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Application settings", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(frame, text="Edit the settings used for new downloads.", style="Subtitle.TLabel").pack(anchor="w", pady=(3, 14))
        location_row = ttk.Frame(frame, style="App.TFrame")
        location_row.pack(fill="x")
        ttk.Label(location_row, text="Default download location", style="Body.TLabel", width=25).pack(side="left")
        ttk.Entry(location_row, textvariable=self.destination_var, style="Folder.TEntry").pack(side="left", fill="x", expand=True)
        ttk.Button(location_row, text="Browse", command=self.choose_folder, style="Secondary.TButton").pack(side="left", padx=(10, 0))
        ttk.Checkbutton(
            frame,
            text="Auto-detect copied magnet links",
            variable=self.auto_detect_magnets_var,
            command=self._on_auto_detect_changed,
            style="Theme.TCheckbutton",
        ).pack(anchor="w", pady=(18, 0))
        ttk.Label(frame, text="Appearance", style="CardTitle.TLabel").pack(anchor="w", pady=(24, 8))
        appearance_frame = ttk.Frame(frame, style="App.TFrame")
        appearance_frame.pack(fill="x")
        for index, (theme, label) in enumerate(THEME_LABELS.items()):
            ttk.Radiobutton(
                appearance_frame,
                text=label,
                variable=self.theme_var,
                value=theme,
                command=lambda selected=theme: self._apply_theme(selected),
                style="Theme.TRadiobutton",
            ).grid(row=index // 2, column=index % 2, sticky="w", padx=(0, 28), pady=5)
        ttk.Button(frame, text="Done", command=dialog.destroy, style="Primary.TButton").pack(anchor="e", pady=(18, 0))

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

    def show_load_dialog(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Load files")
        dialog.geometry("680x180")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()
        frame = ttk.Frame(dialog, style="App.TFrame", padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Peer or magnet link", style="CardTitle.TLabel").pack(anchor="w")
        entry = ttk.Entry(frame, textvariable=self.link_var, style="Link.TEntry", font=("Segoe UI", 11))
        entry.pack(fill="x", pady=(10, 14))

        def submit() -> None:
            if self.link_var.get().strip():
                dialog.destroy()
                self.load_link()

        actions = ttk.Frame(frame, style="App.TFrame")
        actions.pack(fill="x")
        ttk.Button(actions, text="Cancel", command=dialog.destroy, style="Secondary.TButton").pack(side="right")
        ttk.Button(actions, text="Load files", command=submit, style="Primary.TButton").pack(side="right", padx=(0, 8))
        entry.bind("<Return>", lambda _event: submit())
        entry.focus_set()

    def show_saved_shares(self) -> None:
        records = self.store.records()
        if not records:
            self._refresh_resume_button()
            return
        dialog = tk.Toplevel(self)
        dialog.title("Resume saved share")
        dialog.geometry("620x320")
        dialog.transient(self)
        dialog.grab_set()
        frame = ttk.Frame(dialog, style="App.TFrame", padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Incomplete magnet downloads", style="CardTitle.TLabel").pack(anchor="w", pady=(0, 10))
        listbox = tk.Listbox(frame, height=8, activestyle="none", exportselection=False, font=("Segoe UI", 10))
        listbox.pack(fill="both", expand=True)
        for record in records:
            files = sum(1 for file in record.get("files", []) if file.get("selected", True) and not file.get("completed", False))
            listbox.insert("end", f"{record.get('name', 'Magnet share')}  |  {files} file(s) remaining  |  {record.get('destination', '')}")
        listbox.selection_set(0)

        def resume_selected() -> None:
            selected = listbox.curselection()
            if not selected:
                return
            record = records[selected[0]]
            self.link_var.set(record["link"])
            self.destination_var.set(record.get("destination", str(DEFAULT_DOWNLOAD_DIR)))
            dialog.destroy()
            self.load_link()

        ttk.Button(frame, text="Resume selected", command=resume_selected, style="Primary.TButton").pack(anchor="e", pady=(12, 0))

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
        saved = self.store.get(link)
        if saved and saved.get("destination"):
            self.destination_var.set(saved["destination"])
        self._set_busy(True)
        self.status_var.set("Reading share metadata...")
        threading.Thread(target=self._load_worker, args=(link,), daemon=True).start()

    def _load_worker(self, link: str) -> None:
        try:
            saved = self.store.get(link)
            destination = Path(saved.get("destination", self.destination_var.get()) if saved else self.destination_var.get()).expanduser()
            result = LinkClient.fetch_link(link, destination, saved)
            self.events.put(("loaded", result))
        except (ValueError, json.JSONDecodeError, urllib.error.URLError, TimeoutError, OSError, RuntimeError) as error:
            self.events.put(("error", str(error)))

    def _set_files(self, name: str, files: list[FileEntry]) -> None:
        self.manifest_name = name
        self.share_title_var.set(name or "Unnamed share")
        self.total_progress_label_var.set("All selected: 0%")
        self.progress_var.set(0)
        self.files = sort_share_files(files)
        self.check_vars = []
        self.file_vars = {}
        self.file_progress_vars = {}
        self.file_progress_values = {}
        self.file_pause_buttons = {}
        self.file_paused = {}
        self.group_expanded = {}
        saved = self.store.get(self.current_magnet_link) if self.current_magnet_link else None
        saved_files = {int(item["index"]): item for item in (saved or {}).get("files", []) if "index" in item}
        for file in self.files:
            file_key = id(file)
            self.file_vars[file_key] = tk.BooleanVar(value=file.selected)
            self.file_progress_vars[file_key] = tk.StringVar(value="0%")
            self.file_progress_values[file_key] = tk.DoubleVar(value=0)
            saved_file = saved_files.get(file.torrent_index, {})
            if saved_file.get("completed"):
                self.file_progress_vars[file_key].set("100%")
                self.file_progress_values[file_key].set(100)
            self.file_vars[file_key].trace_add("write", lambda *_args: self._update_selection())
            self.check_vars.append(self.file_vars[file_key])
        self._render_file_list()
        self._update_selection()
        self._update_download_controls()
        self.files_canvas.yview_moveto(0)

    def _render_file_list(self) -> None:
        for child in self.files_frame.winfo_children():
            child.destroy()
        for is_text, extension, files in group_share_files(self.files):
            key = (is_text, extension)
            self.group_expanded.setdefault(key, True)
            header = ttk.Frame(self.files_frame, style="Card.TFrame", padding=(4, 8))
            header.pack(fill="x")
            arrow = "v" if self.group_expanded[key] else ">"
            ttk.Button(header, text=arrow, width=3, style="Secondary.TButton", command=lambda group_key=key: self._toggle_group(group_key)).pack(side="left", padx=(0, 8))
            category = "TEXT" if is_text else "BINARY"
            ttk.Label(header, text=f"{category}  {extension}  ({len(files)})", style="Group.TLabel").pack(side="left")
            if is_text:
                ttk.Button(header, text="Deselect text", style="Secondary.TButton", command=lambda group_files=files: self._deselect_text_group(group_files)).pack(side="right")
            if self.group_expanded[key]:
                for file in files:
                    self._render_file_row(file)

    def _render_file_row(self, file: FileEntry) -> None:
        file_key = id(file)
        row = ttk.Frame(self.files_frame, style="Card.TFrame", padding=(34, 7, 4, 7))
        row.pack(fill="x")
        variable = self.file_vars[file_key]
        ttk.Checkbutton(row, variable=variable).pack(side="left", padx=(0, 10))
        text = ttk.Frame(row, style="Card.TFrame")
        text.pack(side="left", fill="x", expand=True)
        ttk.Label(text, text=file.name, style="File.TLabel").pack(anchor="w")
        ttk.Label(text, text=file.kind, style="Meta.TLabel").pack(anchor="w", pady=(2, 0))
        progress = ttk.Progressbar(row, variable=self.file_progress_values[file_key], maximum=100, length=75, style="Download.Horizontal.TProgressbar")
        progress.pack(side="right", padx=(10, 8))
        ttk.Label(row, textvariable=self.file_progress_vars[file_key], style="Meta.TLabel", width=5, anchor="e").pack(side="right")
        ttk.Label(row, text=file.size or "Size unknown", style="Meta.TLabel", width=12, anchor="e").pack(side="right")
        if file.torrent_index is not None:
            button = ttk.Button(row, text="Pause", style="Secondary.TButton", command=lambda target=file: self.toggle_file_pause(target))
            button.pack(side="right", padx=(8, 0))
            self.file_pause_buttons[file_key] = button

    def _toggle_group(self, key: tuple[bool, str]) -> None:
        self.group_expanded[key] = not self.group_expanded[key]
        self._render_file_list()

    def _deselect_text_group(self, files: list[FileEntry]) -> None:
        for file in files:
            self.file_vars[id(file)].set(False)
        self._update_selection()

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

    def _update_selection(self, *_args: object) -> None:
        selected = sum(variable.get() for variable in self.file_vars.values())
        self.selection_var.set(f"{selected} of {len(self.files)} selected") if self.files else self.selection_var.set("0 files selected")
        self.download_button.configure(state="normal" if selected and not self.busy else "disabled")

    def _save_current_record(self, destination: Path) -> None:
        if not self.torrent_share or not self.current_magnet_link:
            return
        previous = self.store.get(self.current_magnet_link)
        previous_files = {int(item["index"]): item for item in (previous or {}).get("files", []) if "index" in item}
        files = []
        for file in self.files:
            file_key = id(file)
            old = previous_files.get(file.torrent_index, {})
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

    def choose_folder(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.destination_var.get())
        if chosen:
            self.destination_var.set(chosen)

    def confirm_magnet_download(self, files: list[FileEntry]) -> Path | None:
        dialog = tk.Toplevel(self)
        dialog.title("Confirm magnet download")
        dialog.geometry("680x360")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()
        frame = ttk.Frame(dialog, style="App.TFrame", padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=f"Download {len(files)} selected file(s)", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(frame, text="Review the files and choose where this magnet download should be saved.", style="Subtitle.TLabel").pack(anchor="w", pady=(3, 12))
        file_list = tk.Listbox(frame, height=8, activestyle="none", exportselection=False, font=("Segoe UI", 9))
        file_list.pack(fill="both", expand=True)
        for file in files:
            file_list.insert("end", f"{file.name}  |  {file.size or 'Size unknown'}")
        destination = tk.StringVar(value=self.destination_var.get())
        ttk.Label(frame, text="Download location", style="Body.TLabel").pack(anchor="w", pady=(12, 4))
        folder_row = ttk.Frame(frame, style="App.TFrame")
        folder_row.pack(fill="x")
        ttk.Entry(folder_row, textvariable=destination, style="Folder.TEntry").pack(side="left", fill="x", expand=True)

        def browse() -> None:
            chosen = filedialog.askdirectory(initialdir=destination.get(), parent=dialog)
            if chosen:
                destination.set(chosen)

        ttk.Button(folder_row, text="Browse", command=browse, style="Secondary.TButton").pack(side="left", padx=(10, 0))
        result: list[Path | None] = [None]

        def confirm() -> None:
            value = destination.get().strip()
            if not value:
                messagebox.showinfo(APP_TITLE, "Choose a download location first.", parent=dialog)
                return
            result[0] = Path(value).expanduser()
            dialog.destroy()

        actions = ttk.Frame(frame, style="App.TFrame")
        actions.pack(fill="x", pady=(14, 0))
        ttk.Button(actions, text="Cancel", command=dialog.destroy, style="Secondary.TButton").pack(side="right")
        ttk.Button(actions, text="Start download", command=confirm, style="Primary.TButton").pack(side="right", padx=(0, 8))
        dialog.wait_window()
        if result[0] is not None:
            self.destination_var.set(str(result[0]))
        return result[0]

    def download_selected(self) -> None:
        selected = [file for file, variable in zip(self.files, self.check_vars) if variable.get()]
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
        if self.torrent_share:
            self.torrent_share.paused = False
            self._save_current_record(destination)
            self.share_control_var.set("Pause all")
        self._update_download_controls()
        self.status_var.set(f"Preparing {len(selected)} file(s)...")
        threading.Thread(target=self._download_worker, args=(selected, destination, self.torrent_share), daemon=True).start()

    def _download_worker(self, files: list[FileEntry], destination: Path, torrent_share: TorrentShare | None) -> None:
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
            self.events.put(("finished", (completed, len(files), failures, destination)))
            return
        for file in files:
            try:
                self.events.put(("status", f"Downloading {file.name}..."))
                LinkClient.download(file, destination, lambda copied, total: self.events.put(("progress", (completed, len(files), copied, total))))
                completed += 1
            except (urllib.error.URLError, OSError, ValueError) as error:
                failures.append(f"{file.name}: {error}")
        self.events.put(("finished", (completed, len(files), failures, destination)))

    def _process_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "loaded":
                    name, files, torrent_share = payload
                    self.torrent_share = torrent_share
                    self.current_magnet_link = torrent_share.link if torrent_share else ""
                    self.current_destination = Path(self.destination_var.get()).expanduser()
                    self._set_files(name, files)
                    if torrent_share:
                        self._save_current_record(self.current_destination)
                    self._set_busy(False)
                    self.status_var.set(f"{name} loaded. Confirm the files you want to download.")
                elif event == "error":
                    self._set_busy(False)
                    self.status_var.set("Could not load this link.")
                    messagebox.showerror(APP_TITLE, payload)
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
                    completed, total_files, failures, destination = payload
                    was_complete = completed == total_files and not failures
                    self._set_busy(False)
                    self.download_running = False
                    self.share_control_var.set("Pause all")
                    self._update_download_controls()
                    self.progress_var.set(100 if completed == total_files else 0)
                    self.total_progress_label_var.set("All selected: 100%" if completed == total_files else "All selected: 0%")
                    self.status_var.set(f"{completed} of {total_files} file(s) downloaded to {destination}")
                    if failures:
                        messagebox.showwarning(APP_TITLE, "Some files could not be downloaded:\n\n" + "\n".join(failures))
                    else:
                        if self.torrent_share and was_complete:
                            self._remove_completed_record()
                        messagebox.showinfo(APP_TITLE, f"Downloaded {completed} file(s) to:\n{destination}")
        except queue.Empty:
            pass
        self.after(100, self._process_events)

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

