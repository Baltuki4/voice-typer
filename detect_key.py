"""
detect_key.py — Press any key to see its pynput name.
Copy the printed value into PTT_KEY in voice_typer.py.
Press Ctrl+C to quit.
"""

from pynput import keyboard


def on_press(key):
    if key == keyboard.Key.esc:
        return False  # stops the listener

    print(f"  Key pressed:  {key!r}")
    print(f"  Use in PTT_KEY:  {key}")
    print()


print("Press any key to see its name (Esc to quit)...\n")

with keyboard.Listener(on_press=on_press) as listener:
    listener.join()
