"""
voice_typer.py - Local voice dictation using Whisper.

Runs as a system tray app.
Hold PTT_KEY -> speak -> release -> transcribed text is pasted at your cursor.
Right-click the tray icon to quit.
"""

import ctypes
import datetime as _dt
import os
import re
import signal
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_PROJECT_DIR = Path(__file__).resolve().parent


def _maybe_reexec_into_project_venv() -> None:
    """
    Keep manual launches aligned with startup by preferring the project's venv.
    Works on Windows, macOS, and Linux.
    """
    if os.name == "nt":
        bin_dir = _PROJECT_DIR / ".venv" / "Scripts"
        preferred_name = (
            "pythonw.exe"
            if Path(sys.executable).name.lower() == "pythonw.exe"
            else "python.exe"
        )
    else:
        bin_dir = _PROJECT_DIR / ".venv" / "bin"
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

import numpy as np
import pyperclip
import pystray
import sounddevice as sd
from dotenv import load_dotenv
from faster_whisper import WhisperModel
from PIL import Image, ImageDraw
from pynput import keyboard

load_dotenv(_PROJECT_DIR / ".env")

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

# ---- HELPERS ----------------------------------------------------------------

_LOG_PATH_SETTING = Path(os.environ.get("VOICE_TYPER_LOG", "voice_typer.log"))
_LOG_PATH = _LOG_PATH_SETTING if _LOG_PATH_SETTING.is_absolute() else _PROJECT_DIR / _LOG_PATH_SETTING


def _log(message: str) -> None:
    """Log to the terminal when present and to a file for pythonw/startup runs."""
    timestamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"

    stream = getattr(sys, "stdout", None)
    if stream is not None:
        try:
            print(line, flush=True)
        except Exception:
            pass

    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int | None = None) -> int:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    try:
        parsed = int(value)
    except ValueError:
        _log(f"Invalid integer for {name}={value!r}; using {default}.")
        return default
    return max(parsed, minimum) if minimum is not None else parsed


def _env_float(name: str, default: float, minimum: float | None = None) -> float:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    try:
        parsed = float(value)
    except ValueError:
        _log(f"Invalid float for {name}={value!r}; using {default}.")
        return default
    return max(parsed, minimum) if minimum is not None else parsed


def _parse_audio_latency(value: str) -> str | float:
    value = value.strip()
    if value in {"", "low", "high"}:
        return value or "low"
    try:
        return float(value)
    except ValueError:
        _log(f"Invalid AUDIO_LATENCY={value!r}; using 'low'.")
        return "low"


def _parse_audio_device(value: str | None) -> int | str | None:
    if value is None or value.strip() == "":
        return None
    value = value.strip()
    try:
        return int(value)
    except ValueError:
        return value

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
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small").strip() or "small"  # tiny | base | small | medium | large-v3
WHISPER_LANG = os.environ.get("WHISPER_LANG", "es") or None  # None = auto-detect, or "en", "es", "fr", ...
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cpu").strip() or "cpu"
_DEFAULT_COMPUTE_TYPE = "float16" if WHISPER_DEVICE.startswith("cuda") else "int8"
WHISPER_COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", _DEFAULT_COMPUTE_TYPE).strip() or _DEFAULT_COMPUTE_TYPE
WHISPER_CPU_THREADS = _env_int("WHISPER_CPU_THREADS", max(1, min(8, (os.cpu_count() or 4) - 2)), minimum=1)
WHISPER_NUM_WORKERS = _env_int("WHISPER_NUM_WORKERS", 1, minimum=1)
WHISPER_BEAM_SIZE = _env_int("WHISPER_BEAM_SIZE", 1, minimum=1)
WHISPER_VAD_FILTER = _env_bool("WHISPER_VAD_FILTER", True)
WHISPER_CONDITION_ON_PREVIOUS_TEXT = _env_bool("WHISPER_CONDITION_ON_PREVIOUS_TEXT", False)
WHISPER_INITIAL_PROMPT = os.environ.get("WHISPER_INITIAL_PROMPT", "").strip() or None

