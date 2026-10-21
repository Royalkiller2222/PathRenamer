"""Colour themes.

A theme is a palette: a colour for each role below (window background, text,
buttons, table rows, ...). The app's stylesheet is built from the palette, so
the built-in themes and the user's Custom theme work the same way.
"""
import re

# (role, label, group) — the order and labels are what the Custom theme editor shows
ROLES = [
    ("bg", "Background", "Window"),
    ("text", "Text", "Window"),
    ("muted", "Secondary text", "Window"),
    ("accent", "Accent (titles, focus, progress)", "Window"),
    ("border", "Borders", "Window"),
    ("bar", "Menu and status bar", "Window"),
    ("bar_border", "Status bar line", "Window"),
    ("surface", "Text boxes and lists background", "Inputs"),
    ("list_bg", "File list background", "Inputs"),
    ("label_bg", "Example boxes (format editor)", "Inputs"),
    ("tab", "Tabs", "Inputs"),
    ("grid", "Table grid lines", "Inputs"),
    ("button", "Button", "Buttons"),
    ("button_border", "Button border", "Buttons"),
    ("hover", "Button under the mouse", "Buttons"),
    ("pressed", "Button pressed / selected toggle", "Buttons"),
    ("selected", "Selected menu and list item", "Buttons"),
    ("soft", "List item under the mouse", "Buttons"),
    ("disabled_bg", "Disabled button", "Buttons"),
    ("disabled_text", "Disabled text", "Buttons"),
    ("preview_bg", "Preview button", "Preview button"),
    ("preview_hover", "Preview button under the mouse", "Preview button"),
    ("preview_fg", "Preview button text", "Preview button"),
    ("rename_bg", "Rename button", "Rename button"),
    ("rename_hover", "Rename button under the mouse", "Rename button"),
    ("rename_fg", "Rename button text", "Rename button"),
    ("rename_border", "Rename button border", "Rename button"),
    ("rename_dis_bg", "Disabled background", "Rename button"),
    ("rename_dis_fg", "Disabled text", "Rename button"),
    ("rename_dis_border", "Disabled border", "Rename button"),
    ("remove_bg", "Remove button", "Remove button"),
    ("remove_hover", "Remove button under the mouse", "Remove button"),
    ("remove_fg", "Remove button text and border", "Remove button"),
    ("remove_dis_bg", "Disabled background", "Remove button"),
    ("remove_dis_fg", "Disabled text", "Remove button"),
    ("remove_dis_border", "Disabled border", "Remove button"),
    ("undo_bg", "Undo button", "Undo button"),
    ("undo_hover", "Undo button under the mouse", "Undo button"),
    ("undo_fg", "Undo button text and border", "Undo button"),
    ("undo_dis_bg", "Disabled background", "Undo button"),
    ("undo_dis_fg", "Disabled text", "Undo button"),
    ("undo_dis_border", "Disabled border", "Undo button"),
    ("row_ok_bg", "Ready", "Preview rows (background)"),
    ("row_low_bg", "Needs checking", "Preview rows (background)"),
    ("row_conflict_bg", "Conflict", "Preview rows (background)"),
    ("row_error_bg", "Error", "Preview rows (background)"),
    ("row_ok_fg", "Ready", "Preview rows (text)"),
    ("row_low_fg", "Needs checking", "Preview rows (text)"),
    ("row_conflict_fg", "Conflict", "Preview rows (text)"),
    ("row_error_fg", "Error", "Preview rows (text)"),
]
ROLE_KEYS = [r for r, _, _ in ROLES]

_DARK_ROWS = dict(row_ok_bg="#1d4023", row_ok_fg="#a8f0a8", row_conflict_bg="#4a1d1d",
                  row_conflict_fg="#ff9090", row_low_bg="#3d3517", row_low_fg="#f0d890",
                  row_error_bg="#33203d", row_error_fg="#cc80ff")

