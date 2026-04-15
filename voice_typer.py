"""
voice_typer.py - Local voice dictation using Whisper.

Runs as a system tray app.
Hold PTT_KEY -> speak -> release -> transcribed text is pasted at your cursor.
Right-click the tray icon to quit.
"""

import ctypes
import os
import re
import signal
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anthropic
import numpy as np
import openai as _openai
import pyperclip
import pystray
import sounddevice as sd
from dotenv import load_dotenv
from faster_whisper import WhisperModel
from PIL import Image, ImageDraw
from pynput import keyboard

load_dotenv(Path(__file__).resolve().parent / ".env")

# Normalise left/right modifier variants to their canonical form so that
# combo matching works regardless of which physical key the user presses.
_MODIFIER_CANONICAL: dict = {
    keyboard.Key.ctrl_l:  keyboard.Key.ctrl,
    keyboard.Key.ctrl_r:  keyboard.Key.ctrl,
    keyboard.Key.shift_l: keyboard.Key.shift,
    keyboard.Key.shift_r: keyboard.Key.shift,
    keyboard.Key.alt_l:   keyboard.Key.alt,
    keyboard.Key.alt_r:   keyboard.Key.alt,
}


def _maybe_reexec_into_project_venv() -> None:
    """
    Keep manual launches aligned with startup by preferring the project's venv.
    Works on Windows, macOS, and Linux.
    """
    project_dir = Path(__file__).resolve().parent

    if os.name == "nt":
        bin_dir = project_dir / ".venv" / "Scripts"
        preferred_name = (
            "pythonw.exe"
            if Path(sys.executable).name.lower() == "pythonw.exe"
            else "python.exe"
        )
    else:
        bin_dir = project_dir / ".venv" / "bin"
        preferred_name = "python"

    if not bin_dir.exists():
        return

    current_executable = Path(sys.executable).resolve()
    try:
        if current_executable.parent.samefile(bin_dir):
            return
    except FileNotFoundError:
        pass

    preferred_executable = bin_dir / preferred_name
    if not preferred_executable.exists():
        return

    os.execv(str(preferred_executable), [str(preferred_executable), *sys.argv])


_maybe_reexec_into_project_venv()

# ---- HELPERS ----------------------------------------------------------------

def _parse_single_key(name: str) -> keyboard.Key | keyboard.KeyCode:
    """Convert a single key name string (e.g. 'f15', 'ctrl') to a pynput key object."""
    key = getattr(keyboard.Key, name.lower(), None)
    if key is not None:
        return key
    return keyboard.KeyCode.from_char(name)


_MODIFIER_NAMES = {"ctrl", "shift", "alt", "alt_gr", "cmd", "super"}


def _parse_key_combo(combo_str: str) -> tuple[frozenset, keyboard.Key | keyboard.KeyCode]:
    """Parse a key combo string into (modifiers_frozenset, trigger_key).

    Examples:
        'f15'              -> (frozenset(), Key.f15)
        'ctrl+shift+f9'   -> ({Key.ctrl, Key.shift}, Key.f9)
        'ctrl+alt+r'      -> ({Key.ctrl, Key.alt}, KeyCode('r'))
    """
    parts = [p.strip().lower() for p in combo_str.split("+")]
    modifiers: set = set()
    trigger = None
    for part in parts:
        k = _parse_single_key(part)
        if part in _MODIFIER_NAMES:
            modifiers.add(k)
        else:
            trigger = k
    if trigger is None:
        # Fallback: treat the last token as the trigger.
        trigger = _parse_single_key(parts[-1])
    return frozenset(modifiers), trigger


def _key_label(key: keyboard.Key | keyboard.KeyCode) -> str:
    """Return a human-readable label for a single key."""
    if isinstance(key, keyboard.Key):
        return str(key).replace("Key.", "").upper()
    if getattr(key, "char", None):
        return key.char.upper()
    return str(key)


def _combo_label(combo: tuple[frozenset, keyboard.Key | keyboard.KeyCode]) -> str:
    """Return a human-readable combo label, e.g. 'CTRL+SHIFT+F9'."""
    modifiers, trigger = combo
    _mod_order = {"CTRL": 0, "SHIFT": 1, "ALT": 2, "CMD": 3}
    sorted_mods = sorted((_key_label(m) for m in modifiers), key=lambda x: _mod_order.get(x, 99))
    return "+".join(sorted_mods + [_key_label(trigger)])


