import tkinter as tk
from tkinter import messagebox

from ..models import is_magnet_link
from .base import UIBase
from .constants import APP_TITLE


class ClipboardMixin(UIBase):
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
                        f"Download copied magnet link?\n\n{self._get_magnet_title(clipboard_text)}",
                    )
                    if accepted:
                        self.link_var.set(clipboard_text)
                finally:
                    self.clipboard_prompt_active = False
        self.after(500, self._poll_clipboard)

    def _get_magnet_title(self, clipboard_text):
        # Extract the name from the magnet link using regex
        import re
        name_match = re.search(r'dn=([^&]+)', clipboard_text)
        if name_match:
            # URL decode the name
            import urllib.parse
            return urllib.parse.unquote(name_match.group(1))
        else:
            # If no name found, return a default or the whole link
            return "Unnamed Magnet Link"
