# ITC-C80 Camera Kitting Tool

Python backend and local HTML interface for MAC-based camera discovery, USB appearance inspection, RTSP/IR-CUT confirmation, config import and final IP assignment.

- [Inspection and recovery](JOB_WORKFLOW.md)
- [HTML interface](WEB_UI.md)
- [Evidence layout](SAVE_LAYOUT.md)
- [EXE launcher and GitHub updates](LAUNCHER.md)

## Start

Use `start.bat`, or build/use `ITC-C80 Launcher.exe`. Python 3.9+ and Npcap are required for camera operations. Put the exported config in `config file/`. Access credentials and network settings are entered in the application's settings dialog. Password defaults are empty; local settings are excluded from Git.

Copy `launcher_settings.example.yaml` to `launcher_settings.yaml` and fill in the GitHub repository after creating it.

## Validation

```powershell
python -m unittest discover -s . -p 'test_*.py'
```

Tests use simulated devices. `hardware_smoke.py --run` and `hardware_finish.py` perform real device writes and are for explicitly authorized hardware verification.

## GitHub releases

```powershell
python build_release.py --version 2.1.0
```

Upload the generated files in `release_files/` as release assets. Never commit or upload `records/`, `data/`, `evidence/`, `config file/`, or local credential settings. Generated EXE/ZIP files belong in Release assets rather than source history.
