"""
STEP1 - 外観証拠収集

Step2の四枠にUSB Camera映像を表示し、外観確認を必須とする。
外観OK/NGのどちらも確認完了後に初期化・RTSP検査へ進む。外観NGは撮影した問題箇所を
左ドラッグで囲み、右クリックで最後の枠を削除して確定・保存する。
単独実行時はUSB Camera選択、MAC入力、任意のSN入力で外観証拠を保存する。

保存規約は SAVE_LAYOUT.md を参照する。
画像: evidence/YYYY-MM-DD/B/B-{SN}_{MAC}_{日時}.jpg
記録: records/step1_evidence.csv
MACは必須、SNは任意。元画像解像度と元画像座標の枠を保存し、
既存画像を上書きしない。

AI MAINTENANCE RULES
===============================================================================

1. MACは必須
2. SNは任意
3. 保存画像名Prefixは B-
4. 元画像解像度で保存
5. UI表示用縮小画像と元画像座標を混同しない
6. NG枠は元画像座標で管理する
7. 既存画像を上書きしない
8. USB Camera読込でTkinter UIを止めない
9. Camera検索も別Threadで行う
10. 保存後はMAC / SNをクリアする
11. Cameraはユーザーが選択できるようにする
12. USB Camera再検索を可能にする

===============================================================================
"""

import csv
import ctypes
import importlib.util
import os
import re
import threading
import time
import uuid
from collections import deque
from datetime import datetime

import cv2
import tkinter as tk

from tkinter import messagebox, ttk
from PIL import Image, ImageTk, ImageDraw


# =============================================================================
# 基本設定
# =============================================================================

from app_paths import APP_ROOT, CODE_DIR
BASE_DIR = os.path.relpath(APP_ROOT)

_store_spec = importlib.util.spec_from_file_location("itc_evidence_store", os.path.join(CODE_DIR, "evidence_store.py"))
evidence_store = importlib.util.module_from_spec(_store_spec)
_store_spec.loader.exec_module(evidence_store)

EVIDENCE_DIR = os.path.join(
    BASE_DIR,
    "evidence"
)

CSV_FILE = os.path.join(
    BASE_DIR,
    "records",
    "step1_evidence.csv"
)


# =============================================================================
# USB Camera
# =============================================================================

CAMERA_SEARCH_START = 0
CAMERA_SEARCH_END = 9

CAMERA_WIDTH = 1920
CAMERA_HEIGHT = 1080

CAMERA_FPS = 30


# =============================================================================
# UI
# =============================================================================

WINDOW_WIDTH = 1180
WINDOW_HEIGHT = 930

VIDEO_WIDTH = 960
VIDEO_HEIGHT = 540

FONT = "Yu Gothic UI"


# =============================================================================
# Color
# =============================================================================

COLOR_BG = "#EEF1F5"

COLOR_CARD = "#FFFFFF"

COLOR_HEADER = "#17212B"

COLOR_TEXT = "#202630"

COLOR_SUBTEXT = "#667085"

COLOR_BORDER = "#D9DEE7"

COLOR_BLUE = "#2672EC"

COLOR_BLUE_HOVER = "#185ABD"

COLOR_GREEN = "#159447"

COLOR_GREEN_HOVER = "#107C3A"

COLOR_RED = "#D83B3B"

COLOR_ORANGE = "#E67E22"

COLOR_ORANGE_HOVER = "#C96916"

COLOR_GRAY = "#687386"

COLOR_GRAY_HOVER = "#525C6C"

COLOR_VIDEO = "#080A0D"

COLOR_DISABLED = "#AAB2BD"


# =============================================================================
# Camera Worker
# =============================================================================

class CameraWorker:
    """
    USB Cameraを別Threadで読み込む。

    Tkinter Main Threadでcap.read()を実行すると
    UIが止まる可能性があるため、
    Camera処理は別Threadで行う。
    """

    def __init__(
        self,
        camera_index
    ):

        self.camera_index = camera_index

        self.capture = None

        self.thread = None

        self.stop_event = (
            threading.Event()
        )

        self.frame_lock = (
            threading.Lock()
        )

        self.latest_frame = None

        self.connected = False

        self.error = None


    # =========================================================================
    # Start
    # =========================================================================

    def start(self):

        self.stop_event.clear()

        self.error = None

        self.latest_frame = None

        self.thread = threading.Thread(
            target=self.run,
            daemon=True
        )

        self.thread.start()


    # =========================================================================
    # Stop
    # =========================================================================

    def stop(self):

        self.stop_event.set()

        if self.capture is not None:

            try:

                self.capture.release()

            except Exception:

                pass

        self.capture = None

        self.connected = False


    # =========================================================================
    # Get Frame
    # =========================================================================

    def get_frame(self):

        with self.frame_lock:

            if self.latest_frame is None:

                return None

            return self.latest_frame.copy()


    # =========================================================================
    # Open Camera
    # =========================================================================

    def open_camera(self):

        cap = None


        # ---------------------------------------------------------------------
        # Windows
        # ---------------------------------------------------------------------

        if os.name == "nt":

            cap = cv2.VideoCapture(
                self.camera_index,
                cv2.CAP_DSHOW
            )

        else:

            cap = cv2.VideoCapture(
                self.camera_index
            )


        # ---------------------------------------------------------------------
        # Fallback
        # ---------------------------------------------------------------------

        if not cap.isOpened():

            cap.release()

            cap = cv2.VideoCapture(
                self.camera_index
            )


        if not cap.isOpened():

            return None


        # ---------------------------------------------------------------------
        # MJPG
        # ---------------------------------------------------------------------

        try:

            fourcc = (
                cv2.VideoWriter_fourcc(
                    *"MJPG"
                )
            )

            cap.set(
                cv2.CAP_PROP_FOURCC,
                fourcc
            )

        except Exception:

            pass


        # ---------------------------------------------------------------------
        # Resolution
        # ---------------------------------------------------------------------

        cap.set(
            cv2.CAP_PROP_FRAME_WIDTH,
            CAMERA_WIDTH
        )

        cap.set(
            cv2.CAP_PROP_FRAME_HEIGHT,
            CAMERA_HEIGHT
        )

        cap.set(
            cv2.CAP_PROP_FPS,
            CAMERA_FPS
        )


        return cap


    # =========================================================================
    # Thread
    # =========================================================================

    def run(self):

        self.capture = (
            self.open_camera()
        )


        if self.capture is None:

            self.error = (
                "USB Cameraを開けません。"
            )

            self.connected = False

            return


        self.connected = True


        while not self.stop_event.is_set():

            try:

                ret, frame = (
                    self.capture.read()
                )

            except Exception as e:

                self.error = str(e)

                time.sleep(
                    0.1
                )

                continue


            if not ret:

                time.sleep(
                    0.05
                )

                continue


            with self.frame_lock:

                self.latest_frame = frame


        try:

            self.capture.release()

        except Exception:

            pass


        self.connected = False


