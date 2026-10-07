"""Source-based launcher: no custom packaged EXE and no console window."""
import importlib
import os
from pathlib import Path
import subprocess
import sys


def main():
    os.environ['PYTHONUTF8'] = '1'
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    root = Path(__file__).resolve().parents[1]
    log_dir = root / 'data' / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / 'script_launcher.log').open('a', encoding='utf-8', buffering=1) as log:
        sys.stdout = sys.stderr = log
        try:
            print('Python:', sys.version, '\nExecutable:', sys.executable, '\nRoot:', root)
            if sys.version_info < (3, 9):
                raise RuntimeError('Python 3.9 or later is required.')
            for module, package in [('yaml', 'PyYAML'), ('requests', 'requests')]:
                try:
                    importlib.import_module(module)
                except ImportError:
                    if '--self-test' in sys.argv:
                        raise RuntimeError('Missing dependency: ' + package)
                    print('Installing launcher dependency: ' + package)
                    subprocess.run([sys.executable, '-m', 'pip', 'install', package], check=True,
                                   stdout=log, stderr=log,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                    importlib.invalidate_caches()
                    importlib.import_module(module)
            from launcher import main as launch
            return launch()
        except Exception:
            import traceback
            traceback.print_exc()
            if '--self-test' not in sys.argv:
                import tkinter as tk
                from tkinter import messagebox
                window = tk.Tk()
                window.withdraw()
                messagebox.showerror('ITC-C80', 'Launcher failed. See data/logs/script_launcher.log.', parent=window)
                window.destroy()
            return 1


if __name__ == '__main__':
    raise SystemExit(main())