THEMES = {
    # Neutral dark greys; the coloured buttons keep their meaning (green = rename,
    # red = remove, amber = undo) and problem rows stand out.
    "dark": dict(
        bg="#141416", text="#e4e4e7", muted="#9a9aa3", accent="#d4d4d8", border="#2e2e34",
        bar="#0e0e10", bar_border="#232328", surface="#1c1c20", list_bg="#141416",
        label_bg="#1c1c20", tab="#1c1c20", grid="#222227",
        button="#222226", button_border="#36363d", hover="#2b2b31", pressed="#34343b",
        selected="#2a2a30", soft="#1a1a1e", disabled_bg="#18181b", disabled_text="#5c5c66",
        preview_bg="#26262c", preview_hover="#303037", preview_fg="#f4f4f5",
        rename_bg="#142a1d", rename_hover="#1a3625", rename_fg="#86efac", rename_border="#86efac",
        rename_dis_bg="#151b17", rename_dis_fg="#43574a", rename_dis_border="#223228",
        remove_bg="#2e1616", remove_hover="#3b1c1c", remove_fg="#f87171",
        remove_dis_bg="#1d1515", remove_dis_fg="#5e3d3d", remove_dis_border="#2f1e1e",
        undo_bg="#2c2412", undo_hover="#392e17", undo_fg="#fbbf24",
        undo_dis_bg="#1c1910", undo_dis_fg="#5e5232", undo_dis_border="#2e2817",
        row_ok_bg="#1b1d1c", row_ok_fg="#9ee6b4", row_conflict_bg="#3a1a1a",
        row_conflict_fg="#ff9b9b", row_low_bg="#332c14", row_low_fg="#f2dc96",
        row_error_bg="#2d1f38", row_error_fg="#d29bff"),
    # The original green "Dark" theme
    "emerald": dict(
        bg="#0b100b", text="#c2dcc2", muted="#7ba37b", accent="#4ade80", border="#234023",
        bar="#080c08", bar_border="#1a281a", surface="#131b13", list_bg="#0b100b",
        label_bg="#131b13", tab="#162116", grid="#182418",
        button="#162116", button_border="#2a4d2a", hover="#1e2e1e", pressed="#274027",
        selected="#162116", soft="#101710", disabled_bg="#0f150f", disabled_text="#4a5f4a",
        preview_bg="#0e2416", preview_hover="#123120", preview_fg="#4ade80",
        rename_bg="#123c1e", rename_hover="#174d27", rename_fg="#86efac", rename_border="#86efac",
        rename_dis_bg="#0d1f12", rename_dis_fg="#3f5f4a", rename_dis_border="#1e3a26",
        remove_bg="#3d1414", remove_hover="#4e1a1a", remove_fg="#f87171",
        remove_dis_bg="#241010", remove_dis_fg="#6a3a3a", remove_dis_border="#3a1a1a",
        undo_bg="#3a2a10", undo_hover="#4a3615", undo_fg="#fbbf24",
        undo_dis_bg="#241d0a", undo_dis_fg="#6a5a2a", undo_dis_border="#3a2f14",
        **_DARK_ROWS),
    "midnight": dict(
        bg="#060a12", text="#bcd0f0", muted="#5d739e", accent="#4d9fff", border="#1b2b4a",
        bar="#04070d", bar_border="#131e33", surface="#0d1524", list_bg="#060a12",
        label_bg="#0d1524", tab="#0d1524", grid="#111c30",
        button="#101a2e", button_border="#22355c", hover="#16233d", pressed="#1d2f52",
        selected="#101a2e", soft="#0b1220", disabled_bg="#0a1120", disabled_text="#44577e",
        preview_bg="#0d2138", preview_hover="#122c4a", preview_fg="#4d9fff",
        rename_bg="#0f2f1e", rename_hover="#143d28", rename_fg="#3fb950", rename_border="#3fb950",
        rename_dis_bg="#0b1f16", rename_dis_fg="#2d5a3f", rename_dis_border="#1a3a2a",
        remove_bg="#331116", remove_hover="#43161d", remove_fg="#f85149",
        remove_dis_bg="#220e12", remove_dis_fg="#6a3038", remove_dis_border="#3a1a20",
        undo_bg="#332508", undo_hover="#43310c", undo_fg="#d29922",
        undo_dis_bg="#221a08", undo_dis_fg="#6a5424", undo_dis_border="#3a2e12",
        **_DARK_ROWS),
    "light": dict(
        bg="#faf9f7", text="#262421", muted="#706a5f", accent="#5b5bd6", border="#ddd8d0",
        bar="#f1efec", bar_border="#ddd8d0", surface="#ffffff", list_bg="#ffffff",
        label_bg="#f1efec", tab="#f1efec", grid="#e5e1da",
        button="#f1efec", button_border="#ddd8d0", hover="#e9e6e1", pressed="#ddd8d0",
        selected="#e9e6e1", soft="#f1efec", disabled_bg="#f1efec", disabled_text="#a8a29a",
        preview_bg="#eceafd", preview_hover="#dedafb", preview_fg="#4a3fc4",
        rename_bg="#e3f6e6", rename_hover="#d0efd6", rename_fg="#177a33", rename_border="#1a7f37",
        rename_dis_bg="#eef7f0", rename_dis_fg="#8fbf9c", rename_dis_border="#c4e2cc",
        remove_bg="#fdecea", remove_hover="#fbdcd8", remove_fg="#c2352b",
        remove_dis_bg="#fbf1f0", remove_dis_fg="#dc9a94", remove_dis_border="#efccc8",
        undo_bg="#fdf4d7", undo_hover="#fbecba", undo_fg="#96660a",
        undo_dis_bg="#fbf6e6", undo_dis_fg="#c9ab6e", undo_dis_border="#ecdcb4",
        row_ok_bg="#e3f6e6", row_ok_fg="#14662b", row_conflict_bg="#fdecea",
        row_conflict_fg="#a72a20", row_low_bg="#fdf4d7", row_low_fg="#7a5605",
        row_error_bg="#f3e8fd", row_error_fg="#6d28d9"),
}
# Menu order and names
THEME_NAMES = [("dark", "Dark"), ("emerald", "Emerald"), ("midnight", "Midnight"),
               ("light", "Light"), ("custom", "Custom")]