# =============================================================================
# MAC
# =============================================================================

def normalize_mac(value):
    """
    MAC入力を整形。

    対応例:

        AA:BB:CC:DD:EE:FF
        AA-BB-CC-DD-EE-FF
        AABBCCDDEEFF

    return:

        AA:BB:CC:DD:EE:FF
        AA-BB-CC-DD-EE-FF
    """

    compact = re.sub(
        r"[^0-9A-Fa-f]",
        "",
        value
    ).upper()


    if len(compact) != 12:

        return None, None


    display = ":".join(
        compact[i:i + 2]

        for i in range(
            0,
            12,
            2
        )
    )


    filename = "-".join(
        compact[i:i + 2]

        for i in range(
            0,
            12,
            2
        )
    )


    return (
        display,
        filename
    )


# =============================================================================
# JPEG Save
# =============================================================================

def save_jpeg(
    path,
    image,
    quality=95
):
    """
    日本語Path対策のため
    cv2.imwriteではなくimencode + tofileを使用。
    """

    success, encoded = cv2.imencode(
        ".jpg",
        image,
        [
            cv2.IMWRITE_JPEG_QUALITY,
            quality
        ]
    )


    if not success:

        return False


    try:

        encoded.tofile(
            path
        )

        return True

    except Exception:

        return False


# =============================================================================
# Main UI
# =============================================================================

