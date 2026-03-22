"""
install_startup.py — Add VoiceTyper to Windows startup.
Run once. After this, it will launch automatically every time you log in.
"""

import sys
import winreg
from pathlib import Path

APP_NAME    = "VoiceTyper"
project_dir = Path(__file__).resolve().parent
script_path = project_dir / "voice_typer.py"

# Prefer the project's venv so startup matches manual launches.
project_pythonw = project_dir / ".venv" / "Scripts" / "pythonw.exe"
project_python = project_dir / ".venv" / "Scripts" / "python.exe"

if project_pythonw.exists():
    pythonw = project_pythonw
elif project_python.exists():
    pythonw = project_python
else:
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
