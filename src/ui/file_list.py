from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
import tkinter as tk

from ..models import FileEntry, format_bytes, group_share_files, is_text_file, sort_share_files
from .base import UIBase


@dataclass
class _DirectoryNode:
    directories: dict[str, _DirectoryNode] = field(default_factory=dict)
    files: list[FileEntry] = field(default_factory=list)


def build_directory_tree(files: list[FileEntry]) -> tuple[_DirectoryNode, dict[tuple[str, ...], list[FileEntry]]]:
    root = _DirectoryNode()
    for file in files:
        parts = tuple(
            part for part in PurePosixPath(file.name.replace("\\", "/")).parts
            if part not in {"/", "", "."}
        )
        if not parts:
            parts = (file.name,)
        directory = root
        for part in parts[:-1]:
            directory = directory.directories.setdefault(part, _DirectoryNode())
        directory.files.append(file)

    directory_files: dict[tuple[str, ...], list[FileEntry]] = {}

    def collect_files(directory: _DirectoryNode) -> list[FileEntry]:
        files_in_directory = list(directory.files)
        for child in directory.directories.values():
            files_in_directory.extend(collect_files(child))
        return files_in_directory

    def index_directories(directory: _DirectoryNode, path: tuple[str, ...]) -> None:
        for name, child in directory.directories.items():
            child_path = path + (name,)
            directory_files[child_path] = collect_files(child)
            index_directories(child, child_path)

    index_directories(root, ())
    return root, directory_files


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
        self.selected_file_count = 0
        self.file_group_keys = {}
        self.group_selected_counts = {}
        self.group_selected_sizes = {}
        self.group_total_sizes = {}
        self.group_has_known_sizes = {}
        self.group_expanded = {}
        self.group_vars = {}
        self.group_files = {}
        self.tree_group_labels = {}
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
            self.selected_file_count += int(selected)
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
        self.file_group_keys = {}
        self.group_selected_counts = {}
        self.group_selected_sizes = {}
        self.group_total_sizes = {}
        self.group_has_known_sizes = {}
        self.tree_group_items = {}
        self.selected_file_count = 0
        self.tree_group_labels = {}
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
        self.group_vars = {}
        self.group_files = {}
        self.file_group_keys = {}
        self.group_selected_counts = {}
        self.group_selected_sizes = {}
        self.group_total_sizes = {}
        self.group_has_known_sizes = {}
        self.tree_group_items = {}
        self.tree_group_labels = {}
        next_group_id = 0
        row_index = 0

        def row_tags(*tags: str) -> tuple[str, ...]:
            nonlocal row_index
            row_index += 1
            return (*tags, "row-alt") if row_index % 2 == 0 else tags

        def insert_file(
            parent_id: str,
            file: FileEntry,
            group_keys: tuple[tuple[str, ...], ...],
        ) -> None:
            file_id = f"file-{id(file)}"
            self.tree_file_keys[file_id] = file
            self.file_group_keys[id(file)] = group_keys
            display_name = PurePosixPath(file.name.replace("\\", "/")).name or file.name
            self.files_tree.insert(
                parent_id,
                "end",
                iid=file_id,
                text=display_name,
                values=(self._checkbox_text(self.file_vars[id(file)].get()), self._display_file_size(file)),
                tags=row_tags(),
            )

        def insert_directory(
            parent_id: str,
            directory: _DirectoryNode,
            path: tuple[str, ...],
            section_key: tuple[str, ...],
            directory_files: dict[tuple[str, ...], list[FileEntry]],
            parent_group_keys: tuple[tuple[str, ...], ...],
        ) -> None:
            nonlocal next_group_id
            for name in sorted(directory.directories, key=str.casefold):
                child = directory.directories[name]
                directory_path = path + (name,)
                key = section_key + ("directory",) + directory_path
                files = directory_files[directory_path]
                self.group_files[key] = files
                self._register_selection_group(key, files)
                self.group_expanded.setdefault(key, False)
                selected_count = self.group_selected_counts[key]
                self.group_vars[key] = tk.BooleanVar(value=selected_count == len(files))
                directory_id = f"group-{next_group_id}"
                next_group_id += 1
                self.tree_group_keys[directory_id] = key
                self.tree_group_items[key] = directory_id
                self.tree_group_labels[directory_id] = name
                self.files_tree.insert(
                    parent_id,
                    "end",
                    iid=directory_id,
                    text=f"{name} ({selected_count} of {len(files)})",
                    values=(
                        self._checkbox_text(self.group_vars[key].get()),
                        self._selection_group_size_summary(key),
                    ),
                    open=self.group_expanded[key],
                    tags=row_tags("group"),
                )
                group_keys = parent_group_keys + (key,)
                insert_directory(directory_id, child, directory_path, section_key, directory_files, group_keys)
                for file in sorted(child.files, key=lambda entry: entry.name.casefold()):
                    insert_file(directory_id, file, group_keys)

        for is_text, extension, files in group_share_files(self.files):
            category = "TEXT" if is_text else "BINARY"
            section_label = f"{category}  {extension}"
            section_key = ("section", category, extension)
            self.group_files[section_key] = files
            self._register_selection_group(section_key, files)
            self.group_expanded.setdefault(section_key, False)
            selected_count = self.group_selected_counts[section_key]
            self.group_vars[section_key] = tk.BooleanVar(value=selected_count == len(files))
            section_id = f"group-{next_group_id}"
            next_group_id += 1
            self.tree_group_keys[section_id] = section_key
            self.tree_group_items[section_key] = section_id
            self.tree_group_labels[section_id] = section_label
            self.files_tree.insert(
                "",
                "end",
                iid=section_id,
                text=f"{section_label} ({selected_count} of {len(files)})",
                values=(
                    self._checkbox_text(self.group_vars[section_key].get()),
                    self._selection_group_size_summary(section_key),
                ),
                open=self.group_expanded[section_key],
                tags=row_tags("group"),
            )
            root, directory_files = build_directory_tree(files)
            insert_directory(section_id, root, (), section_key, directory_files, (section_key,))
            for file in sorted(root.files, key=lambda entry: entry.name.casefold()):
                insert_file(section_id, file, (section_key,))

    def _register_selection_group(self, key: tuple[str, ...], files: list[FileEntry]) -> None:
        selected_count = 0
        selected_size = 0
        total_size = 0
        has_known_sizes = False
        for file in files:
            is_selected = self.file_vars[id(file)].get()
            if is_selected:
                selected_count += 1
            if file.size_bytes is None:
                continue
            has_known_sizes = True
            total_size += file.size_bytes
            if is_selected:
                selected_size += file.size_bytes
        self.group_selected_counts[key] = selected_count
        self.group_selected_sizes[key] = selected_size
        self.group_total_sizes[key] = total_size
        self.group_has_known_sizes[key] = has_known_sizes

    def _selection_group_size_summary(self, key: tuple[str, ...]) -> str:
        selected_size = self.group_selected_sizes[key]
        total_size = self.group_total_sizes[key]
        if self.group_has_known_sizes[key]:
            return f"{format_bytes(selected_size)} of {format_bytes(total_size)}"
        return "Size unknown of Size unknown"

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
            selected = self.group_selected_counts[key] != len(self.group_files[key])
            self._set_group_selection(key, selected)
        elif item in self.tree_file_keys:
            file = self.tree_file_keys[item]
            variable = self.file_vars[id(file)]
            self._set_file_selection(file, not variable.get())
        return "break"

    def _set_group_selection(self, key: tuple[str, ...], selected: bool) -> None:
        changed_files = []
        affected_groups = set()
        for file in self.group_files[key]:
            file_key = id(file)
            variable = self.file_vars[file_key]
            if variable.get() == selected:
                continue
            variable.set(selected)
            changed_files.append(file)
            delta = 1 if selected else -1
            self.selected_file_count += delta
            for group_key in self.file_group_keys[file_key]:
                self.group_selected_counts[group_key] += delta
                if file.size_bytes is not None:
                    self.group_selected_sizes[group_key] += delta * file.size_bytes
                affected_groups.add(group_key)
        self._refresh_selection_display(changed_files, affected_groups)

    def _set_file_selection(self, file: FileEntry, selected: bool) -> None:
        file_key = id(file)
        variable = self.file_vars[file_key]
        if variable.get() == selected:
            return
        variable.set(selected)
        delta = 1 if selected else -1
        self.selected_file_count += delta
        group_keys = self.file_group_keys[file_key]
        for group_key in group_keys:
            self.group_selected_counts[group_key] += delta
            if file.size_bytes is not None:
                self.group_selected_sizes[group_key] += delta * file.size_bytes
        self._refresh_selection_display([file], group_keys)

    def _refresh_selection_display(
        self,
        files: list[FileEntry],
        group_keys: Iterable[tuple[str, ...]],
    ) -> None:
        selected = self.selected_file_count
        self.selection_var.set(
            f"{selected} of {len(self.files)} selected" if self.files else "0 files selected"
        )
        self.download_button.configure(state="normal" if selected and not self.busy else "disabled")
        if not hasattr(self, "files_tree"):
            return
        for file in files:
            self.files_tree.set(f"file-{id(file)}", "select", self._checkbox_text(self.file_vars[id(file)].get()))
        for key in group_keys:
            group_item = self.tree_group_items[key]
            selected_count = self.group_selected_counts[key]
            all_selected = selected_count == len(self.group_files[key])
            self.group_vars[key].set(all_selected)
            self.files_tree.item(
                group_item,
                text=f"{self.tree_group_labels[group_item]} ({selected_count} of {len(self.group_files[key])})",
                values=(self._checkbox_text(all_selected), self._selection_group_size_summary(key)),
            )

    def _update_selection(self, *_args: object) -> None:
        selected = self.selected_file_count
        self.selection_var.set(f"{selected} of {len(self.files)} selected") if self.files else self.selection_var.set("0 files selected")
        self.download_button.configure(state="normal" if selected and not self.busy else "disabled")
