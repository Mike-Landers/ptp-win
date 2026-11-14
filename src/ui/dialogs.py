import tkinter as tk
from tkinter import filedialog, ttk

from .base import UIBase
from .constants import DEFAULT_DOWNLOAD_DIR
from .theme import THEME_LABELS


class DialogMixin(UIBase):
    def _refresh_resume_button(self) -> None:
        if hasattr(self, "file_menu"):
            count = len(self.store.records())
            self.file_menu.entryconfigure(self.resume_menu_index, label=f"Resume saved ({count})", state="normal" if count else "disabled")

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

    def choose_folder(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.destination_var.get())
        if chosen:
            self.destination_var.set(chosen)
