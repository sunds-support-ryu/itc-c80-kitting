"""Build a source-only distribution without private data or packaged executables."""
from pathlib import Path
import sys
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from launcher_core import MANAGED


def build():
    root = Path(__file__).resolve().parents[1]
    destination = root / 'release' / 'ITC-C80-Script-Launcher.zip'
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for relative in sorted(MANAGED | {'start.vbs'}):
            bundle.write(root / relative, relative)
        bundle.write(root / 'examples' / 'launcher_settings.yaml', 'launcher_settings.yaml')
    print(destination)
    return destination


if __name__ == '__main__':
    build()
