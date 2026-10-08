"""Windowed updater and application launcher; no console window."""
from i18n import ui, set_language, localize_widgets
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import yaml
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from launcher_core import Updater, save_yaml
from instance_lock import InstanceLock

DEFAULT_REPOSITORY = 'sunds-support-ryu/itc-c80-kitting'


def configured_repository(config):
    return str(config.get('github_repository') or '').strip() or DEFAULT_REPOSITORY


def python_command(root, configured):
    if configured:
        candidate = Path(configured)
        if not candidate.is_absolute(): candidate = root / candidate
        if not candidate.is_file(): raise ValueError('Configured Python executable not found')
        return [str(candidate)]
    if (root / 'runtime' / 'python.exe').is_file(): return [str(root / 'runtime' / 'python.exe')]
    if not getattr(sys, 'frozen', False): return [sys.executable]
    for command in ('py', 'python'):
        found = shutil.which(command)
        if found:
            check = [found, '-3'] if command == 'py' else [found]
            try:
                subprocess.run(check + ['-c', 'import sys;assert sys.version_info >= (3,9)'],
                               check=True, timeout=10, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
                return check
            except (OSError, subprocess.SubprocessError): pass
    raise ValueError('Configure Python 3.9+ (launcher_settings.yaml: python_executable)')


def main():
    os.environ['PYTHONUTF8'] = '1'
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    parser = argparse.ArgumentParser()
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--update-only', action='store_true')
    args = parser.parse_args()
    location = Path(sys.executable if getattr(sys, 'frozen', False) else __file__).resolve().parent
    root_dir = next((folder for folder in (location, *location.parents) if (folder/'src/bootstrap.py').is_file()), location)
    log_dir=root_dir/'data/logs';log_dir.mkdir(parents=True,exist_ok=True)
    log=(log_dir/'launcher.log').open('a',encoding='utf-8',buffering=1)
    sys.stdout=sys.stderr=log
    if args.self_test:
        check=tk.Tk();check.withdraw();check.destroy()
        print('Windowed launcher 1.0 / Tk OK / repository='+DEFAULT_REPOSITORY)
        return 0
    os.chdir(root_dir)
    path=root_dir/'launcher_settings.yaml'
    config=yaml.safe_load(path.read_text(encoding='utf-8')) if path.exists() else {}
    config=config or {}
    try:
        import json
        settings=json.loads((root_dir/'records/settings.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):settings={}
    set_language(settings.get('ui_language','ja'))
    root=tk.Tk();root.title(ui('ITC-C80 Launcher 1.0'));root.geometry('500x170');root.resizable(False,False)
    status=tk.StringVar(value=ui('Checking for updates...'))
    ttk.Label(root,textvariable=status,wraplength=470,padding=16).pack(fill='x')
    progress=ttk.Progressbar(root,mode='indeterminate');progress.pack(fill='x',padx=18,pady=8);progress.start()
    events=queue.Queue();ready=root_dir/'data/native_ready';child=[None];launcher_lock=InstanceLock(root_dir/'data/launcher.lock')
    def start_application():
        if args.update_only:root.destroy();return
        try:
            ready.unlink(missing_ok=True)
            command=python_command(root_dir,str(config.get('python_executable') or '').strip())
            child[0]=subprocess.Popen(command+[str(root_dir/'src/bootstrap.py')],cwd=root_dir,
                stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            status.set(ui('Checking dependencies and opening the inspection window...'))
        except Exception as error:messagebox.showerror(ui('Startup failed'), ui(str(error)), parent=root);root.destroy()
    def update():
        try:
            with InstanceLock(root_dir/'data/application.lock'):
                updater=Updater(root_dir,configured_repository(config),log=lambda text:(print(text),events.put(('status',text))))
                try:updater.update()
                finally:updater.close()
            events.put(('done',None))
        except Exception as error:events.put(('error',str(error)))
    def tick():
        try:
            while True:
                kind,value=events.get_nowait()
                if kind=='status':status.set(ui(value))
                elif kind=='done':start_application()
                elif kind=='error':
                    print('Update failed:',value)
                    unsafe=(root_dir/'data/updates/transaction.yaml').exists()
                    if not unsafe and not args.update_only and (root_dir/'src/bootstrap.py').exists() and messagebox.askyesno(ui('Update failed'), ui(value + '\nStart the current version?'), parent=root):start_application()
                    else:messagebox.showerror(ui('Update failed'), ui(value), parent=root);root.destroy();return
        except queue.Empty:pass
        if child[0]:
            if ready.exists():root.destroy();return
            code=child[0].poll()
            if code is not None:
                if code:messagebox.showerror(ui('Startup failed'), ui('Check data/logs/launcher.log.'), parent=root)
                root.destroy();return
        if root.winfo_exists():root.after(150,tick)
    threading.Thread(target=update,daemon=True).start();root.after(100,tick)
    try:root.mainloop()
    finally:launcher_lock.close()
    return 0


if __name__=='__main__':
    try:code=main()
    except Exception as error:
        try:
            root=tk.Tk();root.withdraw();messagebox.showerror(ui('Launcher error'), ui(str(error)), parent=root);root.destroy()
        except Exception:pass
        code=1
    raise SystemExit(code)
