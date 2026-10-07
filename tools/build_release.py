from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
"""Build versioned GitHub Release assets; never includes production data or passwords."""
import argparse
import hashlib
from pathlib import Path
import shutil
import zipfile
import yaml
from launcher_core import MANAGED, save_yaml

BASE = Path(__file__).resolve().parents[1]


def build(version):
    folder = BASE / 'release' / 'assets'
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
    manifest = {'schema': 1, 'version': version, 'entrypoint': 'src/bootstrap.py', 'files': entries}
    save_yaml(path, manifest)
    exe = BASE / 'release' / '1.0' / 'ITC-C80-Launcher-1.0.exe'
    if exe.exists():
        shutil.copy2(exe, folder / exe.name)
        with zipfile.ZipFile(BASE / 'release' / 'ITC-C80-Portable.zip', 'w', zipfile.ZIP_DEFLATED) as bundle:
            bundle.write(exe, exe.name)
            bundle.write(BASE / 'examples/launcher_settings.yaml', 'launcher_settings.yaml')
            bundle.write(path, 'update-manifest.yaml')
            for relative in sorted(MANAGED): bundle.write(BASE / relative, relative)
    print('GitHub Release assets:', folder)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', default='1.0')
    build(parser.parse_args().version)