# ---- CONFIG -----------------------------------------------------------------
PTT_KEY          = _parse_key_combo(os.environ.get("PTT_KEY", "f15"))
PTT_KEY_OPTIMIZE = _parse_key_combo(os.environ.get("PTT_KEY_OPTIMIZE", "f16"))
PTT_LABEL          = _combo_label(PTT_KEY)
PTT_OPTIMIZE_LABEL = _combo_label(PTT_KEY_OPTIMIZE)
WHISPER_MODEL  = "small"                                        # tiny | base | small | medium | large-v3
WHISPER_LANG   = os.environ.get("WHISPER_LANG", "es") or None  # None = auto-detect, or "en", "es", "fr", ...
WHISPER_DEVICE = "cpu"                                          # forced to keep startup and manual runs identical
SAMPLE_RATE    = 16_000
MIN_DURATION   = 0.4       # seconds; shorter clips are discarded

OPTIMIZE_PROVIDER = os.environ.get("OPTIMIZE_PROVIDER", "anthropic")  # override via env var

# Add or edit providers here.  Keys used:
#   api_key_env  — environment variable that holds the API key
#   model        — model ID to use
#   base_url     — OpenAI-compatible endpoint (None = default OpenAI endpoint)
#   sdk          — "anthropic" uses the Anthropic SDK; "openai" uses the OpenAI SDK
OPTIMIZE_PROVIDERS: dict[str, dict] = {
    "anthropic": {
        "sdk": "anthropic",
        "api_key_env": "ANTHROPIC_API_KEY",
        "model": "claude-haiku-4-5",
    },
    "openai": {
        "sdk": "openai",
        "api_key_env": "OPENAI_API_KEY",
        "model": "gpt-4o-mini",
        "base_url": None,
    },
    "gemini": {
        "sdk": "openai",
        "api_key_env": "GEMINI_API_KEY",
        "model": "gemini-2.0-flash",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
    },
    "ollama": {
        "sdk": "openai",
        "api_key_env": "OLLAMA_API_KEY",  # not validated by Ollama; set any value in .env
        "model": "phi4-mini",             # change to any model you have pulled locally
        "base_url": "http://localhost:11434/v1",
    },
}

# ISO 639-1 code -> full language name (extend as needed)
_LANG_NAMES: dict[str, str] = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German",
    "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "pl": "Polish",
    "ru": "Russian", "zh": "Chinese", "ja": "Japanese", "ko": "Korean",
    "ar": "Arabic", "tr": "Turkish", "sv": "Swedish", "da": "Danish",
    "fi": "Finnish", "nb": "Norwegian", "cs": "Czech", "ro": "Romanian",
}


def _build_system_prompt() -> str:
    """Return a system prompt with an explicit language instruction derived from WHISPER_LANG.

    For local models (ollama), only English and Spanish are reliably supported;
    any other language falls back to English.
    For cloud providers, all languages in _LANG_NAMES are passed through as-is.
    """
    lang_code = (WHISPER_LANG or "").lower()

    if OPTIMIZE_PROVIDER == "ollama":
        effective_lang = lang_code if lang_code in ("en", "es") else "en"
    else:
        effective_lang = lang_code

    lang_name = _LANG_NAMES.get(effective_lang, effective_lang.capitalize() if effective_lang else None)
    lang_instruction = (
        f" You MUST write your response in {lang_name}."
        if lang_name
        else " You MUST write your response in the same language as the input text."
    )
    return (
        "You are a prompt engineering expert."
        " The user will give you raw spoken text transcribed from voice dictation."
        " Rewrite it as a clear, well-structured prompt suitable for an AI assistant."
        " Preserve the original intent."
        f"{lang_instruction}"
        " Output only the improved prompt, with no preamble, explanation, or quotes."
    )

