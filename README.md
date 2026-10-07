# ITC-C80 Camera Kitting Tool

MAC-based discovery, USB appearance inspection, automatic IR-CUT checks, config import, final IP allocation and GAS reporting.

```text
src/          Application and launcher source
tests/       Offline automated tests
samples/      Hardware test/demo scripts
examples/     GAS receiver and configuration examples
tools/        Build and release scripts
docs/         Operating instructions
release/      Generated EXE, ZIP and GitHub release assets
data/         Local job state, logs and build cache (private)
records/      Local credentials and history (private)
evidence/     Inspection images (private)
config file/  Camera config (private)
```

## Start

Use `start.vbs` or the windowed `release/native/ITC-C80-Launcher-1.0.exe`. No console or browser is opened. The launcher finds the project root and starts `src/bootstrap.py` after checking updates. Camera operations need Python 3.9+ and Npcap. Configure credentials and network settings in the application; put the camera config in `config file/`. Existing production folders remain at the project root.

Copy `examples/launcher_settings.yaml` to `launcher_settings.yaml` if needed.

## Validation

```powershell
python -m unittest discover -s tests -p 'test_*.py'
python samples/ir_cut_sample.py --ip 192.168.0.150
```

Automated tests use simulated devices. The device flow and final IP samples write to real cameras and require explicit hardware-test authorization.

## Build

```powershell
./tools/build_launcher.ps1
python tools/build_release.py --version 1.0
```

Release assets are generated in `release/assets/`; initial distribution is `release/ITC-C80-Portable.zip`. Never publish local credentials, production records, images or camera config.

- [Inspection workflow](docs/JOB_WORKFLOW.md)
- [Native desktop interface](docs/DESKTOP_UI.md)
- [Evidence layout](docs/SAVE_LAYOUT.md)
- [Launcher](docs/LAUNCHER.md)
