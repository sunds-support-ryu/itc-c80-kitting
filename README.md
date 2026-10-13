# ITC-C80 Camera Kitting Tool

ITC-C80 is an ITEC OEM product. The workspace location is
`Python/ITEC/products/ITC-C80/`; original scripts are preserved under
`Python/ITEC/legacy/`. The existing OEM Git repository and release format remain
independent, so downloaded portable packages still start from their own root.

MAC-based discovery, USB appearance inspection, automatic IR-CUT checks, config import, final IP allocation and GAS reporting.

```text
src/          Application and launcher source
tests/       Offline automated tests
cgi/          HTTP camera interfaces and config probes
rtsp/         RTSP diagnostic probes
network/      L2/MAC/IP diagnostic utilities
inspection/   Appearance/IR inspection experiments
validation/   Cross-module verification and shared timing records
samples/      Archived UI/launch examples
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

Double-click `start.pyw` from `release/ITC-C80-Script-Launcher.zip`. The entire startup/update/UI chain uses installed Python, without a custom EXE or console. The Python GUI updater downloads and verifies GitHub assets, then starts the native inspection window through `src/bootstrap.py`. Python 3.9+ with Tk and Npcap are required. Associate `.pyw` with your installed Python's `pythonw.exe` if needed. Configure credentials and network settings in the application; put the camera config in `config file/`. Existing production folders remain at the project root. `start.vbs` remains an optional compatibility entry point when Windows has no `.pyw` association.

Copy `examples/launcher_settings.yaml` to `launcher_settings.yaml` if needed.

## Validation

```powershell
python -m unittest discover -s tests -p 'test_*.py'
python inspection/ir_cut_sample.py --ip 192.168.0.150
```

Automated tests use simulated devices. The device flow and final IP samples write to real cameras and require explicit hardware-test authorization.

## Build

```powershell
python tools/build_release.py --version 1.1.1 --script-only
```

Release assets are generated in `release/assets/1.1.1/`; initial distribution is `release/ITC-C80-Script-Launcher.zip`. Never publish local credentials, production records, images or camera config.

- [Inspection workflow](docs/JOB_WORKFLOW.md)
- [Native desktop interface](docs/DESKTOP_UI.md)
- [Evidence layout](docs/SAVE_LAYOUT.md)
- [Launcher](docs/LAUNCHER.md)
- [Independent CGI probes and next-day validation](cgi/README.md)
- [CGI comparison against workspace sources](cgi/COMPARISON.md)
