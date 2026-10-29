"""Start PathRenamer in the tray when Windows starts.

Windows starts the programs listed under
HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run when the
user signs in. PathRenamer adds itself there as "PathRenamer.exe --tray" when
"Start PathRenamer when Windows starts" is on. Only PathRenamer.exe (installed
or portable) can do this, not a copy run from the source code.
"""
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "PathRenamer"
TRAY_ARG = "--tray"


def supported(frozen=None, platform=None) -> bool:
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    return (platform or sys.platform) == "win32" and bool(frozen)


def command(exe=None) -> str:
    """What Windows runs: this PathRenamer.exe, starting in the tray."""
    return f'"{Path(exe or sys.executable)}" {TRAY_ARG}'


class WindowsRegistry:
    """The Run value in the current user's registry."""

    def get(self) -> str:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                return str(winreg.QueryValueEx(k, NAME)[0])
        except OSError:
            return ""

    def set(self, value: str):
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, value)

    def delete(self):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, NAME)
        except OSError:
            pass


def sync(enabled: bool, registry=None, exe=None, frozen=None, platform=None) -> bool:
    """Make Windows start this PathRenamer.exe (or not) to match the setting.
    Also points the entry at this exe again after it moved. Returns whether
    anything changed; raises OSError when the registry can't be written."""
    if not supported(frozen, platform):
        return False
    registry = registry or WindowsRegistry()
    current = registry.get()
    if enabled:
        wanted = command(exe)
        if current != wanted:
            registry.set(wanted)
            return True
    elif current:
        registry.delete()
        return True
    return False