SAMPLE_RATE = _env_int("SAMPLE_RATE", 16_000, minimum=8_000)
AUDIO_DEVICE = _parse_audio_device(os.environ.get("AUDIO_DEVICE"))
AUDIO_KEEP_OPEN = _env_bool("AUDIO_KEEP_OPEN", False)
AUDIO_BLOCK_SIZE = _env_int("AUDIO_BLOCK_SIZE", 512, minimum=128)
AUDIO_LATENCY = _parse_audio_latency(os.environ.get("AUDIO_LATENCY", "low"))
AUDIO_TAIL_SECONDS = _env_float("AUDIO_TAIL_SECONDS", 0.12, minimum=0.0)
AUDIO_PAD_SECONDS = _env_float("AUDIO_PAD_SECONDS", 0.10, minimum=0.0)
MIN_DURATION = _env_float("MIN_DURATION", 0.35, minimum=0.0)  # seconds; shorter clips are discarded

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
_capture_until = 0.0
_lock = threading.Lock()
_audio_stream_lock = threading.Lock()
_audio_stream: sd.InputStream | None = None
_audio_status_counts: Counter = Counter()
_tray: pystray.Icon | None = None
_stopping = threading.Event()
_executor = ThreadPoolExecutor(max_workers=1)
_instance_mutex: int | None = None
_lock_file_handle = None  # Unix file-lock handle (IO | None)
_keyboard_listener: keyboard.Listener | None = None
_console_ctrl_handler = None
_timer_resolution_ms: int | None = None


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


def _configure_process_performance() -> None:
    """Keep startup-launched runs responsive during short transcription bursts."""
    global _timer_resolution_ms

    if os.name != "nt":
        return

    priority_name = os.environ.get("VOICE_TYPER_PRIORITY", "above_normal").strip().lower()
    priority_classes = {
        "normal": 0x00000020,
        "above_normal": 0x00008000,
        "high": 0x00000080,
    }
    priority_class = priority_classes.get(priority_name)
    if priority_class is None:
        _log(f"Unknown VOICE_TYPER_PRIORITY={priority_name!r}; using above_normal.")
        priority_class = priority_classes["above_normal"]

    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), priority_class)
    except Exception as exc:
        _log(f"Could not set process priority: {exc}")

    timer_ms = _env_int("VOICE_TYPER_TIMER_MS", 1, minimum=0)
    if timer_ms <= 0:
        return
    try:
        winmm = ctypes.WinDLL("winmm")
        if winmm.timeBeginPeriod(timer_ms) == 0:
            _timer_resolution_ms = timer_ms
    except Exception as exc:
        _log(f"Could not set Windows timer resolution: {exc}")


def _restore_process_performance() -> None:
    global _timer_resolution_ms

    if os.name != "nt" or _timer_resolution_ms is None:
        return
    try:
        ctypes.WinDLL("winmm").timeEndPeriod(_timer_resolution_ms)
    except Exception:
        pass
    _timer_resolution_ms = None


def _audio_callback(indata, _frames, _time_info, status) -> None:
    if status:
        status_key = str(status)
        _audio_status_counts[status_key] += 1
        if _audio_status_counts[status_key] in {1, 2, 3, 10, 50}:
            _log(f"Audio input status: {status_key}")

    if _stopping.is_set():
        return

    now = time.perf_counter()
    with _lock:
        if _recording or now <= _capture_until:
            _audio_chunks.append(indata[:, 0].copy())


def _build_audio_stream(low_latency: bool) -> sd.InputStream:
    kwargs = {
        "samplerate": SAMPLE_RATE,
        "channels": 1,
        "dtype": "float32",
        "callback": _audio_callback,
    }
    if AUDIO_DEVICE is not None:
        kwargs["device"] = AUDIO_DEVICE
    if low_latency:
        kwargs["blocksize"] = AUDIO_BLOCK_SIZE
        kwargs["latency"] = AUDIO_LATENCY
    return sd.InputStream(**kwargs)