BASE_THEMES = [k for k, _ in THEME_NAMES if k != "custom"]

_HEX = re.compile(r"#[0-9a-fA-F]{6}")

def is_color(value) -> bool:
    return isinstance(value, str) and bool(_HEX.fullmatch(value))

def custom_palette(custom) -> dict:
    """The Custom theme from its saved form {"base": theme, "colors": {role: "#rrggbb"}}:
    the base theme with the user's colours on top (bad values are ignored)."""
    custom = custom if isinstance(custom, dict) else {}
    base = custom.get("base") if custom.get("base") in THEMES else "dark"
    pal = dict(THEMES[base])
    for role, value in (custom.get("colors") or {}).items():
        if role in pal and is_color(value):
            pal[role] = value.lower()
    return pal

def palette_for(cfg) -> dict:
    """The palette of the theme chosen in the settings."""
    name = cfg.get("theme", "dark")
    if name == "custom":
        return custom_palette(cfg.get("custom_theme"))
    return dict(THEMES.get(name, THEMES["dark"]))

def is_light(pal) -> bool:
    """True for light backgrounds (used for a few hard-coded greys)."""
    c = pal.get("bg", "#000000")
    r, g, b = (int(c[i:i + 2], 16) for i in (1, 3, 5))
    return (0.299 * r + 0.587 * g + 0.114 * b) > 140


