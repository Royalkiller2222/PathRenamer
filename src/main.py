"""PathRenamer for Windows: the window, dialogs and tray. The engine
(scanning, matching, naming, renaming) lives in core.py."""
from core import *  # noqa: F401,F403  (the engine; see core.py)
from core import (_cleanup_empty_dirs, _is_inside, _normalize_rel, _same_path, _skip_watch_dir, _watchable)  # noqa: F401
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QComboBox, QFileDialog, QStatusBar,
    QMessageBox, QProgressBar, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QDialog, QFormLayout, QCheckBox, QDialogButtonBox,
    QGroupBox, QTabWidget, QListWidget, QListWidgetItem, QScrollArea,
    QGridLayout, QFrame, QScrollBar, QSizePolicy, QTextEdit,
    QMenu, QInputDialog, QStyledItemDelegate, QSplitter, QStackedWidget, QButtonGroup,
    QTextBrowser, QSystemTrayIcon,
)
from PyQt6.QtGui import (QColor, QAction, QActionGroup, QDesktopServices, QDragEnterEvent,
                         QDropEvent, QKeySequence, QShortcut, QPainter, QPalette)
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QLocalServer, QLocalSocket
from PyQt6.QtCore import (Qt, QThread, pyqtSignal, QObject, QUrl, QTimer, QVariantAnimation,
                          QEasingCurve, QByteArray, QEvent)
from watchfolder import FolderWatcher
import updates
import startup

# ═══════════════════════════════════════════════════════════════════
#  THEMES  (palettes and stylesheets live in themes.py)
# ═══════════════════════════════════════════════════════════════════
from themes import (THEMES, THEME_NAMES, BASE_THEMES, ROLES, palette_for, custom_palette,
                    is_color, app_stylesheet, base_stylesheet, tab_stylesheet)

# ═══════════════════════════════════════════════════════════════════
#  BACKGROUND JOBS (downloads, media servers, license checks)
# ═══════════════════════════════════════════════════════════════════
class BackgroundJobs(QObject):
    """Runs slow network work in a background thread and hands the result
    back on the UI thread as on_done(result, error)."""
    _done = pyqtSignal(object, object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.running = 0
        self._done.connect(self._deliver)

    def run(self, work, on_done=None):
        self.running += 1

        def target():
            try:
                result, error = work(), None
            except Exception as e:
                log.warning(f"Background job failed: {e!r}")
                result, error = None, e
            try:
                self._done.emit(on_done, result, error)
            except RuntimeError:              # closed meanwhile
                pass
        threading.Thread(target=target, daemon=True).start()

    def _deliver(self, on_done, result, error):
        self.running -= 1
        if on_done is None:
            return
        try:
            on_done(result, error)
        except RuntimeError as e:             # its window was closed meanwhile
            if "has been deleted" not in str(e):
                raise


def _set_link_color(pal):
    """Links in labels and release notes in the theme's accent colour (Qt's
    default dark blue can't be read on the dark themes)."""
    app = QApplication.instance()
    if app is None or not pal.get("accent"):
        return
    palette = app.palette()
    for role in (QPalette.ColorRole.Link, QPalette.ColorRole.LinkVisited):
        palette.setColor(role, QColor(pal["accent"]))
    app.setPalette(palette)


def _note_label(text):
    note = QLabel(text)
    note.setWordWrap(True)
    note.setStyleSheet("color:#888;font-size:11px")
    return note


class UpdateDialog(QDialog):
    """A newer PathRenamer: what's new, and Update Now. One click downloads
    it from GitHub, checks it against what GitHub published, installs it and
    starts the new version (the portable exe is swapped in place; a copy run
    from source opens the download page instead)."""
    _progress = pyqtSignal(int, int)
    _finished = pyqtSignal(object, object)

    def __init__(self, release, parent=None, start=False, kind=None):
        super().__init__(parent)
        self.release = release
        self.kind = kind or updates.install_kind()
        self.asset = release.installer if self.kind == "installer" else release.portable
        if self.kind != "source" and self.asset is None:
            self.kind = "source"                  # no file for this kind of copy: the page has it
        self.skipped = self.installing = False
        self._cancel = False
        self._busy = False
        self.setWindowTitle("Update PathRenamer")
        self.setMinimumWidth(520)
        L = QVBoxLayout(self)
        head = QLabel(f"<b>PathRenamer {release.version} is available.</b> You have {APP_VERSION}.")
        head.setWordWrap(True)
        L.addWidget(head)
        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(release.notes or "See the release page for what's new.")
        notes.setMinimumHeight(220)
        L.addWidget(notes, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        L.addWidget(self.progress)
        self.message = _note_label({
            "installer": "Update Now downloads the new version, installs it and starts it again. "
                         "Your settings and rename history are kept.",
            "portable": "Update Now downloads the new PathRenamer.exe, puts it in place of this one and "
                        "starts it. Your settings and rename history are kept.",
            "source": "This copy runs from the source code: Update Now opens the download page.",
        }[self.kind])
        L.addWidget(self.message)
        row = QHBoxLayout()
        self.btn_skip = QPushButton("Skip This Version")
        self.btn_skip.clicked.connect(self._skip)
        self.btn_later = QPushButton("Later")
        self.btn_later.clicked.connect(self._later)
        self.btn_update = QPushButton("Update Now" if self.kind != "source" else "Open Download Page")
        self.btn_update.setDefault(True)
        self.btn_update.clicked.connect(self.update_now)
        row.addWidget(self.btn_skip)
        row.addStretch()
        row.addWidget(self.btn_later)
        row.addWidget(self.btn_update)
        L.addLayout(row)
        self._progress.connect(self._on_progress)
        self._finished.connect(self._on_finished)
        if start:
            QTimer.singleShot(0, self.update_now)

    def update_now(self):
        if self._busy:
            return
        if self.kind == "source":
            QDesktopServices.openUrl(QUrl(self.release.page_url))
            self.accept()
            return
        self._busy, self._cancel = True, False
        self.btn_update.setEnabled(False)
        self.btn_skip.setEnabled(False)
        self.btn_later.setText("Cancel")
        self.progress.setValue(0)
        self.progress.show()
        self.message.setText("Downloading…")
        threading.Thread(target=self._download, daemon=True).start()

    def _download(self):
        try:
            path = updates.download(self.asset, progress=self._report, should_cancel=lambda: self._cancel)
            result = (path, None)
        except Exception as e:                    # shown in the dialog
            result = (None, e)
        try:
            self._finished.emit(*result)
        except RuntimeError:                      # the dialog was closed meanwhile
            pass

    def _report(self, done, total):
        try:
            self._progress.emit(done, total)
        except RuntimeError:
            pass

    def _on_progress(self, done, total):
        mb = 1024 * 1024
        if total:
            self.progress.setValue(min(100, done * 100 // total))
            self.message.setText(f"Downloading… {done / mb:.1f} MB of {total / mb:.1f} MB")
        else:
            self.message.setText(f"Downloading… {done / mb:.1f} MB")

    def _on_finished(self, path, error):
        self._busy = False
        if error is None and self._cancel:        # closed or cancelled as it finished: don't install
            error = updates.UpdateError("The download was cancelled.")
        if error is None:
            self.message.setText("Installing the update: PathRenamer closes and starts again…")
            try:
                updates.start_update(path, self.kind)
            except Exception as e:
                error = e
            else:
                self.installing = True
                self.accept()
                return
        self.progress.hide()
        self.message.setText(f"The update didn't finish: {error}" if not self._cancel else
                             "The download was cancelled.")
        self.btn_update.setEnabled(True)
        self.btn_update.setText("Try Again")
        self.btn_skip.setEnabled(True)
        self.btn_later.setText("Later")

    def _skip(self):
        self.skipped = True
        self.reject()

    def _later(self):
        if self._busy:                            # Cancel: the download stops at its next chunk
            self._cancel = True
            self.btn_later.setEnabled(False)
            self.message.setText("Cancelling…")
            return
        self.reject()

    def reject(self):
        if self._busy:
            self._cancel = True
        super().reject()


class PlexSignInDialog(QDialog):
    """Sign in with Plex: plex.tv opens in the browser, the user approves
    PathRenamer there, and their server's address and token come back."""
    POLL_MS = 2000
    TIMEOUT_S = 300

    def __init__(self, client_id, parent=None, sign_in=None):
        super().__init__(parent)
        self.setWindowTitle("Sign in with Plex")
        self.setMinimumWidth(440)
        self.server = None                    # {name, url, token} once signed in
        self._sign_in = sign_in or PlexSignIn(client_id)
        self._pin = None
        self._page = ""
        self._closed = False
        self._started = time.monotonic()
        self.jobs = BackgroundJobs(self)
        L = QVBoxLayout(self)
        self.msg = QLabel("Opening plex.tv in your browser…")
        self.msg.setWordWrap(True)
        L.addWidget(self.msg)
        row = QHBoxLayout()
        self.btn_open = QPushButton("Open the Page Again")
        self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self._page)))
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(self.btn_open); row.addStretch(); row.addWidget(cancel)
        L.addLayout(row)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._poll)
        self.jobs.run(self._sign_in.start, self._pin_ready)

    def done(self, result):
        self._closed = True
        self._timer.stop()
        super().done(result)

    def _pin_ready(self, result, error):
        if self._closed:
            return
        if error is not None:
            self.msg.setText(f"Couldn't start signing in: {error}")
            return
        pin_id, code, self._page = result
        self._pin = (pin_id, code)
        self.btn_open.setEnabled(True)
        QDesktopServices.openUrl(QUrl(self._page))
        self.msg.setText("Approve PathRenamer on the plex.tv page that just opened in your browser. "
                         "This window closes by itself when you're done.")
        self._timer.start(self.POLL_MS)

    def _poll(self):
        if self._closed:
            return
        if time.monotonic() - self._started > self.TIMEOUT_S:
            self.msg.setText("Signing in took too long. Close this window and try again.")
            return
        pin = self._pin
        self.jobs.run(lambda: self._sign_in.token(*pin), self._token_ready)

    def _token_ready(self, token, error):
        if self._closed:
            return
        if error is not None or not token:    # not approved yet (or a hiccup): keep waiting
            self._timer.start(self.POLL_MS)
            return
        self.msg.setText("Signed in. Looking for your Plex server…")
        self.jobs.run(lambda: self._sign_in.servers(token),
                      lambda servers, err: self._servers_ready(servers, err, token))

    def _servers_ready(self, servers, error, token):
        if self._closed:
            return
        # No server found: the token still works with an address typed by hand
        self.server = servers[0] if servers and error is None else {"name": "", "url": "", "token": token}
        self.accept()


