"""Build versioned GitHub Release assets; never includes production data or passwords."""
import argparse
import hashlib
from pathlib import Path
import shutil
import zipfile
import yaml
from launcher_core import MANAGED, save_yaml

BASE = Path(__file__).resolve().parent


def build(version):
    folder = BASE / 'release_files'
    folder.mkdir(exist_ok=True)
    path = folder / 'update-manifest.yaml'
    previous = yaml.safe_load(path.read_text(encoding='utf-8')) if path.exists() else {}
    old = {item['path']: item for item in (previous or {}).get('files', [])}
    entries = []
    for relative in sorted(MANAGED):
        source = BASE / relative
        if not source.is_file(): raise ValueError('Missing release file: ' + relative)
        sha = hashlib.sha256(source.read_bytes()).hexdigest()
        prior = old.get(relative, {})
        asset = relative.replace('/', '__')
        shutil.copy2(source, folder / asset)
        entries.append({'path': relative, 'version': prior.get('version', version) if prior.get('sha256') == sha else version,
                        'asset': asset, 'size': source.stat().st_size, 'sha256': sha})
    manifest = {'schema': 1, 'version': version, 'entrypoint': 'step0.py', 'files': entries}
    save_yaml(path, manifest)
    save_yaml(BASE / 'update-manifest.yaml', manifest)
    executables = [path for path in [BASE / 'ITC-C80 Launcher.exe', *BASE.glob('ITC-C80 Launcher-*.exe')] if path.exists()]
    if executables:
        exe = max(executables, key=lambda path: path.stat().st_mtime)
        shutil.copy2(exe, folder / 'ITC-C80 Launcher.exe')
        with zipfile.ZipFile(BASE / 'ITC-C80-Launcher-Portable.zip', 'w', zipfile.ZIP_DEFLATED) as bundle:
            bundle.write(exe, 'ITC-C80 Launcher.exe')
            bundle.write(BASE / 'launcher_settings.yaml', 'launcher_settings.yaml')
            bundle.write(path, 'update-manifest.yaml')
            for relative in sorted(MANAGED): bundle.write(BASE / relative, relative)
    print('GitHub Release assets:', folder)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', default='2.1.0')
    build(parser.parse_args().version)
