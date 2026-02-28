"""
voice_typer.py — Local voice dictation using Whisper
─────────────────────────────────────────────────────
Runs as a system tray app.
Hold PTT_KEY → speak → release → transcribed text is pasted at your cursor.

Right-click the tray icon to quit.
"""

import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pyperclip
import pystray
import sounddevice as sd
from faster_whisper import WhisperModel
from PIL import Image, ImageDraw
from pynput import keyboard

# ── CONFIG ────────────────────────────────────────────────────────────────────
PTT_KEY       = keyboard.Key.f15   # key to hold while speaking
WHISPER_MODEL = "small"              # tiny | base | small | medium | large-v3
WHISPER_LANG  = "es"               # None = auto-detect, or "en", "es", "fr" …
SAMPLE_RATE   = 16_000
MIN_DURATION  = 0.4                # seconds — shorter clips are discarded
# ─────────────────────────────────────────────────────────────────────────────


# ── State ─────────────────────────────────────────────────────────────────────
_recording    = False
_audio_chunks: list[np.ndarray] = []
_lock         = threading.Lock()
_tray: pystray.Icon | None = None
_stopping     = threading.Event()
_executor     = ThreadPoolExecutor(max_workers=1)
# ─────────────────────────────────────────────────────────────────────────────


# ── Tray icon drawing ─────────────────────────────────────────────────────────
def _make_icon(recording: bool = False) -> Image.Image:
    """Draw a simple microphone icon. Red dot overlay when recording."""
    size  = 64
    img   = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d     = ImageDraw.Draw(img)
    white = "#ffffff"
    red   = "#ff4444"

    # Mic capsule
    d.rounded_rectangle([23, 4, 41, 34], radius=9, fill=white)
    # Mic stand arc
    d.arc([14, 26, 50, 50], start=0, end=180, fill=white, width=3)
    # Mic stand pole
    d.line([32, 50, 32, 58], fill=white, width=3)
    # Base
    d.line([22, 58, 42, 58], fill=white, width=3)

    if recording:
        # Red dot in top-right corner
        d.ellipse([44, 2, 62, 20], fill=red)

    return img
# ─────────────────────────────────────────────────────────────────────────────


def _set_tray(title: str, recording: bool = False) -> None:
    if _tray:
        _tray.icon  = _make_icon(recording)
        _tray.title = title


def _request_stop(message: str | None = None) -> None:
    global _recording

    if _stopping.is_set():
        return

    _stopping.set()
    _recording = False

    if message:
        print(message, flush=True)

    if _tray:
        _tray.stop()


def _record_loop() -> None:
    """Runs in a daemon thread: fills _audio_chunks from the microphone."""
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32") as stream:
        while _recording:
            chunk, _ = stream.read(1024)
            with _lock:
                _audio_chunks.append(chunk.flatten())


def _transcribe(model: WhisperModel, audio: np.ndarray) -> str:
    # Pass the numpy array directly — no temp file needed
    segments, _ = model.transcribe(
        audio,
        language=WHISPER_LANG,
        beam_size=1,        # greedy decoding: fastest
        vad_filter=True,    # skip silence automatically
    )
    return " ".join(s.text for s in segments).strip()


def _paste(text: str, kb_ctrl: keyboard.Controller) -> None:
    """Paste text at the current cursor position via clipboard."""
    try:
        previous = pyperclip.paste()
    except Exception:
        previous = ""

    pyperclip.copy(text)
    time.sleep(0.01)
    with kb_ctrl.pressed(keyboard.Key.ctrl):
        kb_ctrl.tap("v")
    time.sleep(0.05)

    try:
        pyperclip.copy(previous)
    except Exception:
        pass


def _run_keyboard_listener(model: WhisperModel, kb_ctrl: keyboard.Controller) -> None:
    global _recording

    def on_press(key) -> None:
        global _recording
        if key == PTT_KEY and not _recording:
            _recording = True
            with _lock:
                _audio_chunks.clear()
            _set_tray("Voice Typer — Recording…", recording=True)
            threading.Thread(target=_record_loop, daemon=True).start()

    def _transcribe_and_paste(audio: np.ndarray) -> None:
        text = _transcribe(model, audio)
        if text:
            _paste(text, kb_ctrl)
        _set_tray("Voice Typer — F15 to record")

    def on_release(key) -> None:
        global _recording
        if key != PTT_KEY or not _recording:
            return

        _recording = False
        _set_tray("Voice Typer — Transcribing…")

        with _lock:
            audio = (
                np.concatenate(_audio_chunks)
                if _audio_chunks
                else np.array([], dtype=np.float32)
            )

        if len(audio) / SAMPLE_RATE < MIN_DURATION:
            _set_tray("Voice Typer — F15 to record")
            return

        # Run transcription in background so the key listener stays responsive
        _executor.submit(_transcribe_and_paste, audio)

    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()


def main() -> None:
    global _tray

    kb_ctrl = keyboard.Controller()

    # Load Whisper model in a background thread so the tray appears instantly
    model_ready     = threading.Event()
    model_holder: list[WhisperModel | None] = [None]

    def _load():
        try:
            model_holder[0] = WhisperModel(WHISPER_MODEL, device="cuda", compute_type="float16")
            print("Modelo cargado en GPU (CUDA).", flush=True)
        except Exception:
            model_holder[0] = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
            print("Modelo cargado en CPU.", flush=True)
        model_ready.set()
        print("Voice Typer activo. Manten F15 para dictar. Usa Ctrl+C o Quit para cerrar.", flush=True)
        _set_tray("Voice Typer — F15 to record")

    threading.Thread(target=_load, daemon=True).start()

    # Start the keyboard listener once the model is ready
    def _start_kb():
        model_ready.wait()
        _run_keyboard_listener(model_holder[0], kb_ctrl)

    threading.Thread(target=_start_kb, daemon=True).start()

    # Build tray
    def on_quit(icon: pystray.Icon, _item) -> None:
        _request_stop("Cerrando Voice Typer desde la taskbar...")

    def _handle_signal(_sig, _frame) -> None:
        _request_stop("Ctrl+C detectado. Cerrando Voice Typer...")

    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)

    _tray = pystray.Icon(
        name  = "VoiceTyper",
        icon  = _make_icon(),
        title = "Voice Typer — Loading model…",
        menu  = pystray.Menu(
            pystray.MenuItem("Voice Typer", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", on_quit),
        ),
    )

    _tray.run()   # blocks main thread (Windows message loop)


if __name__ == "__main__":
    main()