_BASE_QSS = """
QMainWindow,QWidget{background:%(bg)s;color:%(text)s;font-family:'Segoe UI',Arial,sans-serif;font-size:13px}
QGroupBox{border:1px solid %(border)s;border-radius:6px;margin-top:8px;padding-top:8px;font-weight:bold;color:%(accent)s}
QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 4px}
QLineEdit,QComboBox{background:%(surface)s;border:1px solid %(border)s;border-radius:4px;padding:5px 8px;color:%(text)s}
QLineEdit:focus,QComboBox:focus{border:1px solid %(accent)s}
QComboBox::drop-down{border:none}
QTextEdit{background:%(surface)s;border:1px solid %(border)s;color:%(text)s}
QPushButton{background:%(button)s;border:1px solid %(button_border)s;border-radius:5px;padding:6px 16px;color:%(text)s;font-weight:bold}
QPushButton:hover{background:%(hover)s}
QPushButton:pressed{background:%(pressed)s}
QPushButton:disabled{color:%(disabled_text)s;background:%(disabled_bg)s}
QPushButton#btn_preview{background:%(preview_bg)s;border-color:%(accent)s;color:%(preview_fg)s}
QPushButton#btn_preview:hover{background:%(preview_hover)s}
QPushButton#btn_rename{background:%(rename_bg)s;border-color:%(rename_border)s;color:%(rename_fg)s}
QPushButton#btn_rename:hover{background:%(rename_hover)s}
QPushButton#btn_rename:disabled{background:%(rename_dis_bg)s;color:%(rename_dis_fg)s;border-color:%(rename_dis_border)s}
QPushButton#btn_remove{background:%(remove_bg)s;border-color:%(remove_fg)s;color:%(remove_fg)s}
QPushButton#btn_remove:hover{background:%(remove_hover)s}
QPushButton#btn_remove:disabled{background:%(remove_dis_bg)s;color:%(remove_dis_fg)s;border-color:%(remove_dis_border)s}
QPushButton#btn_undo{background:%(undo_bg)s;border-color:%(undo_fg)s;color:%(undo_fg)s}
QPushButton#btn_undo:hover{background:%(undo_hover)s}
QPushButton#btn_undo:disabled{background:%(undo_dis_bg)s;color:%(undo_dis_fg)s;border-color:%(undo_dis_border)s}
QStatusBar{background:%(bar)s;color:%(muted)s;border-top:1px solid %(bar_border)s}
QProgressBar{border:1px solid %(border)s;border-radius:4px;background:%(surface)s;text-align:center;color:%(text)s;height:16px}
QProgressBar::chunk{background:%(accent)s;border-radius:3px}
QMenuBar{background:%(bar)s;color:%(text)s}
QMenuBar::item:selected{background:%(selected)s}
QMenu{background:%(bg)s;border:1px solid %(border)s}
QMenu::item:selected{background:%(selected)s}
QTableWidget{gridline-color:%(grid)s;border:1px solid %(grid)s}
QHeaderView::section{background:%(bg)s;color:%(text)s;padding:6px;border:none;border-bottom:2px solid %(border)s;font-weight:bold}
QTableWidget::item{padding:4px 8px}
QListWidget{background:%(list_bg)s;border:1px solid %(border)s;border-radius:4px}
QListWidget::item{padding:6px 8px;border-radius:2px;color:%(text)s}
QListWidget::item:selected{background:%(selected)s;color:%(text)s}
QListWidget::item:hover{background:%(soft)s}
QLabel#preview_label{background:%(label_bg)s;border:1px solid %(border)s;border-radius:4px;padding:8px;font-size:13px}
"""

_TAB_QSS = """
            QTabWidget::pane { border: 1px solid %(border)s; background: %(bg)s; }
            QTabBar::tab { background: %(tab)s; color: %(text)s; padding: 8px 16px; border: 1px solid %(border)s; border-bottom: none; }
            QTabBar::tab:selected { background: %(bg)s; color: %(accent)s; }
            QTabBar::tab:hover:!selected { background: %(hover)s; }
        """

