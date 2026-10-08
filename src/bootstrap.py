import importlib
import os
import subprocess
import sys

# Redirected output on Korean Windows defaults to CP949. Japanese UI/log
# strings must use the same UTF-8 encoding as the launcher's log file.
os.environ['PYTHONUTF8'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'
for output in (sys.stdout, sys.stderr):
    if output is not None and hasattr(output, 'reconfigure'):
        output.reconfigure(encoding='utf-8', errors='backslashreplace')


# ============================================================
# Settings.
# ============================================================

TARGET_SCRIPT = "native_window.py"

# Import name to pip package mapping.
REQUIRED_MODULES = {
    "aiohttp": "aiohttp",
    "cv2": "opencv-python",
    "PIL": "pillow",
    "cryptography": "cryptography",
    "requests": "requests",
    "scapy": "scapy",
}


# ============================================================
# pip install
# ============================================================

def install_package(package_name):
    'Install packages into the active Python environment.'

    print(f"[Install] {package_name}")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            package_name
        ], creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0
    )

    return result.returncode == 0


# ============================================================
# Module Check
# ============================================================

def check_modules():

    print("========================================")
    print(' Module checks')
    print("========================================")

    failed = []

    for import_name, package_name in REQUIRED_MODULES.items():

        try:

            module = importlib.import_module(
                import_name
            )

            version = getattr(
                module,
                "__version__",
                ""
            )

            if version:
                print(
                    f"[OK] {import_name} "
                    f"({version})"
                )
            else:
                print(
                    f"[OK] {import_name}"
                )

        except ImportError:

            print(
                f'[NG] {import_name} unavailable'
            )

            print(
                f'     → {package_name} install'
            )

            success = install_package(
                package_name
            )

            if not success:

                failed.append(
                    package_name
                )

                continue

            # Recheck after installation.
            try:

                importlib.invalidate_caches()

                importlib.import_module(
                    import_name
                )

                print(
                    f'[OK] {import_name} installation complete'
                )

            except ImportError:

                failed.append(
                    package_name
                )

    return failed


# ============================================================
# tkinter Check
# ============================================================

def check_tkinter():
    'Tkinter is part of the Python installation, not a pip package.'

    try:

        import tkinter

        print("[OK] tkinter")

        return True

    except ImportError:

        print('[NG] tkinter is unavailable')
        print(
            'Reinstall Python.'
        )

        return False


# ============================================================
# Start inspection interface
# ============================================================

def start_step2():

    base_dir = os.path.dirname(os.path.abspath(__file__))

    script_path = os.path.join(
        base_dir,
        TARGET_SCRIPT
    )

    if not os.path.exists(
        script_path
    ):

        print()
        print(
            f'[NG] {TARGET_SCRIPT} not found'
        )

        print(
            'Place bootstrap.py and native_window.py'
        )

        print(
            'in the same folder.'
        )

        return False

    print()
    print("========================================")
    print(f' {TARGET_SCRIPT} starting')
    print("========================================")
    print()

    try:

        subprocess.run(
            [
                sys.executable,
                TARGET_SCRIPT
            ],
            cwd=base_dir, creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0, check=True
        )

        return True

    except Exception as e:

        print(
            f'[Startup Error] {e}'
        )

        return False


# ============================================================
# Main
# ============================================================

def main():

    print()
    print("========================================")
    print(" Camera Inspection Launcher")
    print("========================================")
    print()

    print(
        f"Python : {sys.version.split()[0]}"
    )

    print(
        f"Path   : {sys.executable}"
    )

    print()

    # --------------------------------------------------------
    # tkinter
    # --------------------------------------------------------

    if not check_tkinter():

        return False

    # --------------------------------------------------------
    # Python Modules
    # --------------------------------------------------------

    failed = check_modules()

    if failed:

        print()
        print("========================================")
        print(' Installation failed')
        print("========================================")

        for package in failed:

            print(
                f"- {package}"
            )

        return False

    # --------------------------------------------------------
    # Start
    # --------------------------------------------------------

    print()
    print('[OK] Required modules verified')

    return start_step2()


# ============================================================

if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
