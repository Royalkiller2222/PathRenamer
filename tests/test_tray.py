"""The tray icon, closing to the tray, one running copy, and starting with Windows."""
import os

import pytest

import startup
from conftest import wait_until


class FakeRegistry:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value

    def delete(self):
        self.value = ""


def test_windows_start_entry():
    reg = FakeRegistry()
    exe = r"C:\Program Files\PathRenamer\PathRenamer.exe"
    win = dict(frozen=True, platform="win32", registry=reg)
    assert startup.sync(True, exe=exe, **win) and reg.value == f'"{exe}" --tray'
    assert not startup.sync(True, exe=exe, **win)                      # already there
    assert startup.sync(True, exe=r"D:\Tools\PathRenamer.exe", **win)  # the exe moved
    assert reg.value == r'"D:\Tools\PathRenamer.exe" --tray'
    assert startup.sync(False, exe=exe, **win) and reg.value == ""
    assert not startup.sync(True, exe=exe, frozen=False, platform="win32", registry=reg)   # from source
    assert reg.value == "" and not startup.supported(frozen=True, platform="linux")


@pytest.fixture
def tray_ok(main, monkeypatch):
    monkeypatch.setattr(main.QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))


def test_closing_keeps_running_in_the_tray(make_window, tray_ok, monkeypatch):
    w = make_window(tray_on_close=True)
    w.show()
    assert w.tray is not None and w.tray.isVisible()
    shown = []
    monkeypatch.setattr(w.tray, "showMessage", lambda *a: shown.append(a))
    w.close()
    assert not w.isVisible() and w.tray.isVisible()
    assert shown and make_window.saved["tray_hint_shown"] is True        # said once
    w.show_window()
    assert w.isVisible()
    w.close()
    assert len(shown) == 1
    w._quitting = True                                                   # Quit: really closes
    w.close()
    assert not w.tray.isVisible()


def test_without_a_tray_closing_quits(main, make_window, monkeypatch):
    monkeypatch.setattr(main.QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: False))
    w = make_window(tray_on_close=True)
    w.show()
    w.close()
    assert w.tray is None and not w.isVisible()


def test_windows_start_opens_only_the_tray(make_window, tray_ok):
    w = make_window()
    assert w.tray is None
    assert w.start_in_tray() and w.tray.isVisible() and not w.isVisible()


def test_tray_menu_follows_the_watch_folder(make_window, tray_ok, tmp_path):
    w = make_window(tray_on_close=True, watch_enabled=True, watch_folder=str(tmp_path))
    assert w.tray_watch.isChecked() and str(tmp_path) in w.tray.toolTip()
    w.tray_watch.trigger()
    assert not w.watcher.is_running() and not w.tray_watch.isChecked() and not w.act_watch.isChecked()
    assert w.tray.toolTip() == "PathRenamer"


def test_messages_only_while_the_window_is_hidden(make_window, tray_ok, monkeypatch):
    w = make_window(tray_on_close=True)
    messages = []
    monkeypatch.setattr(w.tray, "showMessage", lambda *a: messages.append(a))
    w._notify("New downloads renamed", "2 file(s) went into your library.")
    w.show()
    w._notify("Not shown", "the window is open")
    assert messages == [("New downloads renamed", "2 file(s) went into your library.")]


def test_settings_switch_the_tray_on(main, make_window, tray_ok, monkeypatch):
    w = make_window()
    d = main.SettingsDialog(w.cfg, w)
    assert not d.start_with_windows.isEnabled()                          # only PathRenamer.exe can
    d.tray_on_close.setChecked(True)
    d._save()
    assert d.cfg["tray_on_close"] is True
    synced = []
    monkeypatch.setattr(main.startup, "sync", lambda on: synced.append(on))
    w.cfg = d.cfg
    w._apply_windows_options()
    assert w.tray.isVisible() and synced == [False]


def test_a_second_start_shows_the_running_window(main, make_window, qapp):
    w = make_window()
    name = f"PathRenamer-test-{os.getpid()}"
    w.listen_for_second_starts(name)
    try:
        assert main._show_running_copy(name)
        assert wait_until(qapp, w.isVisible)
    finally:
        w._instance_server.close()
    assert not main._show_running_copy(name + "-nobody")
