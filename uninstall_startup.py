"""
uninstall_startup.py — Remove VoiceTyper from system startup.

  Windows : removes the Registry entry under HKCU\\...\\Run
  macOS   : unloads and deletes the LaunchAgent plist
  Linux   : deletes the XDG autostart .desktop file
"""

import os
import sys
from pathlib import Path

APP_NAME    = "VoiceTyper"
PLIST_LABEL = "com.voicetyper"


def _uninstall_windows() -> None:
    import winreg
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        )
        winreg.DeleteValue(key, APP_NAME)
        winreg.CloseKey(key)
        print("Done! VoiceTyper removed from startup.")
    except FileNotFoundError:
        print("VoiceTyper was not registered in startup.")


def _uninstall_macos() -> None:
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"
    if plist_path.exists():
        os.system(f"launchctl unload '{plist_path}'")
        plist_path.unlink()
        print("Done! VoiceTyper removed from startup.")
    else:
        print("VoiceTyper was not registered in startup.")


def _uninstall_linux() -> None:
    desktop_path = Path.home() / ".config" / "autostart" / "voicetyper.desktop"
    if desktop_path.exists():
        desktop_path.unlink()
        print("Done! VoiceTyper removed from startup.")
    else:
        print("VoiceTyper was not registered in startup.")


if os.name == "nt":
    _uninstall_windows()
elif sys.platform == "darwin":
    _uninstall_macos()
else:
    _uninstall_linux()
