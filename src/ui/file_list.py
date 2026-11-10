import tkinter as tk

from ..models import FileEntry, format_bytes, group_share_files, is_text_file, sort_share_files
from .base import UIBase



class FileListMixin(UIBase):
    def _show_empty_state(self) -> None:
        self.files_tree.delete(*self.files_tree.get_children())
        self.files_tree.insert("", "end", iid="empty", text="No files loaded", values=("", ""))

    def _block_column_resize(self, event: tk.Event) -> str | None:
        if self.files_tree.identify_region(event.x, event.y) == "separator":
            return "break"
        return None

    def _fit_columns_to_table(self, _event: tk.Event | None = None) -> None:
        if not self.files_tree.winfo_width():
            return
        available = max(3, self.files_tree.winfo_width() - 2)
        file_width = round(available * 0.80)
        select_width = round(available * 0.10)
        size_width = available - file_width - select_width
        self.files_tree.column("#0", width=file_width)
        self.files_tree.column("select", width=select_width)
        self.files_tree.column("size", width=size_width)

    def _set_files(self, name: str, files: list[FileEntry]) -> None:
        self.manifest_name = name
        self.share_title_var.set(name or "Unnamed share")
        self.total_progress_label_var.set("All selected: 0%")
        self.progress_var.set(0)
        self.files = sort_share_files(files)
        self.file_vars = {}
        self.file_progress_vars = {}
        self.file_progress_values = {}
        self.file_pause_buttons = {}
        self.file_paused = {}
        self.group_expanded = {}
        self.group_vars = {}
        self.group_files = {}
        saved = self.store.get(self.current_magnet_link) if self.current_magnet_link else None
        saved_files = {int(item["index"]): item for item in (saved or {}).get("files", []) if "index" in item}
        for file in self.files:
            file_key = id(file)
            saved_file = saved_files.get(file.torrent_index, {}) if file.torrent_index is not None else {}
            default_selected = not is_text_file(file.name)
            selected = saved_file.get("selected", default_selected)
            file.selected = selected
            self.file_vars[file_key] = tk.BooleanVar(value=selected)
            self.file_progress_vars[file_key] = tk.StringVar(value="0%")
            self.file_progress_values[file_key] = tk.DoubleVar(value=0)
            if saved_file.get("completed"):
                self.file_progress_vars[file_key].set("100%")
                self.file_progress_values[file_key].set(100)
            self.file_vars[file_key].trace_add("write", lambda *_args: self._update_selection())
        self._render_file_list()
        self._update_selection()
        self._update_download_controls()
        self.files_tree.yview_moveto(0)

    def _clear_loaded_share(self) -> None:
        self.manifest_name = ""
        self.files = []
        self.torrent_share = None
        self.current_magnet_link = ""
        self.file_vars = {}
        self.file_progress_vars = {}
        self.file_progress_values = {}
        self.file_pause_buttons = {}
        self.file_paused = {}
        self.group_expanded = {}
        self.group_vars = {}
        self.group_files = {}
        self.tree_group_keys = {}
        self.tree_file_keys = {}
        self.share_title_var.set("No share loaded")
        self.total_progress_label_var.set("All selected: 0%")
        self.progress_var.set(0)
        self._show_empty_state()
        self._update_selection()
        self._update_download_controls()

    def _render_file_list(self) -> None:
        self.files_tree.delete(*self.files_tree.get_children())
        self.tree_group_keys = {}
        self.tree_file_keys = {}
        for is_text, extension, files in group_share_files(self.files):
            key = (is_text, extension)
            self.group_expanded.setdefault(key, False)
            self.group_files[key] = files
            self.group_vars[key] = tk.BooleanVar(value=all(self.file_vars[id(file)].get() for file in files))
            group_id = f"group-{len(self.tree_group_keys)}"
            self.tree_group_keys[group_id] = key
            category = "TEXT" if is_text else "BINARY"
            selected_count = sum(self.file_vars[id(file)].get() for file in files)
            selected_files = [file for file in files if self.file_vars[id(file)].get()]
            group_text = f"{category}  {extension} ({selected_count} of {len(files)})"
            self.files_tree.insert(
                "", "end", iid=group_id, text=group_text,
                values=(self._checkbox_text(self.group_vars[key].get()), self._size_summary(selected_files, files)),
                open=self.group_expanded[key], tags=("group",),
            )
            for file in files:
                file_id = f"file-{id(file)}"
                self.tree_file_keys[file_id] = file
                self.files_tree.insert(
                    group_id, "end", iid=file_id, text=file.name,
                    values=(self._checkbox_text(self.file_vars[id(file)].get()), self._display_file_size(file)),
                )

    @staticmethod
    def _display_file_size(file: FileEntry) -> str:
        return file.size or format_bytes(file.size_bytes) or "Size unknown"

    @staticmethod
    def _size_summary(selected: list[FileEntry], files: list[FileEntry]) -> str:
        selected_size = sum(file.size_bytes for file in selected if file.size_bytes is not None)
        total_size = sum(file.size_bytes for file in files if file.size_bytes is not None)
        selected_known = any(file.size_bytes is not None for file in selected)
        total_known = any(file.size_bytes is not None for file in files)
        selected_label = format_bytes(selected_size) if selected_known or total_known else "Size unknown"
        total_label = format_bytes(total_size) if total_known else "Size unknown"
        return f"{selected_label} of {total_label}"

    @staticmethod
    def _checkbox_text(selected: bool) -> str:
        return "☑" if selected else "☐"

    def _on_file_tree_click(self, event: tk.Event) -> str | None:
        if self.files_tree.identify_region(event.x, event.y) == "separator":
            return "break"
        item = self.files_tree.identify_row(event.y)
        column = self.files_tree.identify_column(event.x)
        if not item or column != "#1":
            return None
        if item in self.tree_group_keys:
            key = self.tree_group_keys[item]
            self.group_vars[key].set(not self.group_vars[key].get())
            self._set_group_selection(key, self.group_vars[key].get())
        elif item in self.tree_file_keys:
            file = self.tree_file_keys[item]
            variable = self.file_vars[id(file)]
            variable.set(not variable.get())
            self._update_selection()
        return "break"

    def _set_group_selection(self, key: tuple[bool, str], selected: bool) -> None:
        files = self.group_files[key]
        for file in files:
            self.file_vars[id(file)].set(selected)
        self._update_selection()

    def _update_selection(self, *_args: object) -> None:
        selected = sum(variable.get() for variable in self.file_vars.values())
        self.selection_var.set(f"{selected} of {len(self.files)} selected") if self.files else self.selection_var.set("0 files selected")
        self.download_button.configure(state="normal" if selected and not self.busy else "disabled")
        if not hasattr(self, "files_tree"):
            return
        for key, files in self.group_files.items():
            group_selected = all(self.file_vars[id(file)].get() for file in files)
            self.group_vars[key].set(group_selected)
        for item, key in self.tree_group_keys.items():
            files = self.group_files[key]
            selected_count = sum(self.file_vars[id(file)].get() for file in files)
            category = "TEXT" if key[0] else "BINARY"
            selected_files = [file for file in files if self.file_vars[id(file)].get()]
            self.files_tree.item(
                item,
                text=f"{category}  {key[1]} ({selected_count} of {len(files)})",
                values=(self._checkbox_text(selected_count == len(files)), self._size_summary(selected_files, files)),
            )
        for item, file in self.tree_file_keys.items():
            self.files_tree.set(item, "select", self._checkbox_text(self.file_vars[id(file)].get()))
            self.files_tree.set(item, "size", self._display_file_size(file))