class Step1UI:

    def __init__(
        self,
        root
    ):

        self.root = root


        # =====================================================================
        # Camera
        # =====================================================================

        self.camera = None

        self.camera_devices = {}


        # =====================================================================
        # Image
        # =====================================================================

        self.frozen_frame = None


        # 元画像座標
        self.boxes = []


        self.drag_start = None

        self.drag_current = None


        # =====================================================================
        # Canvas Mapping
        # =====================================================================

        self.render_scale = 1.0

        self.render_offset_x = 0

        self.render_offset_y = 0

        self.render_image_width = 0

        self.render_image_height = 0


        # =====================================================================
        # Mode
        # =====================================================================

        # LIVE
        # EDIT

        self.mode = "LIVE"


        # =====================================================================
        # Window
        # =====================================================================

        self.root.title(
            "Step1 - 外観証拠収集"
        )


        self.root.geometry(
            f"{WINDOW_WIDTH}x"
            f"{WINDOW_HEIGHT}"
        )


        self.root.minsize(
            1080,
            850
        )


        self.root.configure(
            bg=COLOR_BG
        )


        self.root.protocol(
            "WM_DELETE_WINDOW",
            self.close
        )


        # =====================================================================
        # Header
        # =====================================================================

        header = tk.Frame(
            root,
            bg=COLOR_HEADER,
            height=68
        )


        header.pack(
            fill="x"
        )


        header.pack_propagate(
            False
        )


        tk.Label(
            header,
            text="Step1  外観証拠収集",
            bg=COLOR_HEADER,
            fg="white",
            font=(
                FONT,
                21,
                "bold"
            )
        ).pack(
            side="left",
            padx=26
        )


        self.header_status = tk.Label(
            header,
            text="● Camera検索中",
            bg=COLOR_HEADER,
            fg="#D2D8E0",
            font=(
                FONT,
                11,
                "bold"
            )
        )


        self.header_status.pack(
            side="right",
            padx=26
        )


        # =====================================================================
        # Main
        # =====================================================================

        main = tk.Frame(
            root,
            bg=COLOR_BG
        )


        main.pack(
            fill="both",
            expand=True,
            padx=22,
            pady=16
        )


        # =====================================================================
        # Information Card
        # =====================================================================

        info_card = tk.Frame(
            main,
            bg=COLOR_CARD,
            highlightbackground=COLOR_BORDER,
            highlightthickness=1
        )


        info_card.pack(
            fill="x",
            pady=(
                0,
                12
            )
        )


        info_row = tk.Frame(
            info_card,
            bg=COLOR_CARD
        )


        info_row.pack(
            fill="x",
            padx=20,
            pady=14
        )


        # ---------------------------------------------------------------------
        # MAC
        # ---------------------------------------------------------------------

        tk.Label(
            info_row,
            text="MAC",
            bg=COLOR_CARD,
            fg=COLOR_SUBTEXT,
            font=(
                FONT,
                10,
                "bold"
            )
        ).grid(
            row=0,
            column=0,
            sticky="w"
        )


        self.mac_entry = tk.Entry(
            info_row,
            width=22,
            font=(
                "Consolas",
                14,
                "bold"
            ),
            relief="solid",
            bd=1
        )


        self.mac_entry.grid(
            row=1,
            column=0,
            padx=(
                0,
                24
            ),
            ipady=5
        )


        # ---------------------------------------------------------------------
        # SN
        # ---------------------------------------------------------------------

        tk.Label(
            info_row,
            text="SN  ※任意",
            bg=COLOR_CARD,
            fg=COLOR_SUBTEXT,
            font=(
                FONT,
                10,
                "bold"
            )
        ).grid(
            row=0,
            column=1,
            sticky="w"
        )


        self.sn_entry = tk.Entry(
            info_row,
            width=24,
            font=(
                "Consolas",
                14,
                "bold"
            ),
            relief="solid",
            bd=1
        )


        self.sn_entry.grid(
            row=1,
            column=1,
            padx=(
                0,
                24
            ),
            ipady=5
        )


        # ---------------------------------------------------------------------
        # USB Camera Label
        # ---------------------------------------------------------------------

        tk.Label(
            info_row,
            text="USB Camera",
            bg=COLOR_CARD,
            fg=COLOR_SUBTEXT,
            font=(
                FONT,
                10,
                "bold"
            )
        ).grid(
            row=0,
            column=2,
            sticky="w"
        )


        # ---------------------------------------------------------------------
        # USB Camera Combobox
        # ---------------------------------------------------------------------

        self.camera_combo = ttk.Combobox(
            info_row,
            width=18,
            state="readonly",
            font=(
                FONT,
                11
            )
        )


        self.camera_combo.grid(
            row=1,
            column=2,
            ipady=4
        )


        # ---------------------------------------------------------------------
        # 再検索
        # ---------------------------------------------------------------------

        self.camera_refresh_button = tk.Button(
            info_row,
            text="再検索",
            command=self.scan_usb_cameras,
            bg=COLOR_GRAY,
            fg="white",
            activebackground=COLOR_GRAY_HOVER,
            activeforeground="white",
            relief="flat",
            bd=0,
            cursor="hand2",
            font=(
                FONT,
                10,
                "bold"
            ),
            width=8
        )


        self.camera_refresh_button.grid(
            row=1,
            column=3,
            padx=(
                8,
                0
            ),
            ipady=5
        )


        # ---------------------------------------------------------------------
        # 接続
        # ---------------------------------------------------------------------

        self.camera_button = tk.Button(
            info_row,
            text="接続",
            command=self.connect_camera,
            bg=COLOR_BLUE,
            fg="white",
            activebackground=COLOR_BLUE_HOVER,
            activeforeground="white",
            relief="flat",
            bd=0,
            cursor="hand2",
            font=(
                FONT,
                10,
                "bold"
            ),
            width=8
        )


        self.camera_button.grid(
            row=1,
            column=4,
            padx=(
                8,
                0
            ),
            ipady=5
        )


        # =====================================================================
        # Status
        # =====================================================================

        status_card = tk.Frame(
            main,
            bg=COLOR_CARD,
            highlightbackground=COLOR_BORDER,
            highlightthickness=1
        )


        status_card.pack(
            fill="x",
            pady=(
                0,
                12
            )
        )


        self.status_label = tk.Label(
            status_card,
            text="USB Camera検索中...",
            bg=COLOR_CARD,
            fg=COLOR_SUBTEXT,
            font=(
                FONT,
                13,
                "bold"
            )
        )


        self.status_label.pack(
            side="left",
            padx=20,
            pady=10
        )


        self.help_label = tk.Label(
            status_card,
            text=(
                "左ドラッグ = 枠選択    "
                "右クリック = 1つ戻す"
            ),
            bg=COLOR_CARD,
            fg=COLOR_SUBTEXT,
            font=(
                FONT,
                10
            )
        )


        self.help_label.pack(
            side="right",
            padx=20
        )


        # =====================================================================
        # Video
        # =====================================================================

        video_card = tk.Frame(
            main,
            bg=COLOR_CARD,
            highlightbackground=COLOR_BORDER,
            highlightthickness=1
        )


        video_card.pack(
            pady=(
                0,
                12
            )
        )


        self.canvas = tk.Canvas(
            video_card,
            width=VIDEO_WIDTH,
            height=VIDEO_HEIGHT,
            bg=COLOR_VIDEO,
            highlightthickness=0,
            cursor="crosshair"
        )


        self.canvas.pack(
            padx=10,
            pady=10
        )


        # =====================================================================
        # Mouse
        # =====================================================================

        self.canvas.bind(
            "<ButtonPress-1>",
            self.mouse_down
        )


        self.canvas.bind(
            "<B1-Motion>",
            self.mouse_move
        )


        self.canvas.bind(
            "<ButtonRelease-1>",
            self.mouse_up
        )


        self.canvas.bind(
            "<Button-3>",
            self.undo_last_box
        )


        # =====================================================================
        # Controls
        # =====================================================================

        control_card = tk.Frame(
            main,
            bg=COLOR_CARD,
            highlightbackground=COLOR_BORDER,
            highlightthickness=1
        )


        control_card.pack(
            fill="x",
            pady=(
                0,
                12
            )
        )


        button_row = tk.Frame(
            control_card,
            bg=COLOR_CARD
        )


        button_row.pack(
            pady=14
        )


        # ---------------------------------------------------------------------
        # 撮影
        # ---------------------------------------------------------------------

        self.capture_button = tk.Button(
            button_row,
            text="撮影",
            command=self.capture_image,
            width=13,
            bg=COLOR_BLUE,
            fg="white",
            activebackground=COLOR_BLUE_HOVER,
            activeforeground="white",
            relief="flat",
            bd=0,
            cursor="hand2",
            font=(
                FONT,
                14,
                "bold"
            )
        )


        self.capture_button.pack(
            side="left",
            padx=8,
            ipady=10
        )


        # ---------------------------------------------------------------------
        # 再撮影
        # ---------------------------------------------------------------------

        self.retry_button = tk.Button(
            button_row,
            text="再撮影",
            command=self.retry_capture,
            width=13,
            bg=COLOR_ORANGE,
            fg="white",
            activebackground=COLOR_ORANGE_HOVER,
            activeforeground="white",
            relief="flat",
            bd=0,
            cursor="hand2",
            font=(
                FONT,
                14,
                "bold"
            )
        )


        self.retry_button.pack(
            side="left",
            padx=8,
            ipady=10
        )


        # ---------------------------------------------------------------------
        # Clear
        # ---------------------------------------------------------------------

        self.clear_button = tk.Button(
            button_row,
            text="枠を全削除",
            command=self.clear_boxes,
            width=13,
            bg=COLOR_GRAY,
            fg="white",
            activebackground=COLOR_GRAY_HOVER,
            activeforeground="white",
            relief="flat",
            bd=0,
            cursor="hand2",
            font=(
                FONT,
                14,
                "bold"
            )
        )


        self.clear_button.pack(
            side="left",
            padx=8,
            ipady=10
        )


        # ---------------------------------------------------------------------
        # Save
        # ---------------------------------------------------------------------

        self.save_button = tk.Button(
            button_row,
            text="確定・保存",
            command=self.confirm_save,
            width=16,
            bg=COLOR_GREEN,
            fg="white",
            activebackground=COLOR_GREEN_HOVER,
            activeforeground="white",
            relief="flat",
            bd=0,
            cursor="hand2",
            font=(
                FONT,
                15,
                "bold"
            )
        )


        self.save_button.pack(
            side="left",
            padx=18,
            ipady=10
        )


        # =====================================================================
        # Bottom
        # =====================================================================

        bottom_card = tk.Frame(
            main,
            bg=COLOR_CARD,
            highlightbackground=COLOR_BORDER,
            highlightthickness=1
        )


        bottom_card.pack(
            fill="x"
        )


        self.box_count_label = tk.Label(
            bottom_card,
            text="枠 : 0",
            bg=COLOR_CARD,
            fg=COLOR_TEXT,
            font=(
                FONT,
                11,
                "bold"
            )
        )


        self.box_count_label.pack(
            side="left",
            padx=18,
            pady=10
        )


        self.save_path_label = tk.Label(
            bottom_card,
            text="保存先 : evidence",
            bg=COLOR_CARD,
            fg=COLOR_SUBTEXT,
            font=(
                FONT,
                10
            )
        )


        self.save_path_label.pack(
            side="right",
            padx=18
        )


        # =====================================================================
        # Initial
        # =====================================================================

        self.update_buttons()


        self.update_preview()


        # 起動後USB Camera自動検索
        self.root.after(
            300,
            self.scan_usb_cameras
        )


    # =========================================================================
    # USB Camera Scan
    # =========================================================================

    def scan_usb_cameras(self):
        """
        Camera 0～9を検索。

        Camera検索中もUIを止めないため
        別Threadで実行。
        """

        # 現在Camera使用中の場合、
        # 再検索前に解放する。
        if self.camera is not None:

            self.camera.stop()

            self.camera = None


        self.mode = "LIVE"

        self.frozen_frame = None

        self.boxes.clear()

        self.drag_start = None

        self.drag_current = None


        self.camera_combo.set(
            "検索中..."
        )


        self.camera_combo.config(
            state="disabled"
        )


        self.camera_refresh_button.config(
            state="disabled"
        )


        self.camera_button.config(
            state="disabled"
        )


        self.status_label.config(
            text="USB Camera検索中...",
            fg=COLOR_BLUE
        )


        self.header_status.config(
            text="● Camera検索中",
            fg="#F6C85F"
        )


        threading.Thread(
            target=self._scan_usb_cameras_worker,
            daemon=True
        ).start()


    # =========================================================================
    # USB Camera Scan Worker
    # =========================================================================

    def _scan_usb_cameras_worker(self):

        found = []


        for index in range(
            CAMERA_SEARCH_START,
            CAMERA_SEARCH_END + 1
        ):

            cap = None


            try:

                if os.name == "nt":

                    cap = cv2.VideoCapture(
                        index,
                        cv2.CAP_DSHOW
                    )

                else:

                    cap = cv2.VideoCapture(
                        index
                    )


                if not cap.isOpened():

                    cap.release()

                    continue


                # 実際にFrame取得可能か確認
                ret, frame = (
                    cap.read()
                )


                if (
                    ret
                    and
                    frame is not None
                ):

                    found.append(
                        index
                    )


                cap.release()


            except Exception:

                if cap is not None:

                    try:

                        cap.release()

                    except Exception:

                        pass


        self.root.after(
            0,
            lambda: self._update_camera_list(
                found
            )
        )


    # =========================================================================
    # Update Camera List
    # =========================================================================

    def _update_camera_list(
        self,
        found
    ):

        self.camera_devices.clear()


        values = []


        for index in found:

            name = (
                f"USB Camera {index}"
            )


            values.append(
                name
            )


            self.camera_devices[
                name
            ] = index


        self.camera_combo[
            "values"
        ] = values


        self.camera_combo.config(
            state="readonly"
        )


        self.camera_refresh_button.config(
            state="normal"
        )


        if values:

            self.camera_combo.current(
                0
            )


            self.camera_button.config(
                state="normal"
            )


            self.status_label.config(
                text=(
                    f"USB Camera "
                    f"{len(values)}台 検出"
                ),
                fg=COLOR_GREEN
            )


            self.header_status.config(
                text="● Camera検出",
                fg="#6EE7A0"
            )


        else:

            self.camera_combo.set(
                "Cameraなし"
            )


            self.camera_button.config(
                state="disabled"
            )


            self.status_label.config(
                text="USB Cameraが見つかりません",
                fg=COLOR_RED
            )


            self.header_status.config(
                text="● Cameraなし",
                fg="#FF7777"
            )


        self.update_buttons()


    # =========================================================================
    # Connect Camera
    # =========================================================================

    def connect_camera(self):

        selected = (
            self.camera_combo
            .get()
        )


        if (
            not selected
            or
            selected not in self.camera_devices
        ):

            messagebox.showwarning(
                "USB Camera",
                "Cameraを選択してください。"
            )

            return


        camera_index = (
            self.camera_devices[
                selected
            ]
        )


        # ---------------------------------------------------------------------
        # Stop old Camera
        # ---------------------------------------------------------------------

        if self.camera is not None:

            self.camera.stop()


        # ---------------------------------------------------------------------
        # Reset State
        # ---------------------------------------------------------------------

        self.mode = "LIVE"

        self.frozen_frame = None

        self.boxes.clear()

        self.drag_start = None

        self.drag_current = None


        # ---------------------------------------------------------------------
        # Start Camera
        # ---------------------------------------------------------------------

        self.camera = CameraWorker(
            camera_index
        )


        self.camera.start()


        self.status_label.config(
            text=(
                f"{selected} 接続中..."
            ),
            fg=COLOR_BLUE
        )


        self.header_status.config(
            text="● 接続中",
            fg="#F6C85F"
        )


        self.update_buttons()


    # =========================================================================
    # Capture
    # =========================================================================

    def capture_image(self):

        if self.camera is None:

            messagebox.showwarning(
                "Camera",
                "USB Cameraを接続してください。"
            )

            return


        frame = (
            self.camera.get_frame()
        )


        if frame is None:

            messagebox.showwarning(
                "Camera",
                "映像がありません。"
            )

            return


        self.frozen_frame = frame

        self.boxes.clear()

        self.drag_start = None

        self.drag_current = None

        self.mode = "EDIT"


        self.status_label.config(
            text="問題箇所をマウスで選択してください",
            fg=COLOR_ORANGE
        )


        self.header_status.config(
            text="● 編集中",
            fg="#F6C85F"
        )


        self.update_buttons()


    # =========================================================================
    # Retry
    # =========================================================================

    def retry_capture(self):

        self.frozen_frame = None

        self.boxes.clear()

        self.drag_start = None

        self.drag_current = None

        self.mode = "LIVE"


        self.status_label.config(
            text="リアルタイム映像",
            fg=COLOR_GREEN
        )


        self.header_status.config(
            text="● Camera ON",
            fg="#6EE7A0"
        )


        self.update_buttons()

        self.update_box_count()


    # =========================================================================
    # Clear Boxes
    # =========================================================================

    def clear_boxes(self):

        if self.mode != "EDIT":

            return


        self.boxes.clear()

        self.drag_start = None

        self.drag_current = None

        self.update_box_count()


    # =========================================================================
    # Undo
    # =========================================================================

    def undo_last_box(
        self,
        event=None
    ):

        if self.mode != "EDIT":

            return


        if self.boxes:

            self.boxes.pop()


        self.drag_start = None

        self.drag_current = None

        self.update_box_count()


    # =========================================================================
    # Canvas -> Image
    # =========================================================================

    def canvas_to_image(
        self,
        canvas_x,
        canvas_y
    ):

        if self.render_scale <= 0:

            return None


        x = (
            canvas_x
            - self.render_offset_x
        )


        y = (
            canvas_y
            - self.render_offset_y
        )


        if (
            x < 0
            or
            y < 0
            or
            x >= self.render_image_width
            or
            y >= self.render_image_height
        ):

            return None


        image_x = int(
            x
            / self.render_scale
        )


        image_y = int(
            y
            / self.render_scale
        )


        return (
            image_x,
            image_y
        )


    # =========================================================================
    # Mouse Down
    # =========================================================================

    def mouse_down(
        self,
        event
    ):

        if self.mode != "EDIT":

            return


        pos = self.canvas_to_image(
            event.x,
            event.y
        )


        if pos is None:

            return


        self.drag_start = pos

        self.drag_current = pos


    # =========================================================================
    # Mouse Move
    # =========================================================================

    def mouse_move(
        self,
        event
    ):

        if self.mode != "EDIT":

            return


        if self.drag_start is None:

            return


        pos = self.canvas_to_image(
            event.x,
            event.y
        )


        if pos is None:

            return


        self.drag_current = pos


    # =========================================================================
    # Mouse Up
    # =========================================================================

    def mouse_up(
        self,
        event
    ):

        if self.mode != "EDIT":

            return


        if self.drag_start is None:

            return


        pos = self.canvas_to_image(
            event.x,
            event.y
        )


        if pos is None:

            self.drag_start = None

            self.drag_current = None

            return


        x1, y1 = (
            self.drag_start
        )


        x2, y2 = pos


        left = min(
            x1,
            x2
        )

        right = max(
            x1,
            x2
        )

        top = min(
            y1,
            y2
        )

        bottom = max(
            y1,
            y2
        )


        # 小さすぎる枠は無視
        if (
            right - left >= 8
            and
            bottom - top >= 8
        ):

            self.boxes.append(
                (
                    left,
                    top,
                    right,
                    bottom
                )
            )


        self.drag_start = None

        self.drag_current = None

        self.update_box_count()


    # =========================================================================
    # Draw Boxes
    # =========================================================================

    def draw_boxes(
        self,
        frame,
        include_drag=True
    ):

        output = frame.copy()


        # ---------------------------------------------------------------------
        # Saved Boxes
        # ---------------------------------------------------------------------

        for index, box in enumerate(
            self.boxes,
            start=1
        ):

            x1, y1, x2, y2 = box


            cv2.rectangle(
                output,
                (
                    x1,
                    y1
                ),
                (
                    x2,
                    y2
                ),
                (
                    0,
                    0,
                    255
                ),
                4
            )


            text = (
                f"NG {index}"
            )


            label_top = max(
                0,
                y1 - 32
            )


            cv2.rectangle(
                output,
                (
                    x1,
                    label_top
                ),
                (
                    x1 + 90,
                    y1
                ),
                (
                    0,
                    0,
                    255
                ),
                -1
            )


            cv2.putText(
                output,
                text,
                (
                    x1 + 6,
                    max(
                        23,
                        y1 - 7
                    )
                ),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (
                    255,
                    255,
                    255
                ),
                2,
                cv2.LINE_AA
            )


        # ---------------------------------------------------------------------
        # Current Drag Box
        # ---------------------------------------------------------------------

        if (
            include_drag
            and
            self.drag_start is not None
            and
            self.drag_current is not None
        ):

            x1, y1 = (
                self.drag_start
            )

            x2, y2 = (
                self.drag_current
            )


            cv2.rectangle(
                output,
                (
                    x1,
                    y1
                ),
                (
                    x2,
                    y2
                ),
                (
                    0,
                    165,
                    255
                ),
                3
            )


        return output


    # =========================================================================
    # Render
    # =========================================================================

    def render_frame(
        self,
        frame
    ):

        if frame is None:

            return


        if self.mode == "EDIT":

            frame = self.draw_boxes(
                frame,
                include_drag=True
            )


        image_height, image_width = (
            frame.shape[:2]
        )


        scale = min(
            VIDEO_WIDTH
            / image_width,

            VIDEO_HEIGHT
            / image_height
        )


        display_width = int(
            image_width
            * scale
        )


        display_height = int(
            image_height
            * scale
        )


        offset_x = int(
            (
                VIDEO_WIDTH
                - display_width
            )
            / 2
        )


        offset_y = int(
            (
                VIDEO_HEIGHT
                - display_height
            )
            / 2
        )


        # ---------------------------------------------------------------------
        # Mapping
        # ---------------------------------------------------------------------

        self.render_scale = scale

        self.render_offset_x = (
            offset_x
        )

        self.render_offset_y = (
            offset_y
        )

        self.render_image_width = (
            display_width
        )

        self.render_image_height = (
            display_height
        )


        # ---------------------------------------------------------------------
        # Resize
        # ---------------------------------------------------------------------

        resized = cv2.resize(
            frame,
            (
                display_width,
                display_height
            )
        )


        resized = cv2.cvtColor(
            resized,
            cv2.COLOR_BGR2RGB
        )


        image = Image.fromarray(
            resized
        )


        # ---------------------------------------------------------------------
        # Black Background
        # ---------------------------------------------------------------------

        background = Image.new(
            "RGB",
            (
                VIDEO_WIDTH,
                VIDEO_HEIGHT
            ),
            (
                8,
                10,
                13
            )
        )


        background.paste(
            image,
            (
                offset_x,
                offset_y
            )
        )


        photo = ImageTk.PhotoImage(
            background
        )


        self.canvas.delete(
            "all"
        )


        self.canvas.create_image(
            0,
            0,
            anchor="nw",
            image=photo
        )


        self.canvas.image = photo


    # =========================================================================
    # Preview Loop
    # =========================================================================

    def update_preview(self):

        frame = None


        if self.mode == "EDIT":

            frame = (
                self.frozen_frame
            )


        else:

            if self.camera is not None:

                frame = (
                    self.camera
                    .get_frame()
                )


        if frame is not None:

            self.render_frame(
                frame
            )


            if self.mode == "LIVE":

                self.status_label.config(
                    text="リアルタイム映像",
                    fg=COLOR_GREEN
                )


                self.header_status.config(
                    text="● Camera ON",
                    fg="#6EE7A0"
                )


        elif (
            self.camera is not None
            and
            self.camera.error
        ):

            self.status_label.config(
                text=self.camera.error,
                fg=COLOR_RED
            )


            self.header_status.config(
                text="● Camera NG",
                fg="#FF7777"
            )


        self.root.after(
            33,
            self.update_preview
        )


    # =========================================================================
    # Unique Filename
    # =========================================================================

    def get_unique_path(
        self,
        mac_filename
    ):

        from types import SimpleNamespace
        camera = SimpleNamespace(sn=self.sn_entry.get().strip() or "UNKNOWN", mac=mac_filename)
        return evidence_store.evidence_path(EVIDENCE_DIR, "B", camera, "jpg")


    # =========================================================================
    # CSV
    # =========================================================================

    def save_csv(
        self,
        sn,
        mac,
        filename
    ):

        os.makedirs(os.path.dirname(CSV_FILE), exist_ok=True)
        exists = os.path.exists(
            CSV_FILE
        )


        with open(
            CSV_FILE,
            "a",
            newline="",
            encoding="utf-8-sig"
        ) as f:

            writer = csv.writer(
                f
            )


            if not exists:

                writer.writerow([
                    "日時",
                    "SN",
                    "MAC",
                    "画像",
                    "枠数"
                ])


            writer.writerow([
                datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                sn,
                mac,
                filename,
                len(
                    self.boxes
                )
            ])


    # =========================================================================
    # Confirm Save
    # =========================================================================

    def confirm_save(self):

        if (
            self.mode != "EDIT"
            or
            self.frozen_frame is None
        ):

            messagebox.showwarning(
                "保存",
                "先に撮影してください。"
            )

            return


        # ---------------------------------------------------------------------
        # MAC
        # ---------------------------------------------------------------------

        raw_mac = (
            self.mac_entry
            .get()
            .strip()
        )


        mac_display, mac_filename = (
            normalize_mac(
                raw_mac
            )
        )


        if mac_display is None:

            messagebox.showerror(
                "MAC",
                (
                    "MACを確認してください。\n\n"
                    "例：AA:BB:CC:DD:EE:FF"
                )
            )


            self.mac_entry.focus_set()

            return


        # ---------------------------------------------------------------------
        # SN
        # ---------------------------------------------------------------------

        sn = (
            self.sn_entry
            .get()
            .strip()
        )


        if not sn:

            sn = "-"


        # ---------------------------------------------------------------------
        # Confirmation
        # ---------------------------------------------------------------------

        confirm_text = (
            f"MAC : {mac_display}\n"
            f"SN  : {sn}\n"
            f"枠  : {len(self.boxes)}\n\n"
            "保存しますか？"
        )


        result = (
            messagebox.askyesno(
                "保存確認",
                confirm_text
            )
        )


        if not result:

            return


        # ---------------------------------------------------------------------
        # Full Resolution Image
        # ---------------------------------------------------------------------

        output = self.draw_boxes(
            self.frozen_frame,
            include_drag=False
        )


        # ---------------------------------------------------------------------
        # Path
        # ---------------------------------------------------------------------

        path = self.get_unique_path(
            mac_filename
        )


        # ---------------------------------------------------------------------
        # Save Image
        # ---------------------------------------------------------------------

        success = save_jpeg(
            path,
            output,
            quality=95
        )


        if not success:

            messagebox.showerror(
                "保存NG",
                "画像を保存できません。"
            )

            return


        filename = os.path.basename(
            path
        )


        # ---------------------------------------------------------------------
        # CSV
        # ---------------------------------------------------------------------

        self.save_csv(
            sn,
            mac_display,
            os.path.relpath(path, os.path.dirname(CSV_FILE))
        )


        # ---------------------------------------------------------------------
        # Status
        # ---------------------------------------------------------------------

        self.save_path_label.config(
            text=(
                f"保存完了 : "
                f"{filename}"
            ),
            fg=COLOR_GREEN
        )


        self.status_label.config(
            text="保存完了",
            fg=COLOR_GREEN
        )


        # ---------------------------------------------------------------------
        # Clear Identity
        # ---------------------------------------------------------------------

        self.mac_entry.delete(
            0,
            "end"
        )


        self.sn_entry.delete(
            0,
            "end"
        )


        # ---------------------------------------------------------------------
        # Next
        # ---------------------------------------------------------------------

        self.frozen_frame = None

        self.boxes.clear()

        self.drag_start = None

        self.drag_current = None

        self.mode = "LIVE"


        self.update_box_count()

        self.update_buttons()


        self.mac_entry.focus_set()


    # =========================================================================
    # Box Count
    # =========================================================================

    def update_box_count(self):

        self.box_count_label.config(
            text=(
                f"枠 : "
                f"{len(self.boxes)}"
            )
        )


    # =========================================================================
    # Button State
    # =========================================================================

    def update_buttons(self):

        editing = (
            self.mode == "EDIT"
        )


        camera_available = (
            self.camera is not None
        )


        # ---------------------------------------------------------------------
        # EDIT
        # ---------------------------------------------------------------------

        if editing:

            self.capture_button.config(
                state="disabled",
                bg=COLOR_DISABLED
            )


            self.retry_button.config(
                state="normal",
                bg=COLOR_ORANGE
            )


            self.clear_button.config(
                state="normal",
                bg=COLOR_GRAY
            )


            self.save_button.config(
                state="normal",
                bg=COLOR_GREEN
            )


        # ---------------------------------------------------------------------
        # LIVE
        # ---------------------------------------------------------------------

        else:

            if camera_available:

                self.capture_button.config(
                    state="normal",
                    bg=COLOR_BLUE
                )

            else:

                self.capture_button.config(
                    state="disabled",
                    bg=COLOR_DISABLED
                )


            self.retry_button.config(
                state="disabled",
                bg=COLOR_DISABLED
            )


            self.clear_button.config(
                state="disabled",
                bg=COLOR_DISABLED
            )


            self.save_button.config(
                state="disabled",
                bg=COLOR_DISABLED
            )


    # =========================================================================
    # Close
    # =========================================================================

    def close(self):

        if self.camera is not None:

            self.camera.stop()


        self.root.destroy()