# ═══════════════════════════════════════════════════════════════════
#  SETTINGS DIALOG
# ═══════════════════════════════════════════════════════════════════
class SettingsDialog(QDialog):
    PRO_SWITCHES = ("watch_auto_rename", "plex_auto_scan", "subs_auto")

    def __init__(self, cfg, parent=None, page=None):
        super().__init__(parent)
        self.cfg = dict(cfg)
        self._initial = dict(cfg)
        self.license = License(self.cfg)
        self.jobs = BackgroundJobs(self)
        self.setWindowTitle("Settings"); self.setMinimumWidth(600)
        self.setMinimumHeight(500)
        L = QVBoxLayout(self)

        theme = self.cfg.get("theme", "dark")
        pages = [
            ("Folders", self._build_folders_tab(theme)),
            ("Formats", self._build_formats_tab(theme)),
            ("Appearance", self._build_appearance_tab(theme)),
            ("Options", self._build_options_tab(theme)),
            ("Automation", self._build_automation_tab(theme)),
            ("Subtitles", self._build_subtitles_tab(theme)),
            ("API Keys", self._build_api_tab(theme)),
            ("Pro", self._build_pro_tab(theme)),
            ("About", self._build_about_tab(theme)),
        ]
        names = [name for name, _ in pages]
        start = names.index(page) if page in names else 0
        self.nav = None
        if self.cfg.get("layout", "modern") == "modern":
            # Sidebar navigation (styled by the #settingsModern rules)
            self.setObjectName("settingsModern")
            self.setMinimumWidth(720)
            L.setContentsMargins(0, 0, 0, 12)
            body = QHBoxLayout(); body.setSpacing(0)
            self.nav = QListWidget(); self.nav.setObjectName("settingsNav")
            self.nav.setFixedWidth(170)
            self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            stack = QStackedWidget()
            for name, page in pages:
                self.nav.addItem(name)
                page.layout().setContentsMargins(18, 14, 18, 8)
                stack.addWidget(page)
            self.nav.currentRowChanged.connect(stack.setCurrentIndex)
            self.nav.setCurrentRow(start)
            body.addWidget(self.nav); body.addWidget(stack, 1)
            L.addLayout(body, 1)
        else:
            tabs = QTabWidget()
            tabs.setStyleSheet(tab_stylesheet(palette_for(self.cfg)))
            for name, page in pages:
                tabs.addTab(page, name)
            tabs.setCurrentIndex(start)
            L.addWidget(tabs)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._save); btns.rejected.connect(self.reject)
        if self.nav is not None:
            row = QHBoxLayout(); row.setContentsMargins(0, 0, 14, 0); row.addWidget(btns)
            L.addLayout(row)
        else:
            L.addWidget(btns)

    def _get_tab_style(self, theme):
        return tab_stylesheet(palette_for(self.cfg))

    def _edit_custom_theme(self):
        main_window = self.parent()
        preview = getattr(main_window, "_apply_theme", None)
        dlg = CustomThemeDialog(self.cfg, self, preview=preview)
        if dlg.exec():
            self.cfg["custom_theme"] = dlg.result_theme()
            self.theme_combo.setCurrentIndex([k for k, _ in THEME_NAMES].index("custom"))
        if preview:
            preview(palette_for(main_window.cfg))   # the window keeps its theme until OK

    def _build_folders_tab(self, theme):
        w = QWidget()
        L = QVBoxLayout(w)
        L.setContentsMargins(10, 10, 10, 10)

        grp = QGroupBox("Library Root Folders")
        gl = QFormLayout(grp)

        self.root_tv = QLineEdit(self.cfg.get("root_tv", ""))
        self.root_tv.setPlaceholderText("e.g., D:/Plex/TV Shows")
        btn_tv = QPushButton("Browse")
        btn_tv.clicked.connect(lambda: self._browse_folder(self.root_tv))
        gl.addRow("TV Shows:", self._make_browse_row(self.root_tv, btn_tv))

        self.root_movie = QLineEdit(self.cfg.get("root_movie", ""))
        self.root_movie.setPlaceholderText("e.g., D:/Plex/Movies")
        btn_movie = QPushButton("Browse")
        btn_movie.clicked.connect(lambda: self._browse_folder(self.root_movie))
        gl.addRow("Movies:", self._make_browse_row(self.root_movie, btn_movie))

        self.root_anime = QLineEdit(self.cfg.get("root_anime", ""))
        self.root_anime.setPlaceholderText("e.g., D:/Plex/Anime")
        btn_anime = QPushButton("Browse")
        btn_anime.clicked.connect(lambda: self._browse_folder(self.root_anime))
        gl.addRow("Anime:", self._make_browse_row(self.root_anime, btn_anime))

        L.addWidget(grp)
        L.addStretch()
        return w

    def _make_browse_row(self, lineedit, button):
        row = QHBoxLayout()
        row.addWidget(lineedit, 1)
        row.addWidget(button)
        return row

    def _browse_folder(self, lineedit):
        f = QFileDialog.getExistingDirectory(self, "Select Folder", lineedit.text())
        if f:
            lineedit.setText(f)

    def _build_formats_tab(self, theme):
        w = QWidget()
        L = QVBoxLayout(w)
        L.setContentsMargins(10, 10, 10, 10)

        grp = QGroupBox("Naming Templates")
        gl = QFormLayout(grp)

        self.fmt_tv = QLineEdit(self.cfg.get("format_tv", ""))
        self.fmt_tv.setPlaceholderText("{show}/Season {s00}/{show} - {s00e00} - {title}")
        gl.addRow("TV Shows:", self.fmt_tv)

        self.fmt_movie = QLineEdit(self.cfg.get("format_movie", ""))
        self.fmt_movie.setPlaceholderText(DEFAULTS["format_movie"])
        gl.addRow("Movies:", self.fmt_movie)

        self.fmt_anime = QLineEdit(self.cfg.get("format_anime", ""))
        self.fmt_anime.setPlaceholderText("{show}/Season {s00}/{show} - {s00e00} - {title}")
        gl.addRow("Anime:", self.fmt_anime)

        L.addWidget(grp)

        help_grp = QGroupBox("Text Format Editor")
        help_l = QVBoxLayout(help_grp)

        edit_btn = QPushButton("Edit Text Format →")
        edit_btn.setStyleSheet("font-size: 14px; padding: 8px;")
        edit_btn.clicked.connect(lambda: FormatEditorDialog(self.cfg, self).exec())
        help_l.addWidget(edit_btn)

        hint = QLabel("Customize how files are renamed with variables like {show}, {s00e00}, {title}, etc.")
        hint.setStyleSheet("color: #888; font-size: 11px; margin-top: 8px;")
        help_l.addWidget(hint)

        L.addWidget(help_grp)

        grp3 = QGroupBox("Episode Numbering")
        gl3 = QFormLayout(grp3)

        self.episode_tv = QComboBox()
        self.episode_tv.addItems(["DVD", "Absolute", "Streaming"])
        ep_map = {"dvd": 0, "absolute": 1, "streaming": 2}
        self.episode_tv.setCurrentIndex(ep_map.get(self.cfg.get("episode_tv", "dvd"), 0))
        gl3.addRow("TV Shows:", self.episode_tv)

        self.episode_anime = QComboBox()
        self.episode_anime.addItems(["DVD", "Absolute", "Streaming"])
        self.episode_anime.setCurrentIndex(ep_map.get(self.cfg.get("episode_anime", "dvd"), 0))
        gl3.addRow("Anime:", self.episode_anime)

        L.addWidget(grp3)
        L.addStretch()
        return w

    def _build_appearance_tab(self, theme):
        w = QWidget()
        L = QVBoxLayout(w)
        L.setContentsMargins(10, 10, 10, 10)

        grp = QGroupBox("Theme")
        gl = QFormLayout(grp)

        self.theme_combo = QComboBox()
        for key, label in THEME_NAMES:
            self.theme_combo.addItem(label, key)
        keys = [k for k, _ in THEME_NAMES]
        current = self.cfg.get("theme", "dark")
        self.theme_combo.setCurrentIndex(keys.index(current) if current in keys else 0)
        theme_row = QHBoxLayout()
        theme_row.addWidget(self.theme_combo, 1)
        btn_custom = QPushButton("Edit Custom Theme…")
        btn_custom.setToolTip("Choose every color of the Custom theme yourself")
        btn_custom.clicked.connect(self._edit_custom_theme)
        theme_row.addWidget(btn_custom)
        gl.addRow("Color Scheme:", theme_row)

        L.addWidget(grp)

        grp_layout = QGroupBox("Layout")
        fl = QFormLayout(grp_layout)
        self.layout_combo = QComboBox()
        self.layout_combo.addItems(["Modern — side-by-side panes", "Classic — original layout"])
        self.layout_combo.setCurrentIndex(LAYOUTS.index(self.cfg.get("layout", "modern"))
                                          if self.cfg.get("layout") in LAYOUTS else 0)
        fl.addRow("Window layout:", self.layout_combo)

        self.panel_combo = QComboBox()
        self.panel_combo.addItems(["Automatic (by window width)", "Side by side", "Stacked"])
        pm = self.cfg.get("panel_mode", "auto")
        self.panel_combo.setCurrentIndex(PANEL_MODES.index(pm) if pm in PANEL_MODES else 0)
        fl.addRow("File list position:", self.panel_combo)

        self.density_combo = QComboBox()
        self.density_combo.addItems(["Comfortable", "Compact"])
        dn = self.cfg.get("density", "comfortable")
        self.density_combo.setCurrentIndex(DENSITIES.index(dn) if dn in DENSITIES else 0)
        fl.addRow("Row spacing:", self.density_combo)

        self.show_details_chk = QCheckBox("Show the details panel (where each file and subtitle goes)")
        self.show_details_chk.setChecked(self.cfg.get("show_details", True))
        fl.addRow(self.show_details_chk)

        self.animations_chk = QCheckBox("Smooth animations")
        self.animations_chk.setChecked(self.cfg.get("animations", True))
        fl.addRow(self.animations_chk)

        layout_hint = QLabel("The Modern options apply to the Modern layout. "
                             "Colors always follow the theme above.")
        layout_hint.setWordWrap(True)
        layout_hint.setStyleSheet("color:#888;font-size:11px")
        fl.addRow(layout_hint)

        def _modern_only(index):
            for wdg in (self.panel_combo, self.density_combo, self.show_details_chk, self.animations_chk):
                wdg.setEnabled(index == 0)
        self.layout_combo.currentIndexChanged.connect(_modern_only)
        _modern_only(self.layout_combo.currentIndex())
        L.addWidget(grp_layout)

        grp2 = QGroupBox("Table Display")
        gl2 = QFormLayout(grp2)

        self.show_new_path = QCheckBox("Show full new path in table")
        self.show_new_path.setChecked(self.cfg.get("show_full_path", True))
        gl2.addRow(self.show_new_path)

        self.show_detected = QCheckBox("Show detected match name")
        self.show_detected.setChecked(self.cfg.get("show_detected", True))
        gl2.addRow(self.show_detected)

        L.addWidget(grp2)
        L.addStretch()
        return w

    def _build_options_tab(self, theme):
        w = QWidget()
        L = QVBoxLayout(w)
        L.setContentsMargins(10, 10, 10, 10)

        grp = QGroupBox("File Operations")
        gl = QFormLayout(grp)

        self.action_combo = QComboBox()
        for key, label in (("move", "Move the files (default)"),
                           ("copy", "Copy the files (originals stay)"),
                           ("hardlink", "Hard-link the files (torrents keep seeding)")):
            self.action_combo.addItem(label, key)
        self.action_combo.setCurrentIndex(max(0, self.action_combo.findData(self.cfg.get("rename_action", "move"))))
        self.action_combo.setToolTip(
            "Hard links put the file in your library without using extra space and without\n"
            "moving the download, so your torrent client keeps seeding. They only work when the\n"
            "downloads and the library are on the same drive; otherwise the file is copied.")
        gl.addRow("When renaming:", self.action_combo)

        self.rename_subs = QCheckBox("Rename subtitle files alongside video")
        self.rename_subs.setChecked(self.cfg.get("rename_subtitles", True))
        gl.addRow(self.rename_subs)

        self.read_media_info = QCheckBox("Read resolution and codecs from the video when the name doesn't say")
        self.read_media_info.setChecked(self.cfg.get("read_media_info", True))
        self.read_media_info.setToolTip("Fills {res}, {vc}, {c} and {ach} in your formats from the file itself\n"
                                        "(MKV, MP4 and AVI), e.g. for home videos or renamed files.")
        gl.addRow(self.read_media_info)

        self.cleanup_folders = QCheckBox("Delete empty folders after rename")
        self.cleanup_folders.setChecked(self.cfg.get("cleanup_empty_folders", True))
        gl.addRow(self.cleanup_folders)

        self.confirm_rename = QCheckBox("Show confirmation before renaming")
        self.confirm_rename.setChecked(self.cfg.get("confirm_rename", True))
        gl.addRow(self.confirm_rename)

        self.confirm_conflicts = QCheckBox("Ask before skipping conflicting files")
        self.confirm_conflicts.setChecked(self.cfg.get("confirm_conflicts", True))
        gl.addRow(self.confirm_conflicts)

        L.addWidget(grp)

        grp2 = QGroupBox("Behavior")
        gl2 = QFormLayout(grp2)

        self.remember_folders = QCheckBox("Remember last source folder")
        self.remember_folders.setChecked(self.cfg.get("remember_folders", True))
        gl2.addRow(self.remember_folders)

        self.auto_detect_type = QCheckBox("Auto-detect media type from folder structure")
        self.auto_detect_type.setChecked(self.cfg.get("auto_detect_type", False))
        gl2.addRow(self.auto_detect_type)

        L.addWidget(grp2)

        grp3 = QGroupBox("Tray and Windows Start")
        gl3 = QVBoxLayout(grp3)
        self.tray_on_close = QCheckBox("Keep PathRenamer running in the tray when the window is closed")
        self.tray_on_close.setChecked(bool(self.cfg.get("tray_on_close")))
        self.tray_on_close.setToolTip("The watch folder keeps working. Open or quit PathRenamer from its "
                                      "icon next to the clock.")
        gl3.addWidget(self.tray_on_close)
        self.start_with_windows = QCheckBox("Start PathRenamer in the tray when Windows starts")
        self.start_with_windows.setChecked(bool(self.cfg.get("start_with_windows")))
        if not startup.supported():
            self.start_with_windows.setEnabled(False)
            self.start_with_windows.setToolTip("Available in PathRenamer.exe (installed or portable).")
        gl3.addWidget(self.start_with_windows)
        gl3.addWidget(_note_label("Both keep the watch folder (Tools → Watch Folder) running in the "
                                  "background, so new downloads are handled even with the window closed."))
        L.addWidget(grp3)
        L.addStretch()
        return w

    # ── Automation, Subtitles, Pro ──────────────────────────────────
    def _pro_checkbox(self, text, key):
        """A switch for a Pro feature. Turning one on starts the free trial;
        after the trial they are locked until Pro is activated."""
        box = QCheckBox(f"{text}  (Pro)")
        allowed = self.license.can_try()
        box.setChecked(bool(self.cfg.get(key)) and allowed)
        if not allowed:
            box.setEnabled(False)
            box.setToolTip("Part of PathRenamer Pro. Your trial has ended: see Settings → Pro.")
        elif self.license.enabled and not self.license.is_pro():
            box.setToolTip("Part of PathRenamer Pro. Turning it on starts a free 14-day trial.")
        return box

    def _build_automation_tab(self, theme):
        w = QWidget()
        L = QVBoxLayout(w)
        L.setContentsMargins(10, 10, 10, 10)

        grp = QGroupBox("Watch Folder")
        gl = QFormLayout(grp)
        self.watch_enabled = QCheckBox("Watch a folder for new downloads while PathRenamer is running")
        self.watch_enabled.setChecked(bool(self.cfg.get("watch_enabled")))
        gl.addRow(self.watch_enabled)
        self.watch_folder = QLineEdit(self.cfg.get("watch_folder", ""))
        self.watch_folder.setPlaceholderText("e.g., D:/Downloads")
        btn = QPushButton("Browse")
        btn.clicked.connect(lambda: self._browse_folder(self.watch_folder))
        gl.addRow("Folder:", self._make_browse_row(self.watch_folder, btn))
        self.watch_type = QComboBox()
        for key, label in (("auto", "TV or movie, detected from the name"), ("tv", "TV Shows"),
                           ("movie", "Movies"), ("anime", "Anime")):
            self.watch_type.addItem(label, key)
        self.watch_type.setCurrentIndex(max(0, self.watch_type.findData(self.cfg.get("watch_media_type", "auto"))))
        gl.addRow("New files are:", self.watch_type)
        self.watch_auto = self._pro_checkbox("Rename the files PathRenamer is sure about automatically",
                                             "watch_auto_rename")
        gl.addRow(self.watch_auto)
        self.watch_existing = QCheckBox("Also handle the files already in the folder when watching starts")
        self.watch_existing.setChecked(bool(self.cfg.get("watch_include_existing")))
        gl.addRow(self.watch_existing)
        gl.addRow("", _note_label(
            "New files are matched once they finish downloading, with your formats and library "
            "folders. Anything PathRenamer isn't sure about is put in the preview for you to check."))
        L.addWidget(grp)

        grp2 = QGroupBox("Media Server")
        g2 = QFormLayout(grp2)
        self.server_kind = QComboBox()
        for key, label in SERVER_KINDS:
            self.server_kind.addItem(label, key)
        self.server_kind.setCurrentIndex(max(0, self.server_kind.findData(self.cfg.get("media_server", "plex"))))
        g2.addRow("Server:", self.server_kind)
        self.server_pages = QStackedWidget()

        plex_page = QWidget()
        pf = QFormLayout(plex_page)
        pf.setContentsMargins(0, 0, 0, 0)
        self.plex_url = QLineEdit(self.cfg.get("plex_url", ""))
        self.plex_url.setPlaceholderText("e.g., http://localhost:32400")
        pf.addRow("Address:", self.plex_url)
        self.plex_token = QLineEdit(unprotect(self.cfg.get("plex_token", "")))
        self.plex_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.plex_token.setPlaceholderText("Filled in when you sign in")
        sign_in = QPushButton("Sign in with Plex…")
        sign_in.clicked.connect(self._plex_sign_in)
        pf.addRow("Token:", self._make_browse_row(self.plex_token, sign_in))
        self.server_pages.addWidget(plex_page)

        self.server_fields = {}
        for kind in ("jellyfin", "emby"):
            page = QWidget()
            f = QFormLayout(page)
            f.setContentsMargins(0, 0, 0, 0)
            url = QLineEdit(self.cfg.get(f"{kind}_url", ""))
            url.setPlaceholderText("e.g., http://localhost:8096")
            key = QLineEdit(unprotect(self.cfg.get(f"{kind}_key", "")))
            key.setEchoMode(QLineEdit.EchoMode.Password)
            key.setPlaceholderText("Dashboard → API Keys → +")
            f.addRow("Address:", url)
            f.addRow("API key:", key)
            self.server_fields[kind] = (url, key)
            self.server_pages.addWidget(page)

        kodi_page = QWidget()
        kf = QFormLayout(kodi_page)
        kf.setContentsMargins(0, 0, 0, 0)
        self.kodi_url = QLineEdit(self.cfg.get("kodi_url", ""))
        self.kodi_url.setPlaceholderText("e.g., http://192.168.1.20:8080")
        self.kodi_user = QLineEdit(self.cfg.get("kodi_user", ""))
        self.kodi_pass = QLineEdit(unprotect(self.cfg.get("kodi_password", "")))
        self.kodi_pass.setEchoMode(QLineEdit.EchoMode.Password)
        kf.addRow("Address:", self.kodi_url)
        kf.addRow("Username:", self.kodi_user)
        kf.addRow("Password:", self.kodi_pass)
        kf.addRow("", _note_label("In Kodi, turn on Settings → Services → Control → "
                                  "Allow remote control via HTTP."))
        self.server_pages.addWidget(kodi_page)
        self.server_kind.currentIndexChanged.connect(self._show_server_page)
        self._show_server_page(self.server_kind.currentIndex())
        g2.addRow(self.server_pages)

        test_row = QHBoxLayout()
        self.plex_test_btn = QPushButton("Test Connection")
        self.plex_test_btn.clicked.connect(self._plex_test)
        self.plex_status = QLabel("")
        self.plex_status.setWordWrap(True)
        test_row.addWidget(self.plex_test_btn)
        test_row.addWidget(self.plex_status, 1)
        g2.addRow("", test_row)
        self.server_auto = self._pro_checkbox("Ask the media server to scan right after renaming", "plex_auto_scan")
        g2.addRow(self.server_auto)
        g2.addRow("", _note_label("Only the folders that changed are scanned, so new episodes show up in "
                                  "seconds, also in apps like Plezy. Tools → Scan Media Server Now works "
                                  "without Pro."))
        L.addWidget(grp2)
        L.addStretch()
        return w

    def _show_server_page(self, index):
        """Only the chosen server's fields take room (no gap left by the others)."""
        self.server_pages.setCurrentIndex(index)
        for i in range(self.server_pages.count()):
            policy = QSizePolicy.Policy.Preferred if i == index else QSizePolicy.Policy.Ignored
            self.server_pages.widget(i).setSizePolicy(policy, policy)
        self.server_pages.adjustSize()

    def _server_from_fields(self):
        """A media server client from what is typed on the page (for Test)."""
        kind = self.server_kind.currentData()
        if kind in self.server_fields:
            url, key = self.server_fields[kind]
            return JellyfinClient(url.text(), key.text(), emby=kind == "emby")
        if kind == "kodi":
            return KodiClient(self.kodi_url.text(), self.kodi_user.text(), self.kodi_pass.text())
        return PlexClient(self.plex_url.text(), self.plex_token.text())

    def _plex_sign_in(self):
        if not self.cfg.get("plex_client_id"):
            self.cfg["plex_client_id"] = uuid.uuid4().hex
        dlg = PlexSignInDialog(self.cfg["plex_client_id"], self)
        if dlg.exec() and dlg.server:
            if dlg.server.get("url"):
                self.plex_url.setText(dlg.server["url"])
            self.plex_token.setText(dlg.server["token"])
            name = dlg.server.get("name")
            self.plex_status.setText(f"Signed in to {name}." if name else
                                     "Signed in. Type your server's address above.")

    def _plex_test(self):
        client = self._server_from_fields()
        self.plex_test_btn.setEnabled(False)
        self.plex_status.setText("Connecting…")

        def work():
            return client.server_name(), [lib["title"] for lib in client.libraries()
                                          if lib["type"] in VIDEO_LIBRARY_TYPES]

        def done(result, error):
            self.plex_test_btn.setEnabled(True)
            if error is not None:
                self.plex_status.setText(str(error))
                return
            name, libraries = result
            self.plex_status.setText(f"Connected to {name}: " + (", ".join(libraries) or
                                                                  "no movie or TV libraries yet") + ".")
        self.jobs.run(work, done)

    def _build_subtitles_tab(self, theme):
        w = QWidget()
        L = QVBoxLayout(w)
        L.setContentsMargins(10, 10, 10, 10)

        grp = QGroupBox("Subtitles from OpenSubtitles.com")
        gl = QFormLayout(grp)
        self.subs_languages = QLineEdit(self.cfg.get("subs_languages", "English"))
        self.subs_languages.setPlaceholderText("e.g., English, Spanish")
        gl.addRow("Languages:", self.subs_languages)
        self.subs_auto = self._pro_checkbox("Download missing subtitles after renaming", "subs_auto")
        gl.addRow(self.subs_auto)
        self.subs_hi = QCheckBox("Prefer subtitles for the deaf and hard of hearing (SDH)")
        self.subs_hi.setChecked(bool(self.cfg.get("subs_hearing_impaired")))
        gl.addRow(self.subs_hi)
        gl.addRow("", _note_label("Subtitles made for your exact file are picked first. Without Pro: select "
                                  "files in the preview and choose Tools → Download Subtitles."))
        L.addWidget(grp)

        grp2 = QGroupBox("OpenSubtitles Account (optional)")
        g2 = QFormLayout(grp2)
        self.os_user = QLineEdit(self.cfg.get("opensubtitles_username", ""))
        g2.addRow("Username:", self.os_user)
        self.os_pass = QLineEdit(unprotect(self.cfg.get("opensubtitles_password", "")))
        self.os_pass.setEchoMode(QLineEdit.EchoMode.Password)
        signup = QPushButton("Create a Free Account")
        signup.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl("https://www.opensubtitles.com/en/users/sign_up")))
        g2.addRow("Password:", self._make_browse_row(self.os_pass, signup))
        self.os_key = QLineEdit(self.cfg.get("opensubtitles_api_key", ""))
        self.os_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.os_key.setPlaceholderText("Built in" if OPENSUBTITLES_APP_KEY else
                                       "Needed: get one free at opensubtitles.com/consumers")
        g2.addRow("API key:", self.os_key)
        g2.addRow("", _note_label("Signing in raises your daily download limit. The password is stored "
                                  "encrypted for your Windows account."))
        L.addWidget(grp2)
        L.addStretch()
        return w

    def _build_pro_tab(self, theme):
        w = QWidget()
        L = QVBoxLayout(w)
        L.setContentsMargins(10, 10, 10, 10)
        grp = QGroupBox("PathRenamer Pro")
        gl = QVBoxLayout(grp)
        self.pro_status = QLabel(self.license.summary())
        self.pro_status.setWordWrap(True)
        self.pro_status.setStyleSheet("font-weight:600")
        gl.addWidget(self.pro_status)
        gl.addWidget(_note_label("Renaming by hand stays free, forever. Pro adds what PathRenamer does on "
                                 "its own:\n• " + "\n• ".join(PRO_FEATURES.values())))

        self.pro_key_box = QWidget()
        kl = QVBoxLayout(self.pro_key_box)
        kl.setContentsMargins(0, 6, 0, 0)
        row = QHBoxLayout()
        self.license_key = QLineEdit(self.license.key)
        self.license_key.setPlaceholderText("Paste your license key")
        self.btn_activate = QPushButton("Activate")
        self.btn_activate.clicked.connect(self._activate_license)
        row.addWidget(self.license_key, 1)
        row.addWidget(self.btn_activate)
        kl.addLayout(row)
        row2 = QHBoxLayout()
        buy = QPushButton("Get Pro")
        buy.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(BUY_URL)))
        manage = QPushButton("Manage My License")
        manage.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(MANAGE_URL)))
        self.btn_check = QPushButton("Check Now")
        self.btn_check.clicked.connect(self._check_license_now)
        self.btn_deactivate = QPushButton("Remove From This PC")
        self.btn_deactivate.setToolTip("Frees this computer's seat, to use your key on another computer.")
        self.btn_deactivate.clicked.connect(self._deactivate_license)
        for b in (buy, manage, self.btn_check, self.btn_deactivate):
            row2.addWidget(b)
        row2.addStretch()
        kl.addLayout(row2)
        gl.addWidget(self.pro_key_box)
        self.pro_key_box.setVisible(self.license.enabled)
        L.addWidget(grp)
        L.addStretch()
        self._update_pro_buttons()
        return w

    def _update_pro_buttons(self):
        has_key = bool(self.license.key)
        self.btn_check.setEnabled(has_key)
        self.btn_deactivate.setEnabled(has_key)
        self.pro_status.setText(self.license.summary())
        allowed = self.license.can_try()
        for box in (getattr(self, "watch_auto", None), getattr(self, "server_auto", None),
                    getattr(self, "subs_auto", None)):
            if box is not None:
                box.setEnabled(allowed)

    def _license_job(self, work, busy_text):
        """Run a License call on a copy of the settings in the background;
        on success its results are kept and saved right away (a seat on the
        license server was used or freed)."""
        lic = License(dict(self.cfg))
        for b in (self.btn_activate, self.btn_check, self.btn_deactivate):
            b.setEnabled(False)
        self.pro_status.setText(busy_text)

        def done(result, error):
            self.btn_activate.setEnabled(True)
            if error is None:
                for k in LICENSE_KEYS:
                    if k in lic.cfg:
                        self.cfg[k] = lic.cfg[k]
                    else:
                        self.cfg.pop(k, None)
                disk = cfg_load()
                disk.update({k: self.cfg[k] for k in LICENSE_KEYS if k in self.cfg})
                for k in LICENSE_KEYS:
                    if k not in self.cfg:
                        disk.pop(k, None)
                cfg_save(disk)
            self._update_pro_buttons()
            if error is not None:
                self.pro_status.setText(str(error))
        self.jobs.run(lambda: work(lic), done)

    def _activate_license(self):
        key = self.license_key.text()
        self._license_job(lambda lic: lic.activate(key), "Checking your key…")

    def _check_license_now(self):
        self._license_job(lambda lic: lic.check(), "Checking your license…")

    def _deactivate_license(self):
        self.license_key.clear()
        self._license_job(lambda lic: lic.deactivate(), "Removing the license from this PC…")

    def _build_api_tab(self, theme):
        w = QWidget()
        L = QVBoxLayout(w)
        L.setContentsMargins(10, 10, 10, 10)

        grp = QGroupBox("Metadata Sources")
        gl = QFormLayout(grp)

        self.use_tvmaze = QCheckBox("Use TVMaze for TV show metadata & posters")
        self.use_tvmaze.setChecked(self.cfg.get("use_tvmaze", True))
        gl.addRow(self.use_tvmaze)

        self.use_anidb = QCheckBox("Use AniDB for anime metadata")
        self.use_anidb.setChecked(self.cfg.get("use_anidb", True))
        gl.addRow(self.use_anidb)

        self.use_cinemeta = QCheckBox("Use Cinemeta for movie titles && years (no key)")
        self.use_cinemeta.setChecked(self.cfg.get("use_cinemeta", True))
        gl.addRow(self.use_cinemeta)

        note = QLabel("TVMaze (TV), AniDB (anime) and Cinemeta (movies) are free — no key needed.")
        note.setStyleSheet("color:#888;font-size:11px")
        gl.addRow("", note)

        L.addWidget(grp)

        grp2 = QGroupBox("TMDB (Movies)")
        gl2 = QFormLayout(grp2)

        self.tmdb_key = QLineEdit(self.cfg.get("tmdb_api_key", ""))
        self.tmdb_key.setPlaceholderText("Paste your free TMDB v3 API key here")
        self.tmdb_key.setEchoMode(QLineEdit.EchoMode.Password)
        key_row = QHBoxLayout()
        key_row.addWidget(self.tmdb_key, 1)
        get_key = QPushButton("Get Free Key")
        get_key.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl("https://www.themoviedb.org/settings/api")))
        key_row.addWidget(get_key)
        gl2.addRow("API Key:", key_row)

        note2 = QLabel("Optional. With a key, movies are matched against TMDB for correct "
                       "titles and years; without one, movie names "
                       "come from the filename only.")
        note2.setWordWrap(True)
        note2.setStyleSheet("color:#888;font-size:11px")
        gl2.addRow("", note2)

        L.addWidget(grp2)
        L.addStretch()
        return w

    def _build_about_tab(self, theme):
        w = QWidget()
        L = QVBoxLayout(w)
        L.setContentsMargins(10, 10, 10, 10)

        grp = QGroupBox("Support PathRenamer")
        gl = QVBoxLayout(grp)

        kofi_btn = QPushButton("☕ Buy me a coffee on Ko-fi")
        kofi_btn.setStyleSheet("font-size: 14px; padding: 10px; background: #ff7744; color: white; font-weight: bold; border: none;")
        kofi_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://ko-fi.com/pathrenamer")))
        gl.addWidget(kofi_btn)

        desc = QLabel(
            "Renaming with PathRenamer is free. "
            "A lot of time went into this - if you find it useful, consider PathRenamer Pro or a coffee!"
        )
        desc.setStyleSheet("color: #888; font-size: 11px; margin-top: 8px;")
        desc.setWordWrap(True)
        gl.addWidget(desc)

        site = QPushButton("PathRenamer Website and Guides")
        site.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(WEBSITE_URL)))
        gl.addWidget(site)

        credits = QLabel(
            "Show and episode data from TVmaze (CC BY-SA 4.0), movie data from Cinemeta, anime data "
            "from AniDB, subtitles from OpenSubtitles.com. With your own key, movie data from TMDB: "
            "this product uses the TMDB API but is not endorsed or certified by TMDB.")
        credits.setWordWrap(True)
        credits.setStyleSheet("color: #888; font-size: 11px; margin-top: 8px;")
        gl.addWidget(credits)

        license_note = QLabel('PathRenamer is free software under the GNU General Public License v3.0 '
                              '(<a href="https://github.com/Royalkiller2222/PathRenamer/blob/main/LICENSE">license</a>, '
                              '<a href="https://github.com/Royalkiller2222/PathRenamer">source code</a>).')
        license_note.setWordWrap(True)
        license_note.setOpenExternalLinks(True)
        license_note.setStyleSheet("color: #888; font-size: 11px; margin-top: 8px;")
        gl.addWidget(license_note)

        L.addWidget(grp)

        grp_up = QGroupBox("Updates")
        ul = QVBoxLayout(grp_up)
        row = QHBoxLayout()
        version = QLabel(f"You have PathRenamer {APP_VERSION}.")
        row.addWidget(version, 1)
        check_now = getattr(self.parent(), "check_for_updates", None)
        self.btn_check_updates = QPushButton("Check for Updates")
        self.btn_check_updates.setVisible(check_now is not None)
        if check_now is not None:
            self.btn_check_updates.clicked.connect(check_now)
        row.addWidget(self.btn_check_updates)
        ul.addLayout(row)
        self.check_updates_chk = QCheckBox("Check for updates automatically (once a day)")
        self.check_updates_chk.setChecked(self.cfg.get("check_updates", True))
        ul.addWidget(self.check_updates_chk)
        ul.addWidget(_note_label("When a new version is out, “Update to …” appears at the bottom right of the "
                                 "window: one click downloads it from GitHub, checks it and installs it."))
        L.addWidget(grp_up)
        L.addStretch()
        return w

    def _save(self):
        ep_map = {0: "dvd", 1: "absolute", 2: "streaming"}

        self.cfg.update({
            "root_tv": self.root_tv.text().strip(),
            "root_movie": self.root_movie.text().strip(),
            "root_anime": self.root_anime.text().strip(),

            "format_tv": self.fmt_tv.text().strip(),
            "format_movie": self.fmt_movie.text().strip(),
            "format_anime": self.fmt_anime.text().strip(),
            "episode_tv": ep_map[self.episode_tv.currentIndex()],
            "episode_anime": ep_map[self.episode_anime.currentIndex()],
            "theme": self.theme_combo.currentData(),
            "layout": LAYOUTS[self.layout_combo.currentIndex()],
            "panel_mode": PANEL_MODES[self.panel_combo.currentIndex()],
            "density": DENSITIES[self.density_combo.currentIndex()],
            "show_details": self.show_details_chk.isChecked(),
            "animations": self.animations_chk.isChecked(),
            "show_full_path": self.show_new_path.isChecked(),
            "show_detected": self.show_detected.isChecked(),
            "rename_subtitles": self.rename_subs.isChecked(),
            "read_media_info": self.read_media_info.isChecked(),
            "rename_action": self.action_combo.currentData(),
            "cleanup_empty_folders": self.cleanup_folders.isChecked(),
            "confirm_rename": self.confirm_rename.isChecked(),
            "confirm_conflicts": self.confirm_conflicts.isChecked(),
            "remember_folders": self.remember_folders.isChecked(),
            "auto_detect_type": self.auto_detect_type.isChecked(),
            "use_tvmaze": self.use_tvmaze.isChecked(),
            "use_anidb": self.use_anidb.isChecked(),
            "use_cinemeta": self.use_cinemeta.isChecked(),
            "tmdb_api_key": self.tmdb_key.text().strip(),
            "watch_enabled": self.watch_enabled.isChecked(),
            "watch_folder": self.watch_folder.text().strip(),
            "watch_media_type": self.watch_type.currentData(),
            "watch_auto_rename": self.watch_auto.isChecked(),
            "watch_include_existing": self.watch_existing.isChecked(),
            "media_server": self.server_kind.currentData(),
            "plex_url": self.plex_url.text().strip(),
            "plex_token": protect(self.plex_token.text().strip()),
            "jellyfin_url": self.server_fields["jellyfin"][0].text().strip(),
            "jellyfin_key": protect(self.server_fields["jellyfin"][1].text().strip()),
            "emby_url": self.server_fields["emby"][0].text().strip(),
            "emby_key": protect(self.server_fields["emby"][1].text().strip()),
            "kodi_url": self.kodi_url.text().strip(),
            "kodi_user": self.kodi_user.text().strip(),
            "kodi_password": protect(self.kodi_pass.text()),
            "plex_auto_scan": self.server_auto.isChecked(),
            "subs_languages": ", ".join(self._language_names()) or "English",
            "subs_auto": self.subs_auto.isChecked(),
            "subs_hearing_impaired": self.subs_hi.isChecked(),
            "opensubtitles_api_key": self.os_key.text().strip(),
            "opensubtitles_username": self.os_user.text().strip(),
            "opensubtitles_password": protect(self.os_pass.text()),
            "check_updates": self.check_updates_chk.isChecked(),
            "tray_on_close": self.tray_on_close.isChecked(),
            "start_with_windows": self.start_with_windows.isChecked(),
            "first_run": False,
        })
        turned_on = [k for k in self.PRO_SWITCHES if self.cfg.get(k) and not self._initial.get(k)]
        trial = bool(turned_on) and self.license.start_trial()
        cfg_save(self.cfg)
        if trial:
            QMessageBox.information(self, "PathRenamer Pro",
                                    "Your free 14-day Pro trial has started. Enjoy the automation!\n\n"
                                    "Renaming by hand stays free after the trial.")
        self.accept()

    def _language_names(self):
        """The typed subtitle languages, cleaned up ("en, Spanish" -> English, Spanish)."""
        names = {"eng": "English", "spa": "Spanish", "fre": "French", "ger": "German", "ita": "Italian",
                 "por": "Portuguese", "dut": "Dutch", "jpn": "Japanese", "kor": "Korean", "chi": "Chinese"}
        return [names.get(code, code) for code in parse_languages(self.subs_languages.text())]