def _ensure_audio_stream() -> bool:
    global _audio_stream

    if _stopping.is_set():
        return False

    with _audio_stream_lock:
        if _audio_stream is not None:
            return True

        for low_latency in (True, False):
            stream = None
            try:
                stream = _build_audio_stream(low_latency=low_latency)
                stream.start()
                _audio_stream = stream
                mode = "low-latency" if low_latency else "default-latency"
                block = AUDIO_BLOCK_SIZE if low_latency else "default"
                device = f", device={AUDIO_DEVICE!r}" if AUDIO_DEVICE is not None else ""
                _log(f"Audio input stream ready ({mode}, {SAMPLE_RATE} Hz, block={block}{device}).")
                return True
            except Exception as exc:
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
                if low_latency:
                    _log(f"Low-latency audio input failed, retrying with device defaults: {exc}")
                else:
                    _log(f"Audio input unavailable: {exc}")

    return False


def _close_audio_stream() -> None:
    global _audio_stream

    with _audio_stream_lock:
        stream = _audio_stream
        _audio_stream = None

    if stream is None:
        return
    try:
        stream.stop()
    except Exception:
        pass
    try:
        stream.close()
    except Exception:
        pass


def _warm_audio_input() -> None:
    if AUDIO_KEEP_OPEN:
        _ensure_audio_stream()


def _close_audio_stream_unless_keep_open() -> None:
    if not AUDIO_KEEP_OPEN:
        _close_audio_stream()


def _prepare_audio_for_transcription(audio: np.ndarray) -> np.ndarray:
    audio = np.asarray(audio, dtype=np.float32)
    if audio.size == 0:
        return audio

    audio = np.nan_to_num(audio, copy=False)
    audio = audio - float(np.mean(audio))

    if AUDIO_PAD_SECONDS > 0:
        pad = np.zeros(int(SAMPLE_RATE * AUDIO_PAD_SECONDS), dtype=np.float32)
        audio = np.concatenate((pad, audio, pad))

    return audio


def _request_stop(message: str | None = None) -> None:
    global _recording

    if _stopping.is_set():
        return

    _stopping.set()
    _recording = False

    if message:
        _log(message)

    _close_audio_stream()

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
    _log(
        f"Loading Whisper model '{WHISPER_MODEL}' on {WHISPER_DEVICE} "
        f"({WHISPER_COMPUTE_TYPE}, cpu_threads={WHISPER_CPU_THREADS}, workers={WHISPER_NUM_WORKERS})..."
    )
    return WhisperModel(
        WHISPER_MODEL,
        device=WHISPER_DEVICE,
        compute_type=WHISPER_COMPUTE_TYPE,
        cpu_threads=WHISPER_CPU_THREADS,
        num_workers=WHISPER_NUM_WORKERS,
    )


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


def _transcribe(model: WhisperModel, audio: np.ndarray) -> str:
    audio = _prepare_audio_for_transcription(audio)
    transcribe_kwargs = {
        "language": WHISPER_LANG,
        "beam_size": WHISPER_BEAM_SIZE,
        "vad_filter": WHISPER_VAD_FILTER,
        "condition_on_previous_text": WHISPER_CONDITION_ON_PREVIOUS_TEXT,
    }
    if WHISPER_INITIAL_PROMPT:
        transcribe_kwargs["initial_prompt"] = WHISPER_INITIAL_PROMPT

    segments, _ = model.transcribe(
        audio,
        **transcribe_kwargs,
    )
    return " ".join(s.text for s in segments).strip()


def _optimize_prompt(raw_text: str) -> str:
    """Send transcribed text to the configured provider and return an improved prompt."""
    cfg = OPTIMIZE_PROVIDERS.get(OPTIMIZE_PROVIDER)
    if not cfg:
        _log(f"Unknown provider '{OPTIMIZE_PROVIDER}'. Available: {list(OPTIMIZE_PROVIDERS)}")
        return raw_text

    api_key = os.environ.get(cfg["api_key_env"], "")
    if not api_key:
        _log(f"{cfg['api_key_env']} not set; pasting raw transcription without optimizing.")
        return raw_text

    try:
        if cfg["sdk"] == "anthropic":
            return _optimize_via_anthropic(raw_text, api_key, cfg["model"])
        else:
            return _optimize_via_openai(raw_text, api_key, cfg["model"], cfg.get("base_url"))
    except Exception as exc:
        _log(f"Error optimizing prompt ({OPTIMIZE_PROVIDER}): {exc}")
        return raw_text