# =============================================================================
# Main
# =============================================================================

def list_usb_camera_devices():
    """DirectShowと同じ順序で名前を取得。列挙だけで映像デバイスを開かない。"""
    if os.name != "nt":
        return []
    class GUID(ctypes.Structure):
        _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16),
                    ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]
    class Variant(ctypes.Structure):
        _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort),
                    ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                    ("data", ctypes.c_void_p), ("extra", ctypes.c_void_p)]
    def guid(value):
        return GUID.from_buffer_copy(uuid.UUID(value).bytes_le)
    def invoke(pointer, slot, types, *args):
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *types)(table[slot])(pointer, *args)
    ole = ctypes.OleDLL("ole32")
    initialized = ole.CoInitialize(None) >= 0
    device_enum, monikers = ctypes.c_void_p(), ctypes.c_void_p()
    devices = []
    try:
        hr = ole.CoCreateInstance(ctypes.byref(guid("62BE5D10-60EB-11D0-BD3B-00A0C911CE86")),
                                  None, 1, ctypes.byref(guid("29840822-5B84-11D0-BD3B-00A0C911CE86")),
                                  ctypes.byref(device_enum))
        if hr < 0:
            return devices
        hr = invoke(device_enum, 3, [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p), ctypes.c_ulong],
                    ctypes.byref(guid("860BB310-5D01-11D0-BD3B-00A0C911CE86")), ctypes.byref(monikers), 0)
        if hr != 0:
            return devices
        index = 0
        while True:
            moniker, count = ctypes.c_void_p(), ctypes.c_ulong()
            if invoke(monikers, 3, [ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_ulong)],
                      1, ctypes.byref(moniker), ctypes.byref(count)) != 0:
                break
            bag = ctypes.c_void_p()
            name = f"Camera {index}"
            try:
                hr = invoke(moniker, 9, [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)],
                            None, None, ctypes.byref(guid("55272A00-42CB-11CE-8135-00AA004BB851")), ctypes.byref(bag))
                if hr == 0:
                    value = Variant()
                    try:
                        hr = invoke(bag, 3, [ctypes.c_wchar_p, ctypes.POINTER(Variant), ctypes.c_void_p],
                                    "FriendlyName", ctypes.byref(value), None)
                        if hr == 0 and value.vt == 8:
                            name = ctypes.wstring_at(value.data)
                    finally:
                        ctypes.OleDLL("oleaut32").VariantClear(ctypes.byref(value))
                devices.append((index, name))
            finally:
                if bag:
                    invoke(bag, 2, [])
                invoke(moniker, 2, [])
            index += 1
        return devices
    finally:
        if monikers:
            invoke(monikers, 2, [])
        if device_enum:
            invoke(device_enum, 2, [])
        if initialized:
            ole.CoUninitialize()


