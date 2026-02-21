"""
install_startup.py — Add VoiceTyper to Windows startup.
Run once. After this, it will launch automatically every time you log in.
"""

import sys
import winreg
from pathlib import Path

APP_NAME    = "VoiceTyper"
script_path = Path(__file__).resolve().parent / "voice_typer.py"

# pythonw.exe runs Python without a console window
pythonw = Path(sys.executable).parent / "pythonw.exe"
if not pythonw.exists():
    # Fallback: some envs only have python.exe
    pythonw = Path(sys.executable)

command = f'"{pythonw}" "{script_path}"'

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
print()
print("To remove it, run:  python uninstall_startup.py")
