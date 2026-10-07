"""Windowed updater and application launcher; no console window."""
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
        if not candidate.is_file(): raise ValueError('指定したPythonがありません')
        return [str(candidate)]
    if (root / 'runtime' / 'python.exe').is_file(): return [str(root / 'runtime' / 'python.exe')]
    for command in ('py', 'python'):
        found = shutil.which(command)
        if found:
            check = [found, '-3'] if command == 'py' else [found]
            try:
                subprocess.run(check + ['-c', 'import sys;assert sys.version_info >= (3,9)'],
                               check=True, timeout=10, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
                return check
            except (OSError, subprocess.SubprocessError): pass
    if not getattr(sys, 'frozen', False): return [sys.executable]
    raise ValueError('Python 3.9以降を設定してください（launcher_settings.yaml: python_executable）')


def main():
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
    root=tk.Tk();root.title('ITC-C80 Launcher 1.0');root.geometry('500x170');root.resizable(False,False)
    status=tk.StringVar(value='更新を確認しています…')
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
            status.set('必要なライブラリを確認し、検査ウィンドウを起動しています…')
        except Exception as error:messagebox.showerror('起動失敗',str(error),parent=root);root.destroy()
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
                if kind=='status':status.set(value)
                elif kind=='done':start_application()
                elif kind=='error':
                    print('Update failed:',value)
                    unsafe=(root_dir/'data/updates/transaction.yaml').exists()
                    if not unsafe and not args.update_only and (root_dir/'src/bootstrap.py').exists() and messagebox.askyesno('更新失敗',value+'\n現在版を起動しますか？',parent=root):start_application()
                    else:messagebox.showerror('更新失敗',value,parent=root);root.destroy();return
        except queue.Empty:pass
        if child[0]:
            if ready.exists():root.destroy();return
            code=child[0].poll()
            if code is not None:
                if code:messagebox.showerror('起動失敗','data/logs/launcher.log を確認してください。',parent=root)
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
            root=tk.Tk();root.withdraw();messagebox.showerror('Launcher error',str(error),parent=root);root.destroy()
        except Exception:pass
        code=1
    raise SystemExit(code)
