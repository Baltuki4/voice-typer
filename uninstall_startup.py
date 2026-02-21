"""
uninstall_startup.py — Remove VoiceTyper from Windows startup.
"""

import winreg

APP_NAME = "VoiceTyper"

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
