"""Console EXE bootstrap. Frozen launcher never uses its EXE as Python."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import yaml
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
                               check=True, timeout=10, capture_output=True)
                return check
            except (OSError, subprocess.SubprocessError): pass
    if not getattr(sys, 'frozen', False): return [sys.executable]
    raise ValueError('Python 3.9以降を設定してください（launcher_settings.yaml: python_executable）')


def main():
    if hasattr(sys.stdout, 'reconfigure'): sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
    parser = argparse.ArgumentParser()
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--update-only', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        from launcher_core import MANAGED
        print('ITC-C80 Launcher 1.0 / repository=' + DEFAULT_REPOSITORY + ' / managed files=' + str(len(MANAGED)))
        return 0
    location = Path(sys.executable if getattr(sys, 'frozen', False) else __file__).resolve().parent
    root = next((folder for folder in (location, *location.parents) if (folder / 'src/bootstrap.py').is_file()), location)
    os.chdir(root)
    config_path = root / 'launcher_settings.yaml'
    config = yaml.safe_load(config_path.read_text(encoding='utf-8')) if config_path.exists() else {}
    config = config or {}
    with InstanceLock(root / 'data' / 'launcher.lock'):
        with InstanceLock(root / 'data' / 'application.lock'):
            repository = configured_repository(config)
            if repository:
                updater = Updater(root, repository)
                try:
                    updater.update()
                    print('[OK] 更新確認完了')
                except Exception as error:
                    print('[更新失敗]', str(error))
                    if (root / 'data' / 'updates' / 'transaction.yaml').exists():
                        print('[NG] 更新の復旧が未完了です。現在版も起動しません')
                        return 1
                    if args.update_only or not (root / 'src/bootstrap.py').exists(): return 1
                    if input('現在版を起動しますか？ [y/N]: ').strip().lower() != 'y': return 1
                finally: updater.close()
            else:
                print('[INFO] GitHub未設定。ダウンロードなし、現在版を使用')
        if args.update_only: return 0
        command = python_command(root, str(config.get('python_executable') or '').strip())
        print('[Start] Camera Inspection')
        return subprocess.run(command + [str(root / 'src/bootstrap.py')], cwd=root).returncode


if __name__ == '__main__':
    try:
        code = main()
    except Exception as error:
        print('[NG]', str(error))
        code = 1
    if code and not any(argument in sys.argv for argument in ('--self-test', '--update-only')):
        input('Enterで終了...')
    raise SystemExit(code)