# Rules for the Modern layout and the sidebar Settings window. Every rule is
# scoped to #modernRoot / #settingsModern, so the Classic layout is untouched.
_MODERN_QSS = """
QWidget#modernRoot{background:%(bg)s}
#modernRoot QFrame#topBar{background:%(bar)s;border:none;border-bottom:1px solid %(border)s}
#modernRoot QFrame#card{background:%(bg)s;border:1px solid %(border)s;border-radius:10px}
#modernRoot QFrame#actionBar{background:%(bg)s;border:none;border-top:1px solid %(border)s}
#modernRoot QLabel#paneTitle{color:%(accent)s;font-weight:bold;font-size:12px;letter-spacing:1px;background:transparent}
#modernRoot QLabel#paneCount,#modernRoot QLabel#muted{color:%(muted)s;background:transparent}
#modernRoot QLabel#summary{color:%(muted)s;background:transparent;padding-left:2px}
#modernRoot QLineEdit#srcEdit{padding:7px 10px;border-radius:8px}
#modernRoot QComboBox{border-radius:8px;padding:6px 10px}
#modernRoot QPushButton{border-radius:8px;padding:7px 16px}
#modernRoot QPushButton#flat{background:transparent;border:1px solid transparent;color:%(muted)s;padding:4px 8px;font-weight:normal}
#modernRoot QPushButton#flat:hover{background:%(hover)s;border-color:%(button_border)s;color:%(text)s}
#modernRoot QPushButton#flat:disabled{color:%(disabled_text)s}
#modernRoot QPushButton#link{background:transparent;border:none;color:%(accent)s;padding:0 4px;font-weight:normal;text-decoration:underline}
#modernRoot QPushButton#seg{padding:7px 14px}
#modernRoot QPushButton#seg:checked{background:%(pressed)s;color:%(accent)s;border-color:%(accent)s}
#modernRoot QFrame#dropZone{background:%(bg)s;border:2px dashed %(border)s;border-radius:10px}
#modernRoot QFrame#dropZone[hover="true"]{border-color:%(accent)s;background:%(soft)s}
#modernRoot QLabel#dropTitle{color:%(text)s;font-size:15px;font-weight:bold;background:transparent}
#modernRoot QLabel#dropHint{color:%(muted)s;background:transparent}
#modernRoot QLabel#emptyHint{color:%(muted)s;background:transparent;font-size:13px}
#modernRoot QListWidget{border:none;background:%(bg)s;border-radius:6px}
#modernRoot QListWidget::item{padding:%(list_pad)s;border-radius:6px;margin:1px 0}
#modernRoot QTableWidget{border:none;background:%(bg)s}
#modernRoot QHeaderView::section{background:%(bg)s}
#modernRoot QLabel#details{background:transparent;color:%(text)s}
#modernRoot QSplitter::handle{background:transparent}
#modernRoot QSplitter::handle:hover{background:%(border)s}
#modernRoot QProgressBar#slimProgress{border:none;border-radius:2px;background:%(surface)s;max-height:4px;min-height:4px}
#modernRoot QProgressBar#slimProgress::chunk{background:%(accent)s;border-radius:2px}
#modernRoot QScrollBar:vertical{background:transparent;width:10px;margin:2px}
#modernRoot QScrollBar:horizontal{background:transparent;height:10px;margin:2px}
#modernRoot QScrollBar::handle:vertical{background:%(border)s;border-radius:3px;min-height:28px}
#modernRoot QScrollBar::handle:horizontal{background:%(border)s;border-radius:3px;min-width:28px}
#modernRoot QScrollBar::handle:hover{background:%(button_border)s}
#modernRoot QScrollBar::add-line,#modernRoot QScrollBar::sub-line{width:0;height:0;border:none;background:none}
#modernRoot QScrollBar::add-page,#modernRoot QScrollBar::sub-page{background:none}
QDialog#settingsModern QListWidget#settingsNav{background:%(bar)s;border:none;border-right:1px solid %(border)s;padding:8px 6px;outline:0}
QDialog#settingsModern QListWidget#settingsNav::item{padding:9px 14px;border-radius:8px;margin:1px 0;color:%(text)s}
QDialog#settingsModern QListWidget#settingsNav::item:selected{background:%(pressed)s;color:%(accent)s}
QDialog#settingsModern QListWidget#settingsNav::item:hover:!selected{background:%(hover)s}
QDialog#settingsModern QGroupBox{border-radius:10px;margin-top:12px;padding-top:12px}
QDialog#settingsModern QPushButton{border-radius:8px}
QDialog#settingsModern QLineEdit,QDialog#settingsModern QComboBox{border-radius:8px}
"""

def base_stylesheet(pal) -> str:
    return _BASE_QSS % pal

def tab_stylesheet(pal) -> str:
    """QTabWidget/QTabBar styling for dialogs with tabs."""
    return _TAB_QSS % pal

def modern_stylesheet(pal, density="comfortable") -> str:
    return _MODERN_QSS % dict(pal, list_pad="3px 8px" if density == "compact" else "7px 10px")

def app_stylesheet(pal, density="comfortable") -> str:
    """The whole window's stylesheet: the theme plus the (scoped) Modern rules."""
    return base_stylesheet(pal) + modern_stylesheet(pal, density)
