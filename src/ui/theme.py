from tkinter import ttk

from .base import UIBase

THEMES = {
    "standard_light": {
        "app": "#f5f7fb", "card": "#ffffff", "title": "#172033", "body": "#526078",
        "meta": "#8490a5", "group": "#344057", "status": "#eef2f8", "button": "#eef2f8",
        "button_active": "#eef2f8", "primary": "#1d6fe8", "primary_active": "#1d6fe8", "hover": "#ffffff",
        "disabled": "#b8c7dc", "entry": "#ffffff", "trough": "#e7edf5", "progress": "#1d6fe8",
        "row_alt": "#f7f9fc",
    },
    "high_contrast_light": {
        "app": "#ffffff", "card": "#ffffff", "title": "#000000", "body": "#111111",
        "meta": "#000000", "group": "#000000", "status": "#ffffff", "button": "#000000",
        "button_active": "#000000", "primary": "#000000", "primary_active": "#000000", "hover": "#ffffff",
        "disabled": "#000000", "entry": "#ffffff", "trough": "#ffffff", "progress": "#000000",
        "row_alt": "#e6e6e6",
    },
    "standard_dark": {
        "app": "#1e232b", "card": "#28303b", "title": "#f5f7fb", "body": "#c5ceda",
        "meta": "#a8b3c2", "group": "#e3e9f2", "status": "#303946", "button": "#ffffff",
        "button_active": "#ffffff", "primary": "#ffffff", "primary_active": "#ffffff", "hover": "#28303b",
        "disabled": "#ffffff", "entry": "#202631", "trough": "#414b59", "progress": "#4da3ff",
        "row_alt": "#2d3642",
    },
    "high_contrast_dark": {
        "app": "#000000", "card": "#0b0b0b", "title": "#ffffff", "body": "#ffffff",
        "meta": "#ffffff", "group": "#ffffff", "status": "#000000", "button": "#ffffff",
        "button_active": "#ffffff", "primary": "#ffffff", "primary_active": "#ffffff", "hover": "#0b0b0b",
        "disabled": "#ffffff", "entry": "#000000", "trough": "#000000", "progress": "#ffffff",
        "row_alt": "#202020",
    },
}


THEME_LABELS = {
    "standard_light": "Standard light",
    "high_contrast_light": "High-contrast light",
    "standard_dark": "Standard dark",
    "high_contrast_dark": "High-contrast dark",
}


class ThemeMixin(UIBase):
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
        style.configure("File.TCheckbutton", background=colors["card"], foreground=colors["title"], font=("Segoe UI", 9))
        style.map("File.TCheckbutton", background=[("active", colors["card"])], foreground=[("active", colors["title"])])
        style.configure("Files.Treeview", background=colors["card"], fieldbackground=colors["card"], foreground=colors["title"], rowheight=38, font=("Segoe UI", 9))
        style.configure("Files.Treeview.Heading", background=colors["status"], foreground=colors["group"], font=("Segoe UI Semibold", 9))
        style.map("Files.Treeview", background=[("selected", colors["hover"])], foreground=[("selected", colors["title"])])
        style.map("Files.Treeview.Heading", background=[("active", colors["status"])], foreground=[("active", colors["group"])])
        style.configure("Link.TEntry", fieldbackground=colors["entry"], foreground=colors["title"], padding=10, borderwidth=1)
        style.configure("Folder.TEntry", fieldbackground=colors["entry"], foreground=colors["title"], padding=8, borderwidth=1)
        style.configure("Download.Horizontal.TProgressbar", troughcolor=colors["trough"], background=colors["progress"], borderwidth=0, thickness=8)
        self.configure(bg=colors["app"])
        if hasattr(self, "files_tree"):
            self.files_tree.configure(style="Files.Treeview")
            self.files_tree.tag_configure("row-alt", background=colors["row_alt"])
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
