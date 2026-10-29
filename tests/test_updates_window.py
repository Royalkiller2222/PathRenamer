"""Updates in the window: a quiet daily check, Help → Check for Updates, and
the one-click update."""
import pytest

import updates
from conftest import wait_until
from updates import Asset, Release, UpdateError


def _release(version="9.0.0"):
    url = updates.DOWNLOAD_PREFIX + f"V{version}/"
    return Release(version=version, tag="V" + version, notes="**New:** everything.",
                   page_url="https://github.com/Royalkiller2222/PathRenamer/releases/tag/V" + version,
                   installer=Asset(f"PathRenamer-Setup-{version}.exe", url + f"PathRenamer-Setup-{version}.exe", 10),
                   portable=Asset("PathRenamer.exe", url + "PathRenamer.exe", 10))


@pytest.fixture
def run_dialogs(main, qapp, monkeypatch):
    """UpdateDialog.exec() without blocking: show it and let it run until it closes."""
    shown = []

    def run(self):
        shown.append(self)
        self.show()
        wait_until(qapp, lambda: not self.isVisible(), timeout=5)
        return self.result()
    monkeypatch.setattr(main.UpdateDialog, "exec", run)
    return shown


def test_daily_check_shows_the_update_button(make_window, qapp, monkeypatch):
    calls = []
    monkeypatch.setattr(updates, "check", lambda current: calls.append(current) or _release())
    w = make_window(check_updates=True)
    assert w.update_btn.isHidden()
    w._auto_check_updates()
    assert wait_until(qapp, lambda: not w._update_busy)
    assert calls and w.update_btn.text() == "Update to 9.0.0" and not w.update_btn.isHidden()
    assert make_window.saved["update_available"] == "9.0.0" and make_window.saved["update_checked_at"]
    assert "9.0.0 is available" in w.status.currentMessage()
    w._auto_check_updates()                                    # not again the same day
    assert len(calls) == 1


def test_no_automatic_check_when_switched_off(make_window, monkeypatch):
    monkeypatch.setattr(updates, "check", lambda current: pytest.fail("checked anyway"))
    w = make_window(check_updates=False)
    w._auto_check_updates()
    assert not w._update_busy


def test_one_click_downloads_installs_and_restarts(make_window, qapp, monkeypatch, tmp_path, run_dialogs):
    w = make_window()
    w._update_release = _release()
    w.cfg["update_available"] = "9.0.0"
    w._show_update_button()
    started, quit_calls = [], []

    def download(asset, progress=None, should_cancel=None, **kw):
        progress(5, 10)
        progress(10, 10)
        path = tmp_path / asset.name
        path.write_bytes(b"new version")
        return path
    monkeypatch.setattr(updates, "install_kind", lambda: "installer")
    monkeypatch.setattr(updates, "download", download)
    monkeypatch.setattr(updates, "start_update", lambda path, kind: started.append((path.name, kind)))
    monkeypatch.setattr(w, "quit_app", lambda: quit_calls.append(True))
    w.update_btn.click()                                        # the one click
    assert started == [("PathRenamer-Setup-9.0.0.exe", "installer")] and quit_calls == [True]
    assert run_dialogs[0].installing


def test_a_failed_download_can_be_tried_again(main, qapp, monkeypatch):
    def broken(asset, **kw):
        raise UpdateError("The download didn't match what GitHub published, so it was deleted.")
    monkeypatch.setattr(updates, "download", broken)
    monkeypatch.setattr(updates, "start_update", lambda *a: pytest.fail("installed a broken download"))
    d = main.UpdateDialog(_release(), kind="portable")
    d.update_now()
    assert wait_until(qapp, lambda: not d._busy)
    assert "didn't match" in d.message.text() and d.btn_update.text() == "Try Again"
    assert not d.installing and d.btn_update.isEnabled()


def test_a_source_copy_opens_the_download_page(main, monkeypatch):
    opened = []
    monkeypatch.setattr(main.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    d = main.UpdateDialog(_release(), kind="source")
    assert d.btn_update.text() == "Open Download Page"
    d.update_now()
    assert opened == [_release().page_url]


def test_check_by_hand(main, make_window, qapp, monkeypatch, run_dialogs):
    from PyQt6.QtWidgets import QMessageBox
    said = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: said.append(a[2]))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: said.append(a[2]))
    w = make_window()
    monkeypatch.setattr(updates, "check", lambda current: None)
    w.check_for_updates()
    assert wait_until(qapp, lambda: said)
    assert "latest version" in said[-1] and w.update_btn.isHidden()
    monkeypatch.setattr(updates, "check", lambda current: (_ for _ in ()).throw(UpdateError("offline")))
    w.check_for_updates()
    assert wait_until(qapp, lambda: len(said) == 2) and said[-1] == "offline"
    # a newer version: the dialog opens; Skip This Version hides the button
    monkeypatch.setattr(updates, "check", lambda current: _release())
    monkeypatch.setattr(main.UpdateDialog, "exec", lambda self: self._skip())
    w.check_for_updates()
    assert wait_until(qapp, lambda: w.cfg.get("update_skip_version") == "9.0.0")
    assert w.update_btn.isHidden()


def test_about_page_has_the_update_switch(main, make_window):
    w = make_window()
    d = main.SettingsDialog(w.cfg, w, page="About")
    assert d.btn_check_updates.isVisibleTo(d) and d.check_updates_chk.isChecked() is False
    d.check_updates_chk.setChecked(True)
    d._save()
    assert d.cfg["check_updates"] is True
    alone = main.SettingsDialog(dict(main.DEFAULTS))
    assert not alone.btn_check_updates.isVisibleTo(alone)


def test_a_closed_window_checks_nothing(make_window, monkeypatch):
    """(Its timers can still fire after it closed.)"""
    monkeypatch.setattr(updates, "check", lambda current: pytest.fail("checked after closing"))
    w = make_window(check_updates=True)
    w._quitting = True
    w.close()
    w._auto_check_updates()
    w._check_license()
    assert not w._update_busy and not w._update_timer.isActive()