# ═══════════════════════════════════════════════════════════════════
#  RENAME TABLE
# ═══════════════════════════════════════════════════════════════════
# Row colours per status: (background, foreground)
def row_colors(pal):
    """Preview row colours per status, (background, text), from a theme palette."""
    return {k: (QColor(pal[f"row_{k}_bg"]), QColor(pal[f"row_{k}_fg"]))
            for k in ("ok", "conflict", "low", "error")}

COLUMNS     = ["Original Filename","Detected Match","New Path","Subtitles","Status"]
STATUS_COL  = 4

class _WrapDelegate(QStyledItemDelegate):
    """Rows tall enough for every wrapped line plus the cell padding, so the
    last line is never cut to "…"."""
    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        width = self.parent().columnWidth(index.column()) - 34      # minus padding and margins
        text = index.data() or ""
        if width > 20 and text:
            fm = option.fontMetrics
            # Qt breaks at spaces and, for long words such as paths, anywhere:
            # measure both ways and keep the taller
            needed = max(fm.boundingRect(0, 0, width, 100000, int(flags), text).height()
                         for flags in (Qt.TextFlag.TextWordWrap,
                                       Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextWrapAnywhere))
            size.setHeight(max(size.height(), needed + 12))
        return size


class RenameTable(QTableWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cands = []
        self._dest_root = ""
        self._show_detected = True
        self._show_full_path = True
        self._modern = False
        self._row_colors = row_colors(THEMES["dark"])
        self.setColumnCount(len(COLUMNS))
        self.setHorizontalHeaderLabels(COLUMNS)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.verticalHeader().setVisible(False)
        hh = self.horizontalHeader()
        self._user_sized = False
        self._wrap = _WrapDelegate(self)
        self.viewport().installEventFilter(self)
        hh.sectionResized.connect(self._on_section_resized)
        self.set_modern(False)

        self.cellClicked.connect(self._on_cell_clicked)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._build_context_menu)

    def set_display_options(self, show_detected=True, show_full_path=True):
        self._show_detected = show_detected
        self._show_full_path = show_full_path
        self.setColumnHidden(1, not show_detected)
        if not getattr(self, "_user_sized", False):
            self._fit_columns()

    # Starting share of the width for each column; every column can be
    # resized by dragging its header edge, and text wraps to fit.
    COLUMN_SHARES = (0.28, 0.17, 0.33, 0.11, 0.11)

    def set_modern(self, modern: bool, density: str = "comfortable"):
        """Modern layout: smooth scrolling, no grid, subtitle counts instead of
        names. In both layouts all columns are resizable and long text wraps
        onto more lines instead of being cut off."""
        self._modern = modern
        hh, vh = self.horizontalHeader(), self.verticalHeader()
        self.setWordWrap(True)
        self.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.setItemDelegate(self._wrap)
        for col in range(len(COLUMNS)):
            hh.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        hh.setStretchLastSection(False)
        hh.setMinimumSectionSize(60)
        hh.setDefaultSectionSize(80)          # also the stretched last column's minimum
        vh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        if modern:
            self.setShowGrid(False)
            self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
            self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
            vh.setMinimumSectionSize(24 if density == "compact" else 32)
            hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.horizontalHeaderItem(STATUS_COL).setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        else:
            self.setShowGrid(True)
            self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerItem)
            self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerItem)
            vh.setMinimumSectionSize(30)
            hh.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self._user_sized = False
        self._fit_columns()
        if self._cands:
            self.populate(self._cands, self._dest_root)

    def _fit_columns(self):
        """Share the width between the columns until the user resizes one."""
        width = self.viewport().width()
        visible = [c for c in range(len(COLUMNS)) if not self.isColumnHidden(c)]
        if width < 100 or not visible:
            return
        if self.verticalScrollBar().isVisible():
            width = self.viewport().width()
        width -= 2
        total = sum(self.COLUMN_SHARES[c] for c in visible)
        last = visible[-1]                  # the last one stretches to fill what's left
        last_w = max(int(width * self.COLUMN_SHARES[last] / total),
                     self.horizontalHeader().sectionSizeHint(last),
                     self.horizontalHeader().defaultSectionSize())
        rest = sum(self.COLUMN_SHARES[c] for c in visible[:-1]) or 1
        self._fitting = True
        try:
            used = 0
            for c in visible[:-1]:
                w = max(60, int((width - last_w) * self.COLUMN_SHARES[c] / rest))
                self.setColumnWidth(c, w)
                used += w
            self.setColumnWidth(last, max(60, width - used))
        finally:
            self._fitting = False

    def _on_section_resized(self, *_):
        # Only a drag on a header edge counts as the user choosing widths
        if not getattr(self, "_fitting", False) and QApplication.mouseButtons() != Qt.MouseButton.NoButton:
            self._user_sized = True
        self.resizeRowsToContents()

    def resizeEvent(self, event):
        self._fitting = True
        try:
            super().resizeEvent(event)
        finally:
            self._fitting = False
        if not getattr(self, "_user_sized", False):
            self._fit_columns()

    def eventFilter(self, obj, event):
        # The visible area also shrinks when a scroll bar appears: refit then too
        if obj is self.viewport() and event.type() == QEvent.Type.Resize \
                and not getattr(self, "_user_sized", False) and not getattr(self, "_fitting", False):
            QTimer.singleShot(0, self._fit_columns)
        return super().eventFilter(obj, event)

    def set_palette(self, pal):
        self._row_colors = row_colors(pal)
        if self._cands:
            self.populate(self._cands, self._dest_root)

    def _fill_row(self, row, c):
        if self._show_full_path and self._dest_root:
            new_path = str(Path(self._dest_root) / c.dest_rel)
        else:
            new_path = c.dest_rel
        detected = Path(c.dest_rel).stem
        if c.error:
            bg, fg, status = *self._row_colors["error"], f"⚠ Error: {c.error[:50]}"
        elif c.conflict:
            bg, fg, status = *self._row_colors["conflict"], "✖ Conflict"
        elif c.confidence >= 0.85: bg, fg, status = *self._row_colors["ok"], "✔ Ready"
        else:
            bg, fg, status = *self._row_colors["low"], f"? Low ({int(c.confidence*100)}%)"

        sub_count = len(c.subtitles) if hasattr(c, "subtitles") and c.subtitles else 0
        if self._modern:
            sub_text = f"{sub_count} subtitle{'s' if sub_count != 1 else ''}" if sub_count else "—"
        elif sub_count > 0:
            sub_names = ", ".join(s.name for s, _ in c.subtitles[:5])
            if sub_count > 5:
                sub_text = f"{sub_names} +{sub_count-5} more"
            else:
                sub_text = sub_names
        else:
            sub_text = "None"
        # Hovering the Subtitles cell shows where each subtitle goes
        sub_tip = "\n".join(f"{s.name}  →  {Path(d).name}" for s, d in c.subtitles) if sub_count else ""

        texts = [c.source.name, detected, new_path, sub_text, status]
        tips = [str(c.source), "", new_path, sub_tip, c.error]
        for col, text in enumerate(texts):
            item = QTableWidgetItem(text)
            item.setBackground(bg); item.setForeground(fg)
            if tips[col]:
                item.setToolTip(tips[col])
            if col == STATUS_COL: item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.setItem(row, col, item)
        self.resizeRowToContents(row)

    def populate(self, cands, dest_root):
        self._cands = cands
        self._dest_root = dest_root
        self.setRowCount(len(cands))
        for row, c in enumerate(cands):
            self._fill_row(row, c)

    def append_candidate(self, c, dest_root):
        self._dest_root = dest_root
        if c not in self._cands:
            self._cands.append(c)
        row = self.rowCount()
        self.setRowCount(row + 1)
        self._fill_row(row, c)

    def replace_candidate(self, row, c, dest_root):
        self._dest_root = dest_root
        self._cands[row] = c
        self._fill_row(row, c)

    def refresh_rows(self):
        """Redraw every row (after duplicates / conflicts were re-checked)."""
        rows = self.selected_rows()
        self.populate(self._cands, self._dest_root)
        self._select_rows(rows)

    def move_rows(self, rows, delta):
        """Move the given rows up (-1) or down (+1) together; returns their new rows."""
        rows = sorted(set(rows))
        if not rows or rows[0] + delta < 0 or rows[-1] + delta >= len(self._cands):
            return rows
        moving = [self._cands[r] for r in rows]
        rest = [c for i, c in enumerate(self._cands) if i not in set(rows)]
        at = rows[0] + delta
        self._cands = rest[:at] + moving + rest[at:]
        self.populate(self._cands, self._dest_root)
        new_rows = list(range(at, at + len(moving)))
        self._select_rows(new_rows)
        return new_rows

    def _select_rows(self, rows):
        self.clearSelection()
        mode = self.selectionMode()
        self.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        for r in rows:
            if 0 <= r < self.rowCount():
                self.selectRow(r)
        self.setSelectionMode(mode)

    def clear_all(self): self._cands = []; self.setRowCount(0)
    def get_cands(self): return self._cands
    def conflict_count(self): return sum(1 for c in self._cands if c.conflict)
    def error_count(self):    return sum(1 for c in self._cands if c.error)
    def selected_rows(self): return sorted(set(item.row() for item in self.selectedItems()), reverse=True)
    def selected_items(self): return list(self.selectedItems())
    def remove_selected(self):
        rows = self.selected_rows()
        for row in rows:
            self.removeRow(row)
        self._cands = [c for i, c in enumerate(self._cands) if i not in rows]

    def _on_cell_clicked(self, row, col):
        if col == STATUS_COL:
            item = self.item(row, STATUS_COL)
            if item and "Error:" in item.text():
                c = self._cands[row]
                dlg = QDialog()
                dlg.setWindowTitle("Error Details")
                dlg.setMinimumSize(500, 200)
                L = QVBoxLayout(dlg)
                text = QTextEdit(c.error if c.error else "Unknown error")
                text.setReadOnly(True)
                L.addWidget(text)
                btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
                btns.accepted.connect(dlg.accept)
                L.addWidget(btns)
                dlg.exec()

    def _build_context_menu(self, pos):
        menu = QMenu()
        copy_action = QAction("Copy", self)
        copy_action.triggered.connect(self._copy_selection)
        menu.addAction(copy_action)
        menu.exec(self.mapToGlobal(pos))

    def _copy_selection(self):
        selected = self.selectedItems()
        if selected:
            text = "\t".join(item.text() for item in selected)
            QApplication.clipboard().setText(text)

# ═══════════════════════════════════════════════════════════════════
#  BACKGROUND WORKER
# ═══════════════════════════════════════════════════════════════════
class ScanWorker(QObject):
    """Runs core.match_files in a thread and reports each match to the window."""
    progress       = pyqtSignal(int, int)
    candidate_ready = pyqtSignal(object)
    finished       = pyqtSignal()

    def __init__(self, files, engine, media_type, dest_root, selected_overrides=None,
                 episode_overrides=None):
        super().__init__()
        self.files = files; self.engine = engine
        self.media_type = media_type; self.dest_root = dest_root
        self.selected_overrides = selected_overrides or {}
        self.episode_overrides = episode_overrides or {}

    def run(self):
        total = len(self.files)
        done = 0
        for cand in match_files(self.files, self.engine, self.media_type, self.dest_root,
                                self.selected_overrides, self.episode_overrides):
            self.candidate_ready.emit(cand)
            done += 1
            self.progress.emit(min(done, total), total)
        self.progress.emit(total, total)
        self.finished.emit()

