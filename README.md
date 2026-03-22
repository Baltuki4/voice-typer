![banner](docs/assets/banner.png)

---

A lightweight Windows tray app that lets you dictate text by voice and paste it instantly at your cursor — in any application, without switching windows.

It runs entirely in the background using [faster-whisper](https://github.com/SYSTRAN/faster-whisper) for local, offline transcription. No cloud service is required for basic dictation.

On top of plain transcription, it includes an optional **prompt optimizer** mode: hold a second key, speak your idea in natural language, and the app transcribes it, rewrites it as a clean, well-structured AI prompt, and pastes the result. The optimizer supports cloud providers (Anthropic, OpenAI, Gemini) and fully local models via [Ollama](https://ollama.com) — no data leaves your machine if you use the local option.

---

## How it works

```
Hold PTT key  ──►  Whisper transcribes  ──►  Auto-paste at cursor
```

Two modes, same workflow:

| Mode | You do | You get |
|------|--------|---------|
| Hold `PTT_KEY` | Speak naturally | Literal transcription pasted instantly |
| Hold `PTT_KEY_OPTIMIZE` | Speak your idea | AI-rewritten structured prompt, pasted |

**Example — plain dictation** (`PTT_KEY`):

> You say: *"send the report to Maria before Friday"*
>
> Pasted: `Send the report to Maria before Friday.`

**Example — prompt optimizer** (`PTT_KEY_OPTIMIZE`):

> You say: *"write me an email asking for a meeting next week"*
>
> Pasted: `Write a concise, professional email requesting a meeting next week. Include a suggested time slot and a brief agenda. Tone: friendly but formal.`

---

## Before vs After

| Without Voice Typer | With Voice Typer |
|---------------------|-----------------|
| Open editor, type rough draft | Hold PTT key |
| Manually rephrase and fix tone | Speak your idea |
| Copy, switch app, paste | Release key |
| Repeat for every message/prompt | ✅ Already pasted where you need it |

---

## Providers

The prompt optimizer works with cloud and local backends — choose one:

| Provider | Type | API key needed |
|----------|------|----------------|
| **Anthropic** (Claude Haiku) | ☁️ Cloud | Yes |
| **OpenAI** (GPT-4o mini) | ☁️ Cloud | Yes |
| **Gemini** (2.0 Flash) | ☁️ Cloud | Yes |
| **Ollama** (phi4-mini, llama3.2…) | 💻 Local | No — runs on your machine |

> **Ollama** runs fully offline. No subscription, no account, no data sent anywhere.

---

## Quick setup

```powershell
git clone <this-repo>
cd voice-dictation
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # then edit .env with your keys and PTT keys
python .\voice_typer.py
```

See [Installation](#installation) and [Configuration](#configuration) below for full details.

---

<div align="center">

### 🔒 Free. Local. Yours.

No subscription · No account · No cloud · Just install and speak

[![Open Source](https://img.shields.io/badge/-Open%20Source-brightgreen?style=flat-square)]()
[![Offline](https://img.shields.io/badge/-Offline-blue?style=flat-square)]()
[![No Data Sent](https://img.shields.io/badge/-No%20Data%20Sent-orange?style=flat-square)]()
[![Windows](https://img.shields.io/badge/-Windows-0078D4?style=flat-square&logo=windows&logoColor=white)]()

</div>

---

The app runs in the background as a system tray icon:

| Key | Action |
|---|---|
| `PTT_KEY` (hold) | Record and paste the literal transcription |
| `PTT_KEY_OPTIMIZE` (hold) | Record, transcribe, and rewrite the text as an optimized AI prompt |
| Release key | Process and paste at the current cursor position |

Close from the tray icon (`Quit`) or with `Ctrl+C` in the console.

> **Note:** When launched via Windows Startup, the app uses `pythonw.exe` (no console window), so `Ctrl+C` does not apply — use `Quit` from the tray icon instead.

---

## Requirements

- Windows
- Python 3.10+
- Microphone

---

## Installation

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env` and fill in your API key(s) before running.

---

## Configuration

### `.env` file

All key bindings and API settings are managed via the `.env` file at the project root (not tracked by git). Copy `.env.example` to `.env` and fill in your values:

```ini
# Key name (pynput): f1-f24, ctrl, alt, shift, space, tab, etc.
# Run detect_key.py to find the exact name for any key.
PTT_KEY=f15
PTT_KEY_OPTIMIZE=f16

# Whisper transcription language. Leave empty for auto-detect.
# Examples: en, es, fr, de, it, pt, zh, ja
WHISPER_LANG=es

# Provider: anthropic | openai | gemini | ollama
OPTIMIZE_PROVIDER=anthropic

# API keys — only the key for the selected provider is required
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
GEMINI_API_KEY=

# Local model — any non-empty value works as the key (Ollama does not validate it)
OLLAMA_API_KEY=ollama
```

If `OPTIMIZE_PROVIDER` is not set, it defaults to `anthropic`. If the selected provider's API key is missing, `PTT_KEY_OPTIMIZE` falls back to plain dictation and prints a warning to the console.

### Whisper

`WHISPER_LANG` is configured via `.env` (see above). The following constants can be changed directly in `voice_typer.py` if needed:

| Variable | Default | Description |
|---|---|---|
| `WHISPER_MODEL` | `small` | Model size: `tiny`, `base`, `small`, `medium`, `large-v3` |
| `MIN_DURATION` | `0.4` | Minimum audio duration in seconds before transcribing |

The Whisper backend is fixed to CPU so that manual runs and Windows Startup behave identically.

### Prompt optimizer — available providers

| Provider | SDK | Default model |
|---|---|---|
| `anthropic` | Anthropic SDK | `claude-haiku-4-5` |
| `openai` | OpenAI SDK | `gpt-4o-mini` |
| `gemini` | OpenAI SDK (compatible endpoint) | `gemini-2.0-flash` |
| `ollama` | OpenAI SDK (local) | `phi4-mini` |

#### Language behavior

- **Cloud providers** (`anthropic`, `openai`, `gemini`): respond in whichever language `WHISPER_LANG` is set to.
- **Ollama**: responds in Spanish if `WHISPER_LANG=es`, English if `WHISPER_LANG=en`, and defaults to English for any other language.

#### Using Ollama (local, no API key required)

1. Install Ollama:
   ```powershell
   irm https://ollama.com/install.ps1 | iex
   ```
2. Pull a model:
   ```powershell
   ollama pull phi4-mini
   ```
3. Set in `.env`:
   ```ini
   OPTIMIZE_PROVIDER=ollama
   OLLAMA_API_KEY=ollama
   ```

Ollama starts automatically as a background service after installation and listens at `http://localhost:11434`. To use a different model, change the `"model"` value for the `"ollama"` entry in `OPTIMIZE_PROVIDERS` inside `voice_typer.py`.

Recommended models:

| Model | Size | Best for |
|---|---|---|
| `phi4-mini` | 3.8B | Best quality/speed balance |
| `llama3.2:3b` | 3B | Fastest, lowest RAM (~3 GB) |
| `qwen2.5:7b` | 7B | Best quality on capable hardware |

#### Adding a new provider

Any OpenAI-compatible API (OpenRouter, Groq, Mistral, etc.) can be added as a new entry in the `OPTIMIZE_PROVIDERS` dict inside `voice_typer.py`:

```python
"groq": {
    "sdk": "openai",
    "api_key_env": "GROQ_API_KEY",
    "model": "llama-3.3-70b-versatile",
    "base_url": "https://api.groq.com/openai/v1",
},
```

---

## Running

```powershell
python .\voice_typer.py
```

If `.\.venv` exists, the script automatically re-launches itself using the project's Python so that manual runs and Windows Startup use the same environment.

On first run, Whisper may download the selected model. The console will print which provider is active and whether the API key is found.

---

## Windows Startup

Register at startup:

```powershell
python .\install_startup.py
```

`install_startup.py` prefers `.\.venv\Scripts\pythonw.exe` so that Startup uses the same environment as manual runs.

Remove from startup:

```powershell
python .\uninstall_startup.py
```

---

## Finding a key name

Before configuring PTT keys in `.env`, use `detect_key.py` to find the exact pynput name for any key:

```powershell
python .\detect_key.py
```

Press any key and the script prints:

```
  Key pressed:  <Key.f15: 0>
  Use in PTT_KEY:  f15
```

Copy the value shown on the **"Use in PTT_KEY"** line (`f15` in the example) into `.env`:

```ini
PTT_KEY=f15
PTT_KEY_OPTIMIZE=f16
```

Press `Esc` to quit the script.

### Tips for choosing a key

- **High function keys** (`f13`–`f24`): ideal as PTT keys because almost no application uses them. Standard keyboards only go up to `f12`, but many gaming mice, macro keyboards, and Stream Deck devices can emit them.
- **Avoid** common keys (`ctrl`, `alt`, letters) — they will interfere with normal typing.
- If the script prints something like `'a'` instead of a named key, that key is not suitable as a PTT key.

---

## Multiple instances

`voice_typer.py` uses a Windows mutex to prevent running two instances simultaneously. If an instance is already active (e.g., one from Startup and one launched manually), the new copy exits immediately to avoid double-pasting.

---

## Git: remove already-tracked files now in .gitignore

```powershell
git rm -r --cached -- .claude __pycache__
git commit -m "Stop tracking ignored local files"
git push
```

After that, git will respect `.gitignore` for those paths.
