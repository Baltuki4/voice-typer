"""
install_startup.py — Add VoiceTyper to system startup.

  Windows : adds a Registry entry under HKCU\\...\\Run
  macOS   : installs a LaunchAgent plist in ~/Library/LaunchAgents/
  Linux   : creates an XDG autostart .desktop file in ~/.config/autostart/

Run once. After this, VoiceTyper will launch automatically every time you log in.
"""

import os
import sys
from pathlib import Path

APP_NAME    = "VoiceTyper"
PLIST_LABEL = "com.voicetyper"

project_dir = Path(__file__).resolve().parent
script_path = project_dir / "voice_typer.py"


def _find_python() -> Path:
    """Return the best Python executable to use (prefer project venv)."""
    if os.name == "nt":
        venv_pythonw = project_dir / ".venv" / "Scripts" / "pythonw.exe"
        venv_python  = project_dir / ".venv" / "Scripts" / "python.exe"
        if venv_pythonw.exists():
            return venv_pythonw
        if venv_python.exists():
            return venv_python
        fallback = Path(sys.executable).parent / "pythonw.exe"
        return fallback if fallback.exists() else Path(sys.executable)
    else:
        venv_python = project_dir / ".venv" / "bin" / "python"
        return venv_python if venv_python.exists() else Path(sys.executable)


def _install_windows(python: Path) -> None:
    import winreg
    command = f'"{python}" "{script_path}"'
    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0,
        winreg.KEY_SET_VALUE,
    )
    winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command)
    winreg.CloseKey(key)
    print(f"Done! VoiceTyper will now start automatically at login.")
    print(f"Command registered: {command}")


def _install_macos(python: Path) -> None:
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / f"{PLIST_LABEL}.plist"
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>{script_path}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>/tmp/voicetyper.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/voicetyper.err</string>
</dict>
</plist>
"""
    plist_path.write_text(plist_content)
    os.system(f"launchctl load '{plist_path}'")
    print("Done! VoiceTyper will now start automatically at login.")
    print(f"LaunchAgent installed: {plist_path}")


def _install_linux(python: Path) -> None:
    autostart_dir = Path.home() / ".config" / "autostart"
    autostart_dir.mkdir(parents=True, exist_ok=True)
    desktop_path = autostart_dir / "voicetyper.desktop"
    desktop_content = f"""[Desktop Entry]
Type=Application
Name=VoiceTyper
Comment=Local voice dictation tool
Exec={python} {script_path}
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
"""
    desktop_path.write_text(desktop_content)
    print("Done! VoiceTyper will now start automatically at login.")
    print(f"Autostart entry installed: {desktop_path}")


python = _find_python()

if os.name == "nt":
    _install_windows(python)
elif sys.platform == "darwin":
    _install_macos(python)
else:
    _install_linux(python)

print()
print("To remove it, run:  python uninstall_startup.py")