def _optimize_via_anthropic(text: str, api_key: str, model: str) -> str:
    import anthropic

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
    import openai as _openai

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
    global _keyboard_listener, _recording, _active_ptt_key, _pressed_keys, _capture_until

    _idle_label = f"Voice Typer - {PTT_LABEL} / {PTT_OPTIMIZE_LABEL} to record"

    def _canonical(key):
        return _MODIFIER_CANONICAL.get(key, key)

    def on_press(key) -> None:
        global _recording, _active_ptt_key, _capture_until
        if _stopping.is_set():
            return
        canonical = _canonical(key)
        _pressed_keys.add(canonical)

        if _recording:
            return

        for combo, label_suffix in ((PTT_KEY, ""), (PTT_KEY_OPTIMIZE, " [OPTIMIZE]")):
            mods, trigger = combo
            if canonical == trigger and mods.issubset(_pressed_keys):
                if not _ensure_audio_stream():
                    _set_tray("Voice Typer - Microphone unavailable")
                    return
                _active_ptt_key = combo
                with _lock:
                    _audio_chunks.clear()
                    _recording = True
                    _capture_until = float("inf")
                _set_tray(f"Voice Typer - Recording{label_suffix}...", recording=True)
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
        global _recording, _active_ptt_key, _capture_until
        if _stopping.is_set():
            return

        canonical = _canonical(key)

        if _recording and _active_ptt_key is not None:
            _, trigger = _active_ptt_key
            if canonical == trigger:
                optimize = _active_ptt_key == PTT_KEY_OPTIMIZE
                _active_ptt_key = None
                _set_tray("Voice Typer - Transcribing...")

                with _lock:
                    _recording = False
                    _capture_until = time.perf_counter() + AUDIO_TAIL_SECONDS

                tail_wait = AUDIO_TAIL_SECONDS + (AUDIO_BLOCK_SIZE / SAMPLE_RATE) + 0.01
                if tail_wait > 0:
                    time.sleep(min(tail_wait, 0.25))

                with _lock:
                    _capture_until = 0.0
                    audio = np.concatenate(_audio_chunks) if _audio_chunks else np.array([], dtype=np.float32)

                _close_audio_stream_unless_keep_open()

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
        _log("Voice Typer is already running in another instance. Closing this copy.")
        return

    try:
        _configure_process_performance()
        kb_ctrl = keyboard.Controller()

        model_ready = threading.Event()
        model_holder: list[WhisperModel | None] = [None]

        def _load() -> None:
            try:
                model_holder[0] = _build_whisper_model()
            except Exception as exc:
                _log(f"Failed to load Whisper: {exc}")
                _request_stop("Failed to start Voice Typer.")
            finally:
                model_ready.set()

            if model_holder[0] is not None:
                cfg = OPTIMIZE_PROVIDERS.get(OPTIMIZE_PROVIDER, {})
                api_key_env = cfg.get("api_key_env", "")
                optimize_status = "OK" if os.environ.get(api_key_env) else f"MISSING {api_key_env}"
                _log(
                    f"Voice Typer ready. {PTT_LABEL}=dictate | {PTT_OPTIMIZE_LABEL}=dictate+optimize [{OPTIMIZE_PROVIDER}] ({optimize_status}). "
                    f"beam={WHISPER_BEAM_SIZE}, vad={WHISPER_VAD_FILTER}, audio_tail={AUDIO_TAIL_SECONDS:.2f}s, "
                    f"audio_keep_open={AUDIO_KEEP_OPEN}. "
                    "Press Ctrl+C or use tray Quit to exit."
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
        if AUDIO_KEEP_OPEN:
            threading.Thread(target=_warm_audio_input, daemon=True).start()
        try:
            while not _stopping.is_set():
                time.sleep(0.1)
        except KeyboardInterrupt:
            _request_stop("Ctrl+C detected. Shutting down Voice Typer...")
    finally:
        _request_stop()
        _restore_process_performance()
        _release_single_instance_lock()


if __name__ == "__main__":
    main()