# ═══════════════════════════════════════════════════════════════════
#  MATCH SELECTOR DIALOG
# ═══════════════════════════════════════════════════════════════════
class MatchSelectorDialog(QDialog):
    def __init__(self, filename, matches, parent=None):
        super().__init__(parent)
        self.matches = matches
        self.selected_match = None
        self.setWindowTitle(f"Select Match - {filename}")
        self.setMinimumSize(900, 650)
        self._build_ui()

    def _build_ui(self):
        L = QVBoxLayout(self)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(550)

        grid = QWidget()
        gl = QGridLayout(grid)
        gl.setSpacing(20)

        for i, m in enumerate(self.matches):
            card = self._create_match_card(m)
            gl.addWidget(card, i // 2, i % 2)

        scroll.setWidget(grid)
        L.addWidget(scroll)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        btns.rejected.connect(self.reject)
        L.addWidget(btns)

    def _create_match_card(self, match):
        card = QFrame()
        card.setFrameStyle(QFrame.Shape.Box)
        card.setLineWidth(1)
        card.setMinimumHeight(280)
        card.setMinimumWidth(380)

        hl = QHBoxLayout(card)

        poster_frame = QFrame()
        poster_frame.setFixedWidth(120)
        poster_frame.setMinimumHeight(180)
        poster_layout = QVBoxLayout(poster_frame)
        poster_layout.setContentsMargins(0, 0, 0, 0)

        poster_url = match.get("poster", "")
        log.debug(f"Match card poster_url: '{poster_url}' for '{match.get('title', match.get('show', 'Unknown'))}'")
        if not poster_url:
            poster_label = QLabel("No Image")
            poster_label.setFixedSize(120, 180)
            poster_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            poster_label.setStyleSheet("color: #666; background: #2a2a3a; border: 1px solid #45475a;")
            poster_layout.addWidget(poster_label)
        else:
            poster_label = QLabel("Loading...")
            poster_label.setFixedSize(120, 180)
            poster_label.setScaledContents(False)
            poster_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            poster_label.setStyleSheet("color: #666; background: #2a2a3a; border: 1px solid #45475a;")
            poster_layout.addWidget(poster_label)

            if poster_url.startswith("http"):
                self._load_poster(poster_label, poster_url)
            elif poster_url.startswith("/"):
                full_url = "https://image.tmdb.org/t/p/w200" + poster_url
                self._load_poster(poster_label, full_url)
            else:
                poster_label.setText("No Image")
                poster_label.setStyleSheet("color: #666; background: #2a2a3a;")

        info_frame = QWidget()
        info_layout = QVBoxLayout(info_frame)

        title_text = match.get("title", match.get("show", "Unknown"))
        title = QLabel(f"<b>{title_text}</b>")
        title.setWordWrap(True)
        title.setStyleSheet("font-size: 14px;")
        info_layout.addWidget(title)

        year = match.get("year", "") or match.get("first_air_date", "") or match.get("release_date", "")
        if year and len(str(year)) == 4:
            info_layout.addWidget(QLabel(f"Year: {year}"))

        rating = match.get("rating", 0)
        if rating:
            info_layout.addWidget(QLabel(f"Rating: {rating:.1f}/10"))

        overview = match.get("overview", "")
        if overview:
            desc = QLabel(overview[:200] + "..." if len(overview) > 200 else overview)
            desc.setWordWrap(True)
            desc.setStyleSheet("color: #888; font-size: 11px;")
            info_layout.addWidget(desc)

        btn = QPushButton("Use This Match")
        btn.setMinimumHeight(30)
        btn.clicked.connect(lambda: self._select(match))
        info_layout.addWidget(btn)

        hl.addWidget(poster_frame)
        hl.addWidget(info_frame, 1)

        return card

    def _load_poster(self, label, url):
        try:
            import requests
            response = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            if response.status_code == 200:
                from PyQt6.QtGui import QPixmap
                pixmap = QPixmap()
                pixmap.loadFromData(response.content)
                if not pixmap.isNull() and pixmap.width() > 0:
                    label.setText("")
                    label.setPixmap(pixmap.scaled(120, 180, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                    label.setStyleSheet("background: transparent; border: 1px solid #45475a;")
                    log.info(f"Poster loaded successfully: {url}")
                else:
                    label.setText("No Image")
                    label.setStyleSheet("color: #666; background: #2a2a3a;")
                    log.warning("Poster image empty or invalid")
            else:
                label.setText("No Image")
                label.setStyleSheet("color: #666; background: #2a2a3a;")
                log.warning(f"Poster HTTP error: {response.status_code}")
        except Exception as e:
            log.warning(f"Poster fetch exception: {e}")
            label.setText("No Image")
            label.setStyleSheet("color: #666; background: #2a2a3a;")

    def _on_poster_timeout(self, reply, label):
        if reply.isRunning():
            reply.abort()
        label.setText("Timeout")
        label.setStyleSheet("color: #666; background: #2a2a3a;")
        log.warning("Poster request timed out")

    def _on_poster_loaded(self, reply, label):
        try:
            if reply.error() == 0:
                data = reply.readAll()
                from PyQt6.QtGui import QPixmap
                pixmap = QPixmap()
                pixmap.loadFromData(data)
                if not pixmap.isNull() and pixmap.width() > 0:
                    label.setText("")
                    label.setPixmap(pixmap.scaled(120, 180, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                    label.setStyleSheet("background: transparent; border: 1px solid #45475a;")
                    log.info("Poster loaded successfully")
                else:
                    label.setText("No Image")
                    label.setStyleSheet("color: #666; background: #2a2a3a;")
                    log.warning("Poster image empty or invalid")
            else:
                label.setText("No Image")
                label.setStyleSheet("color: #666; background: #2a2a3a;")
                log.warning(f"Poster HTTP error: {reply.error()} - {reply.errorString()}")
            reply.deleteLater()
        except Exception as e:
            label.setText("No Image")
            label.setStyleSheet("color: #666; background: #2a2a3a;")
            log.warning(f"Poster load exception: {e}")

    def _select(self, match):
        self.selected_match = match
        self.accept()

# ═══════════════════════════════════════════════════════════════════
#  CUSTOM THEME EDITOR
# ═══════════════════════════════════════════════════════════════════
class CustomThemeDialog(QDialog):
    """Every colour of the Custom theme, grouped, with a live preview in the
    main window. Starts from a built-in theme; themes can be exported and
    imported as small JSON files."""

    def __init__(self, cfg, parent=None, preview=None):
        super().__init__(parent)
        self.setWindowTitle("Custom Theme")
        self.setMinimumSize(600, 640)
        self._preview = preview
        custom = cfg.get("custom_theme")
        if isinstance(custom, dict) and custom.get("colors"):
            self._base = custom.get("base") if custom.get("base") in THEMES else "dark"
            self._pal = custom_palette(custom)
        else:            # first time: start from the theme in use
            current = cfg.get("theme", "dark")
            self._base = current if current in THEMES else "dark"
            self._pal = dict(THEMES[self._base])
        self._rows = {}
        self._labels = {role: label for role, label, _ in ROLES}

        L = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Start from:"))
        self.base_combo = QComboBox()
        for key, label in THEME_NAMES:
            if key in THEMES:
                self.base_combo.addItem(label, key)
        self.base_combo.setCurrentIndex(max(0, self.base_combo.findData(self._base)))
        top.addWidget(self.base_combo)
        btn_load = QPushButton("Use Its Colors")
        btn_load.setToolTip("Replace every color below with the chosen theme's colors")
        btn_load.clicked.connect(self._load_base)
        top.addWidget(btn_load)
        top.addStretch()
        btn_import = QPushButton("Import…"); btn_import.clicked.connect(self._import)
        btn_export = QPushButton("Export…"); btn_export.clicked.connect(self._export)
        top.addWidget(btn_import); top.addWidget(btn_export)
        L.addLayout(top)

        hint = QLabel("Click a color square to pick a color, or type a hex code like #1e1e24. "
                      "The window shows your changes as you make them.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888;font-size:11px")
        L.addWidget(hint)

        body = QWidget()
        bl = QVBoxLayout(body); bl.setContentsMargins(0, 0, 6, 0)
        groups = []
        for role, label, group in ROLES:
            if not groups or groups[-1][0] != group:
                groups.append((group, []))
            groups[-1][1].append((role, label))
        for group, roles in groups:
            box = QGroupBox(group)
            form = QFormLayout(box)
            for role, label in roles:
                swatch = QPushButton()
                swatch.setFixedSize(40, 24)
                swatch.setCursor(Qt.CursorShape.PointingHandCursor)
                swatch.setToolTip(f"Pick the color for “{label}”")
                swatch.clicked.connect(lambda _=False, r=role: self._pick(r))
                edit = QLineEdit()
                edit.setFixedWidth(96)
                edit.setMaxLength(7)
                edit.editingFinished.connect(lambda r=role: self._typed(r))
                row = QHBoxLayout()
                row.addWidget(swatch); row.addWidget(edit); row.addStretch()
                form.addRow(label + ":", row)
                self._rows[role] = (swatch, edit)
            bl.addWidget(box)
        bl.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(body)
        L.addWidget(scroll, 1)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept); btns.rejected.connect(self.reject)
        L.addWidget(btns)
        for role in self._rows:
            self._show(role)
        self._apply_preview()

    def result_theme(self):
        """What the settings keep: {"base": theme, "colors": every role}."""
        return {"base": self._base, "colors": dict(self._pal)}

    def palette(self):
        return dict(self._pal)

    def set_color(self, role, value):
        if role in self._pal and is_color(value):
            self._pal[role] = value.lower()
            self._show(role)
            self._apply_preview()
            return True
        return False

    def _show(self, role):
        swatch, edit = self._rows[role]
        color = self._pal[role]
        swatch.setStyleSheet(f"QPushButton{{background:{color};border:1px solid #808080;border-radius:4px;padding:0}}")
        edit.setText(color)

    def _apply_preview(self):
        if self._preview:
            try:
                self._preview(dict(self._pal))
            except Exception as e:           # a preview must never break editing
                log.debug(f"Theme preview failed: {e}")

    def _pick(self, role):
        from PyQt6.QtWidgets import QColorDialog
        color = QColorDialog.getColor(QColor(self._pal[role]), self, self._labels[role])
        if color.isValid():
            self.set_color(role, color.name())

    def _typed(self, role):
        _, edit = self._rows[role]
        text = edit.text().strip()
        if text and not text.startswith("#"):
            text = "#" + text
        if not self.set_color(role, text):
            edit.setText(self._pal[role])          # not a colour: put the old one back

    def _load_base(self):
        self._base = self.base_combo.currentData()
        self._pal = dict(THEMES[self._base])
        for role in self._rows:
            self._show(role)
        self._apply_preview()

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export Theme", "PathRenamer theme.json",
                                              "Theme files (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"pathrenamer_theme": 1, **self.result_theme()}, f, indent=2)
        except OSError as e:
            QMessageBox.warning(self, "Export Theme", f"Couldn't save the theme:\n{e}")

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import Theme", "", "Theme files (*.json)")
        if path:
            self.import_file(path)

    def import_file(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or not isinstance(data.get("colors"), dict):
                raise ValueError("not a PathRenamer theme file")
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Import Theme", f"Couldn't read the theme:\n{e}")
            return False
        self._base = data.get("base") if data.get("base") in THEMES else self._base
        self._pal = custom_palette({"base": self._base, "colors": data["colors"]})
        self.base_combo.setCurrentIndex(max(0, self.base_combo.findData(self._base)))
        for role in self._rows:
            self._show(role)
        self._apply_preview()
        return True


# ═══════════════════════════════════════════════════════════════════
#  RENAME HISTORY
# ═══════════════════════════════════════════════════════════════════
class HistoryDialog(QDialog):
    """Past renames (kept between sessions); any of them can be undone."""

    def __init__(self, renamer, parent=None):
        super().__init__(parent)
        self.renamer = renamer
        self.undo_results = []
        self.setWindowTitle("Rename History")
        self.setMinimumSize(820, 520)
        L = QVBoxLayout(self)
        hint = QLabel("Every rename is listed here, newest first, also after closing the app. "
                      "Select one to see its files; Undo puts them back.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888;font-size:11px")
        L.addWidget(hint)
        split = QSplitter(Qt.Orientation.Vertical)
        self.batch_list = QTableWidget(0, 3)
        self.batch_list.setHorizontalHeaderLabels(["When", "What", "Status"])
        self.batch_list.verticalHeader().setVisible(False)
        self.batch_list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.batch_list.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.batch_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        hh = self.batch_list.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.batch_list.itemSelectionChanged.connect(self._show_files)
        split.addWidget(self.batch_list)
        self.files = QTableWidget(0, 2)
        self.files.setHorizontalHeaderLabels(["From", "To"])
        self.files.verticalHeader().setVisible(False)
        self.files.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.files.setTextElideMode(Qt.TextElideMode.ElideLeft)
        for col in (0, 1):
            self.files.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        split.addWidget(self.files)
        L.addWidget(split, 1)
        row = QHBoxLayout()
        self.btn_clear = QPushButton("Clear History")
        self.btn_clear.setToolTip("Forget every entry (files are not touched)")
        self.btn_clear.clicked.connect(self._clear)
        row.addWidget(self.btn_clear)
        row.addStretch()
        self.btn_undo = QPushButton("↩  Undo This Rename")
        self.btn_undo.setObjectName("btn_undo")
        self.btn_undo.clicked.connect(self._undo_selected)
        row.addWidget(self.btn_undo)
        close = QPushButton("Close"); close.clicked.connect(self.accept)
        row.addWidget(close)
        L.addLayout(row)
        self._fill()

    def _fill(self):
        self._batches = self.renamer.history.batches()
        self.batch_list.setRowCount(len(self._batches))
        for i, b in enumerate(self._batches):
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(b.get("time", 0)))
            what = describe_batch(b) + ("  (watch folder)" if b.get("source") == "watch" else "")
            status = "Undone" if b.get("undone") else "Done"
            for col, text in enumerate((when, what, status)):
                self.batch_list.setItem(i, col, QTableWidgetItem(text))
        if self._batches:
            self.batch_list.selectRow(0)
        else:
            self.files.setRowCount(0)
        self.btn_clear.setEnabled(bool(self._batches))
        self._update_button()

    def _selected(self):
        rows = sorted({i.row() for i in self.batch_list.selectedItems()})
        return self._batches[rows[0]] if rows and rows[0] < len(self._batches) else None

    def _show_files(self):
        b = self._selected()
        ops = b["ops"] if b else []
        self.files.setRowCount(len(ops))
        for i, op in enumerate(ops):
            for col, text in enumerate((op["src"], op["dst"])):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.files.setItem(i, col, item)
        self._update_button()

    def _update_button(self):
        b = self._selected()
        self.btn_undo.setEnabled(bool(b) and not b.get("undone"))

    def _undo_selected(self):
        b = self._selected()
        if not b or b.get("undone"):
            return
        verb = "removes the copies" if all(op.get("method") in ("copy", "hardlink", "none") for op in b["ops"]) \
            else "moves the files back"
        reply = QMessageBox.question(self, "Undo Rename",
                                     f"Undo “{describe_batch(b)}”? This {verb}.",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        result = self.renamer.undo_batch(b["id"])
        self.undo_results.append(result)
        if result.failed:
            details = "\n".join(f"{o.src.name}: {o.error}" for o in result.failed[:10])
            QMessageBox.warning(self, "Some Undos Failed", details)
        row = self.batch_list.currentRow()
        self._fill()
        if 0 <= row < self.batch_list.rowCount():
            self.batch_list.selectRow(row)

    def _clear(self):
        reply = QMessageBox.question(self, "Clear History",
                                     "Forget all past renames? Your files stay where they are, "
                                     "but these renames can't be undone afterwards.",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.renamer.history.clear()
            self._fill()


# ═══════════════════════════════════════════════════════════════════
#  EPISODE NUMBER DIALOG
# ═══════════════════════════════════════════════════════════════════
class EpisodeNumbersDialog(QDialog):
    """Season and episode for one file ("Change Episode…"), or a show name,
    season and first episode to number several files in list order
    ("Number in Order…")."""

    def __init__(self, parent=None, title="", season=1, first=1, last=None, count=1, number_mode=False):
        super().__init__(parent)
        from PyQt6.QtWidgets import QSpinBox
        self.number_mode = number_mode
        self.count = count
        self.setWindowTitle("Number in Order" if number_mode else "Change Episode")
        self.setMinimumWidth(420)
        L = QVBoxLayout(self)
        if number_mode:
            hint = QLabel(f"The {count} selected file(s) get consecutive episode numbers in the "
                          "order shown in the list. Use Move Up / Move Down (Ctrl+↑ / Ctrl+↓) "
                          "to put them in the right order first.")
        else:
            hint = QLabel("Use this when the file name has the wrong episode number, or none.")
        hint.setWordWrap(True)
        L.addWidget(hint)
        form = QFormLayout()
        self.title_edit = QLineEdit(title)
        self.title_edit.setPlaceholderText("Show name")
        form.addRow("Show:", self.title_edit)
        self.season_spin = QSpinBox(); self.season_spin.setRange(0, 999)
        self.season_spin.setValue(season if season is not None else 1)
        form.addRow("Season:", self.season_spin)
        self.first_spin = QSpinBox(); self.first_spin.setRange(0, 9999); self.first_spin.setValue(first or 1)
        form.addRow("First episode:" if number_mode else "Episode:", self.first_spin)
        self.last_spin = None
        if not number_mode:
            self.last_spin = QSpinBox(); self.last_spin.setRange(0, 9999)
            self.last_spin.setValue(last if last else self.first_spin.value())
            self.last_spin.setToolTip("For a file with two or more episodes (S01E01-E02)")
            form.addRow("Last episode:", self.last_spin)
            self.first_spin.valueChanged.connect(
                lambda v: self.last_spin.setValue(max(v, self.last_spin.value())))
        L.addLayout(form)
        self.preview = QLabel(); self.preview.setStyleSheet("color:#888")
        L.addWidget(self.preview)
        for w in (self.season_spin, self.first_spin) + ((self.last_spin,) if self.last_spin else ()):
            w.valueChanged.connect(self._update_preview)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept); btns.rejected.connect(self.reject)
        L.addWidget(btns)
        self._update_preview()

    def _update_preview(self):
        s, first = self.season_spin.value(), self.first_spin.value()
        if self.number_mode:
            last = first + self.count - 1
            self.preview.setText(f"S{s:02d}E{first:02d}" + (f" … S{s:02d}E{last:02d}" if self.count > 1 else ""))
        else:
            last = max(first, self.last_spin.value())
            self.preview.setText(f"S{s:02d}E{first:02d}" + (f"-E{last:02d}" if last > first else ""))

    def values(self):
        """(show name, season, [episodes per file]) — one list per file."""
        s, first = self.season_spin.value(), self.first_spin.value()
        if self.number_mode:
            eps = [[first + i] for i in range(self.count)]
        else:
            eps = [list(range(first, max(first, self.last_spin.value()) + 1))]
        return self.title_edit.text().strip(), s, eps


# ═══════════════════════════════════════════════════════════════════
#  FORMAT EDITOR
# ═══════════════════════════════════════════════════════════════════
# Template editor with live preview (opened from Settings → Formats)
class FormatEditorDialog(QDialog):
    # (variable, description, example, also available for movies)
    VARIABLES = [
        ("{show}", "Show name", "Dandadan", False),
        ("{s}", "Season number (1, 2, etc.)", "1", False),
        ("{s00}", "Season zero-padded", "01", False),
        ("{e}", "Episode number (1, 2, etc.)", "1", False),
        ("{e00}", "Episode zero-padded", "01", False),
        ("{s00e00}", "Plex format (S01E01)", "S01E01", False),
        ("{title}", "Episode/Movie title", "Episode Title", True),
        ("{year}", "Release year", "2024", True),
        ("{edition}", "Plex edition tag, when the name has one", "{edition-Director's Cut}", True),
        ("{vc}", "Video codec", "H.264", True),
        ("{c}", "Audio codec", "AAC", True),
        ("{ach}", "Audio channels", "2.0", True),
        ("{res}", "Resolution", "1080p", True),
        ("{ext}", "File extension (no dot)", "mkv", True),
    ]

    def _detect_episode_type(self, template_text):
        if not template_text:
            return "dvd"
        if "{e00}" in template_text and "{s00e00}" not in template_text:
            return "absolute"
        return "dvd"

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.parent = parent
        self.setWindowTitle("Text Format Editor")
        self.setMinimumSize(700, 550)
        self._build_ui()
        self._apply_theme()

    def _build_ui(self):
        L = QVBoxLayout(self)
        L.setContentsMargins(15, 15, 15, 15)

        self.tabs = QTabWidget()
        L.addWidget(self.tabs)

        self.build_tv_tab()
        self.build_anime_tab()
        self.build_movie_tab()

        btn_box = QDialogButtonBox()
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setStyleSheet("min-width: 80px;")
        self.apply_btn.clicked.connect(self._apply)
        btn_box.addButton(self.apply_btn, QDialogButtonBox.ButtonRole.ActionRole)

        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._save)
        btn_box.addButton(ok_btn, QDialogButtonBox.ButtonRole.AcceptRole)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_box.addButton(cancel_btn, QDialogButtonBox.ButtonRole.RejectRole)

        L.addWidget(btn_box)

    def build_tv_tab(self):
        w = QWidget()
        L = QVBoxLayout(w)
        L.setContentsMargins(10, 10, 10, 10)

        grp = QGroupBox("Preview")
        gl = QFormLayout(grp)

        self.tv_example = QLabel("Dandadan/Season 01/Dandadan S01E01 - That's How Love Starts, Ya Know?.mkv")
        self.tv_example.setObjectName("preview_label")
        self.tv_example.setWordWrap(True)
        gl.addRow("Example:", self.tv_example)

        L.addWidget(grp)

        grp2 = QGroupBox("Template")
        gl2 = QFormLayout(grp2)

        self.tv_template = QLineEdit(self.cfg.get("format_tv", "{show} ({year})/Season {s00}/{show} {s00e00} - {title}"))
        self.tv_template.setPlaceholderText("{show} ({year})/Season {s00}/{show} {s00e00} - {title}")
        self.tv_template.textChanged.connect(self._update_tv_preview)
        gl2.addRow("Template:", self.tv_template)

        self.tv_ep_type = QComboBox()
        self.tv_ep_type.addItems(["DVD", "Absolute", "Streaming"])
        saved_ep = self.cfg.get("format_tv", "")
        ep_type = self._detect_episode_type(saved_ep)
        self.tv_ep_type.setCurrentIndex({"dvd": 0, "absolute": 1, "streaming": 2}.get(ep_type, 0))
        self.tv_ep_type.currentTextChanged.connect(self._update_tv_preview)
        gl2.addRow("Episode Type:", self.tv_ep_type)

        L.addWidget(grp2)

        self._build_var_buttons(L)

        self.tabs.addTab(w, "TV Shows")

        # Show the preview for the current template
        self._update_tv_preview()

    def build_anime_tab(self):
        w = QWidget()
        L = QVBoxLayout(w)
        L.setContentsMargins(10, 10, 10, 10)

        grp = QGroupBox("Preview")
        gl = QFormLayout(grp)

        self.anime_example = QLabel("Dandadan/Season 01/Dandadan S01E01 - That's How Love Starts, Ya Know?.mkv")
        self.anime_example.setObjectName("preview_label")
        self.anime_example.setWordWrap(True)
        gl.addRow("Example:", self.anime_example)

        L.addWidget(grp)

        grp2 = QGroupBox("Template")
        gl2 = QFormLayout(grp2)

        self.anime_template = QLineEdit(self.cfg.get("format_anime", "{show} ({year})/Season {s00}/{show} {s00e00} - {title}"))
        self.anime_template.setPlaceholderText("{show} ({year})/Season {s00}/{show} {s00e00} - {title}")
        self.anime_template.textChanged.connect(self._update_anime_preview)
        gl2.addRow("Template:", self.anime_template)

        self.anime_ep_type = QComboBox()
        self.anime_ep_type.addItems(["DVD", "Absolute", "Streaming"])
        saved_ep = self.cfg.get("format_anime", "")
        ep_type = self._detect_episode_type(saved_ep)
        self.anime_ep_type.setCurrentIndex({"dvd": 0, "absolute": 1, "streaming": 2}.get(ep_type, 0))
        self.anime_ep_type.currentTextChanged.connect(self._update_anime_preview)
        gl2.addRow("Episode Type:", self.anime_ep_type)

        L.addWidget(grp2)

        self._build_var_buttons(L)

        self.tabs.addTab(w, "Anime")

        # Show the preview for the current template
        self._update_anime_preview()

    def build_movie_tab(self):
        w = QWidget()
        L = QVBoxLayout(w)
        L.setContentsMargins(10, 10, 10, 10)

        grp = QGroupBox("Preview")
        gl = QFormLayout(grp)

        self.movie_example = QLabel("Dandadan (2024)/Dandadan (2024).mkv")
        self.movie_example.setObjectName("preview_label")
        self.movie_example.setWordWrap(True)
        gl.addRow("Example:", self.movie_example)

        L.addWidget(grp)

        grp2 = QGroupBox("Template")
        gl2 = QFormLayout(grp2)

        self.movie_template = QLineEdit(self.cfg.get("format_movie", DEFAULTS["format_movie"]))
        self.movie_template.setPlaceholderText(DEFAULTS["format_movie"])
        self.movie_template.textChanged.connect(self._update_movie_preview)
        gl2.addRow("Template:", self.movie_template)

        L.addWidget(grp2)

        self._build_var_buttons(L, movies_only=True)

        self.tabs.addTab(w, "Movies")

        # Show the preview for the current template
        self._update_movie_preview()

    def _build_var_buttons(self, layout, movies_only=False):
        grp = QGroupBox("Variables")
        gl = QGridLayout(grp)

        if movies_only:
            variables = [v for v in self.VARIABLES if v[3]]
        else:
            variables = self.VARIABLES[:8]

        for i, (var, desc, example, _movies) in enumerate(variables):
            row, col = i // 4, i % 4
            btn = QPushButton(var)
            btn.setToolTip(f"{desc}\nExample: {example}")
            btn.clicked.connect(lambda checked, v=var: self._insert_var(v))
            gl.addWidget(btn, row, col)

        accent = palette_for(self.cfg)["accent"]
        link = QLabel(f'<a href="#variables" style="color:{accent}">Variable List</a> '
                      '— view all usable variables')
        link.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        link.linkActivated.connect(lambda _href: self._show_variable_list())
        gl.addWidget(link, (len(variables) - 1) // 4 + 1, 0, 1, 4)
        self.var_list_link = link

        layout.addWidget(grp)

    def _build_variable_list_dialog(self):
        """Dialog listing every template variable with description + example."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Variable List")
        dlg.setMinimumSize(660, 500)
        dlg.setStyleSheet(self.styleSheet())
        L = QVBoxLayout(dlg)

        table = QTableWidget(len(self.VARIABLES), 4, dlg)
        table.setHorizontalHeaderLabels(["Variable", "Description", "Example", "Media Type"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.verticalHeader().setVisible(False)
        hh = table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        for row, (var, desc, example, movies) in enumerate(self.VARIABLES):
            media = "TV/Anime, Movies" if movies else "TV/Anime"
            for col, text in enumerate([var, desc, example, media]):
                item = QTableWidgetItem(text)
                if col == 0:
                    f = item.font(); f.setBold(True); item.setFont(f)
                table.setItem(row, col, item)

        def _copy_row(row):
            var = table.item(row, 0).text()
            QApplication.clipboard().setText(var)

        def _context_menu(pos):
            item = table.itemAt(pos)
            if item is None:
                return
            table.selectRow(item.row())
            var = table.item(item.row(), 0).text()
            menu = QMenu(table)
            act = menu.addAction(f"Copy {var}")
            act.triggered.connect(lambda: _copy_row(item.row()))
            menu.exec(table.viewport().mapToGlobal(pos))

        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.customContextMenuRequested.connect(_context_menu)
        dlg._copy_row = _copy_row
        dlg._table = table
        L.addWidget(table)

        note = QLabel("Tip: click a variable button above the editor to insert it, "
                      "right-click a row here to copy the variable, "
                      "or type any of these directly into a template.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#888;font-size:11px")
        L.addWidget(note)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        btns.accepted.connect(dlg.accept)
        L.addWidget(btns)
        return dlg

    def _show_variable_list(self):
        # Reuse the window if it is already open
        dlg = getattr(self, "_var_list_dlg", None)
        if dlg is not None and dlg.isVisible():
            dlg.raise_()
            dlg.activateWindow()
            return
        self._var_list_dlg = self._build_variable_list_dialog()
        self._var_list_dlg.show()

    def _insert_var(self, var):
        current_tab = self.tabs.currentWidget()
        if current_tab:
            layout = current_tab.layout()
            for i in range(layout.count()):
                item = layout.itemAt(i)
                if item and item.widget():
                    if isinstance(item.widget(), QGroupBox):
                        grp = item.widget()
                        for j in range(grp.layout().count()):
                            child = grp.layout().itemAt(j)
                            if child and child.widget() and isinstance(child.widget(), QLineEdit):
                                le = child.widget()
                                le.insert(var)
                                return

    def _update_tv_preview(self):
        template = self.tv_template.text() or "{show}/Season {s00}/{show} {s00e00} - {title}"
        ep_type = self.tv_ep_type.currentText().lower()
        result = self._format_preview(template, ep_type)
        self.tv_example.setText(result)

    def _update_anime_preview(self):
        template = self.anime_template.text() or "{show}/Season {s00}/{show} {s00e00} - {title}"
        ep_type = self.anime_ep_type.currentText().lower()
        result = self._format_preview(template, ep_type)
        self.anime_example.setText(result)

    def _update_movie_preview(self):
        template = self.movie_template.text() or DEFAULTS["format_movie"]
        result = self._format_movie_preview(template)
        self.movie_example.setText(result)

    def _format_preview(self, template, episode_type="dvd"):
        values = {
            "show": "Dandadan",
            "s": "1",
            "s00": "01",
            "e": "1",
            "e00": "01",
            "s00e00": "S01E01",
            "title": "That's How Love Starts, Ya Know?",
            "year": "2024",
            "vc": "H.264",
            "c": "AAC",
            "ach": "2.0",
            "res": "1080p",
        }
        tpl = template
        if episode_type == "absolute":
            tpl = tpl.replace("{s00e00}", "{e00}")
            tpl = tpl.replace("{s00}", "")
        for key, val in values.items():
            tpl = tpl.replace("{" + key + "}", val)
        tpl = tpl.replace("{ext}", "mkv")
        return tpl

    def _format_movie_preview(self, template):
        values = {
            "title": "Dandadan",
            "year": "2024",
            "edition": "{edition-Director's Cut}",
            "edition_name": "Director's Cut",
            "vc": "H.264",
            "c": "AAC",
            "ach": "2.0",
            "res": "1080p",
        }
        result = template
        for key, val in values.items():
            result = result.replace("{" + key + "}", val)
        result = result.replace("{ext}", "mkv")
        return result

    def _apply(self):
        self.cfg["format_tv"] = self.tv_template.text()
        self.cfg["format_anime"] = self.anime_template.text()
        self.cfg["format_movie"] = self.movie_template.text()
        self.cfg["episode_tv"] = self.tv_ep_type.currentText().lower()
        self.cfg["episode_anime"] = self.anime_ep_type.currentText().lower()
        cfg_save(self.cfg)

        if self.parent and hasattr(self.parent, "fmt_tv"):
            self.parent.fmt_tv.setText(self.cfg["format_tv"])
            self.parent.fmt_anime.setText(self.cfg["format_anime"])
            self.parent.fmt_movie.setText(self.cfg["format_movie"])

        log.info(f"Saved format templates: TV={self.cfg['format_tv']}, Anime={self.cfg['format_anime']}, Movie={self.cfg['format_movie']}, episode_tv={self.cfg['episode_tv']}, episode_anime={self.cfg['episode_anime']}")

    def _save(self):
        self._apply()
        self.accept()

    def _apply_theme(self):
        pal = palette_for(self.cfg)
        self.setStyleSheet(base_stylesheet(pal) + tab_stylesheet(pal))
        _set_link_color(pal)


# ═══════════════════════════════════════════════════════════════════
#  MODERN LAYOUT PIECES
# ═══════════════════════════════════════════════════════════════════
def _animate(owner, start, end, on_value, duration=200, on_done=None, enabled=True):
    """Move a value smoothly from start to end, calling on_value on each step
    (or straight to end when animations are off)."""
    if not enabled or start == end:
        on_value(end)
        if on_done:
            on_done()
        return None
    anim = QVariantAnimation(owner)
    anim.setStartValue(int(start)); anim.setEndValue(int(end)); anim.setDuration(duration)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.valueChanged.connect(on_value)
    if on_done:
        anim.finished.connect(on_done)
    anim.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)
    return anim


class SegmentedControl(QWidget):
    """Toggle buttons where exactly one is on: TV Shows | Movies | Anime."""
    changed = pyqtSignal(int)

    def __init__(self, labels, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(4)
        self._group = QButtonGroup(self); self._group.setExclusive(True)
        for i, text in enumerate(labels):
            b = QPushButton(text); b.setObjectName("seg"); b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self._group.addButton(b, i); lay.addWidget(b)
        self._group.idClicked.connect(self.changed.emit)

    def setCurrentIndex(self, i):
        b = self._group.button(i)
        if b is not None and not b.isChecked():
            b.setChecked(True)

    def currentIndex(self):
        return self._group.checkedId()


class ElidedLabel(QLabel):
    """A one-line label that shortens its text with "…" instead of clipping it."""
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(40)

    def paintEvent(self, event):
        p = QPainter(self)
        r = self.contentsRect()
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideMiddle, r.width())
        self.style().drawItemText(p, r, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                                  self.palette(), self.isEnabled(), text, QPalette.ColorRole.WindowText)


class DropZone(QFrame):
    """The empty file list: a large drag & drop target that also opens Browse."""
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dropZone")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self); lay.setSpacing(6)
        title = QLabel("Drop files or folders here"); title.setObjectName("dropTitle")
        hint = QLabel("or click to choose your downloads folder"); hint.setObjectName("dropHint")
        for lbl in (title, hint):
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter); lbl.setWordWrap(True)
        lay.addStretch(); lay.addWidget(title); lay.addWidget(hint); lay.addStretch()

    def set_hover(self, on):
        self.setProperty("hover", "true" if on else "false")
        self.style().unpolish(self); self.style().polish(self)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class DetailsPanel(QFrame):
    """Where the selected file goes, how sure the match is, and the new name
    of each of its subtitles."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.pal = THEMES["dark"]
        lay = QVBoxLayout(self); lay.setContentsMargins(14, 10, 14, 10); lay.setSpacing(4)
        title = QLabel("DETAILS"); title.setObjectName("paneTitle")
        lay.addWidget(title)
        self.body = QLabel(); self.body.setObjectName("details")
        self.body.setWordWrap(True); self.body.setTextFormat(Qt.TextFormat.RichText)
        self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame); scroll.setWidget(self.body)
        lay.addWidget(scroll, 1)
        self.show_candidate(None)

    def show_candidate(self, cand, dest_root=""):
        p, esc = self.pal, html.escape
        muted = lambda t: f"<span style='color:{p['muted']}'>{esc(t)}</span>"
        arrow = f"<span style='color:{p['accent']}'>&nbsp;→&nbsp;</span>"
        if cand is None:
            self.body.setText(muted("Select a row to see where the file and its subtitles will go."))
            return
        info = [f"<b>{esc(cand.source.name)}</b>", muted("from " + str(cand.source.parent))]
        if cand.error:
            info.append(muted("Not renamed: ") + esc(cand.error))
        else:
            dest = str(Path(dest_root) / cand.dest_rel) if dest_root else cand.dest_rel
            info.append(arrow + esc(dest))
            info.append(muted("A file with this name already exists there, so it will be skipped.")
                        if cand.conflict else muted(f"Match confidence {int(cand.confidence * 100)}%"))
        if cand.subtitles:
            subs = [f"<b>Subtitles ({len(cand.subtitles)})</b>"]
            subs += [esc(s.name) + arrow + esc(Path(d).name) for s, d in cand.subtitles]
        elif cand.error:
            subs = []
        else:
            subs = [muted("No subtitles found for this file.")]
        if subs:
            # File on the left, its subtitles on the right
            self.body.setText(
                "<table width='100%' cellspacing='0' cellpadding='0'><tr>"
                f"<td width='52%' valign='top' style='padding-right:18px'>{'<br>'.join(info)}</td>"
                f"<td valign='top'>{'<br>'.join(subs)}</td></tr></table>")
        else:
            self.body.setText("<br>".join(info))


# ═══════════════════════════════════════════════════════════════════
#  MAIN WINDOW
# ═══════════════════════════════════════════════════════════════════
class MainWindow(QMainWindow):
    MEDIA_TYPES = ("tv", "movie", "anime")

    def __init__(self):
        super().__init__()
        self.cfg = cfg_load()
        self.renamer = Renamer(RenameHistory(HISTORY_PATH))
        self.license = License(self.cfg)
        self.jobs = BackgroundJobs(self)       # downloads, media servers and license checks
        self._watch_queue = []                 # new files waiting to be matched
        self._watch_busy = False
        self._watch_anidb = None
        self._scan_note = ""                   # shown with the next preview's result
        self._cands = []
        self._thread = self._worker = None
        self._scanning = False
        self._layout_name = None
        self._anims = {}              # running panel animations by kind
        self._replace_rows = {}       # rows being re-matched in place
        self._title_overrides = {}    # str(path) -> (title, id) chosen for that file
        self._episode_overrides = {}  # str(path) -> (season, [episodes]) chosen for that file
        self._shown_once = False
        self._quitting = False                 # closing the window really quits (not to the tray)
        self._closed = False                   # really closed: no more background checks
        self._tray_forced = False              # started with --tray
        self.tray = None
        self._instance_server = None
        self._split_main = self._split_preview = None
        self._files_stack = self._preview_stack = None
        self.setWindowTitle("PathRenamer")
        self.setAcceptDrops(True)
        # Window icon: bundled next to the script, or in PyInstaller's
        # temp dir (sys._MEIPASS) when running as the frozen EXE.
        try:
            from PyQt6.QtGui import QIcon
            if getattr(sys, "frozen", False):
                base = Path(sys._MEIPASS)
            else:
                base = Path(__file__).parent
            icon_path = base / "newicon.ico"
            if icon_path.exists():
                self.setWindowIcon(QIcon(str(icon_path)))
                log.info(f"Loaded window icon from: {icon_path}")
        except Exception as e:
            log.debug(f"Could not load window icon: {e}")

        self.status = QStatusBar(); self.setStatusBar(self.status)
        self._build_menu()
        self._create_widgets()
        self._apply_layout()
        self._restore_geometry()
        self.status.showMessage("Ready — select a source folder and click Preview Renames.")
        self._refresh_clients()
        self._update_undo_buttons()        # renames saved from earlier sessions can be undone

        self.watcher = FolderWatcher(accept=_watchable, skip_dir=_skip_watch_dir, parent=self)
        self.watcher.files_ready.connect(self._on_watch_files)
        self.watcher.error.connect(self._on_watch_error)
        self.watch_label = QLabel()
        self.watch_label.setObjectName("watchLabel")
        self.status.addPermanentWidget(self.watch_label)
        self._apply_automation()
        QTimer.singleShot(4000, self._check_license)   # a Pro key is re-checked once a week

        # Updates: a quiet check once a day; "Update to …" appears at the bottom right
        self._update_busy = False
        self._update_release = None
        self.update_btn = QPushButton()
        self.update_btn.setObjectName("updateButton")
        self.update_btn.setFlat(True)
        self.update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_btn.setStyleSheet("font-weight:600; padding:0 8px;")
        self.update_btn.setToolTip("A new version of PathRenamer is ready. Click to download and install it.")
        self.update_btn.clicked.connect(self._update_clicked)
        self.status.addPermanentWidget(self.update_btn)
        self._show_update_button()
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self._auto_check_updates)
        self._update_timer.start(6 * 3600 * 1000)        # (for when PathRenamer runs for days)
        QTimer.singleShot(8000, self._auto_check_updates)
        updates.remove_old_downloads()
        self._apply_windows_options()

        if self.cfg.get("first_run", True):
            self._first_run_wizard()

    def _refresh_clients(self):
        self.tvmaze = TVMazeClient() if self.cfg.get("use_tvmaze", True) else None
        self.anidb  = AniDBClient() if self.cfg.get("use_anidb", True) else None
        key = (self.cfg.get("tmdb_api_key") or "").strip()
        self.tmdb   = TMDBClient(key) if key else None
        self.cinemeta = CinemetaClient() if self.cfg.get("use_cinemeta", True) else None

    def _build_menu(self):
        mb = self.menuBar()
        fm = mb.addMenu("File")
        a = QAction("Settings…", self); a.triggered.connect(self._settings)
        a.setShortcut(QKeySequence("Ctrl+,")); fm.addAction(a)
        fm.addSeparator()
        q = QAction("Quit", self); q.triggered.connect(self.quit_app); fm.addAction(q)
        q.setShortcut(QKeySequence("Ctrl+Q"))

        em = mb.addMenu("Edit")
        self.act_undo = QAction("Undo Last Rename", self)
        self.act_undo.triggered.connect(self._undo); self.act_undo.setEnabled(False)
        self.act_undo.setShortcut(QKeySequence("Ctrl+Z"))
        em.addAction(self.act_undo)
        act_hist = QAction("Rename History…", self)
        act_hist.setShortcut(QKeySequence("Ctrl+H"))
        act_hist.triggered.connect(self._show_history)
        em.addAction(act_hist)

        vm = mb.addMenu("View")
        theme_group = QActionGroup(self); theme_group.setExclusive(True)
        self.act_theme = {}
        for key, label in THEME_NAMES:
            act = QAction(f"{label} Theme", self); act.setCheckable(True)
            act.setChecked(self.cfg.get("theme", "dark") == key)
            act.triggered.connect(lambda _=False, k=key: self._set_theme(k))
            theme_group.addAction(act); vm.addAction(act)
            self.act_theme[key] = act
        act_edit = QAction("Edit Custom Theme…", self)
        act_edit.triggered.connect(self._edit_custom_theme)
        vm.addAction(act_edit)

        self.presets_menu = QMenu("Presets", self)
        self.presets_menu.aboutToShow.connect(self._fill_presets_menu)
        mb.addMenu(self.presets_menu)

        vm.addSeparator()
        lm = vm.addMenu("Layout")
        group = QActionGroup(self); group.setExclusive(True)
        self.act_layout = {}
        for key, label in (("modern", "Modern"), ("classic", "Classic")):
            act = QAction(label, self); act.setCheckable(True)
            act.triggered.connect(lambda _=False, k=key: self._set_layout(k))
            group.addAction(act); lm.addAction(act)
            self.act_layout[key] = act
        self.act_details = QAction("Show Details Panel", self)
        self.act_details.setCheckable(True)
        self.act_details.setChecked(self.cfg.get("show_details", True))
        self.act_details.triggered.connect(self._toggle_details)
        vm.addAction(self.act_details)

        tm = mb.addMenu("Tools")
        self.act_watch = QAction("Watch Folder", self)
        self.act_watch.setCheckable(True)
        self.act_watch.triggered.connect(self._toggle_watch)
        tm.addAction(self.act_watch)
        act = QAction("Check the Watch Folder Now", self)
        act.triggered.connect(self._check_watch_now)
        tm.addAction(act)
        tm.addSeparator()
        self.act_download_subs = QAction("Download Subtitles for Selected Files", self)
        self.act_download_subs.setShortcut(QKeySequence("Ctrl+D"))
        self.act_download_subs.setEnabled(False)
        self.act_download_subs.triggered.connect(self._download_subtitles)
        tm.addAction(self.act_download_subs)
        act = QAction("Scan Media Server Now", self)
        act.triggered.connect(self._scan_server_now)
        tm.addAction(act)
        tm.addSeparator()
        act = QAction("Automation Settings…", self)
        act.triggered.connect(lambda: self._settings("Automation"))
        tm.addAction(act)

        hm = mb.addMenu("Help")
        act = QAction("PathRenamer Pro…", self)
        act.triggered.connect(lambda: self._settings("Pro"))
        hm.addAction(act)
        act = QAction("Website and Guides", self)
        act.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(WEBSITE_URL)))
        hm.addAction(act)
        act = QAction("Check for Updates…", self)
        act.triggered.connect(self.check_for_updates)
        hm.addAction(act)
        act = QAction("About PathRenamer", self)
        act.triggered.connect(lambda: self._settings("About"))
        hm.addAction(act)

    # ── Widgets (made once; each layout arranges them) ───────────────
    def _create_widgets(self):
        remembered = self.cfg.get("last_source_folder","") if self.cfg.get("remember_folders", True) else ""
        self.src = QLineEdit(remembered)
        self.src.setPlaceholderText("Folder containing media files to rename...")
        self.src.textChanged.connect(self._on_src_changed)
        self.btn_browse = QPushButton("Browse…"); self.btn_browse.clicked.connect(self._browse_src)
        self.btn_browse.setToolTip("Choose the folder with your media files (Ctrl+O)")

        self.combo = QComboBox(); self.combo.addItems(["TV Shows","Movies","Anime"])
        saved_type = self.cfg.get("media_type", "tv")
        log.debug(f"Startup: media_type from config = '{saved_type}'")
        self.combo.setCurrentIndex({"tv": 0, "movie": 1, "anime": 2}.get(saved_type, 0))
        self.combo.currentIndexChanged.connect(self._on_media_type_changed)
        self.seg = SegmentedControl(["TV Shows", "Movies", "Anime"])
        self.seg.setCurrentIndex(self.combo.currentIndex())
        self.seg.changed.connect(self.combo.setCurrentIndex)

        self.btn_preview = QPushButton("⟳  Preview Renames")
        self.btn_preview.setObjectName("btn_preview")
        self.btn_preview.setToolTip("Look up the new names (F5)")
        self.btn_preview.clicked.connect(self._preview)

        # Modern: matching mode in the toolbar instead of a question on every preview
        self.match_combo = QComboBox(); self.match_combo.addItems(["Automatic match", "Forced match (TVDB ID)"])
        self.match_combo.setToolTip("Automatic: look the show up by its name.\n"
                                    "Forced: use the show with this TVDB ID for every file.")
        self.tvdb_edit = QLineEdit(); self.tvdb_edit.setPlaceholderText("TVDB ID")
        self.tvdb_edit.setFixedWidth(0)
        self.tvdb_edit.returnPressed.connect(self._preview)
        self.match_combo.currentIndexChanged.connect(lambda _: self._on_match_mode_changed())

        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_list.setAlternatingRowColors(False)
        model = self.file_list.model()
        for sig in (model.rowsInserted, model.rowsRemoved, model.modelReset):
            sig.connect(lambda *_: self._update_files_ui())
        self.file_list.itemSelectionChanged.connect(self._update_files_ui)
        self.btn_sel_all = QPushButton("Select All"); self.btn_sel_all.clicked.connect(self.file_list.selectAll)
        self.btn_rem_files = QPushButton("Remove"); self.btn_rem_files.clicked.connect(self._remove_selected_files)
        self.btn_clear_files = QPushButton("Clear List"); self.btn_clear_files.clicked.connect(self._clear_file_list)

        self.pbar = QProgressBar(); self.pbar.setVisible(False)
        self.table = RenameTable()
        self.table.set_display_options(
            self.cfg.get("show_detected", True),
            self.cfg.get("show_full_path", True),
        )
        self.table.set_palette(palette_for(self.cfg))

        self.btn_remove = QPushButton("✕ Remove Selected")
        self.btn_remove.setObjectName("btn_remove")
        self.btn_remove.setEnabled(False)
        self.btn_remove.setToolTip("Leave the selected rows out of this rename (Delete)")
        self.btn_remove.clicked.connect(self._remove_selected_rows)
        self.table.itemSelectionChanged.connect(self._on_table_selection)
        self.btn_undo = QPushButton("↩  Undo Last")
        self.btn_undo.setObjectName("btn_undo"); self.btn_undo.setEnabled(False)
        self.btn_undo.setToolTip("Put the last renamed files back (Ctrl+Z)")
        self.btn_undo.clicked.connect(self._undo)
        # Fixing single rows: right-click menu, double-click, shortcuts, Fix button
        def _action(text, slot, shortcut=None, tip=""):
            act = QAction(text, self)
            act.triggered.connect(slot)
            if shortcut:
                act.setShortcut(QKeySequence(shortcut))
                act.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                self.table.addAction(act)
            if tip:
                act.setToolTip(tip)
            return act
        self.act_edit_name = _action("Edit New Name…", self._edit_name, "F2")
        self.act_change_episode = _action("Change Episode…", self._change_episode, "Ctrl+E")
        self.act_choose_match = _action("Choose a Different Match…", self._choose_match, "Ctrl+M")
        self.act_number = _action("Number in Order…", self._number_in_order, "Ctrl+N")
        self.act_move_up = _action("Move Up", lambda: self._move_rows(-1), "Ctrl+Up")
        self.act_move_down = _action("Move Down", lambda: self._move_rows(1), "Ctrl+Down")
        self.table.customContextMenuRequested.disconnect()
        self.table.customContextMenuRequested.connect(self._table_menu)
        self.table.cellDoubleClicked.connect(self._on_table_double_click)
        self.table.itemSelectionChanged.connect(self._update_fix_actions)
        self.btn_fix = QPushButton("Fix ▾")
        self.btn_fix.setObjectName("flat")
        self.btn_fix.setToolTip("Fix the selected rows: edit the name, change the episode,\n"
                                "choose a different match, reorder or number them in order.\n"
                                "(Also on right-click.)")
        fix_menu = QMenu(self.btn_fix)
        for act in (self.act_edit_name, self.act_change_episode, self.act_choose_match, self.act_number):
            fix_menu.addAction(act)
        fix_menu.addSeparator()
        fix_menu.addAction(self.act_move_up); fix_menu.addAction(self.act_move_down)
        fix_menu.addSeparator()
        fix_menu.addAction(self.act_download_subs)
        self.btn_fix.setMenu(fix_menu)

        self.btn_rename = QPushButton("✔  Rename Files")
        self.btn_rename.setObjectName("btn_rename"); self.btn_rename.setEnabled(False)
        self.btn_rename.setToolTip("Move the files to their new names (Ctrl+Enter)")
        self.btn_rename.clicked.connect(self._rename)

        # Modern-only pieces
        self.drop_zone = DropZone(); self.drop_zone.clicked.connect(self._browse_src)
        self.details = DetailsPanel()
        self.summary = QLabel(); self.summary.setObjectName("summary")
        self.lib_label = ElidedLabel(); self.lib_label.setObjectName("muted")
        self.btn_presets = QPushButton("Presets ▾"); self.btn_presets.setObjectName("flat")
        self.btn_presets.setToolTip("Saved combinations of naming formats, library folders,\n"
                                    "data sources and how files are placed")
        self.btn_presets.setMenu(self.presets_menu)
        self.btn_change_lib = QPushButton("Change…"); self.btn_change_lib.setObjectName("link")
        self.btn_change_lib.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_change_lib.clicked.connect(self._settings)
        self.files_count = QLabel(); self.files_count.setObjectName("paneCount")
        self.preview_count = QLabel(); self.preview_count.setObjectName("paneCount")
        self.preview_empty = QLabel("Click  ⟳ Preview  to see the new names here.")
        self.preview_empty.setObjectName("emptyHint")
        self.preview_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_empty.setWordWrap(True)

        # Widgets a layout doesn't use wait here (hidden) until it is switched back
        self._parking = QWidget(self); self._parking.hide()
        self._persistent = [
            self.src, self.btn_browse, self.combo, self.seg, self.btn_preview, self.match_combo,
            self.tvdb_edit, self.file_list, self.btn_sel_all, self.btn_rem_files, self.btn_clear_files,
            self.pbar, self.table, self.btn_remove, self.btn_undo, self.btn_rename, self.drop_zone,
            self.details, self.summary, self.lib_label, self.btn_change_lib, self.files_count,
            self.preview_count, self.preview_empty, self.btn_fix, self.btn_presets,
        ]

        # Keyboard shortcuts (all layouts)
        for keys, slot in (("F5", self._preview), ("Ctrl+O", self._browse_src),
                           ("Ctrl+Return", self._rename_if_ready), ("Ctrl+Enter", self._rename_if_ready),
                           ("Delete", self._delete_pressed)):
            QShortcut(QKeySequence(keys), self).activated.connect(slot)

    # ── Layouts ──────────────────────────────────────────────────────
    def _apply_layout(self):
        """Build the window in the layout chosen in Settings, keeping the file
        list, preview and everything typed."""
        name = self.cfg.get("layout", "modern")
        if name not in LAYOUTS:
            name = "modern"
        # The previous layout's frames and splitters are deleted below:
        # stop animations that are still moving them
        self._stop_animation()
        self._split_main = self._split_preview = None
        self._files_stack = self._preview_stack = None
        old = self.takeCentralWidget()
        for w in self._persistent:
            w.setParent(self._parking)
        root = QWidget()
        if name == "modern":
            self._arrange_modern(root)
        else:
            self._arrange_classic(root)
        self.setCentralWidget(root)
        if old is not None:
            old.deleteLater()
        self._layout_name = name
        self.pbar.setVisible(self._scanning)       # setParent() hides widgets
        self._on_match_mode_changed(animate=False)
        self._apply_theme()
        self._update_layout_menu()
        self._update_files_ui()
        self._update_summary()
        self._update_library_label()
        self._update_fix_actions()
        if self.isVisible():
            QTimer.singleShot(0, self._apply_split_sizes)

    def _arrange_classic(self, root):
        """The original PathRenamer layout."""
        self.setMinimumSize(1050, 680)
        for w, name in ((self.src, ""), (self.pbar, ""), (self.btn_sel_all, ""),
                        (self.btn_rem_files, ""), (self.btn_clear_files, "")):
            w.setObjectName(name)
        self.btn_sel_all.setText("Select All"); self.btn_rem_files.setText("Remove")
        self.btn_clear_files.setText("Clear List")
        self.pbar.setTextVisible(True)
        self.table.set_modern(False)
        self.file_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerItem)
        self.file_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.file_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.file_list.setMinimumHeight(120)
        for b in (self.btn_sel_all, self.btn_rem_files, self.btn_clear_files):
            b.setEnabled(True)

        L = QVBoxLayout(root); L.setContentsMargins(12,12,12,12); L.setSpacing(10)
        grp = QGroupBox("Source Folder"); gl = QVBoxLayout(grp)
        sr = QHBoxLayout(); sr.addWidget(QLabel("Folder:"))
        sr.addWidget(self.src, 1); sr.addWidget(self.btn_browse)
        gl.addLayout(sr)

        cr = QHBoxLayout(); cr.addWidget(QLabel("Media Type:"))
        self.combo.setFixedWidth(140); cr.addWidget(self.combo)
        cr.addStretch()
        self.btn_preview.setText("⟳  Preview Renames")
        cr.addWidget(self.btn_preview)
        gl.addLayout(cr)
        L.addWidget(grp)

        file_grp = QGroupBox("Selected Files")
        file_l = QVBoxLayout(file_grp)
        file_l.setContentsMargins(4, 8, 4, 4)
        file_l.addWidget(self.file_list)
        hint = QLabel("Drag & drop files here, or Ctrl/Shift+click to select multiple")
        hint.setStyleSheet("color: #6c7086; font-size: 11px; padding: 2px;")
        file_l.addWidget(hint)
        br_file = QHBoxLayout()
        br_file.addStretch()
        for b in (self.btn_sel_all, self.btn_rem_files, self.btn_clear_files):
            br_file.addWidget(b)
        file_l.addLayout(br_file)
        L.addWidget(file_grp)

        L.addWidget(self.pbar)
        L.addWidget(self.table, 1)

        br = QHBoxLayout(); br.addStretch()
        br.addWidget(self.btn_remove)
        br.addWidget(self.btn_undo)
        self.btn_rename.setFixedWidth(160)
        br.addWidget(self.btn_rename); L.addLayout(br)
        # The Modern layout's stacked panes hide the list and table when they
        # leave them; show everything Classic uses
        for w in (self.src, self.btn_browse, self.combo, self.btn_preview, self.file_list,
                  self.btn_sel_all, self.btn_rem_files, self.btn_clear_files, self.table,
                  self.btn_remove, self.btn_undo, self.btn_rename):
            w.show()

    def _arrange_modern(self, root):
        """Toolbar on top, file list and preview side by side (stacked on narrow
        windows), details under the preview, actions at the bottom."""
        self.setMinimumSize(760, 540)
        root.setObjectName("modernRoot")
        density = self.cfg.get("density", "comfortable")
        self.src.setObjectName("srcEdit")
        self.pbar.setObjectName("slimProgress"); self.pbar.setTextVisible(False)
        for b, text in ((self.btn_sel_all, "Select all"), (self.btn_rem_files, "Remove"),
                        (self.btn_clear_files, "Clear")):
            b.setObjectName("flat"); b.setText(text)
        self.table.set_modern(True, density)
        self.file_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        # Long names are shortened in the middle (full path on hover), no sideways scrolling
        self.file_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.file_list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.file_list.setMinimumHeight(0)
        self.btn_rename.setMinimumWidth(150); self.btn_rename.setMaximumWidth(16777215)

        outer = QVBoxLayout(root); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)

        # Toolbar: folder + Preview, then media type, matching and library folder
        top = QFrame(); top.setObjectName("topBar")
        tl = QVBoxLayout(top); tl.setContentsMargins(16, 12, 16, 10); tl.setSpacing(8)
        r1 = QHBoxLayout(); r1.setSpacing(8)
        r1.addWidget(self.src, 1); r1.addWidget(self.btn_browse)
        self.btn_preview.setText("⟳  Preview")
        self.btn_preview.setMinimumWidth(120)
        r1.addWidget(self.btn_preview)
        tl.addLayout(r1)
        r2 = QHBoxLayout(); r2.setSpacing(8)
        r2.addWidget(self.seg); r2.addSpacing(6)
        r2.addWidget(self.match_combo); r2.addWidget(self.tvdb_edit)
        r2.addSpacing(10)
        r2.addWidget(self.lib_label, 1); r2.addWidget(self.btn_change_lib)
        r2.addSpacing(6); r2.addWidget(self.btn_presets)
        tl.addLayout(r2)
        outer.addWidget(top)
        outer.addWidget(self.pbar)

        body = QWidget(); bl = QVBoxLayout(body); bl.setContentsMargins(14, 12, 14, 10)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False); split.setHandleWidth(10)

        files = QFrame(); files.setObjectName("card"); files.setMinimumSize(290, 150)
        fl = QVBoxLayout(files); fl.setContentsMargins(12, 10, 12, 10); fl.setSpacing(6)
        fh = QHBoxLayout(); fh.setSpacing(4)
        t = QLabel("FILES"); t.setObjectName("paneTitle")
        fh.addWidget(t); fh.addSpacing(4); fh.addWidget(self.files_count); fh.addStretch()
        for b in (self.btn_sel_all, self.btn_rem_files, self.btn_clear_files):
            fh.addWidget(b)
        fl.addLayout(fh)
        self._files_stack = QStackedWidget()
        self._files_stack.addWidget(self.drop_zone); self._files_stack.addWidget(self.file_list)
        fl.addWidget(self._files_stack, 1)
        split.addWidget(files)

        preview = QFrame(); preview.setObjectName("card"); preview.setMinimumSize(360, 150)
        pl = QVBoxLayout(preview); pl.setContentsMargins(12, 10, 12, 10); pl.setSpacing(6)
        ph = QHBoxLayout()
        t2 = QLabel("PREVIEW"); t2.setObjectName("paneTitle")
        ph.addWidget(t2); ph.addSpacing(4); ph.addWidget(self.preview_count); ph.addStretch()
        ph.addWidget(self.btn_fix)
        pl.addLayout(ph)
        self._preview_stack = QStackedWidget()
        self._preview_stack.addWidget(self.preview_empty); self._preview_stack.addWidget(self.table)
        pl.addWidget(self._preview_stack, 1)

        self._split_preview = QSplitter(Qt.Orientation.Vertical)
        self._split_preview.setChildrenCollapsible(False); self._split_preview.setHandleWidth(10)
        self._split_preview.addWidget(preview); self._split_preview.addWidget(self.details)
        self._split_preview.setStretchFactor(0, 1); self._split_preview.setStretchFactor(1, 0)
        self.details.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.details.setVisible(self.cfg.get("show_details", True))
        split.addWidget(self._split_preview)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 1)
        split.splitterMoved.connect(self._on_split_moved)
        self._split_main = split
        bl.addWidget(split)
        outer.addWidget(body, 1)

        bar = QFrame(); bar.setObjectName("actionBar")
        al = QHBoxLayout(bar); al.setContentsMargins(16, 10, 16, 10); al.setSpacing(8)
        al.addWidget(self.summary, 1)
        al.addWidget(self.btn_remove); al.addWidget(self.btn_undo); al.addWidget(self.btn_rename)
        outer.addWidget(bar)
        for w in (self.src, self.btn_browse, self.btn_preview, self.seg, self.match_combo,
                  self.lib_label, self.btn_change_lib, self.files_count, self.preview_count,
                  self.btn_sel_all, self.btn_rem_files, self.btn_clear_files, self.summary,
                  self.btn_remove, self.btn_undo, self.btn_rename, self.btn_fix, self.btn_presets):
            w.show()
        self._update_panel_orientation()

    def _panel_orientation(self):
        mode = self.cfg.get("panel_mode", "auto")
        if mode == "side":
            return Qt.Orientation.Horizontal
        if mode == "stacked":
            return Qt.Orientation.Vertical
        return Qt.Orientation.Horizontal if self.width() >= 980 else Qt.Orientation.Vertical

    def _update_panel_orientation(self):
        if self._split_main is None:
            return
        orient = self._panel_orientation()
        if self._split_main.orientation() != orient:
            self._split_main.setOrientation(orient)
            QTimer.singleShot(0, self._apply_split_sizes)

    def _apply_split_sizes(self):
        """File list share of the window, and the details panel height."""
        split = self._split_main
        if split is None:
            return
        horizontal = split.orientation() == Qt.Orientation.Horizontal
        total = split.width() if horizontal else split.height()
        if total > 0:
            ratio = min(max(float(self.cfg.get("files_ratio", 0.3)), 0.15), 0.7)
            if not horizontal:
                ratio = min(ratio, 0.35)        # stacked: the preview needs the height
            first = int(total * ratio)
            split.setSizes([first, total - first])
        sp = self._split_preview
        if sp is not None and not self.details.isHidden():
            h = sum(sp.sizes()) or sp.height()
            d = self._details_height(h)
            sp.setSizes([max(h - d, 1), d])

    @staticmethod
    def _details_height(total):
        return min(230, max(130, int(total * 0.36)))

    def _on_split_moved(self, *_):
        split = self._split_main
        sizes = split.sizes() if split is not None else []
        if len(sizes) == 2 and sum(sizes) > 0 and split.orientation() == Qt.Orientation.Horizontal:
            self.cfg["files_ratio"] = round(sizes[0] / sum(sizes), 3)

    def _set_details_visible(self, show, animate=True):
        """Slide the details panel in or out under the preview. A click while
        it is still sliding turns it around from where it is."""
        sp = self._split_preview
        if sp is None:
            return
        animate = animate and self.cfg.get("animations", True) and self.isVisible()
        self._stop_animation("details")
        visible = not self.details.isHidden()
        if not show and not visible:
            return
        sizes = sp.sizes()
        total = sum(sizes) or sp.height()
        current = sizes[1] if visible and len(sizes) > 1 else 0
        # Size policy "Ignored" lets the splitter squeeze the panel below its
        # normal minimum while it slides; restored when done.
        self.details.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Ignored)
        def step(v):
            try:
                sp.setSizes([max(total - v, 1), v])
            except RuntimeError:          # layout replaced meanwhile
                pass
        def restore_policy():
            self.details.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        def done_hide():
            self.details.hide()
            restore_policy()
        if show:
            if not visible:
                self.details.show()
                step(0)
            self._update_details()
            self._run_animation("details", current, self._details_height(total), step,
                                on_done=restore_policy, enabled=animate)
        else:
            self._run_animation("details", current, 0, step, on_done=done_hide, enabled=animate)

    def _run_animation(self, key, start, end, on_value, on_done=None, enabled=True):
        """_animate, first stopping the previous animation of the same kind so
        quick repeated clicks always end in the latest state."""
        self._stop_animation(key)
        anim = _animate(self, start, end, on_value, on_done=on_done, enabled=enabled)
        if anim is not None:
            self._anims[key] = anim
            anim.finished.connect(lambda k=key, a=anim: self._anims.get(k) is a and self._anims.pop(k))

    def _stop_animation(self, key=None):
        """Stop one running animation (or all). A stopped animation does not
        run its "done" step."""
        for k in ([key] if key else list(self._anims)):
            anim = self._anims.pop(k, None)
            if anim is not None:
                try:
                    anim.stop()
                except RuntimeError:      # already finished and deleted
                    pass

    def _toggle_details(self, checked):
        self.cfg["show_details"] = bool(checked)
        cfg_save(self.cfg)
        self._set_details_visible(bool(checked))

    def _set_layout(self, name):
        if name == self._layout_name:
            return
        self.cfg["layout"] = name
        cfg_save(self.cfg)
        self._apply_layout()

    def _update_layout_menu(self):
        for key, act in self.act_layout.items():
            act.setChecked(key == self._layout_name)
        self.act_details.setEnabled(self._layout_name == "modern")
        self.act_details.setChecked(self.cfg.get("show_details", True))

    def _on_match_mode_changed(self, animate=True):
        """Modern: show the TVDB ID box (sliding open) when Forced is chosen."""
        forced = self.match_combo.currentIndex() == 1
        if self._layout_name != "modern":
            return
        animate = animate and self.cfg.get("animations", True) and self.isVisible()
        self._stop_animation("tvdb")
        if forced:
            start = 0 if self.tvdb_edit.isHidden() else self.tvdb_edit.width()
            self.tvdb_edit.show()
            self._run_animation("tvdb", start, 130, self.tvdb_edit.setFixedWidth, enabled=animate)
            if animate:
                self.tvdb_edit.setFocus()
        elif self.tvdb_edit.isHidden():
            self.tvdb_edit.setFixedWidth(0)
        else:
            self._run_animation("tvdb", self.tvdb_edit.width(), 0, self.tvdb_edit.setFixedWidth,
                                on_done=self.tvdb_edit.hide, enabled=animate)

    def _restore_geometry(self):
        geo = self.cfg.get("window_geometry")
        if geo:
            try:
                if self.restoreGeometry(QByteArray.fromBase64(geo.encode("ascii"))):
                    return
            except Exception:
                pass
        if self._layout_name == "modern":
            self.resize(1180, 760)

    def closeEvent(self, event):
        try:
            self.cfg["window_geometry"] = bytes(self.saveGeometry().toBase64()).decode("ascii")
            cfg_save(self.cfg)
        except Exception as e:
            log.debug(f"Could not save window size: {e}")
        if not self._quitting and self._tray_active():
            event.ignore()                     # keep running in the tray
            self.hide()
            if not self.cfg.get("tray_hint_shown"):
                self.tray.showMessage("PathRenamer is still running",
                                      "It keeps watching your folder. Right-click this icon to open or quit it.")
                self.cfg["tray_hint_shown"] = True
                cfg_save(self.cfg)
            return
        self.watcher.stop()
        self._closed = True
        self._update_timer.stop()
        if self.tray is not None:
            self.tray.hide()
        super().closeEvent(event)
        app = QApplication.instance()
        if app is not None and not app.quitOnLastWindowClosed():
            app.quit()                         # (the tray keeps the app alive otherwise)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._shown_once:          # (restoring from minimized keeps the user's sizes)
            self._shown_once = True
            QTimer.singleShot(0, self._apply_split_sizes)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_panel_orientation()

    # ── Live parts of the Modern layout ──────────────────────────────
    def _update_files_ui(self):
        try:
            n = self.file_list.count()
        except RuntimeError:          # window is being destroyed
            return
        sel = len(self.file_list.selectedItems())
        self.files_count.setText(f"{n}" + (f"  ·  {sel} selected" if sel else ""))
        if self._files_stack is not None:
            self._files_stack.setCurrentIndex(1 if n else 0)
        if self._layout_name == "modern":
            self.btn_sel_all.setEnabled(n > 0)
            self.btn_clear_files.setEnabled(n > 0)
            self.btn_rem_files.setEnabled(sel > 0)

    def _update_summary(self):
        cands = self.table.get_cands()
        ok = [c for c in cands if not c.error]
        ready = sum(1 for c in ok if not c.conflict and c.confidence >= 0.85)
        check = sum(1 for c in ok if not c.conflict and c.confidence < 0.85)
        conflicts = sum(1 for c in ok if c.conflict)
        errors = len(cands) - len(ok)
        subs = sum(len(c.subtitles) for c in ok if not c.conflict)
        if not cands:
            text = "Scanning…" if self._scanning else "Add files, then click Preview."
        else:
            parts = [f"{ready} ready"]
            if check:
                parts.append(f"{check} to check")
            if conflicts:
                parts.append(f"{conflicts} conflict{'s' if conflicts != 1 else ''}")
            if errors:
                parts.append(f"{errors} can't be renamed")
            if subs:
                parts.append(f"{subs} subtitle{'s' if subs != 1 else ''}")
            text = "  ·  ".join(parts)
        self.summary.setText(text)
        self.preview_count.setText(str(len(cands)) if cands else "")
        if self._preview_stack is not None:
            self._preview_stack.setCurrentIndex(1 if (cands or self._scanning) else 0)

    def _update_details(self):
        if self._layout_name != "modern" or self.details.isHidden():
            return
        rows = self.table.selected_rows()
        cands = self.table.get_cands()
        cand = cands[rows[-1]] if rows and rows[-1] < len(cands) else None
        self.details.show_candidate(cand, self._get_destination())

    def _update_library_label(self):
        kind = {"tv": "TV shows", "movie": "Movies", "anime": "Anime"}[self._media_type()]
        dest = self._get_destination()
        if dest:
            self.lib_label.setText(f"{kind} go to  {dest}")
            self.btn_change_lib.setText("Change…")
        else:
            self.lib_label.setText(f"No library folder set for {kind.lower()} yet")
            self.btn_change_lib.setText("Set folder…")
        self.lib_label.setToolTip(dest)
        self.lib_label.update()

    def _on_table_selection(self):
        try:
            self.btn_remove.setEnabled(bool(self.table.selectedItems()))
        except RuntimeError:          # window is being destroyed
            return
        self._update_details()

    def _rename_if_ready(self):
        if self.btn_rename.isEnabled():
            self._rename()

    def _delete_pressed(self):
        if self.table.hasFocus() and self.table.selectedItems():
            self._remove_selected_rows()
        elif self.file_list.hasFocus() and self.file_list.selectedItems():
            self._remove_selected_files()

    def _browse_src(self):
        f = QFileDialog.getExistingDirectory(self, "Source Folder", self.src.text())
        if f:
            self.src.setText(f)
            if self.cfg.get("remember_folders", True):
                self.cfg["last_source_folder"] = f
                cfg_save(self.cfg)
            files = scan_folder(f)
            if files:
                self._add_files_to_list(files)
                self.status.showMessage(f"{len(files)} file(s) added to list. Select files and click Preview.")
            else:
                files = scan_folder(f, include_subtitles=True)
                if files:
                    self._add_files_to_list(files)
                    self.status.showMessage(f"{len(files)} subtitle file(s) added (no video files found). Select files and click Preview.")

    def _on_src_changed(self, text):
        if not hasattr(self, "_src_timer"):
            self._src_timer = QTimer()
            self._src_timer.setSingleShot(True)
            self._src_timer.timeout.connect(self._scan_src_folder)
        self._pending_src = text
        self._src_timer.start(500)

    def _scan_src_folder(self):
        text = getattr(self, "_pending_src", "").strip()
        if text and os.path.isdir(text):
            files = scan_folder(text)
            if not files:
                files = scan_folder(text, include_subtitles=True)
            if files:
                self._add_files_to_list(files)
    def _settings(self, page=None):
        d = SettingsDialog(self.cfg, self, page=page if isinstance(page, str) else None)
        accepted = d.exec()
        if accepted:
            self.cfg = d.cfg
        else:
            # Cancelled: reload from disk. The Format Editor (opened from the
            # Settings dialog) may already have saved template changes, so the
            # in-memory copy can't simply be kept.
            self.cfg = cfg_load()
        self.license.cfg = self.cfg
        self._refresh_clients()
        if hasattr(self, "watcher"):         # (the first-run wizard opens Settings before it exists)
            self._apply_automation()
        if hasattr(self, "update_btn"):
            self._show_update_button()
            self._apply_windows_options()
        saved_type = self.cfg.get("media_type", "tv")
        idx = {"tv": 0, "movie": 1, "anime": 2}.get(saved_type, 0)
        if self.combo.currentIndex() != idx:
            self.combo.setCurrentIndex(idx)
        self.table.set_display_options(
            self.cfg.get("show_detected", True),
            self.cfg.get("show_full_path", True),
        )
        if self.cfg.get("layout", "modern") != self._layout_name:
            self._apply_layout()
        else:
            self._apply_layout_options()
        self._update_theme_menu()

    def _apply_layout_options(self):
        """Settings changed without switching layout: density, file list
        position, details panel, theme, library folder."""
        if self._layout_name == "modern":
            self.table.set_modern(True, self.cfg.get("density", "comfortable"))
            self._update_panel_orientation()
            self._set_details_visible(self.cfg.get("show_details", True))
        self._apply_theme()
        self._update_layout_menu()
        self._update_library_label()
        self._update_summary()

    def _set_theme(self, theme):
        if theme == "custom" and not self.cfg.get("custom_theme"):
            return self._edit_custom_theme()     # nothing to show yet: make it first
        self.cfg["theme"] = theme
        cfg_save(self.cfg)
        self._apply_theme()
        self._update_theme_menu()

    def _edit_custom_theme(self):
        dlg = CustomThemeDialog(self.cfg, self, preview=self._apply_theme)
        if dlg.exec():
            self.cfg["custom_theme"] = dlg.result_theme()
            self.cfg["theme"] = "custom"
            cfg_save(self.cfg)
        self._apply_theme()
        self._update_theme_menu()

    def _apply_theme(self, pal=None):
        """Style the window with the chosen theme (or with pal, e.g. while the
        Custom theme is being edited)."""
        pal = pal or palette_for(self.cfg)
        self.setStyleSheet(app_stylesheet(pal, self.cfg.get("density", "comfortable")))
        _set_link_color(pal)
        self.table.set_palette(pal)
        self.details.pal = pal
        self._update_details()

    def _update_theme_menu(self):
        theme = self.cfg.get("theme", "dark")
        for key, act in self.act_theme.items():
            act.setChecked(key == theme)

    def _on_media_type_changed(self):
        self.cfg["media_type"] = self._media_type()
        cfg_save(self.cfg)
        self.seg.setCurrentIndex(self.combo.currentIndex())
        self._update_library_label()

    def _get_destination(self):
        mtype = self._media_type()
        log.debug(f"_get_destination: media_type='{mtype}'")
        if mtype == "tv":
            root = self.cfg.get("root_tv", "")
            log.debug(f"_get_destination: root_tv='{root}'")
            return root
        elif mtype == "movie":
            root = self.cfg.get("root_movie", "")
            log.debug(f"_get_destination: root_movie='{root}'")
            return root
        else:
            root = self.cfg.get("root_anime", "")
            log.debug(f"_get_destination: root_anime='{root}'")
            return root

    def _first_run_wizard(self):
        QMessageBox.information(self, "Welcome to PathRenamer",
            "Welcome! Let's set up your library folders.\n\n"
            "Click OK to open the Settings window where you can configure your TV Shows, Movies, and Anime root folders.")
        self._settings()

    def _media_type(self): return ["tv","movie","anime"][self.combo.currentIndex()]

    def _detect_media_type_from_paths(self, files):
        """Guess media type from folder names in the files' paths."""
        joined = " ".join(str(f).lower() for f in files[:20])
        if re.search(r"\banime\b", joined):
            return "anime"
        if re.search(r"\b(movies?|films?)\b", joined):
            return "movie"
        if re.search(r"\b(tv|shows?|series)\b", joined):
            return "tv"
        return None

    def _choose_match_mode(self):
        """("normal" | "forced", TVDB id or None), or (None, None) if cancelled.
        Classic asks in two dialogs; Modern reads the toolbar."""
        if self._layout_name == "modern":
            if self.match_combo.currentIndex() == 1:
                tvdb_id = self.tvdb_edit.text().strip()
                if not tvdb_id:
                    QMessageBox.information(self, "Forced Match",
                                            "Type the show's TVDB ID in the box next to “Forced match”.")
                    self.tvdb_edit.setFocus()
                    return None, None
                return "forced", tvdb_id
            return "normal", None
        match_type, ok = QInputDialog.getItem(
            self, "Match Type", "Select matching mode:",
            ["Normal Match (automatic)", "Forced Match (enter TVDB ID)"], 0, False
        )
        if not ok:
            return None, None
        if "Forced" in match_type:
            forced_id, ok = QInputDialog.getText(self, "Forced Match", "Enter TVDB ID for the show:")
            if not ok or not forced_id.strip():
                return None, None
            return "forced", forced_id.strip()
        return "normal", None

    def _preview(self):
        if self._scanning:
            return
        dst = self._get_destination()
        # Prefer files selected in the list; fall back to the source folder.
        selected_paths = self._get_selected_files()

        if selected_paths:
            # Drop entries whose files were moved/deleted since they were added.
            missing = [p for p in selected_paths if not p.exists()]
            if missing:
                self._purge_files_from_list(missing)
                selected_paths = [p for p in selected_paths if p.exists()]
            files = selected_paths
            log.info(f"Using {len(files)} selected file(s) from list")
            if not files:
                return self.status.showMessage("Selected files no longer exist — list cleaned up.")
            self.status.showMessage(f"Processing {len(files)} selected file(s)…")
        elif self.src.text().strip():
            src = self.src.text().strip()
            files = scan_folder(src)
            log.info(f"Found {len(files)} media file(s) in {src!r}")
            if not files:
                files = scan_folder(src, include_subtitles=True)
                if not files:
                    return self.status.showMessage("No media files found.")
                self.status.showMessage(f"Scanning {len(files)} subtitle file(s)…")
            else:
                self.status.showMessage(f"Scanning {len(files)} file(s)…")
        else:
            return QMessageBox.warning(self, "PathRenamer", "Please either drop files, select files from the list, or choose a source folder.")

        # Optionally switch the media type based on folder names.
        if self.cfg.get("auto_detect_type", False):
            detected = self._detect_media_type_from_paths(files)
            if detected and detected != self._media_type():
                idx = {"tv": 0, "movie": 1, "anime": 2}[detected]
                self.combo.blockSignals(True)
                self.combo.setCurrentIndex(idx)
                self.combo.blockSignals(False)
                self.seg.setCurrentIndex(idx)
                self.cfg["media_type"] = detected
                cfg_save(self.cfg)
                self._update_library_label()
                self.status.showMessage(f"Auto-detected media type: {detected}")
                dst = self._get_destination()

        if not dst:
            return QMessageBox.warning(self, "PathRenamer", "Set destination folder in Settings (tab: Folders).")

        # Matching mode: automatic, or one show forced by its TVDB id
        match_mode, forced_id = self._choose_match_mode()
        if match_mode is None:
            return

        # str(path) -> (title, (source, id)) chosen by the user.
        selected_overrides = {}
        media_type = self._media_type()

        engine = MatchEngine(
            cfg=self.cfg,
            tvmaze=self.tvmaze,
            anidb=self.anidb,
            tmdb=self.tmdb,
            cinemeta=self.cinemeta,
        )

        forced_match = None
        if match_mode == "forced":
            if self.tvmaze:
                try:
                    forced_match = self.tvmaze.get_show_by_tvdb_id(forced_id)
                    if forced_match:
                        log.info(f"Forced match found: {forced_match.get('show')} (TVDB ID: {forced_id})")
                    else:
                        log.warning(f"No show found for TVDB ID: {forced_id}")
                        QMessageBox.warning(self, "Not Found", f"No show found for TVDB ID: {forced_id}")
                        return
                except Exception as e:
                    log.error(f"Failed to lookup TVDB ID {forced_id}: {e}")
                    QMessageBox.warning(self, "Error", f"Failed to lookup TVDB ID: {e}")
                    return
            else:
                QMessageBox.warning(self, "Error", "TVMaze not available for forced match")
                return

            if forced_match:
                for f in files:
                    selected_overrides[str(f)] = (forced_match.get("show", "Unknown"),
                                                  ("tvmaze", forced_match.get("id")))

        else:
            # Normal match: group the files by detected title and let the user
            # confirm the match once per group.
            groups = {}
            for f in files:
                groups.setdefault(derive_title_for(f, media_type), []).append(f)
            log.info(f"Detected {len(groups)} title group(s): {list(groups)}")
            for title, group_files in groups.items():
                matches = engine.search_all_matches(group_files[0], media_type)
                if not matches:
                    continue
                dlg = MatchSelectorDialog(f"{title} — {len(group_files)} file(s)", matches, self)
                if dlg.exec() and dlg.selected_match:
                    selected = dlg.selected_match
                    selected_title = selected.get("show", selected.get("title", ""))
                    selected_id = selected.get("id")
                    selected_src = selected.get("src", "tvmaze")
                    log.info(f"Selected for group '{title}': '{selected_title}' ({selected_src} ID: {selected_id})")
                    for ff in group_files:
                        selected_overrides[str(ff)] = (selected_title, (selected_src, selected_id))

        # Per-file choices start fresh with every preview
        self._title_overrides = dict(selected_overrides)
        self._episode_overrides = {}
        self._start_scan(files, engine, media_type, dst, selected_overrides)

    def _engine(self):
        return MatchEngine(cfg=self.cfg, tvmaze=self.tvmaze, anidb=self.anidb,
                           tmdb=self.tmdb, cinemeta=self.cinemeta)

    def _start_scan(self, files, engine, media_type, dst, selected_overrides=None,
                    episode_overrides=None, replace=None):
        """Match files in a worker thread. replace: {normcase path: row} to
        re-match those rows in place (fixing single rows) instead of a new preview."""
        self._replace_rows = dict(replace or {})
        if not replace:
            self._cands = []
            self.table.clear_all()
        self.table.set_display_options(
            self.cfg.get("show_detected", True),
            self.cfg.get("show_full_path", True),
        )
        self.btn_rename.setEnabled(False)

        self._scanning = True
        self.pbar.setVisible(True)
        self.pbar.setMaximum(len(files))
        self.pbar.setValue(0)
        self.btn_preview.setEnabled(False)
        self._update_summary()
        self._update_details()
        self._update_fix_actions()

        self._thread = QThread()
        self._worker = ScanWorker(files, engine, media_type, dst, selected_overrides, episode_overrides)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(lambda c, t: self.pbar.setValue(c))
        self._worker.candidate_ready.connect(self._on_candidate)
        self._worker.finished.connect(self._on_scan_done)
        self._thread.start()

    def _on_candidate(self, cand):
        row = self._replace_rows.pop(os.path.normcase(str(cand.source)), None) if self._replace_rows else None
        if row is not None and row < self.table.rowCount():
            self.table.replace_candidate(row, cand, self._get_destination())
        else:
            self.table.append_candidate(cand, self._get_destination())
        self._cands = self.table.get_cands()
        self._update_summary()

    def _on_scan_done(self):
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(2000)
        self._scanning = False
        self._replace_rows = {}
        self.pbar.setVisible(False)
        self.btn_preview.setEnabled(True)
        mark_duplicates(self.table.get_cands(), self._get_destination())
        self.table.refresh_rows()
        self._cands = self.table.get_cands()
        self._update_summary()
        self._update_fix_actions()
        if self.table.rowCount() and not self.table.selectedItems() and self._layout_name == "modern":
            self.table.selectRow(0)       # fills the details panel

        total = len(self._cands)
        conflicts = self.table.conflict_count()
        errors = self.table.error_count()

        parts = [f"{total} file(s) matched"]
        if self._scan_note:
            parts.insert(0, self._scan_note)
            self._scan_note = ""
        if conflicts:
            parts.append(f"{conflicts} conflict(s) shown in red")
        if errors:
            parts.append(f"{errors} error(s)")
        self.status.showMessage("  ·  ".join(parts))

        can_rename = total > 0 and (total - errors) > 0
        self.btn_rename.setEnabled(can_rename)

    ACTION_WORDS = ACTION_WORDS

    def _rename_groups(self, cands, dst):
        return rename_groups(cands, dst)

    def _rename(self):
        dst = self._get_destination()
        if not dst:
            QMessageBox.warning(self, "PathRenamer", "No destination folder set.")
            return

        groups = self._rename_groups(self._cands, dst)
        if not groups:
            QMessageBox.warning(self, "PathRenamer", "No files to rename.")
            return

        # A video whose destination already exists is skipped together with
        # its subtitles. Subtitles are checked on their own below, so one
        # existing subtitle doesn't block the video.
        conflicted = [g for g in groups if detect_conflicts([g[0]])]
        if conflicted:
            if self.cfg.get("confirm_conflicts", True):
                reply = QMessageBox.question(
                    self, "Conflicts Detected",
                    f"{len(conflicted)} destination file(s) already exist and will be skipped.\n"
                    "Continue with the remaining files?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
            skip = {id(g) for g in conflicted}
            groups = [g for g in groups if id(g) not in skip]
        if not groups:
            self.status.showMessage("Nothing to rename after skipping conflicts.")
            return

        action = self.cfg.get("rename_action", "move")
        if self.cfg.get("confirm_rename", True):
            n_vids = len(groups)
            n_subs = sum(1 for _, sub_ops in groups for s in sub_ops if not detect_conflicts([s]))
            msg = f"{self.ACTION_WORDS.get(action, self.ACTION_WORDS['move'])[0]} {n_vids} file(s)"
            if n_subs:
                msg += f" and {n_subs} subtitle(s)"
            msg += f" into:\n{dst}\n\nProceed?"
            reply = QMessageBox.question(
                self, "Confirm Rename", msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        renamed = list(self._cands)
        result, msg = self._place_groups(groups, dst, source="manual")
        self.status.showMessage(msg)
        self._after_rename(result, renamed, dst, self.renamer.last_batch_id)
        if result.failed:
            details = "\n".join(f"{o.src.name}: {o.error}" for o in result.failed[:10])
            QMessageBox.warning(self, "Some Renames Failed", details)

        self._cands = []
        self.table.clear_all()
        self.btn_rename.setEnabled(False)
        self._update_summary()
        self._update_details()

    def _place_groups(self, groups, dst, source="manual", cleanup_root=None):
        """Rename (move / copy / hard-link) the groups and clean up empty
        folders under cleanup_root (default: the source folder).
        Returns (result, status message)."""
        result, msg = place_groups(self.renamer, groups, dst, self.cfg.get("rename_action", "move"), source,
                                   cleanup_root or self.src.text().strip(),
                                   self.cfg.get("cleanup_empty_folders", True))
        # Placed files are done; drop them from the list
        self._purge_files_from_list([op.src for op in result.succeeded])
        self._update_undo_buttons()
        return result, msg

    def _undo(self):
        if not self.renamer.can_undo():
            return
        result = self.renamer.undo_last()
        self._report_undo(result)

    def _report_undo(self, result):
        ok_count = len(result.succeeded)
        fail_count = len(result.failed)
        self.status.showMessage(
            f"Undo complete — {ok_count} file(s) restored." +
            (f" {fail_count} failed." if fail_count else "")
        )
        if fail_count:
            details = "\n".join(f"{o.src.name}: {o.error}" for o in result.failed[:10])
            QMessageBox.warning(self, "Some Undos Failed", details)
        self._update_undo_buttons()

    def _show_history(self):
        dlg = HistoryDialog(self.renamer, self)
        dlg.exec()
        if dlg.undo_results:
            self._report_undo(dlg.undo_results[-1])
        self._update_undo_buttons()

    def _update_undo_buttons(self):
        enabled = self.renamer.can_undo()
        self.btn_undo.setEnabled(enabled)
        self.act_undo.setEnabled(enabled)

    def _remove_selected_rows(self):
        self.table.remove_selected()
        self._cands = self.table.get_cands()
        errors = sum(1 for c in self._cands if c.error)
        self.btn_rename.setEnabled(len(self._cands) - errors > 0)
        self._update_summary()
        self._update_details()

    def _purge_files_from_list(self, paths):
        gone = {os.path.normcase(str(p)) for p in paths}
        for i in range(self.file_list.count() - 1, -1, -1):
            item_path = self.file_list.item(i).data(256)
            if item_path is not None and os.path.normcase(str(item_path)) in gone:
                self.file_list.takeItem(i)

    # ── File list ───────────────────────────────────────────────────
    def _add_files_to_list(self, paths):
        existing = {self.file_list.item(i).data(256) for i in range(self.file_list.count())}
        for p in paths:
            if p not in existing:
                item = QListWidgetItem(p.name)
                item.setData(256, p)
                item.setToolTip(str(p))
                self.file_list.addItem(item)

    def _get_selected_files(self):
        selected = self.file_list.selectedItems()
        if selected:
            return [item.data(256) for item in selected]
        return [self.file_list.item(i).data(256) for i in range(self.file_list.count())]

    def _clear_file_list(self):
        self.file_list.clear()
        self._cands = []
        self.table.clear_all()
        self.btn_rename.setEnabled(False)
        self._update_summary()
        self._update_details()

    def _remove_selected_files(self):
        for item in self.file_list.selectedItems():
            self.file_list.takeItem(self.file_list.row(item))

    # ── Presets ──────────────────────────────────────────────────────
    def _fill_presets_menu(self):
        menu = self.presets_menu
        menu.clear()
        presets = self.cfg.get("presets") or {}
        if presets:
            for name in sorted(presets, key=str.lower):
                act = menu.addAction(f"Use “{name}”")
                act.triggered.connect(lambda _=False, n=name: self._apply_preset(n))
        else:
            none = menu.addAction("No presets yet")
            none.setEnabled(False)
        menu.addSeparator()
        menu.addAction("Save Current Settings as Preset…").triggered.connect(self._save_preset)
        if presets:
            delete = menu.addMenu("Delete Preset")
            for name in sorted(presets, key=str.lower):
                delete.addAction(name).triggered.connect(lambda _=False, n=name: self._delete_preset(n))

    def _save_preset(self, name=None):
        if name is None:
            name, ok = QInputDialog.getText(
                self, "Save Preset",
                "Name for these settings (naming formats, library folders, data sources,\n"
                "media type and how files are placed):", QLineEdit.EchoMode.Normal, "")
            name = name.strip()
            if not ok or not name:
                return
            if name in (self.cfg.get("presets") or {}):
                reply = QMessageBox.question(self, "Save Preset", f"Replace the preset “{name}”?",
                                             QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if reply != QMessageBox.StandardButton.Yes:
                    return
        self.cfg.setdefault("presets", {})[name] = {k: self.cfg.get(k, DEFAULTS.get(k)) for k in PRESET_KEYS}
        cfg_save(self.cfg)
        self.status.showMessage(f"Saved preset “{name}”.")

    def _apply_preset(self, name):
        preset = (self.cfg.get("presets") or {}).get(name)
        if not preset:
            return
        for key in PRESET_KEYS:
            if key in preset:
                self.cfg[key] = preset[key]
        cfg_save(self.cfg)
        self._refresh_clients()
        idx = {"tv": 0, "movie": 1, "anime": 2}.get(self.cfg.get("media_type"), 0)
        if self.combo.currentIndex() != idx:
            self.combo.setCurrentIndex(idx)
        self._update_library_label()
        msg = f"Using preset “{name}”."
        if self.table.rowCount():
            msg += " Click Preview to see the names with it."
        self.status.showMessage(msg)

    def _delete_preset(self, name):
        reply = QMessageBox.question(self, "Delete Preset", f"Delete the preset “{name}”?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            (self.cfg.get("presets") or {}).pop(name, None)
            cfg_save(self.cfg)

    # ── Fixing single rows ───────────────────────────────────────────
    def _rows_for_action(self):
        """Selected rows, top to bottom (none while a preview is running)."""
        if self._scanning:
            self.status.showMessage("Wait for the preview to finish first.")
            return []
        return sorted(self.table.selected_rows())

    def _update_fix_actions(self):
        try:
            cands = self.table.get_cands()
            rows = sorted(self.table.selected_rows())
        except RuntimeError:              # window is being destroyed
            return
        has = bool(rows) and not self._scanning
        episodic = has and all(cands[r].media_type != "movie" for r in rows if r < len(cands))
        for act, ok in ((self.act_edit_name, has and len(rows) == 1),
                        (self.act_change_episode, episodic and len(rows) == 1),
                        (self.act_choose_match, has),
                        (self.act_number, episodic),
                        (self.act_move_up, has and rows[0] > 0),
                        (self.act_move_down, has and rows[-1] < len(cands) - 1),
                        (self.act_download_subs, has and any(
                            cands[r].source.suffix.lower() not in SUB_EXT for r in rows if r < len(cands)))):
            act.setEnabled(bool(ok))
        self.btn_fix.setEnabled(has)

    def _table_menu(self, pos):
        menu = QMenu(self)
        for act in (self.act_edit_name, self.act_change_episode, self.act_choose_match, self.act_number):
            menu.addAction(act)
        menu.addSeparator()
        menu.addAction(self.act_move_up); menu.addAction(self.act_move_down)
        menu.addSeparator()
        menu.addAction(self.act_download_subs)
        rm = QAction("Remove From List", menu); rm.triggered.connect(self._remove_selected_rows)
        rm.setEnabled(bool(self.table.selectedItems()) and not self._scanning)
        menu.addAction(rm)
        cp = QAction("Copy", menu); cp.triggered.connect(self.table._copy_selection)
        menu.addAction(cp)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _on_table_double_click(self, row, col):
        if col == 1:
            self._choose_match()
        elif col != STATUS_COL:
            self._edit_name()

    def _edit_name(self):
        rows = self._rows_for_action()
        if len(rows) != 1:
            return
        row = rows[0]
        cand = self.table.get_cands()[row]
        text, ok = QInputDialog.getText(
            self, "Edit New Name",
            "New name inside your library folder (use / between folders):",
            QLineEdit.EchoMode.Normal, cand.dest_rel)
        if not ok or not text.strip():
            return
        rel = _normalize_rel(text.strip().replace("\\", "/"))
        if not rel:
            return
        ext = cand.source.suffix
        if ext and not rel.lower().endswith(ext.lower()):
            rel += ext
        new = dataclasses.replace(cand, dest_rel=rel, error="", conflict=False, confidence=1.0,
                                  manual=True, subtitles=[])
        if cand.source.suffix.lower() not in SUB_EXT:
            self._engine()._subs(cand.source, rel, new)       # subtitles follow the new name
        dest = Path(self._get_destination()) / rel
        new.conflict = dest.exists() and not _same_path(dest, cand.source)
        self.table.replace_candidate(row, new, self._get_destination())
        self._after_rows_changed()

    def _change_episode(self):
        rows = self._rows_for_action()
        if len(rows) != 1:
            return
        cand = self.table.get_cands()[rows[0]]
        eps = cand.episodes or []
        dlg = EpisodeNumbersDialog(self, title=cand.title, season=cand.season if cand.season is not None else 1,
                                   first=eps[0] if eps else 1, last=eps[-1] if eps else None)
        if dlg.exec():
            title, season, episodes = dlg.values()
            self._rematch(rows, title=title, season=season, episodes=episodes)

    def _number_in_order(self):
        rows = self._rows_for_action()
        if not rows:
            return
        first = self.table.get_cands()[rows[0]]
        eps = first.episodes or []
        dlg = EpisodeNumbersDialog(self, title=first.title, season=first.season if first.season is not None else 1,
                                   first=eps[0] if eps else 1, count=len(rows), number_mode=True)
        if dlg.exec():
            title, season, episodes = dlg.values()
            self._rematch(rows, title=title, season=season, episodes=episodes)

    def _choose_match(self):
        rows = self._rows_for_action()
        if not rows:
            return
        cands = self.table.get_cands()
        first = cands[rows[0]]
        media_type = self._media_type()
        start = first.title or derive_title_for(first.source, media_type)
        title, ok = QInputDialog.getText(self, "Choose a Different Match",
                                         "Search for the show or movie:", QLineEdit.EchoMode.Normal, start)
        if not ok or not title.strip():
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            matches = self._engine().search_title(title.strip(), media_type)
        finally:
            QApplication.restoreOverrideCursor()
        if not matches:
            QMessageBox.information(self, "Choose a Different Match", f"Nothing found for “{title.strip()}”.")
            return
        dlg = MatchSelectorDialog(f"{title.strip()} — {len(rows)} file(s)", matches, self)
        if dlg.exec() and dlg.selected_match:
            sel = dlg.selected_match
            chosen = (sel.get("show", sel.get("title", "")), (sel.get("src", "tvmaze"), sel.get("id")))
            self._rematch(rows, match=chosen)

    def _rematch(self, rows, title=None, match=None, season=None, episodes=None):
        """Look the given rows up again with the user's choices, in place.
        title: a show name typed by the user; match: (title, (source, id)) picked
        in the match chooser; episodes: one list of episode numbers per row."""
        cands = self.table.get_cands()
        paths = [cands[r].source for r in rows]
        for i, (row, path) in enumerate(zip(rows, paths)):
            key = str(path)
            if match:
                self._title_overrides[key] = match
            elif title and title != cands[row].title:
                self._title_overrides[key] = (title, None)
            if episodes:
                self._episode_overrides[key] = (season, episodes[min(i, len(episodes) - 1)])
        sel = {str(p): self._title_overrides[str(p)] for p in paths if str(p) in self._title_overrides}
        eps = {str(p): self._episode_overrides[str(p)] for p in paths if str(p) in self._episode_overrides}
        replace = {os.path.normcase(str(p)): r for p, r in zip(paths, rows)}
        self._start_scan(paths, self._engine(), self._media_type(), self._get_destination(),
                         sel, eps, replace=replace)

    def _move_rows(self, delta):
        rows = self._rows_for_action()
        if rows:
            self.table.move_rows(rows, delta)
            self._after_rows_changed()

    def _after_rows_changed(self):
        mark_duplicates(self.table.get_cands(), self._get_destination())
        self.table.refresh_rows()
        self._cands = self.table.get_cands()
        self.btn_rename.setEnabled(any(not c.error for c in self._cands))
        self._update_summary()
        self._update_details()
        self._update_fix_actions()

    # ── Drag & drop ─────────────────────────────────────────────────
    # ── Watch folder ─────────────────────────────────────────────────
    WATCH_SURE = 0.85                 # matches at least this sure are renamed on their own (Pro)

    def _apply_automation(self):
        """Start, move or stop the watch folder to match the settings."""
        folder = (self.cfg.get("watch_folder") or "").strip()
        if self.cfg.get("watch_enabled") and folder:
            current = self.watcher.folder()
            if current is None or not _same_path(current, folder):
                self.watcher.start(folder, include_existing=bool(self.cfg.get("watch_include_existing")))
        elif self.watcher.is_running():
            self.watcher.stop()
        self._update_watch_label()

    def _update_watch_label(self):
        folder = self.watcher.folder()
        self.watch_label.setVisible(folder is not None)
        self.act_watch.setChecked(folder is not None)
        if self.tray is not None:
            self.tray_watch.setChecked(folder is not None)
            self.tray.setToolTip(f"PathRenamer: watching {folder}" if folder is not None else "PathRenamer")
        if folder is not None:
            self.watch_label.setText(f"Watching {folder.name or folder}")
            self.watch_label.setToolTip(f"New videos in {folder} are matched as soon as they finish "
                                        "downloading (Tools → Watch Folder).")

    def _toggle_watch(self, checked):
        if checked and not (self.cfg.get("watch_folder") or "").strip():
            self.act_watch.setChecked(False)
            self._settings("Automation")
            return
        self.cfg["watch_enabled"] = bool(checked)
        cfg_save(self.cfg)
        self._apply_automation()

    def _check_watch_now(self):
        if self.watcher.is_running():
            self.watcher.check_now()
            self.status.showMessage(f"Checking {self.watcher.folder()} for new files…")
        else:
            self.status.showMessage("The watch folder is off: turn it on under Tools → Watch Folder.")

    def _on_watch_error(self, message):
        log.warning(message)
        self.status.showMessage(message)

    def _on_watch_files(self, paths):
        log.info(f"Watch folder: {len(paths)} new file(s): {[p.name for p in paths]}")
        self._watch_queue.extend(paths)
        self._process_watch_queue()

    def _watch_media_type(self, path, cfg=None):
        return detect_media_type(path, (cfg or self.cfg).get("watch_media_type", "auto"))

    def _watch_engine(self):
        """The watch folder matches next to a normal preview, so it uses its
        own connections."""
        if self.cfg.get("use_anidb", True) and self._watch_anidb is None:
            self._watch_anidb = AniDBClient()
        return make_engine(self.cfg, self._watch_anidb)

    def _process_watch_queue(self):
        """Match the next batch of new files (one kind at a time) in the background."""
        if self._watch_busy or not self._watch_queue:
            return
        queue, self._watch_queue = self._watch_queue, []
        cfg, engine = dict(self.cfg), self._watch_engine()
        self._watch_busy = True

        def work():
            batch, media_type, later = [], None, []
            for path in queue:
                if not path.exists():
                    continue
                kind = self._watch_media_type(path, cfg)
                media_type = media_type or kind
                (batch if kind == media_type else later).append(path)
            dst = (cfg.get(f"root_{media_type}") or "").strip() if media_type else ""
            cands = []
            if batch:
                worker = ScanWorker(batch, engine, media_type, dst)
                worker.candidate_ready.connect(cands.append, Qt.ConnectionType.DirectConnection)
                worker.run()
            return media_type, dst, cands, later
        self.jobs.run(work, self._on_watch_matched)

    def _on_watch_matched(self, result, error):
        self._watch_busy = False
        try:
            if error is not None:
                self.status.showMessage(f"Watch folder: {error}")
                return
            media_type, dst, cands, later = result
            self._watch_queue = later + self._watch_queue
            self._handle_watch_matches(media_type, dst, cands)
        finally:
            self._process_watch_queue()

    def _handle_watch_matches(self, media_type, dst, cands):
        if not cands:
            return
        mark_duplicates(cands, dst)
        wanted = bool(self.cfg.get("watch_auto_rename"))
        allowed = wanted and self.license.allows("watch_auto_rename")
        sure = [c for c in cands if allowed and dst and not c.error and not c.conflict
                and c.confidence >= self.WATCH_SURE and c.source.suffix.lower() not in SUB_EXT]
        notes, placed = [], set()
        if sure:
            result, msg = self._place_groups(self._rename_groups(sure, dst), dst, source="watch",
                                             cleanup_root=self.watcher.folder())
            failed = {os.path.normcase(str(op.src)) for op in result.failed}
            renamed = [c for c in sure if os.path.normcase(str(c.source)) not in failed]
            placed = {id(c) for c in renamed}
            notes.append(f"Watch folder: {msg}")
            self._update_summary()
            self._after_rename(result, renamed, dst, self.renamer.last_batch_id)
            if renamed:
                self._notify("New downloads renamed", f"{len(renamed)} file(s) went into your library.")
        rest = [c for c in cands if id(c) not in placed]
        if rest:
            shown = self._show_watch_candidates(rest, media_type, dst)
            where = "in the preview" if shown else "in the file list"
            why = " (automatic renaming is part of PathRenamer Pro)" if wanted and not allowed else ""
            notes.append(f"{len(rest)} new file(s) to check {where}{why}")
            self._notify("New downloads", f"{len(rest)} new file(s) to check in PathRenamer.")
        self.status.showMessage(" · ".join(notes))

    def _show_watch_candidates(self, cands, media_type, dst):
        """New files that need a look go straight into the preview when it is
        empty; otherwise into the file list, for the next preview."""
        self._add_files_to_list([c.source for c in cands])
        if self._scanning or self.table.rowCount():
            return False
        if self._media_type() != media_type:
            self.combo.setCurrentIndex(self.MEDIA_TYPES.index(media_type))
        self._title_overrides, self._episode_overrides = {}, {}
        self.table.set_display_options(self.cfg.get("show_detected", True), self.cfg.get("show_full_path", True))
        self.table.populate(cands, dst)
        self._cands = self.table.get_cands()
        self.btn_rename.setEnabled(any(not c.error for c in self._cands))
        self._update_summary()
        self._update_details()
        self._update_fix_actions()
        return True

    # ── After renaming: subtitles and the media server ───────────────
    def _subtitle_client(self):
        key = (self.cfg.get("opensubtitles_api_key") or "").strip() or OPENSUBTITLES_APP_KEY
        return OpenSubtitlesClient(key, self.cfg.get("opensubtitles_username", ""),
                                   unprotect(self.cfg.get("opensubtitles_password", "")), APP_VERSION)

    def _media_server(self):
        """The Plex, Jellyfin, Emby or Kodi client the settings choose."""
        return server_from_config(self.cfg, unprotect)

    def _subtitle_languages(self):
        return parse_languages(self.cfg.get("subs_languages", "")) or ["eng"]

    def _after_rename(self, result, cands, dst, batch_id):
        """Pro: download missing subtitles, then ask the media server to scan, in the
        background. Downloads join the rename's undo step."""
        subs_on = bool(self.cfg.get("subs_auto")) and self.license.allows("subs_auto")
        plex_on = bool(self.cfg.get("plex_auto_scan")) and self.license.allows("plex_auto_scan")
        if not result.succeeded or not (subs_on or plex_on):
            return
        placed = {os.path.normcase(str(op.src)): op.dst for op in result.succeeded}
        videos = [(c, placed[os.path.normcase(str(c.source))]) for c in cands
                  if c.source.suffix.lower() not in SUB_EXT and os.path.normcase(str(c.source)) in placed]
        subtitles = None
        if subs_on and videos:
            subtitles = {"client": self._subtitle_client(), "languages": self._subtitle_languages(),
                         "hearing_impaired": bool(self.cfg.get("subs_hearing_impaired"))}
        server = self._media_server() if plex_on else None
        if subtitles is None and server is None:
            return
        label = server.label if server is not None else ""
        media_type = cands[0].media_type if cands else self._media_type()
        paths = [op.dst for op in result.succeeded]

        def done(res, error):
            if error is not None:
                self.status.showMessage(f"After renaming: {error}")
                return
            downloaded, scanned, problems = res
            batch = self.renamer.history.get(batch_id) if batch_id else None
            if downloaded and batch is not None:
                if batch.get("undone"):              # the rename was undone meanwhile
                    for f in downloaded:
                        Path(f).unlink(missing_ok=True)
                    downloaded = []
                else:
                    self.renamer.history.add_ops(batch_id, [("OpenSubtitles", f, "download") for f in downloaded])
            parts = []
            if downloaded:
                parts.append(f"downloaded {len(downloaded)} subtitle(s)")
            if scanned:
                parts.append(f"{label} is scanning {', '.join(scanned)}")
            parts += problems
            if parts:
                log.info("After renaming: " + " · ".join(parts))
                self.status.showMessage("After renaming: " + " · ".join(parts))
        self.jobs.run(lambda: run_after_rename(videos, paths, media_type, subtitles, server), done)

    def _download_subtitles(self):
        """Free: download subtitles for the selected rows, next to the
        videos, so they are renamed with them."""
        rows = self._rows_for_action()
        cands = self.table.get_cands()
        videos = [cands[r] for r in rows if r < len(cands) and cands[r].source.suffix.lower() not in SUB_EXT
                  and cands[r].source.exists()]
        if not videos:
            self.status.showMessage("Select videos in the preview first.")
            return
        client = self._subtitle_client()
        if not getattr(client, "api_key", True):
            QMessageBox.information(self, "Subtitles", "Downloading subtitles needs an OpenSubtitles API "
                                    "key. Add one under Settings → Subtitles: it's free.")
            self._settings("Subtitles")
            return
        languages = self._subtitle_languages()
        hearing_impaired = bool(self.cfg.get("subs_hearing_impaired"))
        self.act_download_subs.setEnabled(False)
        self.status.showMessage(f"Looking for subtitles for {len(videos)} file(s)…")

        def work():
            client.login()
            saved, problems = [], []
            for c in videos:
                try:
                    saved += fetch_subtitles(client, c.source, languages, title=c.title, year=c.year or None,
                                             season=c.season, episodes=c.episodes, media_type=c.media_type,
                                             hearing_impaired=hearing_impaired)
                except OpenSubtitlesError as e:      # quota or sign-in: the rest would fail too
                    problems.append(str(e))
                    break
                except OSError as e:
                    problems.append(f"{c.source.name}: {e.strerror or e}")
            return saved, problems

        def done(res, error):
            self._update_fix_actions()
            if error is not None:
                self.status.showMessage("No subtitles downloaded.")
                QMessageBox.warning(self, "Subtitles", str(error))
                return
            saved, problems = res
            message = (f"Downloaded {len(saved)} subtitle(s)" if saved else
                       "No new subtitles found in your languages")
            self.status.showMessage(message)
            if saved and not self._scanning:
                # look those rows up again, so the new subtitles go with their videos
                again = [i for i, c in enumerate(self.table.get_cands())
                         if any(os.path.normcase(str(f)).startswith(os.path.normcase(str(c.source.with_suffix("")) + "."))
                                for f in saved)]
                if again:
                    self._scan_note = message
                    self._rematch(again)
            if problems:
                QMessageBox.warning(self, "Subtitles", "\n".join(problems[:5]))
        self.jobs.run(work, done)

    def _scan_server_now(self):
        """Free: ask the media server to scan every movie and TV library."""
        server = self._media_server()
        if not server.configured:
            QMessageBox.information(self, "Media server", "Connect PathRenamer to your media server first "
                                    "(Plex, Jellyfin, Emby or Kodi): Settings → Automation.")
            self._settings("Automation")
            return
        self.status.showMessage(f"Asking {server.label} to scan…")

        def done(titles, error):
            if error is not None:
                self.status.showMessage(f"{server.label}: {error}")
            else:
                self.status.showMessage(f"{server.label} is scanning {', '.join(titles)}." if titles else
                                        f"{server.label} has no movie or TV libraries to scan.")
        self.jobs.run(server.scan_all, done)

    def _check_license(self):
        """Re-check a Pro key once a week, quietly in the background."""
        if self._closed:
            return
        if self.license.touch():             # (setting the clock back doesn't stretch the trial)
            cfg_save(self.cfg)
        if not self.license.needs_check():
            return
        copy = License(dict(self.cfg))

        def done(changed, error):
            if error is None and changed:
                for k in LICENSE_KEYS:
                    if k in copy.cfg:
                        self.cfg[k] = copy.cfg[k]
                    else:
                        self.cfg.pop(k, None)
                cfg_save(self.cfg)
        self.jobs.run(copy.check, done)

    # ── Tray and Windows start ──────────────────────────────────────
    def _apply_windows_options(self):
        """Settings → Options → Tray and Windows Start."""
        try:
            startup.sync(bool(self.cfg.get("start_with_windows")))
        except OSError as e:
            log.warning(f"Couldn't change the Windows start entry: {e}")
            self.status.showMessage(f"Couldn't set PathRenamer to start with Windows: {e}")
        self._update_tray()

    def _tray_wanted(self):
        return bool(self.cfg.get("tray_on_close") or self.cfg.get("start_with_windows") or self._tray_forced)

    def _tray_active(self):
        """Closing the window hides it only when the tray icon can bring it back."""
        return self.tray is not None and self.tray.isVisible()

    def _update_tray(self):
        if not self._tray_wanted() or not QSystemTrayIcon.isSystemTrayAvailable():
            if self.tray is not None:
                self.tray.hide()
            return
        if self.tray is None:
            self._build_tray()
        self.tray.show()
        self._update_watch_label()

    def _build_tray(self):
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip("PathRenamer")
        menu = QMenu(self)
        act = QAction("Open PathRenamer", self)
        act.triggered.connect(self.show_window)
        menu.addAction(act)
        menu.addSeparator()
        self.tray_watch = QAction("Watch Folder", self)
        self.tray_watch.setCheckable(True)
        self.tray_watch.triggered.connect(self._toggle_watch)
        menu.addAction(self.tray_watch)
        act = QAction("Check the Watch Folder Now", self)
        act.triggered.connect(self._check_watch_now)
        menu.addAction(act)
        act = QAction("Check for Updates…", self)
        act.triggered.connect(self.check_for_updates)
        menu.addAction(act)
        menu.addSeparator()
        act = QAction("Quit PathRenamer", self)
        act.triggered.connect(self.quit_app)
        menu.addAction(act)
        self.tray.setContextMenu(menu)
        self._tray_menu = menu
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.messageClicked.connect(self.show_window)

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_window()

    def start_in_tray(self):
        """Started by Windows with --tray: only the tray icon, no window.
        False when there is no tray to start in."""
        self._tray_forced = True
        self._update_tray()
        return self._tray_active()

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def listen_for_second_starts(self, name):
        """Starting PathRenamer again (from the Start menu) shows this window
        instead of running a second copy that watches the same folder."""
        QLocalServer.removeServer(name)                    # left over after a crash
        self._instance_server = QLocalServer(self)
        if not self._instance_server.listen(name):
            log.debug(f"Couldn't listen for second starts: {self._instance_server.errorString()}")
            return
        self._instance_server.newConnection.connect(self._on_second_start)

    def _on_second_start(self):
        while self._instance_server.hasPendingConnections():
            conn = self._instance_server.nextPendingConnection()
            conn.disconnected.connect(conn.deleteLater)
            conn.disconnectFromServer()
        self.show_window()

    # ── Updates ─────────────────────────────────────────────────────
    def check_for_updates(self):
        """Help → Check for Updates: ask GitHub now and say what was found."""
        self._run_update_check(manual=True)

    def _auto_check_updates(self):
        """Once a day, quietly in the background (Settings → About can turn it off)."""
        if self._closed or not self.cfg.get("check_updates", True) or self._update_busy:
            return
        if time.time() - float(self.cfg.get("update_checked_at") or 0) < 20 * 3600:
            return
        self._run_update_check(manual=False)

    def _update_clicked(self):
        """The "Update to …" button: one click downloads and installs it."""
        if self._update_release is not None and updates.is_newer(self._update_release.version, APP_VERSION):
            self._open_update_dialog(self._update_release, start=True)
        else:                                  # found in an earlier session: get the download details
            self._run_update_check(manual=True, start=True)

    def _run_update_check(self, manual=False, start=False):
        if self._update_busy:
            return
        self._update_busy = True
        if manual:
            self.status.showMessage("Checking for updates…")

        def done(release, error):
            self._update_busy = False
            if error is not None:
                if manual:
                    self.status.showMessage(f"Couldn't check for updates: {error}")
                    QMessageBox.warning(self, "Check for Updates", str(error))
                return
            self._update_release = release
            self.cfg["update_checked_at"] = time.time()
            self.cfg["update_available"] = release.version if release else ""
            cfg_save(self.cfg)
            self._show_update_button()
            if release is None:
                if manual:
                    self.status.showMessage(f"PathRenamer is up to date ({APP_VERSION}).")
                    QMessageBox.information(self, "Check for Updates",
                                            f"You have the latest version of PathRenamer ({APP_VERSION}).")
                return
            if manual:
                self._open_update_dialog(release, start=start)
            elif release.version != self.cfg.get("update_skip_version"):
                self.status.showMessage(f"PathRenamer {release.version} is available: click “Update to "
                                        f"{release.version}” at the bottom right to install it.")
                self._notify("Update available", f"PathRenamer {release.version} is ready to install.")
        self.jobs.run(lambda: updates.check(APP_VERSION), done)

    def _show_update_button(self):
        version = self.cfg.get("update_available") or ""
        show = (bool(version) and updates.is_newer(version, APP_VERSION)
                and version != self.cfg.get("update_skip_version"))
        self.update_btn.setText(f"Update to {version}")
        self.update_btn.setVisible(show)

    def _open_update_dialog(self, release, start=False):
        d = UpdateDialog(release, self, start=start)
        d.exec()
        if d.skipped:
            self.cfg["update_skip_version"] = release.version
            cfg_save(self.cfg)
            self._show_update_button()
            self.status.showMessage(f"Version {release.version} skipped. Help → Check for Updates still finds it.")
        if d.installing:
            self.quit_app()                    # the new version starts on its own

    def _notify(self, title, text):
        """A message from the tray when the window isn't on screen."""
        tray = getattr(self, "tray", None)
        if tray is not None and tray.isVisible() and not self.isVisible():
            tray.showMessage(title, text)

    def quit_app(self):
        """Really quit (also when closing the window would keep PathRenamer in the tray)."""
        self._quitting = True
        self.close()
        QApplication.instance().quit()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.drop_zone.set_hover(True)

    def dragLeaveEvent(self, event):
        self.drop_zone.set_hover(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self.drop_zone.set_hover(False)
        files = []
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.is_file():
                # Videos and subtitles only, not .nfo/.txt/.jpg dropped with them
                if path.suffix.lower() in VIDEO_EXT or path.suffix.lower() in SUB_EXT:
                    files.append(path)
            elif path.is_dir():
                folder_files = scan_folder(str(path))
                if not folder_files:
                    folder_files = scan_folder(str(path), include_subtitles=True)
                files.extend(folder_files)
        if files:
            self._add_files_to_list(files)
            self.status.showMessage(f"{len(files)} file(s) added. Select files and click Preview.")


# ═══════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════

def _instance_name():
    """One PathRenamer per Windows user."""
    import getpass
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return "PathRenamer-" + "".join(ch for ch in user if ch.isalnum())[:40]


def _show_running_copy(name):
    """True when PathRenamer already runs (it is asked to show its window)."""
    sock = QLocalSocket()
    sock.connectToServer(name)
    if not sock.waitForConnected(500):
        return False
    sock.write(b"show")
    sock.flush()
    sock.waitForBytesWritten(500)
    sock.disconnectFromServer()
    return True


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("PathRenamer")
    app.setApplicationVersion(APP_VERSION)
    name = _instance_name()
    if _show_running_copy(name):
        sys.exit(0)
    app.setQuitOnLastWindowClosed(False)       # the tray can keep PathRenamer running; closing quits otherwise
    w = MainWindow()
    w.listen_for_second_starts(name)
    if not (startup.TRAY_ARG in sys.argv[1:] and w.start_in_tray()):
        w.show()
    sys.exit(app.exec())
