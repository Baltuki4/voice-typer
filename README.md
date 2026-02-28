# Voice Dictation (Windows Tray App)

Dictado local por voz usando `faster-whisper`.

La app corre en segundo plano como icono de taskbar:
- Mantener `F15` para grabar.
- Soltar `F15` para transcribir.
- El texto se pega en el cursor actual.
- Se puede cerrar desde tray (`Quit`) o con `Ctrl+C` en consola.

## Requisitos

- Windows
- Python 3.10+ (recomendado usar entorno virtual)
- Microfono disponible

Dependencias Python (ver `requirements.txt`):
- `faster-whisper`
- `sounddevice`
- `numpy`
- `scipy`
- `pynput`
- `pyperclip`
- `pystray`
- `Pillow`

## Instalacion

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Configuracion

Edita `voice_typer.py`:
- `PTT_KEY`: tecla push-to-talk (por defecto `keyboard.Key.f15`)
- `WHISPER_MODEL`: `tiny | base | small | medium | large-v3`
- `WHISPER_LANG`: idioma (`"es"`, `"en"`, etc. o `None`)
- `MIN_DURATION`: duracion minima de audio antes de transcribir

## Ejecucion

```powershell
.\.venv\Scripts\Activate.ps1
python .\voice_typer.py
```

Al primer arranque, Whisper puede descargar el modelo y tardar un poco.

## Inicio automatico en Windows

Registrar en startup:

```powershell
python .\install_startup.py
```

Quitar del startup:

```powershell
python .\uninstall_startup.py
```

## Utilidad: detectar una tecla para PTT

```powershell
python .\detect_key.py
```

Pulsa una tecla y copia el valor mostrado a `PTT_KEY`.

## Git: quitar archivos ya subidos que ahora estan en .gitignore

Si ya subiste archivos ignorados (por ejemplo `.claude` o `__pycache__`), quitalos del indice sin borrar los archivos locales:

```powershell
git rm -r --cached -- .claude __pycache__
git commit -m "Stop tracking ignored local files"
git push
```

Despues de eso, `git` respeta `.gitignore` para esos paths.
