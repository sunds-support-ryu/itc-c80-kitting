import importlib
import os
import subprocess
import sys


# ============================================================
# 設定
# ============================================================

TARGET_SCRIPT = "app_server.py"

# import名 : pipパッケージ名
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
    """
    現在使用中のPython環境へパッケージをインストール。
    """

    print(f"[Install] {package_name}")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            package_name
        ]
    )

    return result.returncode == 0


# ============================================================
# Module Check
# ============================================================

def check_modules():

    print("========================================")
    print(" モジュール確認")
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
                f"[NG] {import_name} がありません"
            )

            print(
                f"     → {package_name} をインストール"
            )

            success = install_package(
                package_name
            )

            if not success:

                failed.append(
                    package_name
                )

                continue

            # インストール後に再確認
            try:

                importlib.invalidate_caches()

                importlib.import_module(
                    import_name
                )

                print(
                    f"[OK] {import_name} "
                    "インストール完了"
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
    """
    tkinterは通常Python標準。
    pipではなくPython本体に含まれます。
    """

    try:

        import tkinter

        print("[OK] tkinter")

        return True

    except ImportError:

        print("[NG] tkinter がありません")
        print(
            "Pythonを再インストールしてください。"
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
            f"[NG] {TARGET_SCRIPT} "
            "が見つかりません"
        )

        print(
            "bootstrap.py と app_server.py を"
        )

        print(
            "同じフォルダに置いてください。"
        )

        return False

    print()
    print("========================================")
    print(f" {TARGET_SCRIPT} 起動")
    print("========================================")
    print()

    try:

        subprocess.run(
            [
                sys.executable,
                TARGET_SCRIPT
            ],
            cwd=base_dir
        )

        return True

    except Exception as e:

        print(
            f"[起動 Error] {e}"
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

        input(
            "\nEnterキーで終了..."
        )

        return

    # --------------------------------------------------------
    # Python Modules
    # --------------------------------------------------------

    failed = check_modules()

    if failed:

        print()
        print("========================================")
        print(" インストール失敗")
        print("========================================")

        for package in failed:

            print(
                f"- {package}"
            )

        input(
            "\nEnterキーで終了..."
        )

        return

    # --------------------------------------------------------
    # Start
    # --------------------------------------------------------

    print()
    print("[OK] 必要なモジュール確認完了")

    start_step2()

    print()
    input(
        "Enterキーで終了..."
    )


# ============================================================

if __name__ == "__main__":
    main()