class FrameRate:
    """直近2秒の受信フレームから実測FPSを計算。再描画回数は数えない。"""
    def __init__(self):
        self.lock = threading.Lock()
        self.times = deque(maxlen=600)

    def record(self):
        now = time.monotonic()
        with self.lock:
            self.times.append(now)
            while self.times and now - self.times[0] > 2:
                self.times.popleft()

    def value(self):
        now = time.monotonic()
        with self.lock:
            while self.times and now - self.times[0] > 2:
                self.times.popleft()
            if len(self.times) < 2 or now - self.times[-1] > 1:
                return 0.0
            duration = self.times[-1] - self.times[0]
            return (len(self.times) - 1) / duration if duration > 0 else 0.0

    def reset(self):
        with self.lock:
            self.times.clear()


class InspectionUSBReader:
    """Step2共有USB入力。切断時に古い画像を破棄し、再接続を試みる。"""
    def __init__(self, index, name=None, retry=True):
        self.retry = retry
        self.done_event = threading.Event()
        self.index = index
        self.name = name
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.frame = None
        self.frame_time = 0
        self.frame_rate = FrameRate()
        self.error = "USB Camera 接続中"

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def stop(self):
        self.stop_event.set()
        self.frame_rate.reset()
        with self.lock:
            self.frame = None

    def get_frame(self):
        with self.lock:
            if self.frame is None or time.monotonic() - self.frame_time > 2 or self.stop_event.is_set():
                return None
            return self.frame.copy()

    def snapshot(self):
        frame = self.get_frame()
        return Image.fromarray(frame) if frame is not None else None

    def run(self):
        while not self.stop_event.is_set():
            cap = None
            try:
                if self.name and os.name == "nt":
                    devices = list_usb_camera_devices()
                    self.index = self.index if (self.index, self.name) in devices else next(
                        (index for index, name in devices if name == self.name), None)
                    if self.index is None:
                        raise RuntimeError("選択したUSB Cameraなし · 再接続待ち")
                cap = cv2.VideoCapture(self.index, cv2.CAP_DSHOW) if os.name == "nt" else cv2.VideoCapture(self.index)
                if not cap.isOpened():
                    raise RuntimeError("USB Cameraを開けません · 再接続待ち")
                # デバイス標準の映像形式を利用する。
                # USB 2.0 Cameraは1080p/MJPGを指定すると黒いフレームを返すことがある。
                while not self.stop_event.is_set():
                    ok, frame = cap.read()
                    if not ok:
                        raise RuntimeError("USB映像なし · 接続を確認してください")
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    with self.lock:
                        self.frame, self.frame_time = frame, time.monotonic()
                    self.frame_rate.record()
                    self.error = ""
                    self.stop_event.wait(.03)
            except Exception as error:
                self.error = str(error)
            finally:
                self.frame_rate.reset()
                with self.lock:
                    self.frame = None
                if cap is not None:
                    cap.release()
            if not self.retry:
                break
            self.stop_event.wait(1)
        self.done_event.set()