# ---- STATE ------------------------------------------------------------------
_recording = False
_active_ptt_key = None  # which PTT combo tuple is currently held down
_pressed_keys: set = set()  # canonical keys currently held (for combo detection)
_audio_chunks: list[np.ndarray] = []
_lock = threading.Lock()
_tray: pystray.Icon | None = None
_stopping = threading.Event()
_executor = ThreadPoolExecutor(max_workers=1)
_instance_mutex: int | None = None
_lock_file_handle = None  # Unix file-lock handle (IO | None)
_keyboard_listener: keyboard.Listener | None = None
_console_ctrl_handler = None


def _make_icon(recording: bool = False) -> Image.Image:
    """Draw a simple microphone icon. Red dot overlay when recording."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    white = "#ffffff"
    red = "#ff4444"

    d.rounded_rectangle([23, 4, 41, 34], radius=9, fill=white)  # mic capsule
    d.arc([14, 26, 50, 50], start=0, end=180, fill=white, width=3)  # mic stand arc
    d.line([32, 50, 32, 58], fill=white, width=3)  # mic stand pole
    d.line([22, 58, 42, 58], fill=white, width=3)  # base

    if recording:
        d.ellipse([44, 2, 62, 20], fill=red)

    return img


def _set_tray(title: str, recording: bool = False) -> None:
    if _tray:
        _tray.icon = _make_icon(recording)
        _tray.title = title


def _request_stop(message: str | None = None) -> None:
    global _recording

    if _stopping.is_set():
        return

    _stopping.set()
    _recording = False

    if message:
        print(message, flush=True)

    if _keyboard_listener:
        try:
            _keyboard_listener.stop()
        except Exception:
            pass

    try:
        _executor.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass

    if _tray:
        _tray.stop()


def _acquire_single_instance_lock() -> bool:
    """
    Prevent multiple instances.
    If two instances run at once they can both paste, causing duplicated text.

    Windows: named mutex via Win32 API.
    macOS/Linux: exclusive file lock via fcntl.
    """
    global _instance_mutex, _lock_file_handle

    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, "Local\\VoiceTyperSingleton")
        if not handle:
            return True
        error_already_exists = 183
        if kernel32.GetLastError() == error_already_exists:
            kernel32.CloseHandle(handle)
            return False
        _instance_mutex = handle
        return True
    else:
        import fcntl
        import tempfile
        lock_path = Path(tempfile.gettempdir()) / "voice_typer.lock"
        try:
            fh = open(lock_path, "w")
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fh.write(str(os.getpid()))
            fh.flush()
            _lock_file_handle = fh
            return True
        except OSError:
            try:
                fh.close()
            except Exception:
                pass
            return False


def _release_single_instance_lock() -> None:
    global _instance_mutex, _lock_file_handle

    if os.name == "nt":
        if _instance_mutex is None:
            return
        ctypes.windll.kernel32.CloseHandle(_instance_mutex)
        _instance_mutex = None
    else:
        if _lock_file_handle is None:
            return
        import fcntl
        try:
            fcntl.flock(_lock_file_handle, fcntl.LOCK_UN)
            _lock_file_handle.close()
        except Exception:
            pass
        _lock_file_handle = None


def _build_whisper_model() -> WhisperModel:
    print("Loading Whisper model on CPU...", flush=True)
    return WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type="int8")


def _install_console_ctrl_handler() -> None:
    """
    On Windows, this catches Ctrl+C/console close events even if a native loop is running.
    """
    global _console_ctrl_handler

    if os.name != "nt" or _console_ctrl_handler is not None:
        return

    ctrl_events_to_handle = {0, 1, 2, 5, 6}
    handler_proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)

    def _handler(ctrl_type: int) -> bool:
        if ctrl_type in ctrl_events_to_handle:
            _request_stop("Console event detected. Shutting down Voice Typer...")
            return True
        return False

    _console_ctrl_handler = handler_proto(_handler)
    try:
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_console_ctrl_handler, True)
    except Exception:
        pass


def _record_loop() -> None:
    """Runs in a daemon thread: fills _audio_chunks from the microphone."""
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32") as stream:
        while _recording:
            chunk, _ = stream.read(1024)
            with _lock:
                _audio_chunks.append(chunk.flatten())


def _transcribe(model: WhisperModel, audio: np.ndarray) -> str:
    segments, _ = model.transcribe(
        audio,
        language=WHISPER_LANG,
        beam_size=1,  # greedy decoding: fastest
        vad_filter=True,  # skip silence automatically
    )
    return " ".join(s.text for s in segments).strip()


def _optimize_prompt(raw_text: str) -> str:
    """Send transcribed text to the configured provider and return an improved prompt."""
    cfg = OPTIMIZE_PROVIDERS.get(OPTIMIZE_PROVIDER)
    if not cfg:
        print(f"Unknown provider '{OPTIMIZE_PROVIDER}'. Available: {list(OPTIMIZE_PROVIDERS)}", flush=True)
        return raw_text

    api_key = os.environ.get(cfg["api_key_env"], "")
    if not api_key:
        print(f"{cfg['api_key_env']} not set; pasting raw transcription without optimizing.", flush=True)
        return raw_text

    try:
        if cfg["sdk"] == "anthropic":
            return _optimize_via_anthropic(raw_text, api_key, cfg["model"])
        else:
            return _optimize_via_openai(raw_text, api_key, cfg["model"], cfg.get("base_url"))
    except Exception as exc:
        print(f"Error optimizing prompt ({OPTIMIZE_PROVIDER}): {exc}", flush=True)
        return raw_text


def _optimize_via_anthropic(text: str, api_key: str, model: str) -> str:
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=_build_system_prompt(),
        messages=[{"role": "user", "content": text}],
    )
    block = response.content[0]
    result = block.text if hasattr(block, "text") else ""  # type: ignore[union-attr]
    return result.strip()


def _optimize_via_openai(text: str, api_key: str, model: str, base_url: str | None) -> str:
    client = _openai.OpenAI(api_key=api_key, base_url=base_url)
    response = client.chat.completions.create(
        model=model,
        max_tokens=1024,
        messages=[
            {"role": "system", "content": _build_system_prompt()},
            {"role": "user", "content": text},
        ],
    )
    return (response.choices[0].message.content or "").strip()


def _paste(text: str, kb_ctrl: keyboard.Controller) -> None:
    """Paste text at the current cursor position via clipboard.

    Uses Cmd+V on macOS and Ctrl+V on Windows/Linux.
    """
    try:
        previous = pyperclip.paste()
    except Exception:
        previous = ""

    pyperclip.copy(text)
    time.sleep(0.01)
    paste_modifier = keyboard.Key.cmd if sys.platform == "darwin" else keyboard.Key.ctrl
    with kb_ctrl.pressed(paste_modifier):
        kb_ctrl.tap("v")
    time.sleep(0.05)

    try:
        pyperclip.copy(previous)
    except Exception:
        pass


def _run_keyboard_listener(model: WhisperModel, kb_ctrl: keyboard.Controller) -> None:
    global _keyboard_listener, _recording, _active_ptt_key, _pressed_keys

    _idle_label = f"Voice Typer - {PTT_LABEL} / {PTT_OPTIMIZE_LABEL} to record"

    def _canonical(key):
        return _MODIFIER_CANONICAL.get(key, key)

    def on_press(key) -> None:
        global _recording, _active_ptt_key
        if _stopping.is_set():
            return
        canonical = _canonical(key)
        _pressed_keys.add(canonical)

        if _recording:
            return

        for combo, label_suffix in ((PTT_KEY, ""), (PTT_KEY_OPTIMIZE, " [OPTIMIZE]")):
            mods, trigger = combo
            if canonical == trigger and mods.issubset(_pressed_keys):
                _active_ptt_key = combo
                _recording = True
                with _lock:
                    _audio_chunks.clear()
                _set_tray(f"Voice Typer - Recording{label_suffix}...", recording=True)
                threading.Thread(target=_record_loop, daemon=True).start()
                break

    def _transcribe_and_paste(audio: np.ndarray) -> None:
        if _stopping.is_set():
            return
        text = _transcribe(model, audio)
        if text and not _stopping.is_set():
            _paste(text, kb_ctrl)
        if not _stopping.is_set():
            _set_tray(_idle_label)

    def _transcribe_optimize_and_paste(audio: np.ndarray) -> None:
        if _stopping.is_set():
            return
        _set_tray("Voice Typer - Transcribing [OPTIMIZE]...")
        text = _transcribe(model, audio)
        if not text or _stopping.is_set():
            _set_tray(_idle_label)
            return
        _set_tray("Voice Typer - Calling Claude API...")
        optimized = _optimize_prompt(text)
        if not _stopping.is_set():
            _paste(optimized, kb_ctrl)
        if not _stopping.is_set():
            _set_tray(_idle_label)

    def on_release(key) -> None:
        global _recording, _active_ptt_key
        if _stopping.is_set():
            return

        canonical = _canonical(key)

        if _recording and _active_ptt_key is not None:
            _, trigger = _active_ptt_key
            if canonical == trigger:
                optimize = _active_ptt_key == PTT_KEY_OPTIMIZE
                _recording = False
                _active_ptt_key = None
                _set_tray("Voice Typer - Transcribing...")

                with _lock:
                    audio = np.concatenate(_audio_chunks) if _audio_chunks else np.array([], dtype=np.float32)

                if len(audio) / SAMPLE_RATE < MIN_DURATION:
                    _set_tray(_idle_label)
                else:
                    try:
                        if optimize:
                            _executor.submit(_transcribe_optimize_and_paste, audio)
                        else:
                            _executor.submit(_transcribe_and_paste, audio)
                    except RuntimeError:
                        # Executor may already be shutting down.
                        return

        _pressed_keys.discard(canonical)

    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        _keyboard_listener = listener
        listener.join()
    _keyboard_listener = None


def main() -> None:
    global _tray

    if not _acquire_single_instance_lock():
        print("Voice Typer is already running in another instance. Closing this copy.", flush=True)
        return

    try:
        kb_ctrl = keyboard.Controller()

        model_ready = threading.Event()
        model_holder: list[WhisperModel | None] = [None]

        def _load() -> None:
            try:
                model_holder[0] = _build_whisper_model()
            except Exception as exc:
                print(f"Failed to load Whisper: {exc}", flush=True)
                _request_stop("Failed to start Voice Typer.")
            finally:
                model_ready.set()

            if model_holder[0] is not None:
                cfg = OPTIMIZE_PROVIDERS.get(OPTIMIZE_PROVIDER, {})
                api_key_env = cfg.get("api_key_env", "")
                optimize_status = "OK" if os.environ.get(api_key_env) else f"MISSING {api_key_env}"
                print(
                    f"Voice Typer ready. {PTT_LABEL}=dictate | {PTT_OPTIMIZE_LABEL}=dictate+optimize [{OPTIMIZE_PROVIDER}] ({optimize_status}). "
                    "Press Ctrl+C or use tray Quit to exit.",
                    flush=True,
                )
                _set_tray(f"Voice Typer - {PTT_LABEL} / {PTT_OPTIMIZE_LABEL} to record")

        threading.Thread(target=_load, daemon=True).start()

        def _start_kb() -> None:
            model_ready.wait()
            if model_holder[0] is None:
                return
            _run_keyboard_listener(model_holder[0], kb_ctrl)

        threading.Thread(target=_start_kb, daemon=True).start()

        def on_quit(icon: pystray.Icon, _item) -> None:
            _request_stop("Shutting down Voice Typer from tray...")

        def _handle_signal(_sig, _frame) -> None:
            _request_stop("Ctrl+C detected. Shutting down Voice Typer...")

        signal.signal(signal.SIGINT, _handle_signal)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _handle_signal)
        _install_console_ctrl_handler()

        _tray = pystray.Icon(
            name="VoiceTyper",
            icon=_make_icon(),
            title="Voice Typer - Loading model...",
            menu=pystray.Menu(
                pystray.MenuItem("Voice Typer", None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", on_quit),
            ),
        )

        _tray.run_detached()
        try:
            while not _stopping.is_set():
                time.sleep(0.1)
        except KeyboardInterrupt:
            _request_stop("Ctrl+C detected. Shutting down Voice Typer...")
    finally:
        _request_stop()
        _release_single_instance_lock()


if __name__ == "__main__":
    main()