def save_exterior_evidence(camera, image, boxes, directory=EVIDENCE_DIR, csv_file=CSV_FILE):
    """元解像度の問題箇所とSN/MACを記録。既存画像は上書きしない。"""
    mac_display, mac_filename = normalize_mac(camera.mac)
    if mac_display is None:
        raise ValueError("外観証拠には有効なMACが必要です。")
    os.makedirs(directory, exist_ok=True)
    output = image.copy()
    draw = ImageDraw.Draw(output)
    for box in boxes:
        draw.rectangle(box, outline="red", width=max(2, image.width // 300))
    path = evidence_store.save_image(directory, "B", camera, output, "jpg")
    os.makedirs(os.path.dirname(csv_file) or ".", exist_ok=True)
    exists = os.path.exists(csv_file) and os.path.getsize(csv_file) > 0
    with open(csv_file, "a", newline="", encoding="utf-8-sig") as target:
        writer = csv.writer(target)
        if not exists:
            writer.writerow(["日時", "SN", "MAC", "画像", "枠数"])
        writer.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), camera.sn,
                         mac_display, os.path.relpath(path, os.path.dirname(csv_file) or "."), len(boxes)])
    return path


def preview_exterior_evidence(parent, camera, image):
    """凍結画像上で問題箇所を指定して保存。キャンセル時は外観検査を継続。"""
    window = tk.Toplevel(parent)
    window.title(f"外観証拠 · SN: {camera.sn} · MAC: {camera.mac}")
    window.transient(parent)
    tk.Label(window, text="左ドラッグ: 問題箇所を囲む / 右クリック: 最後の枠を削除").pack(padx=12, pady=8)
    preview = image.copy()
    preview.thumbnail((960, 540), Image.Resampling.LANCZOS)
    photo = ImageTk.PhotoImage(preview, master=window)
    canvas = tk.Canvas(window, width=preview.width, height=preview.height, highlightthickness=0)
    canvas.pack(padx=12)
    canvas.create_image(0, 0, anchor="nw", image=photo)
    boxes, start = [], []
    result = {"path": None}
    def point(event):
        return (max(0, min(preview.width - 1, event.x)), max(0, min(preview.height - 1, event.y)))
    def down(event):
        start[:] = point(event)
    def move(event):
        canvas.delete("drag")
        if start:
            canvas.create_rectangle(*start, *point(event), outline="red", width=2, tags="drag")
    def up(event):
        if not start:
            return
        x, y = point(event)
        x0, y0 = start
        start.clear()
        canvas.delete("drag")
        if abs(x - x0) < 3 or abs(y - y0) < 3:
            return
        display_box = (min(x, x0), min(y, y0), max(x, x0), max(y, y0))
        original = tuple(round(value * (image.width / preview.width if i % 2 == 0 else image.height / preview.height))
                         for i, value in enumerate(display_box))
        item = canvas.create_rectangle(*display_box, outline="red", width=2)
        boxes.append((original, item))
    def undo(event=None):
        if boxes:
            canvas.delete(boxes.pop()[1])
    def save():
        if not boxes:
            messagebox.showwarning("問題箇所", "問題箇所を枠で囲んでください。", parent=window)
            return
        try:
            result["path"] = save_exterior_evidence(camera, image, [box for box, item in boxes])
        except Exception as error:
            messagebox.showerror("保存失敗", str(error), parent=window)
            return
        window.destroy()
    canvas.bind("<Button-1>", down)
    canvas.bind("<B1-Motion>", move)
    canvas.bind("<ButtonRelease-1>", up)
    canvas.bind("<Button-3>", undo)
    controls = tk.Frame(window)
    controls.pack(fill="x", padx=12, pady=12)
    for label, action in [("最後の枠を削除", undo), ("確定・保存", save), ("キャンセル", window.destroy)]:
        tk.Button(controls, text=label, command=action).pack(side="left", expand=True, padx=5)
    window.grab_set()
    parent.wait_window(window)
    return result["path"]


def main():

    evidence_store.organize_records(BASE_DIR)

    os.makedirs(
        EVIDENCE_DIR,
        exist_ok=True
    )


    root = tk.Tk()


    Step1UI(
        root
    )


    root.mainloop()


# =============================================================================

if __name__ == "__main__":

    main()
