'Camera inspection engine: HTTP identity, reset, USB/RTSP streaming and evidence. Native UI calls the shared engine through the owner-loop adapter.'

import asyncio
import csv
import importlib.util
import json
import os
import queue
import re
import threading
import tempfile
import time
import urllib.error
import urllib.request
from app_paths import APP_ROOT, CODE_DIR
from urllib.parse import quote
from collections import deque
from datetime import datetime

import aiohttp
import cv2
import tkinter as tk

from tkinter import messagebox, simpledialog
from PIL import Image, ImageTk

# Use the same Step1 implementation for direct execution and tests.
_step1_spec = importlib.util.spec_from_file_location(
    "itc_exterior", os.path.join(CODE_DIR, "appearance_inspection.py"))
exterior = importlib.util.module_from_spec(_step1_spec)
_step1_spec.loader.exec_module(exterior)


_step3_spec = importlib.util.spec_from_file_location(
    "itc_step3", os.path.join(CODE_DIR, "config_import.py"))
step3 = importlib.util.module_from_spec(_step3_spec)
_step3_spec.loader.exec_module(step3)

# =============================================================================
# Base settings.
# =============================================================================

NETWORK = "192.168.0."

USERNAME = "admin"
PASSWORD = os.environ.get("ITC_CAMERA_PASSWORD", "")


def redact(text):
    return str(text).replace(PASSWORD, "***") if PASSWORD else str(text)


# =============================================================================
# CGI
# =============================================================================

INFO_PATH = (
    "/cgi-bin/admin/admin.cgi"
    "?action=get.system.information&format=json"
)

RESET_PATH = (
    "/cgi-bin/admin/admin.cgi"
    "?action=set.system.reset&format=json"
)


# =============================================================================
# RTSP
# =============================================================================

RTSP_PORT = 554
RTSP_PATH = "/sub"

RTSP_RETRY_INTERVAL = 2


# =============================================================================
# Network
# =============================================================================

SCAN_INTERVAL = 3

PING_TIMEOUT_MS = 500

HTTP_TIMEOUT = 3

HTTP_CONCURRENCY = 40

RESET_CONCURRENCY = 6


# =============================================================================
# Reset / Reboot
# =============================================================================

RESET_INITIAL_WAIT = 3

OFFLINE_TIMEOUT = 30

ONLINE_TIMEOUT = 90

WEB_START_WAIT = 2


# =============================================================================
# CSV
# =============================================================================

CSV_FILE = os.path.join(os.path.relpath(APP_ROOT), "records", "camera_inspection.csv")


# =============================================================================
# UI
# =============================================================================

WINDOW_WIDTH = 1440
WINDOW_HEIGHT = 810

VIDEO_WIDTH = 960
VIDEO_HEIGHT = 540

FONT = "Yu Gothic UI"


# =============================================================================
# UI Color
# =============================================================================

COLOR_BG = "#EEF1F5"
COLOR_CARD = "#FFFFFF"

COLOR_HEADER = "#17212B"
COLOR_HEADER_TEXT = "#FFFFFF"

COLOR_TEXT = "#202630"
COLOR_SUBTEXT = "#667085"

COLOR_BORDER = "#D9DEE7"

COLOR_BLUE = "#2672EC"
COLOR_BLUE_HOVER = "#185ABD"

COLOR_GREEN = "#159447"
COLOR_GREEN_HOVER = "#107C3A"

COLOR_RED = "#D83B3B"
COLOR_RED_HOVER = "#B52E2E"

COLOR_DISABLED = "#AAB2BD"

COLOR_VIDEO = "#080A0D"


# =============================================================================
# Queue / Global
# =============================================================================

inspection_queue = queue.Queue()

event_queue = queue.Queue()

APP_STOP = threading.Event()
DEBUG_ENABLED = threading.Event()


def send_debug(text):
    if DEBUG_ENABLED.is_set():
        event_queue.put(("debug", redact(text)))



# asyncio
SCANNER_LOOP = None
STEP3_QUEUE = None

http_semaphore = None
reset_semaphore = None
device_lock = None


# =============================================================================
# Device State
# =============================================================================

# Currently processing.
active_sns = set()

# Inspection completed during this run.
processed_sns = set()


# Check SN/MAC consistency.
known_sn_mac = {}
known_mac_sn = {}


CSV_LOCK = threading.Lock()


# =============================================================================
# CameraInfo
# =============================================================================

class CameraInfo:

    def __init__(
        self,
        ip,
        sn,
        mac
    ):

        self.ip = ip
        self.sn = sn
        self.mac = mac


    def __repr__(self):

        return (
            f"IP={self.ip}, "
            f"SN={self.sn}, "
            f"MAC={self.mac}"
        )


# =============================================================================
# Event
# =============================================================================

def send_log(text):

    event_queue.put(
        (
            "log",
            text
        )
    )


def send_scan_status(text):

    event_queue.put(
        (
            "scan_status",
            text
        )
    )


# =============================================================================
# CSV
# =============================================================================

def save_result(
    camera,
    result,
    reset_result,
    ng_reason="",
    evidence_path=""
):

    with CSV_LOCK:

        os.makedirs((os.path.dirname(CSV_FILE) or "."), exist_ok=True)

        exists = os.path.exists(
            CSV_FILE
        )

        # Extend six-column legacy CSVs while preserving previous result rows.
        if exists and os.path.getsize(CSV_FILE):
            with open(CSV_FILE, newline="", encoding="utf-8-sig") as source:
                rows = list(csv.reader(source))
            columns = ['Timestamp', "SN", "MAC", "IP", 'Result', 'Reset', 'NG reason', 'Evidence']
            if rows and len(rows[0]) in (6, 7) and rows[0][1:4] == ['SN', 'MAC', 'IP']:
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8-sig",
                                                     dir=(os.path.dirname(CSV_FILE) or "."),
                                                     delete=False) as target:
                        temporary = target.name
                        writer = csv.writer(target)
                        writer.writerow(columns)
                        writer.writerows(row + [""] * (len(columns) - len(row)) for row in rows[1:])
                    os.replace(temporary, CSV_FILE)
                finally:
                    if temporary and os.path.exists(temporary):
                        os.unlink(temporary)
        else:
            exists = False

        with open(
            CSV_FILE,
            "a",
            newline="",
            encoding="utf-8-sig"
        ) as f:

            writer = csv.writer(f)

            if not exists:

                writer.writerow([
                    'Timestamp',
                    "SN",
                    "MAC",
                    "IP",
                    'Result',
                    'Reset',
                    'NG reason',
                    'Evidence'
                ])

            writer.writerow([
                datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                camera.sn,
                camera.mac,
                camera.ip,
                result,
                reset_result,
                ng_reason,
                os.path.relpath(evidence_path, (os.path.dirname(CSV_FILE) or ".")) if evidence_path else ""
            ])


# =============================================================================
# Ping
# =============================================================================

async def ping(ip):
    'Ping verifies reboot disconnection, not camera discovery.'

    try:

        process = await asyncio.create_subprocess_exec(
            "ping",
            "-n",
            "1",
            "-w",
            str(PING_TIMEOUT_MS),
            ip,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )

        return_code = await process.wait()

        return return_code == 0

    except Exception:

        return False


# =============================================================================
# JSON Helper
# =============================================================================

def normalize_key(key):

    return (
        str(key)
        .lower()
        .replace("_", "")
        .replace("-", "")
        .replace(" ", "")
    )


def find_value(
    data,
    target_keys
):
    'Recursively find SN/MAC across firmware-dependent JSON structures.'

    if isinstance(
        data,
        dict
    ):

        for key, value in data.items():

            key2 = normalize_key(
                key
            )

            if key2 in target_keys:

                if value is not None:

                    return str(
                        value
                    )

        for value in data.values():

            result = find_value(
                value,
                target_keys
            )

            if result is not None:

                return result


    elif isinstance(
        data,
        list
    ):

        for item in data:

            result = find_value(
                item,
                target_keys
            )

            if result is not None:

                return result


    return None


def normalize_mac(mac):

    if not mac:

        return "UNKNOWN"


    mac = (
        mac
        .strip()
        .upper()
        .replace("-", ":")
    )


    # Compact AABBCCDDEEFF format.
    simple = mac.replace(
        ":",
        ""
    )


    if (
        len(simple) == 12
        and
        ":" not in mac
    ):

        mac = ":".join(
            simple[i:i + 2]
            for i in range(
                0,
                12,
                2
            )
        )


    return mac


def extract_camera_info(
    ip,
    data
):

    sn_keys = {
        "sn",
        "sncode",
        "serial",
        "serialno",
        "serialnum",
        "serialnumber",
        "devicesn",
        "deviceserial",
        "deviceserialno",
        "deviceserialnumber"
    }


    mac_keys = {
        "mac",
        "macaddr",
        "macaddress",
        "ethernetmac",
        "ethmac",
        "networkmac"
    }


    sn = find_value(
        data,
        sn_keys
    )


    mac = find_value(
        data,
        mac_keys
    )


    if not sn:

        return None


    sn = sn.strip()

    mac = normalize_mac(
        mac
    )


    return CameraInfo(
        ip=ip,
        sn=sn,
        mac=mac
    )


# =============================================================================
# GET Camera Information
# =============================================================================

def camera_http_request(url, method="GET", body=None):
    'Basic/Digest authentication isolated per request.'
    started = time.monotonic()
    send_debug(f"HTTP {method} {url}")
    passwords = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    passwords.add_password(None, url, USERNAME, PASSWORD)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPDigestAuthHandler(passwords),
        urllib.request.HTTPBasicAuthHandler(passwords),
    )
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method=method)
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with opener.open(request, timeout=HTTP_TIMEOUT) as response:
            text = response.read().decode("utf-8-sig")
            send_debug(f'HTTP {response.status} {url} / {time.monotonic() - started:.2f}s / {len(text)}characters')
            return response.status, text
    except urllib.error.HTTPError as error:
        auth_scheme = error.headers.get("WWW-Authenticate", 'None').split(" ")[0]
        send_debug(f'HTTP {error.code} {url} / authentication={auth_scheme}')
        return error.code, error.read().decode("utf-8", errors="replace")


# Do not repeat the same discovery error on every scan.
info_errors = {}


def report_info_error(ip, reason):
    if info_errors.get(ip) != reason:
        info_errors[ip] = reason
        send_log(f'[Discovery NG] {ip} / {reason}')


async def get_camera_info(session, ip):
    'Discover through CGI with blocking requests on worker threads.'
    url = f"http://{ip}{INFO_PATH}"
    try:
        async with http_semaphore:
            status, text = await asyncio.to_thread(camera_http_request, url)
        if status != 200:
            report_info_error(ip, f'HTTP={status}(check authentication and CGI settings)')
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            report_info_error(ip, 'CGI response is not JSON')
            return None
        camera = extract_camera_info(ip, data)
        if camera is None:
            report_info_error(ip, 'CGI response has no SN')
        else:
            info_errors.pop(ip, None)
            send_debug(f'CGI identity OK: {camera}')
        return camera
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        send_debug(f'CGI connection failed {ip}: {type(error).__name__}: {error}')
        return None
    except Exception as error:
        report_info_error(ip, f"{type(error).__name__}: {error}")
        return None


# =============================================================================
# Reset
# =============================================================================

async def reset_device(
    session,
    camera,
    option
):
    'Option 3 resets while retaining IP; option 2 is managed by config import after authentication changes.'

    if option != 3:
        send_log(f'[Reset rejected] Step2 only allows option=3 / SN={camera.sn}')
        return False

    url = (
        f"http://"
        f"{camera.ip}"
        f"{RESET_PATH}"
    )


    body = {
        "options": option
    }


    if option == 3:

        name = 'Reset retaining IP'

    else:

        name = 'Reset'


    send_log(
        f"[{name}] "
        f"{camera.ip} / "
        f"SN={camera.sn}"
    )


    try:

        async with reset_semaphore:

            status, text = await asyncio.to_thread(
                camera_http_request, url, "POST", body
            )
            if 200 <= status < 300:
                send_log(f"[{name} OK] SN={camera.sn}")
                return True
            send_log(f"[{name} NG] HTTP={status} / SN={camera.sn}")
            return False


    except Exception as e:

        send_log(
            f"[{name} Error] "
            f"SN={camera.sn} / "
            f"{e}"
        )

        return False


# =============================================================================
# Wait for reboot after reset.
# =============================================================================

async def wait_reboot(
    session,
    camera
):
    'Verify offline, online and CGI recovery after reset option 3.'

    ip = camera.ip


    send_log(
        f'[Waiting for reboot] {ip}'
    )


    await asyncio.sleep(
        RESET_INITIAL_WAIT
    )


    # =========================================================================
    # Verify offline state.
    # =========================================================================

    offline_found = False

    start_time = time.monotonic()


    while (
        time.monotonic()
        - start_time
        < OFFLINE_TIMEOUT
    ):

        if APP_STOP.is_set():

            return False


        alive = await ping(
            ip
        )


        if not alive:

            offline_found = True

            send_log(
                f"[Offline] "
                f"{ip}"
            )

            break


        await asyncio.sleep(
            1
        )


    if not offline_found:

        send_log(
            f'[Offline unverified] {ip}'
        )


    # =========================================================================
    # Verify CGI recovery.
    # =========================================================================

    start_time = time.monotonic()


    while (
        time.monotonic()
        - start_time
        < ONLINE_TIMEOUT
    ):

        if APP_STOP.is_set():

            return False


        camera_after = await get_camera_info(
            session,
            ip
        )


        if camera_after is not None:

            send_log(
                f"[Online] "
                f"{ip}"
            )

            return True


        await asyncio.sleep(
            SCAN_INTERVAL
        )


    send_log(
        f"[Online NG] "
        f"{ip}"
    )

    return False


# =============================================================================
# Camera State
# =============================================================================

async def claim_camera(
    camera
):
    'Reserve each camera by serial number, not just IP.'

    async with device_lock:

        sn = camera.sn
        mac = camera.mac


        # =====================================================================
        # SN → MAC
        # =====================================================================

        if sn in known_sn_mac:

            old_mac = (
                known_sn_mac[
                    sn
                ]
            )


            if (
                old_mac != "UNKNOWN"
                and
                mac != "UNKNOWN"
                and
                old_mac != mac
            ):

                send_log(
                    f'[Warning] Same SN / different MAC SN={sn} / {old_mac} → {mac}'
                )


        else:

            known_sn_mac[
                sn
            ] = mac


        # =====================================================================
        # MAC → SN
        # =====================================================================

        if mac != "UNKNOWN":

            if mac in known_mac_sn:

                old_sn = (
                    known_mac_sn[
                        mac
                    ]
                )


                if old_sn != sn:

                    send_log(
                        f'[Warning] Same MAC / different SN MAC={mac} / {old_sn} → {sn}'
                    )


            else:

                known_mac_sn[
                    mac
                ] = sn


        # =====================================================================
        # Completed.
        # =====================================================================

        if sn in processed_sns:

            return False


        # =====================================================================
        # Processing.
        # =====================================================================

        if sn in active_sns:

            return False


        active_sns.add(
            sn
        )


        return True


async def release_camera(sn):

    async with device_lock:

        active_sns.discard(
            sn
        )


async def complete_camera(sn):

    async with device_lock:

        active_sns.discard(
            sn
        )

        processed_sns.add(
            sn
        )


# =============================================================================
# IP Monitor
# =============================================================================

async def reserve_reboot_panel(camera):
    'Wait for required appearance inspection before resetting.'
    future = asyncio.get_running_loop().create_future()
    event_queue.put(("camera_reboot", camera, future))
    try:
        while not APP_STOP.is_set():
            try:
                return await asyncio.wait_for(asyncio.shield(future), timeout=0.5)
            except asyncio.TimeoutError:
                continue
        return False
    finally:
        if not future.done():
            future.cancel()


def resolve_panel_future(future, accepted=True):
    if not future.done():
        future.set_result(accepted)


async def monitor_ip(
    session,
    ip
):
    'Continuously monitor an IP and process a newly connected camera when its SN changes.'

    last_seen_sn = None


    while not APP_STOP.is_set():

        # =====================================================================
        # Camera discovery.
        #
        # Do not use ping for discovery.
        # Verify that CGI provides SN and MAC.
        # =====================================================================

        camera = await get_camera_info(
            session,
            ip
        )


        if camera is None:

            last_seen_sn = None

            await asyncio.sleep(
                SCAN_INTERVAL
            )

            continue


        # =====================================================================
        # Discovered.
        # =====================================================================

        if camera.sn != last_seen_sn:

            send_log(
                f'[Discovery] IP={camera.ip} / SN={camera.sn} / MAC={camera.mac}'
            )

            last_seen_sn = (
                camera.sn
            )


        # =====================================================================
        # Reserve SN.
        # =====================================================================

        claimed = await claim_camera(
            camera
        )


        if not claimed:

            await asyncio.sleep(
                SCAN_INTERVAL
            )

            continue


        # =====================================================================
        # option=3
        # =====================================================================

        # Reserve a panel immediately after identification and show USB appearance video.
        if not await reserve_reboot_panel(camera):
            await release_camera(camera.sn)
            if APP_STOP.is_set():
                return
            await asyncio.sleep(SCAN_INTERVAL)
            continue

        reset_ok = await reset_device(
            session,
            camera,
            3
        )


        if not reset_ok:

            event_queue.put(("camera_failed", camera.sn))

            await release_camera(
                camera.sn
            )

            await asyncio.sleep(
                SCAN_INTERVAL
            )

            continue


        # =====================================================================
        # Reboot
        # =====================================================================

        reboot_ok = await wait_reboot(
            session,
            camera
        )


        if not reboot_ok:

            event_queue.put(("camera_failed", camera.sn))
            await release_camera(
                camera.sn
            )

            continue


        await asyncio.sleep(
            WEB_START_WAIT
        )


        # =====================================================================
        # Recheck SN/MAC.
        # =====================================================================

        camera_after = await get_camera_info(
            session,
            ip
        )


        if camera_after is None:

            send_log(
                f'[Recheck NG] {ip}'
            )

            event_queue.put(("camera_failed", camera.sn))
            await release_camera(
                camera.sn
            )

            continue


        # =====================================================================
        # SN changed.
        # =====================================================================

        if (
            camera_after.sn
            != camera.sn
        ):

            send_log(
                f'[SN changed] {camera.sn} → {camera_after.sn}'
            )

            event_queue.put(("camera_failed", camera.sn))
            await release_camera(
                camera.sn
            )

            last_seen_sn = (
                camera_after.sn
            )

            continue


        # =====================================================================
        # Update MAC.
        # =====================================================================

        if (
            camera_after.mac
            != "UNKNOWN"
        ):

            camera.mac = (
                camera_after.mac
            )


        # =====================================================================
        # Human Inspection Queue
        # =====================================================================

        send_log(
            f'[Waiting for inspection] SN={camera.sn} / MAC={camera.mac}'
        )


        event_queue.put(("camera_ready", camera))


        # The SN remains in active_sns.
        # Do not register the same camera twice.

        await asyncio.sleep(
            SCAN_INTERVAL
        )


# =============================================================================
# Step3 Config Worker
# =============================================================================

async def step3_worker(session):
    'Import config only for approved cameras; reset IP with option 2.'
    def progress(camera, text):
        send_log(f"[Step3] SN={camera.sn} / {text}")
        event_queue.put(("step3_progress", camera.sn, text))
    while not APP_STOP.is_set():
        try:
            camera = await asyncio.wait_for(STEP3_QUEUE.get(), timeout=1)
        except asyncio.TimeoutError:
            continue
        base_dir = os.path.relpath(APP_ROOT)
        success, detail = await asyncio.to_thread(
            step3.run, camera, base_dir, (USERNAME, PASSWORD),
            lambda text: progress(camera, text),
            stopped=APP_STOP.is_set)
        save_result(camera, "OK" if success else "NG", "Step3 OK" if success else "Step3 NG")
        send_log(f"[Step3 {'OK' if success else 'NG'}] SN={camera.sn} / {detail}")
        event_queue.put(("step3_result", camera, success, detail))


# =============================================================================
# Async Scanner
# =============================================================================

async def scanner_main(
    start_ip,
    end_ip
):

    global SCANNER_LOOP
    global STEP3_QUEUE

    global http_semaphore
    global reset_semaphore
    global device_lock


    SCANNER_LOOP = (
        asyncio.get_running_loop()
    )


    STEP3_QUEUE = (
        asyncio.Queue()
    )


    http_semaphore = (
        asyncio.Semaphore(
            HTTP_CONCURRENCY
        )
    )


    reset_semaphore = (
        asyncio.Semaphore(
            RESET_CONCURRENCY
        )
    )


    device_lock = (
        asyncio.Lock()
    )


    ips = [
        f"{NETWORK}{number}"

        for number in range(
            start_ip,
            end_ip + 1
        )
    ]


    send_scan_status(
        'Searching'
    )


    send_log(
        f'[Discovery started] {ips[0]} ～ {ips[-1]}'
    )


    connector = aiohttp.TCPConnector(
        limit=HTTP_CONCURRENCY
    )


    async with aiohttp.ClientSession(
        connector=connector
    ) as session:


        tasks = []


        # =====================================================================
        # 1 IP = 1 Monitor Task
        # =====================================================================

        for ip in ips:

            tasks.append(
                asyncio.create_task(
                    monitor_ip(
                        session,
                        ip
                    )
                )
            )


        # =====================================================================
        # Step3 Config Worker
        # =====================================================================

        tasks.append(
            asyncio.create_task(
                step3_worker(
                    session
                )
            )
        )


        await asyncio.gather(
            *tasks,
            return_exceptions=True
        )


def scanner_thread(
    start_ip,
    end_ip
):

    try:

        asyncio.run(
            scanner_main(
                start_ip,
                end_ip
            )
        )


    except Exception as e:

        send_scan_status(
            "Error"
        )

        send_log(
            f"[Scanner Error] "
            f"{e}"
        )


# =============================================================================
# UI → Async
# =============================================================================

def mark_completed(sn):

    if SCANNER_LOOP is None:

        return


    asyncio.run_coroutine_threadsafe(
        complete_camera(
            sn
        ),
        SCANNER_LOOP
    )


def request_step3(
    camera
):

    if SCANNER_LOOP is None:

        return


    if STEP3_QUEUE is None:

        return


    asyncio.run_coroutine_threadsafe(
        STEP3_QUEUE.put(
            camera
        ),
        SCANNER_LOOP
    )


# =============================================================================
# RTSP Reader
# =============================================================================

class RTSPReader:

    def __init__(
        self,
        camera
    ):

        self.camera = camera
        self.frames = queue.Queue(maxsize=1)
        self.frame_lock = threading.Lock()
        self.latest_frame = None
        self.latest_frame_time = 0
        self.frame_rate = exterior.FrameRate()
        self.last_status = 'RTSP connecting...'


        self.url = (
            f"rtsp://"
            f"{quote(USERNAME, safe='')}:"
            f"{quote(PASSWORD, safe='')}@"
            f"{camera.ip}:"
            f"{RTSP_PORT}"
            f"{RTSP_PATH}"
        )


        self.stop_event = (
            threading.Event()
        )


        self.thread = (
            threading.Thread(
                target=self.run,
                daemon=True
            )
        )


    def snapshot(self):
        'Take a frozen original-resolution RGB frame before display resizing.'
        with self.frame_lock:
            frame = None if self.latest_frame is None or time.monotonic() - self.latest_frame_time > 5 else self.latest_frame.copy()
        return None if frame is None else Image.fromarray(frame)

    def start(self):

        self.thread.start()


    def stop(self):

        self.stop_event.set()
        self.frame_rate.reset()


    def send_status(
        self,
        status
    ):

        self.last_status = status
        if "NG" in status or "Error" in status:
            self.frame_rate.reset()
            with self.frame_lock:
                self.latest_frame = None
        send_debug(f"RTSP {self.camera.ip}: {status}")
        event_queue.put(
            (
                "stream_status",
                self.camera.sn,
                status
            )
        )


    def create_capture(self):

        send_debug(f'RTSP connection rtsp://{self.camera.ip}:{RTSP_PORT}{RTSP_PATH}')
        cap = cv2.VideoCapture(
            self.url, cv2.CAP_FFMPEG,
            [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
             cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000]
        )


        try:

            cap.set(
                cv2.CAP_PROP_BUFFERSIZE,
                1
            )

        except Exception:

            pass


        return cap


    def run(self):

        self.send_status(
            'RTSP connecting...'
        )


        while not self.stop_event.is_set():

            try:
                cap = self.create_capture()
            except Exception as error:
                send_log(f"[RTSP Error] {self.camera.ip} / {type(error).__name__}: {redact(str(error))}")
                self.send_status('RTSP NG / reconnecting...')
                self.stop_event.wait(RTSP_RETRY_INTERVAL)
                continue


            if not cap.isOpened():

                self.send_status(
                    'RTSP NG / reconnecting...'
                )

                cap.release()

                time.sleep(
                    RTSP_RETRY_INTERVAL
                )

                continue


            first_frame = True


            while not self.stop_event.is_set():

                ret, frame = cap.read()


                if not ret:

                    self.send_status(
                        'Video NG / reconnecting...'
                    )

                    break


                if first_frame:
                    self.send_status('Checking video')
                    send_debug(f'Video size {frame.shape[1]}x{frame.shape[0]}')
                    first_frame = False

                # BGR -> RGB
                frame = cv2.cvtColor(
                    frame,
                    cv2.COLOR_BGR2RGB
                )


                self.frame_rate.record()
                with self.frame_lock:
                    self.latest_frame = frame
                    self.latest_frame_time = time.monotonic()

                height, width = (
                    frame.shape[:2]
                )


                scale = min(
                    VIDEO_WIDTH / width,
                    VIDEO_HEIGHT / height
                )


                new_width = int(
                    width * scale
                )

                new_height = int(
                    height * scale
                )


                frame = cv2.resize(
                    frame,
                    (
                        new_width,
                        new_height
                    )
                )


                # Each reader owns a queue; never discard another camera's frames.
                try:
                    self.frames.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self.frames.put_nowait(frame)
                except queue.Full:
                    pass


            cap.release()


            if not self.stop_event.is_set():

                time.sleep(
                    RTSP_RETRY_INTERVAL
                )


# =============================================================================
# UI
# =============================================================================

class CameraPanel:
    'Single-camera frames, state and decisions; Tk operations stay on the UI thread.'
    def __init__(self, app, parent, index):
        self.app, self.index = app, index
        self.camera = self.reader = None
        self.exterior_done = False
        self.stream_ready = False
        self.preparation_future = None
        self.last_camera = None
        self.processing_camera = None
        self.result_state = None
        self.result_hold_until = 0
        self.blink_timer = None
        self.blink_visible = True
        self.log_lines = deque(maxlen=5)
        self.card = tk.Frame(parent, bg=COLOR_CARD, highlightthickness=2,
                             highlightbackground=COLOR_BORDER)
        self.title = tk.Label(self.card, text=f'Camera {index + 1} - disconnected', bg=COLOR_CARD,
                              anchor="w", font=(FONT, 10, "bold"))
        self.title.pack(fill="x", padx=8, pady=4)
        self.status = tk.Label(self.card, text='Waiting for camera', bg=COLOR_CARD,
                               fg=COLOR_SUBTEXT, font=(FONT, 10))
        self.status.pack(fill="x")
        self.host = tk.Frame(self.card, bg=COLOR_VIDEO)
        self.host.pack(fill="both", expand=True, padx=6, pady=6)
        self.video_frame = tk.Frame(self.host, bg=COLOR_VIDEO)
        self.video_frame.place(relx=.5, rely=.5, anchor="center", width=16, height=9)
        self.video = tk.Label(self.video_frame, text='Waiting for camera', bg=COLOR_VIDEO,
                              fg="#7F8792", font=(FONT, 22, "bold"), bd=0)
        self.video.pack(fill="both", expand=True)
        self.fps_label = tk.Label(self.video_frame, text="", bg=COLOR_VIDEO,
                                  fg="#A8E6CF", font=(FONT, 10, "bold"), padx=5, pady=2)
        self.status_detail = tk.Label(self.video_frame, text="", bg=COLOR_VIDEO,
                                      fg="#C6CCD4", font=(FONT, 11), justify="center")
        self.host.bind("<Configure>", self.resize)
        controls = tk.Frame(self.card, bg=COLOR_CARD)
        controls.pack(fill="x", padx=6, pady=(0, 6))
        self.ok = tk.Button(controls, text="OK", bg=COLOR_GREEN, fg="white",
                            state="disabled", command=lambda: app.mark_ok(index))
        self.ok.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.ng = tk.Button(controls, text='NG (check image)', bg=COLOR_RED, fg="white",
                            state="disabled", command=lambda: app.mark_ng(index))
        self.ng.pack(side="left", fill="x", expand=True)
        self.log_box = tk.Text(self.card, height=3, wrap="word", font=(FONT, 9),
                               relief="flat", state="disabled", bg=COLOR_CARD)
        self.log_box.pack(fill="x", padx=6, pady=(0, 6))
        for widget in (self.card, self.title, self.status, self.host, self.video):
            widget.bind("<Button-1>", lambda event: app.select_panel(index))

    def resize(self, event):
        width = max(16, min(event.width, event.height * 16 // 9))
        self.video_frame.place_configure(width=width, height=width * 9 // 16)
        self.video.config(wraplength=max(16, width - 32))
        self.status_detail.config(wraplength=max(16, width - 32))

    def show_message(self, text, color=COLOR_BLUE):
        self.video.config(image="", text=text, font=(FONT, 22, "bold"), fg=color)
        self.video.image = None
        self.status_detail.place_forget()

    def update_fps(self, reader, source):
        if reader is None:
            self.fps_label.place_forget()
            return
        meter = getattr(reader, "frame_rate", None)
        value = meter.value() if meter is not None else 0.0
        self.fps_label.config(text=f"{source}  {value:.1f} FPS")
        self.fps_label.place(relx=1, x=-6, y=6, anchor="ne")
        self.fps_label.lift()

    def add_log(self, text):
        self.log_lines.append(f"{datetime.now():%H:%M:%S} {text}")
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.insert("end", "\n".join(self.log_lines))
        self.log_box.see("end")
        self.log_box.config(state="disabled")

    def reserve(self, camera, preparation_future=None):
        self.clear_result()
        self.camera = self.last_camera = camera
        self.exterior_done = False
        self.stream_ready = False
        self.preparation_future = preparation_future
        self.log_lines.clear()
        self.title.config(text=f'Camera {self.index + 1} · {camera.ip} · SN: {camera.sn}')
        self.status.config(text='Waiting for appearance inspection - USB camera', fg=COLOR_BLUE)
        self.show_message('Step1\nWaiting for appearance inspection')
        self.ok.config(state="disabled")
        self.ng.config(state="disabled")
        self.add_log('Waiting for appearance inspection - reset follows confirmation')

    def start(self, camera):
        if self.camera is None:
            self.reserve(camera)
        self.camera = self.last_camera = camera
        self.status.config(text='Connecting video', fg=COLOR_BLUE)
        self.show_message('Step2\nRTSP connecting')
        self.ok.config(state="normal")
        self.ng.config(state="normal")
        self.reader = RTSPReader(camera)
        self.reader.start()
        self.add_log('Connecting video')

    def finish(self, result=None):
        self.clear_result()
        if self.preparation_future is not None:
            future = self.preparation_future
            self.preparation_future = None
            future.get_loop().call_soon_threadsafe(resolve_panel_future, future, False)
        last_sn = self.camera.sn if self.camera else "—"
        if self.reader is not None:
            self.reader.stop()
        self.reader = self.camera = None
        self.exterior_done = False
        self.stream_ready = False
        self.title.config(text=f'Camera {self.index + 1} - disconnected')
        self.status.config(text=f'Previous {result} · {last_sn} / waiting for next camera' if result else 'Waiting for camera', fg=COLOR_SUBTEXT)
        self.show_message("NG" if result == "NG" else 'Connection failed' if result == "Error" else 'Waiting for camera',
                          COLOR_RED if result in ("NG", "Error") else COLOR_SUBTEXT)
        self.ok.config(state="disabled")
        self.ng.config(state="disabled")

    def clear_result(self):
        if self.blink_timer is not None:
            self.app.root.after_cancel(self.blink_timer)
        self.blink_timer = None
        self.processing_camera = None
        self.result_state = None
        self.result_hold_until = 0
        self.blink_visible = True
        self.fps_label.place_forget()
        self.status_detail.place_forget()

    def show_result(self, state, camera):
        self.clear_result()
        self.result_state = state
        self.processing_camera = camera if state == "RUNING" else None
        self.result_hold_until = 0 if state == "RUNING" else time.monotonic() + 2
        color = COLOR_BLUE if state == "RUNING" else COLOR_GREEN if state == "OK" else COLOR_RED
        self.title.config(text=f'Camera {self.index + 1} · SN: {camera.sn}')
        self.status.config(text=f"Step3 · {state}", fg=color)
        self.video.config(image="", text='Reboot completed\nOK' if state == "OK" else state,
                          font=(FONT, 26 if state == "OK" else 48, "bold"), fg=color)
        self.video.image = None
        self.status_detail.config(text='Step3 - waiting for config import' if state == "RUNING" else
                                  'MAC matched - IP 192.168.5.190 verified' if state == "OK" else 'Config import failed - check logs')
        self.status_detail.place(relx=.5, rely=.82, anchor="center", relwidth=.92)
        if state == "OK":
            self.blink_timer = self.app.root.after(500, self.blink_ok)

    def blink_ok(self):
        if self.result_state != "OK":
            return
        self.blink_visible = not self.blink_visible
        self.video.config(fg=COLOR_GREEN if self.blink_visible else COLOR_VIDEO)
        self.blink_timer = self.app.root.after(500, self.blink_ok)


class InspectionUI:

    def __init__(self, root):
        self.root = root
        self.ng_preview_open = False
        self.usb_reader = None
        self.usb_owner = None
        self.usb_scanning = False
        self.usb_events = queue.Queue()
        self.usb_selected = None
        self.usb_devices = {}
        self.settings_dir = os.path.relpath(APP_ROOT)
        self.settings = step3.load_settings(self.settings_dir)
        self.network_events = queue.Queue()
        self.network_scanning = False
        self.settings_window = None
        self.usb_scan_time = 0
        self.usb_status_window = None
        self.usb_status_values = None
        self.usb_search_error = ""
        self.pending_panels = deque()
        self.selected_panel = 0
        self.mode_var = tk.IntVar(value=4)
        self.ok_count = self.ng_count = 0
        self.scan_started = False
        self.debug_window = None
        self.debug_box = None
        self.debug_history = deque(maxlen=2000)
        self.debug_var = tk.BooleanVar(value=False)
        root.title("ITC Camera")
        root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        root.minsize(1120, 760)
        root.configure(bg=COLOR_BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<Return>", self.enter_ok)
        root.bind("<Escape>", self.escape_ng)
        header = tk.Frame(root, bg=COLOR_HEADER, height=64)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="ITC Camera", bg=COLOR_HEADER,
                 fg="white", font=(FONT, 20, "bold")).pack(side="left", padx=20)
        tk.Checkbutton(header, text='Detailed logs (Debug)', variable=self.debug_var,
                       command=self.toggle_debug, bg=COLOR_HEADER, fg="white",
                       selectcolor=COLOR_HEADER, activebackground=COLOR_HEADER,
                       activeforeground="white").pack(side="right", padx=16)
        self.header_status = tk.Label(header, text='Idle', bg=COLOR_HEADER,
                                      fg="#C6CCD4", font=(FONT, 11, "bold"))
        self.header_status.pack(side="right", padx=12)
        tk.Button(header, text='Settings', command=self.show_settings).pack(side="right", padx=12)
        main = tk.Frame(root, bg=COLOR_BG)
        main.pack(fill="both", expand=True, padx=16, pady=16)
        main.columnconfigure(0, weight=0, minsize=350)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)
        left = tk.Frame(main, bg=COLOR_BG, width=350)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        left.grid_propagate(False)
        right = tk.Frame(main, bg=COLOR_CARD)
        right.grid(row=0, column=1, sticky="nsew")
        def card(parent):
            frame = tk.Frame(parent, bg=COLOR_CARD, highlightbackground=COLOR_BORDER,
                             highlightthickness=1)
            frame.pack(fill="x", pady=(0, 12))
            return frame
        scan = card(left)
        tk.Label(scan, text='IP discovery range', bg=COLOR_CARD,
                 font=(FONT, 13, "bold")).pack(anchor="w", padx=14, pady=(12, 6))
        for label, attr, default in [('Start', "start_entry", "215"),
                                     ('Exit', "end_entry", "254")]:
            row = tk.Frame(scan, bg=COLOR_CARD)
            row.pack(fill="x", padx=14, pady=4)
            tk.Label(row, text=f"{label}  {NETWORK}", bg=COLOR_CARD,
                     font=("Consolas", 12)).pack(side="left")
            entry = tk.Entry(row, width=5, justify="center", font=("Consolas", 13))
            entry.insert(0, default)
            entry.pack(side="left")
            setattr(self, attr, entry)
        modes = tk.Frame(scan, bg=COLOR_CARD)
        modes.pack(fill="x", padx=14, pady=4)
        for label, count in [('Single panel', 1), ('Four panels', 4)]:
            tk.Radiobutton(modes, text=label, variable=self.mode_var, value=count,
                           command=self.change_mode, bg=COLOR_CARD).pack(side="left")
        self.start_button = tk.Button(scan, text='Start discovery', command=self.start_scan,
                                     bg=COLOR_BLUE, fg="white", relief="flat",
                                     font=(FONT, 12, "bold"))
        self.start_button.pack(fill="x", padx=14, pady=8)
        self.scan_range_label = tk.Label(scan, text="—", bg=COLOR_CARD,
                                         fg=COLOR_SUBTEXT, font=(FONT, 9), wraplength=310)
        self.scan_range_label.pack(padx=10, pady=(0, 10))
        usb = card(left)
        tk.Label(usb, text='Step1 - appearance inspection required', bg=COLOR_CARD,
                 font=(FONT, 12, "bold")).pack(anchor="w", padx=14, pady=(8, 4))
        self.usb_label = tk.Label(usb, text='Waiting for USB discovery', bg=COLOR_CARD,
                                  wraplength=310, font=(FONT, 9))
        self.usb_label.pack(fill="x", padx=14)
        usb_controls = tk.Frame(usb, bg=COLOR_CARD)
        usb_controls.pack(fill="x", padx=14, pady=6)
        tk.Label(usb_controls, text='Camera index', bg=COLOR_CARD).pack(side="left")
        self.usb_entry = tk.Entry(usb_controls, width=3)
        self.usb_entry.insert(0, "0")
        self.usb_entry.pack(side="left", padx=4)
        tk.Button(usb_controls, text='Connect', command=self.connect_usb).pack(side="left", padx=3)
        tk.Button(usb_controls, text='Rescan', command=self.scan_usb).pack(side="left", padx=3)
        tk.Button(usb, text='USB connection status', command=self.show_usb_status).pack(fill="x", padx=14, pady=(0, 6))
        self.network_label = tk.Label(usb, text="", bg=COLOR_CARD, fg=COLOR_SUBTEXT,
                                      wraplength=290, justify="left", anchor="w", font=(FONT, 9))
        self.network_label.pack(fill="x", padx=14, pady=(0, 8))
        self.update_network_label()
        device = card(left)
        for attr, text in [("ip_label", "IP   : -"), ("sn_label", "SN   : -"),
                           ("mac_label", "MAC  : -")]:
            widget = tk.Label(device, text=text, bg=COLOR_CARD, fg=COLOR_TEXT,
                              font=("Consolas", 12, "bold"), anchor="w")
            widget.pack(fill="x", padx=14, pady=5)
            setattr(self, attr, widget)
        self.status_label = tk.Label(device, text='Idle', bg="#EFF3F8",
                                     fg=COLOR_BLUE, font=(FONT, 11, "bold"), wraplength=300)
        self.status_label.pack(fill="x", padx=14, pady=10)
        self.counter_label = tk.Label(device, text="", bg=COLOR_CARD,
                                      fg=COLOR_SUBTEXT, font=(FONT, 10))
        self.counter_label.pack(pady=(0, 10))
        tk.Label(right, text='Step1 appearance -> Step2 video; click to select a camera', bg=COLOR_CARD,
                 fg=COLOR_SUBTEXT, font=(FONT, 10)).pack(anchor="w", padx=10, pady=8)
        self.panel_area = tk.Frame(right, bg=COLOR_BG)
        self.panel_area.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.panels = [CameraPanel(self, self.panel_area, i) for i in range(4)]
        self.mode_count = 4
        self.change_mode()
        self.log(f'[Startup] step2 / {os.path.basename(__file__)} / pre-inspection reset option=3')
        self.show_usb_status()
        self.root.after(0, self.scan_usb)
        if not self.settings.get("network_interfaces"):
            self.root.after(0, self.scan_network)
        self.poll()

    def update_network_label(self):
        records = self.settings.get("network_interfaces", [])
        selected = self.settings.get("network_id", "")
        chosen = [item for item in records if not selected or item["id"] == selected]
        text = 'Adapters not discovered'
        if chosen:
            text = 'Saved network adapters\n' + "\n".join(
                f"{item['name']} · {item['ip']}\nMAC {item['mac']}" for item in chosen)
        self.network_label.config(text=text)

    def scan_network(self):
        if self.network_scanning:
            return
        self.network_scanning = True
        self.network_label.config(text='Searching adapters...')
        def scan():
            try:
                self.network_events.put((True, step3.discover_interfaces()))
            except Exception as error:
                self.network_events.put((False, 'Adapter discovery failed - check Scapy/Npcap and connections'))
        threading.Thread(target=scan, daemon=True).start()

    def persist_settings(self, changes):
        try:
            self.settings = step3.save_settings(self.settings_dir, changes)
            return True
        except OSError:
            messagebox.showerror('Save settings', 'Cannot save settings. Check the records folder.', parent=self.root)
            return False

    def show_settings(self):
        if self.settings_window is not None:
            self.settings_window.lift()
            return
        window = self.settings_window = tk.Toplevel(self.root)
        window.title('Settings - network / USB camera')
        window.geometry("620x490")
        window.transient(self.root)
        window.grab_set()
        def close():
            self.settings_window = None
            window.destroy()
        window.protocol("WM_DELETE_WINDOW", close)
        tk.Label(window, text='Network - MAC/IP verification', font=(FONT, 12, "bold")).pack(anchor="w", padx=16, pady=12)
        network_list = tk.Listbox(window, height=5, exportselection=False)
        network_list.pack(fill="x", padx=16)
        choices = [""] + [item["id"] for item in self.settings.get("network_interfaces", [])]
        network_list.insert("end", 'Use all saved network adapters')
        for item in self.settings.get("network_interfaces", []):
            network_list.insert("end", f"{item['name']} / {item['ip']} / {item['mac']}")
        selected = self.settings.get("network_id", "")
        network_list.selection_set(choices.index(selected) if selected in choices else 0)
        def refresh_network():
            close()
            self.scan_network()
        tk.Button(window, text='Rescan adapters and update saved selection', command=refresh_network).pack(anchor="w", padx=16, pady=8)
        tk.Label(window, text='USB camera - appearance inspection', font=(FONT, 12, "bold")).pack(anchor="w", padx=16, pady=8)
        usb_list = tk.Listbox(window, height=4, exportselection=False)
        usb_list.pack(fill="x", padx=16)
        usb_choices = list(self.usb_devices.items())
        for position, (index, name) in enumerate(usb_choices):
            usb_list.insert("end", f"{index}: {name}")
            if index == self.usb_selected or name == self.settings.get("usb_name"):
                usb_list.selection_set(position)
        def refresh_usb():
            close()
            self.scan_usb()
        tk.Button(window, text='Rescan USB cameras', command=refresh_usb).pack(anchor="w", padx=16, pady=8)
        def apply():
            selection = network_list.curselection()
            if not self.persist_settings({"network_id": choices[selection[0]] if selection else ""}):
                return
            self.update_network_label()
            usb_selection = usb_list.curselection()
            if usb_selection:
                index, name = usb_choices[usb_selection[0]]
                self.usb_entry.delete(0, "end")
                self.usb_entry.insert(0, str(index))
                self.connect_usb()
            close()
        tk.Button(window, text='Save and connect USB', command=apply, bg=COLOR_BLUE, fg="white").pack(side="right", padx=16, pady=12)
        tk.Button(window, text='Close', command=close).pack(side="right", pady=12)

    def show_usb_status(self):
        if self.usb_status_window is not None:
            self.usb_status_window.lift()
            return
        window = self.usb_status_window = tk.Toplevel(self.root)
        window.title('USB camera - connection status')
        window.geometry("380x220")
        window.resizable(False, False)
        window.configure(bg=COLOR_CARD)
        window.protocol("WM_DELETE_WINDOW", self.hide_usb_status)
        self.usb_state_label = tk.Label(window, text="", bg=COLOR_CARD,
                                       font=(FONT, 16, "bold"), anchor="w")
        self.usb_state_label.pack(fill="x", padx=16, pady=(14, 8))
        self.usb_device_label = tk.Label(window, text="", bg=COLOR_CARD,
                                        wraplength=345, anchor="w", justify="left", font=(FONT, 10))
        self.usb_device_label.pack(fill="x", padx=16)
        self.usb_detail_label = tk.Label(window, text="", bg=COLOR_CARD,
                                        wraplength=345, anchor="w", justify="left", font=(FONT, 10))
        self.usb_detail_label.pack(fill="x", padx=16, pady=8)
        tk.Button(window, text='Rescan USB cameras', command=self.scan_usb).pack(side="bottom", fill="x", padx=16, pady=12)
        self.usb_status_values = None
        frame = self.usb_reader.get_frame() if self.usb_reader is not None else None
        self.update_usb_status(frame)

    def hide_usb_status(self):
        if self.usb_status_window is not None:
            self.usb_status_window.destroy()
        self.usb_status_window = None
        self.usb_status_values = None

    def update_usb_status(self, frame):
        if self.usb_status_window is None:
            return
        reader = self.usb_reader
        device = self.usb_devices.get(self.usb_selected, getattr(reader, "name", None)) or 'Not selected'
        number = getattr(reader, "index", self.usb_selected)
        device_text = f"Camera {number} · {device}" if reader is not None else device
        if frame is not None:
            state, color = 'Connected - video available', COLOR_GREEN
            height, width = frame.shape[:2]
            owner = f'Appearance inspection: camera {self.usb_owner + 1}' if self.usb_owner is not None else 'Appearance inspection: idle'
            detail = f'Video: {width} × {height}\n{owner}'
        elif reader is not None:
            error = getattr(reader, "error", "") or 'Waiting for USB frames'
            connecting = 'Connecting' in error
            state, color = ('Connecting', COLOR_BLUE) if connecting else ('No frames - waiting for reconnection', COLOR_RED)
            detail = error
        elif self.usb_scanning:
            state, color, detail = 'Searching', COLOR_BLUE, 'Searching USB cameras.'
        elif self.usb_search_error:
            state, color, detail = 'Discovery failed', COLOR_RED, self.usb_search_error
        else:
            state, color, detail = 'Disconnected', COLOR_SUBTEXT, 'Connect a USB camera.'
        values = (state, color, device_text, detail)
        if values != self.usb_status_values:
            self.usb_state_label.config(text=state, fg=color)
            self.usb_device_label.config(text=device_text)
            self.usb_detail_label.config(text=detail)
            self.usb_status_values = values

    def scan_usb(self):
        if self.usb_scanning:
            return
        self.usb_scanning = True
        self.usb_search_error = ""
        self.usb_scan_time = time.monotonic()
        self.usb_label.config(text='Searching USB cameras...')
        def scan():
            try:
                self.usb_events.put(("devices", exterior.list_usb_camera_devices()))
            except Exception as error:
                self.usb_events.put(("error", str(error)))
        threading.Thread(target=scan, daemon=True).start()

    def connect_usb(self):
        try:
            index = int(self.usb_entry.get())
            if index < 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning("USB Camera", 'Camera index must be a nonnegative integer.', parent=self.root)
            return
        if self.usb_reader is not None:
            self.usb_reader.stop()
        self.usb_selected = index
        self.usb_reader = exterior.InspectionUSBReader(index, self.usb_devices.get(index))
        self.usb_reader.start()
        self.persist_settings({"usb_index": index, "usb_name": self.usb_devices.get(index)})

    def assign_usb_panel(self):
        if self.usb_owner is not None:
            panel = self.panels[self.usb_owner]
            if panel.camera is not None and not panel.exterior_done:
                return
        self.usb_owner = next((p.index for p in self.panels[:self.mode_count]
                               if p.camera is not None and not p.exterior_done), None)
        if self.usb_owner is not None:
            self.select_panel(self.usb_owner)
            self.panels[self.usb_owner].add_log('Step1 - USB appearance inspection')

    def mark_exterior_ok(self, index):
        panel = self.panels[index]
        if self.usb_owner != index or self.usb_reader is None or self.usb_reader.snapshot() is None:
            return
        self.complete_exterior(index, "OK")

    def complete_exterior(self, index, result):
        panel = self.panels[index]
        panel.exterior_done = True
        if panel.preparation_future is not None:
            future = panel.preparation_future
            panel.preparation_future = None
            future.get_loop().call_soon_threadsafe(resolve_panel_future, future)
        panel.add_log(f'Appearance {result} - proceed to Step2')
        self.log(f'[Appearance {result}] SN={panel.camera.sn} / MAC={panel.camera.mac}')
        panel.show_message('Step2\nWaiting for reset and reboot')
        panel.ok.config(state="disabled")
        panel.ng.config(state="disabled")
        panel.status.config(text=f'Appearance {result} - waiting for reboot', fg=COLOR_BLUE)
        if panel.stream_ready:
            panel.start(panel.camera)
        self.usb_owner = None
        self.assign_usb_panel()
        self.select_panel(self.selected_panel)

    def mark_exterior_ng(self, index):
        panel = self.panels[index]
        if self.usb_owner != index or self.usb_reader is None:
            return
        image = self.usb_reader.snapshot()
        if image is None:
            return
        camera = panel.camera
        self.ng_preview_open = True
        try:
            path = exterior.preview_exterior_evidence(self.root, camera, image)
            if path is None or APP_STOP.is_set() or panel.camera is not camera:
                return
            save_result(camera, 'Appearance NG', "-", ng_reason='Appearance defect', evidence_path=path)
            panel.add_log(f'Appearance evidence saved - {os.path.basename(path)}')
            self.complete_exterior(index, "NG")
            self.update_counter()
        except Exception as error:
            messagebox.showerror('Save failed', f'NG not confirmed. Retry.\n{error}', parent=self.root)
        finally:
            self.ng_preview_open = False

    def change_mode(self):
        count = self.mode_var.get()
        if count == 1 and any(panel.camera is not None or panel.processing_camera is not None for panel in self.panels[1:]):
            self.mode_var.set(4)
            self.log('[Mode] Finish cameras 2-4 before switching to single panel')
            return
        self.mode_count = count
        for i in range(2):
            self.panel_area.columnconfigure(i, weight=1 if count == 4 or i == 0 else 0, uniform="video" if count == 4 else "")
            self.panel_area.rowconfigure(i, weight=1 if count == 4 or i == 0 else 0, uniform="video" if count == 4 else "")
        for panel in self.panels:
            panel.card.grid_forget()
            if panel.index < count:
                panel.card.grid(row=panel.index // 2 if count == 4 else 0,
                                column=panel.index % 2 if count == 4 else 0,
                                sticky="nsew", padx=3, pady=3)
        self.select_panel(min(self.selected_panel, count - 1))

    def select_panel(self, index):
        self.selected_panel = index
        for panel in self.panels:
            panel.card.config(highlightbackground=COLOR_BLUE if panel.index == index else COLOR_BORDER)
        self.refresh_shortcut_labels()
        panel = self.panels[index]
        camera = panel.camera or (panel.last_camera if panel.result_state else None)
        self.ip_label.config(text=f"IP   : {camera.ip if camera else '-'}")
        self.sn_label.config(text=f"SN   : {camera.sn if camera else '-'}")
        self.mac_label.config(text=f"MAC  : {camera.mac if camera else '-'}")
        self.status_label.config(text=f'Selected camera {index + 1}')

    def refresh_shortcut_labels(self):
        available = self.inspection_shortcut_available()
        for panel in self.panels:
            exterior_check = panel.camera is not None and not panel.exterior_done
            selected = available and panel.index == self.selected_panel
            panel.ok.config(text=('Appearance OK' if exterior_check else "OK") +
                            ("  [Enter]" if selected and str(panel.ok.cget("state")) == "normal" else ""))
            panel.ng.config(text=('Appearance NG (capture)' if exterior_check else 'NG (check image)') +
                            ("  [Esc]" if selected and str(panel.ng.cget("state")) == "normal" else ""))

    def toggle_debug(self):
        if not self.debug_var.get():
            DEBUG_ENABLED.clear()
            if self.debug_window is not None:
                self.debug_window.destroy()
            self.debug_window = self.debug_box = None
            return
        DEBUG_ENABLED.set()
        if self.debug_window is not None:
            self.debug_window.lift()
            return
        window = self.debug_window = tk.Toplevel(self.root)
        window.title("Debug")
        window.geometry("900x520")
        window.protocol("WM_DELETE_WINDOW", self.hide_debug)
        toolbar = tk.Frame(window)
        toolbar.pack(fill="x")
        tk.Label(toolbar, text="HTTP / Reset / RTSP").pack(side="left", padx=8)
        tk.Button(toolbar, text="Clear", command=self.clear_debug).pack(side="right", padx=8)
        scrollbar = tk.Scrollbar(window)
        scrollbar.pack(side="right", fill="y")
        self.debug_box = tk.Text(window, wrap="word", font=("Consolas", 10),
                                 yscrollcommand=scrollbar.set, state="disabled")
        self.debug_box.pack(fill="both", expand=True)
        scrollbar.config(command=self.debug_box.yview)
        self.append_text(self.debug_box, "".join(self.debug_history))
        self.log('[Debug ON] HTTP / CGI / Reset / RTSP details')

    def hide_debug(self):
        self.debug_var.set(False)
        self.toggle_debug()

    def clear_debug(self):
        self.debug_history.clear()
        self.debug_box.config(state="normal")
        self.debug_box.delete("1.0", "end")
        self.debug_box.config(state="disabled")

    @staticmethod
    def append_text(widget, text):
        widget.config(state="normal")
        widget.insert("end", text)
        if int(widget.index("end-1c").split(".")[0]) > 2000:
            widget.delete("1.0", "end-2000l")
        widget.see("end")
        widget.config(state="disabled")

    def debug_log(self, text):
        line = f"{datetime.now():%H:%M:%S} {text.replace(PASSWORD, '***')}\n"
        self.debug_history.append(line)
        if self.debug_box is not None:
            self.append_text(self.debug_box, line)

    # =========================================================================
    # Start Scan
    # =========================================================================

    def start_scan(self):

        if self.scan_started:

            return


        try:
            step3.load_config(os.path.relpath(APP_ROOT))
        except (OSError, step3.Step3Error) as error:
            messagebox.showerror('Step3 config file', str(error), parent=self.root)
            return

        # ---------------------------------------------------------------------
        # Number Check
        # ---------------------------------------------------------------------

        try:

            start_ip = int(
                self.start_entry.get()
            )

            end_ip = int(
                self.end_entry.get()
            )


        except ValueError:

            messagebox.showerror(
                'Input error',
                'IP: enter numbers'
            )

            return


        if not (
            0
            <= start_ip
            <= 255
        ):

            messagebox.showerror(
                'Input error',
                'Starting IP: 0-255'
            )

            return


        if not (
            0
            <= end_ip
            <= 255
        ):

            messagebox.showerror(
                'Input error',
                'End IP: 0-255'
            )

            return


        if start_ip > end_ip:

            messagebox.showerror(
                'Input error',
                'Starting IP <= ending IP'
            )

            return


        # ---------------------------------------------------------------------
        # Start
        # ---------------------------------------------------------------------

        self.scan_started = True


        start_full = (
            f"{NETWORK}"
            f"{start_ip}"
        )


        end_full = (
            f"{NETWORK}"
            f"{end_ip}"
        )


        self.scan_range_label.config(
            text=(
                f"{start_full} ～ "
                f"{end_full}"
            ),
            fg=COLOR_BLUE
        )


        self.header_status.config(
            text='Searching',
            fg="#6EE7A0"
        )


        self.start_button.config(
            text='Searching',
            state="disabled",
            bg=COLOR_DISABLED
        )


        self.start_entry.config(
            state="disabled"
        )


        self.end_entry.config(
            state="disabled"
        )


        self.log(
            f'[Start] {start_full} ～ {end_full}'
        )


        threading.Thread(
            target=scanner_thread,
            args=(
                start_ip,
                end_ip
            ),
            daemon=True
        ).start()


    # =========================================================================
    # Log
    # =========================================================================

    def log(self, text):
        text = redact(text)
        self.debug_log(text)
        # Keep operator status concise; retain full detail in debug logs.
        labels = {
            '[Startup]': 'Idle', '[Start]': 'Searching', '[Start discovery]': 'Searching',
            '[Discovery]': 'Discovery', '[Reset retaining IP]': 'Preparing',
            '[Reset retaining IP OK]': 'Waiting for reboot', '[Reset retaining IP NG]': 'Preparation failed',
            '[Reset retaining IP Error]': 'Preparation failed', '[Waiting for reboot]': 'Waiting for reboot',
            "[Offline]": 'Rebooting', '[Offline unverified]': 'Verifying recovery',
            "[Online]": 'Connection OK', "[Online NG]": 'Connection failed',
            '[Waiting for inspection]': 'Waiting for video', '[Start inspection]': 'Checking',
            '[Inspection OK]': 'Video OK', '[Inspection NG]': "NG",
            "[Step3]": 'Applying config', "[Step3 OK]": 'Config and IP reset completed', "[Step3 NG]": 'Config application failed',
            '[Appearance OK]': 'Appearance OK', '[Appearance NG]': 'Appearance NG',
            '[Reset]': 'Resetting', '[Reset OK]': "Reset OK",
            '[Reset NG]': 'Reset failed', '[Reset Error]': 'Reset failed',
            '[Evidence]': 'Save PNG' if 'Image not saved' not in text else 'No PNG',
            '[Evidence save Error]': 'PNG save failed', '[Discovery NG]': 'Identification failed',
            "[Scanner Error]": 'Discovery failed', "[RTSP Error]": 'Video error',
            '[Recheck NG]': 'Identification failed', '[SN changed]': 'SN changed', '[Mode]': 'Waiting for decisions on cameras 2-4',
        }
        prefix = text.split("]", 1)[0] + "]"
        label = labels.get(prefix)
        if label is None:
            return
        ip = re.search(r"192\.168\.0\.\d+", text)
        sn = re.search(r"SN=([^ /]+)", text)
        identity = ip.group(0) if ip else sn.group(1) if sn else ""
        for panel in self.panels:
            camera = panel.camera or panel.last_camera
            if camera and ((sn and sn.group(1) == camera.sn) or
                           (not sn and ip and ip.group(0) == camera.ip)):
                detail = label
                if prefix == '[Evidence]' and 'Image not saved' not in text:
                    detail = f'Evidence saved - {os.path.basename(text.split('[Evidence]', 1)[-1].strip())}'
                panel.add_log(detail)
                if panel.camera and panel.reader is None:
                    panel.status.config(text=label, fg=COLOR_RED if 'failed' in label else COLOR_BLUE)


    # =========================================================================
    # Appearance / single-panel RTSP shortcuts: Enter = OK, Esc = NG.
    # =========================================================================

    def inspection_shortcut_available(self, event=None):
        if self.ng_preview_open:
            return False
        widget = event.widget if event is not None else self.root.focus_get()
        if widget in (self.start_entry, self.end_entry, self.usb_entry):
            return False
        if widget is not None and widget.winfo_class() in ("Entry", "TEntry", "Text", "Spinbox", "TCombobox"):
            return False
        if self.root.grab_current() is not None:
            return False
        panel = self.panels[self.selected_panel]
        if panel.camera is None:
            return False
        if not panel.exterior_done:
            return (self.usb_owner == self.selected_panel and self.usb_reader is not None
                    and self.usb_reader.snapshot() is not None)
        return self.mode_count == 1 and panel.reader is not None

    def enter_ok(self, event=None):
        if not self.inspection_shortcut_available(event) or str(self.panels[self.selected_panel].ok.cget("state")) != "normal":
            return
        self.mark_ok(self.selected_panel)
        return "break"

    def escape_ng(self, event=None):
        if not self.inspection_shortcut_available(event) or str(self.panels[self.selected_panel].ng.cget("state")) != "normal":
            return
        self.mark_ng(self.selected_panel)
        return "break"

    def mark_ok(self, index=None):
        if self.ng_preview_open:
            return
        index = self.selected_panel if index is None else index
        panel = self.panels[index]
        camera = panel.camera
        if camera is not None and not panel.exterior_done:
            self.mark_exterior_ok(index)
            return
        if camera is None or panel.reader is None:
            return
        self.log(f'[Inspection OK] panel{index + 1} / SN={camera.sn} / MAC={camera.mac}')
        mark_completed(camera.sn)
        request_step3(camera)
        panel.finish('Waiting for Step3')
        panel.show_result("RUNING", camera)
        self.select_panel(self.selected_panel)
        self.update_counter()

    def preview_ng(self, camera, image):
        'Preview captured image before saving. Closing cancels the action.'
        window = tk.Toplevel(self.root)
        window.title(f'NG category - SN: {camera.sn}')
        window.transient(self.root)
        result = {"choice": "cancel"}
        tk.Label(window, text=f"IP: {camera.ip} / SN: {camera.sn} / MAC: {camera.mac}",
                 font=(FONT, 11, "bold")).pack(padx=16, pady=(12, 4))
        tk.Label(window, text='Save this image as NG evidence?').pack(padx=16, pady=4)
        preview = image.copy()
        preview.thumbnail((960, 540), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(image=preview, master=window)
        tk.Label(window, image=photo, bg=COLOR_VIDEO).pack(padx=16, pady=10)
        controls = tk.Frame(window)
        controls.pack(fill="x", padx=16, pady=(0, 14))
        def choose(choice):
            result["choice"] = choice
            window.destroy()

        def choose_other():
            while True:
                reason = simpledialog.askstring('Other', 'Enter an NG reason.', parent=window)
                if reason is None:
                    return
                reason = reason.strip()
                if reason:
                    choose(("save", reason, "Z"))
                    return
                messagebox.showwarning('Confirm input', 'Enter an NG reason.', parent=window)

        for label, choice, color in [('IR-CUT defect', ("save", 'IR-CUT defect', "E2"), COLOR_RED),
                                      ('Other', None, COLOR_RED),
                                      ('NG without saving', "discard", COLOR_SUBTEXT),
                                      ('Cancel', "cancel", COLOR_BLUE)]:
            tk.Button(controls, text=label,
                      command=choose_other if choice is None else lambda value=choice: choose(value),
                      bg=color, fg="white", font=(FONT, 11)).pack(side="left", expand=True, padx=5)
        window.protocol("WM_DELETE_WINDOW", lambda: choose("cancel"))
        window.grab_set()
        self.root.wait_window(window)
        return result["choice"]

    def save_evidence(self, camera, image, category="Z"):
        directory = os.path.join(os.path.relpath(APP_ROOT), "evidence")
        return exterior.evidence_store.save_image(directory, category, camera, image)

    def mark_ng(self, index=None):
        if self.ng_preview_open:
            return
        index = self.selected_panel if index is None else index
        panel = self.panels[index]
        camera = panel.camera
        if camera is not None and not panel.exterior_done:
            self.mark_exterior_ng(index)
            return
        if camera is None or panel.reader is None:
            return
        self.ng_preview_open = True
        evidence_path = None
        ng_reason = ""
        category = "Z"
        try:
            image = panel.reader.snapshot()
            if image is None:
                if not messagebox.askyesno('E1 - RTSP unavailable', 'No RTSP frames received. Save a connection-failure diagram as E1 NG evidence?', parent=self.root):
                    return
                image = exterior.evidence_store.connection_failure_image(camera, getattr(panel.reader, "last_status", 'No RTSP frames'))
                choice, ng_reason, category = "save", 'RTSP unavailable', "E1"
            else:
                choice = self.preview_ng(camera, image)
            if isinstance(choice, tuple):
                if len(choice) == 3:
                    choice, ng_reason, category = choice
                else:
                    choice, ng_reason = choice
                    category = "E2" if ng_reason == 'IR-CUT defect' else "Z"
            if choice == "cancel":
                return
            if APP_STOP.is_set() or panel.camera is not camera:
                return
            if choice == "save":
                try:
                    evidence_path = self.save_evidence(camera, image, category)
                except Exception as error:
                    self.log(f'[Evidence save Error] SN={camera.sn} / {error}')
                    messagebox.showerror('Save failed', f'NG not confirmed. Retry.\n{error}', parent=self.root)
                    return
            if ng_reason:
                save_result(camera, "NG", "-", ng_reason=ng_reason, evidence_path=evidence_path or "")
            elif evidence_path:
                save_result(camera, "NG", "-", evidence_path=evidence_path)
            else:
                save_result(camera, "NG", "-")
            self.ng_count += 1
            self.log(f'[Inspection NG] panel{index + 1} / SN={camera.sn} / MAC={camera.mac}')
            if ng_reason:
                self.log(f'[NG reason] SN={camera.sn} / {ng_reason}')
                panel.add_log(f"NG · {ng_reason}")
            self.log(f'[Evidence] {(evidence_path if evidence_path else 'Image not saved')}')
            panel.add_log(f'Evidence saved - {os.path.basename(evidence_path)}' if evidence_path else 'No image')
            mark_completed(camera.sn)
            panel.finish("NG")
            self.select_panel(self.selected_panel)
            self.update_counter()
        finally:
            self.ng_preview_open = False

    def update_counter(self):
        active = sum(panel.camera is not None or panel.processing_camera is not None for panel in self.panels)
        self.counter_label.config(text=f'OK {self.ok_count} · NG {self.ng_count}\nProcessing {active}cameras - waiting {inspection_queue.qsize() + len(self.pending_panels)}cameras')

    def poll(self):
        try:
            success, data = self.network_events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.network_scanning = False
            if success:
                selected = self.settings.get("network_id", "")
                self.persist_settings({"network_interfaces": data,
                                       "network_id": selected if any(item["id"] == selected for item in data) else ""})
                self.update_network_label()
            else:
                self.network_label.config(text=data)
        # Limit events per tick so four streams and UI input remain responsive.
        for _ in range(250):
            try:
                event = event_queue.get_nowait()
            except queue.Empty:
                break
            if event[0] == "step3_progress":
                _, sn, text = event
                for panel in self.panels:
                    if panel.processing_camera is not None and (panel.processing_camera.sn == sn or panel.processing_camera.mac.lower() == str(sn).lower()):
                        panel.status_detail.config(text="Step3 · " + text)
                continue
            if event[0] == "step3_result":
                _, camera, success, detail = event
                if success:
                    self.ok_count += 1
                else:
                    self.ng_count += 1
                for panel in self.panels:
                    if panel.camera is None and panel.last_camera and panel.last_camera.sn == camera.sn:
                        panel.show_result("OK" if success else "NG", camera)
                        panel.add_log(detail)
                continue
            if event[0] == "camera_reboot":
                self.pending_panels.append((event[1], event[2]))
            elif event[0] == "camera_ready":
                camera = event[1]
                for panel in self.panels:
                    if panel.camera and panel.camera.mac.lower() == camera.mac.lower() and panel.reader is None:
                        panel.stream_ready = True
                        if panel.exterior_done:
                            panel.start(camera)
                        self.select_panel(self.selected_panel)
                        break
            elif event[0] == "camera_failed":
                for panel in self.panels:
                    if panel.camera and panel.camera.sn == event[1] and panel.reader is None:
                        panel.add_log('Connection failed')
                        panel.finish("Error")
                        self.select_panel(self.selected_panel)
            elif event[0] == "log":
                self.log(event[1])
            elif event[0] == "debug":
                self.debug_log(event[1])
            elif event[0] == "scan_status":
                status = event[1]
                self.header_status.config(text=f"● {status}",
                                          fg="#FF7777" if status == "Error" else "#6EE7A0")
            elif event[0] == "stream_status":
                _, sn, status = event
                for panel in self.panels:
                    if panel.camera and (panel.camera.sn == sn or panel.camera.mac.lower() == str(sn).lower()):
                        short = {'RTSP connecting...': 'Connecting video', 'RTSP NG / reconnecting...': 'Video connection failed - reconnecting',
                                 'Video NG / reconnecting...': 'Video interrupted - reconnecting', 'Checking video': 'Check video'}.get(status, status)
                        panel.add_log(short)
                        panel.status.config(text=short, fg=COLOR_RED if "NG" in status else COLOR_GREEN if status == 'Checking video' else COLOR_BLUE)
                        if "NG" in status or status == 'RTSP connecting...':
                            panel.show_message("Step2\n" + short, COLOR_RED if "NG" in status else COLOR_BLUE)
        for panel in self.panels[:self.mode_count]:
            if panel.processing_camera is not None or time.monotonic() < panel.result_hold_until:
                continue
            if panel.camera is None and self.pending_panels:
                while self.pending_panels and self.pending_panels[0][1].done():
                    self.pending_panels.popleft()
                if self.pending_panels:
                    camera, future = self.pending_panels.popleft()
                    panel.reserve(camera, future)
                    self.select_panel(self.selected_panel)
            if panel.camera is None:
                try:
                    camera = inspection_queue.get_nowait()
                except queue.Empty:
                    continue
                panel.reserve(camera)
                panel.stream_ready = True
                self.log(f'[Inspection started] panel{panel.index + 1} / {camera}')
                self.select_panel(self.selected_panel)
            if panel.reader is None:
                continue
            try:
                frame = panel.reader.frames.get_nowait()
            except queue.Empty:
                continue
            image = Image.fromarray(frame)
            image.thumbnail((max(16, panel.video_frame.winfo_width()),
                             max(9, panel.video_frame.winfo_height())), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image=image)
            panel.video.config(image=photo, text="")
            panel.video.image = photo
        self.update_counter()
        while True:
            try:
                kind, value = self.usb_events.get_nowait()
            except queue.Empty:
                break
            self.usb_scanning = False
            if kind == "error":
                self.usb_search_error = value
                self.usb_label.config(text=f'USB discovery failed: {value}')
                continue
            self.usb_label.config(text=" / ".join(f"{i}: {name}" for i, name in value) or 'No USB camera - connect and rescan')
            self.usb_devices = dict(value)
            candidates = [(i, name) for i, name in value if "usb" in name.lower()
                          and not any(word in name.lower() for word in ("virtual", " ir ", "integrated"))]
            preferred = self.settings.get("usb_name")
            if preferred:
                candidates = [(i, name) for i, name in value if name == preferred]
            if self.usb_reader is None and candidates:
                self.usb_entry.delete(0, "end")
                self.usb_entry.insert(0, str(candidates[0][0]))
                self.connect_usb()
        self.assign_usb_panel()
        if self.usb_reader is None and not self.usb_scanning and time.monotonic() - self.usb_scan_time > 3:
            self.scan_usb()
        usb_frame = self.usb_reader.get_frame() if self.usb_reader is not None else None
        self.update_usb_status(usb_frame)
        if self.usb_owner is not None and not self.ng_preview_open:
            panel = self.panels[self.usb_owner]
            frame = usb_frame
            available = frame is not None
            panel.ok.config(state="normal" if available else "disabled")
            panel.ng.config(state="normal" if available else "disabled")
            panel.status.config(text='Step1 - check appearance' if available else
                                (self.usb_reader.error or 'Waiting for USB frames') if self.usb_reader else 'Connect a USB camera',
                                fg=COLOR_BLUE)
            if available:
                image = Image.fromarray(frame)
                image.thumbnail((max(16, panel.video_frame.winfo_width()),
                                 max(9, panel.video_frame.winfo_height())), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(image=image)
                panel.video.config(image=photo, text="")
                panel.video.image = photo
            else:
                panel.show_message("Step1\n" + (self.usb_reader.error or 'Waiting for USB frames' if self.usb_reader else 'Connect a USB camera'))
        for panel in self.panels[:self.mode_count]:
            if panel.camera is None:
                panel.update_fps(None, "")
            elif not panel.exterior_done:
                panel.update_fps(self.usb_reader if panel.index == self.usb_owner else None, "USB")
            else:
                panel.update_fps(panel.reader, "RTSP")
        self.refresh_shortcut_labels()
        self.root.after(33, self.poll)

    def close(self):
        APP_STOP.set()
        DEBUG_ENABLED.clear()
        if self.usb_reader is not None:
            self.usb_reader.stop()
        for panel in self.panels:
            panel.clear_result()
            if panel.reader is not None:
                panel.reader.stop()
        self.root.destroy()


# =============================================================================
# Main
# =============================================================================

def main():

    exterior.evidence_store.organize_records(os.path.relpath(APP_ROOT))

    root = tk.Tk()


    InspectionUI(
        root
    )


    root.mainloop()


# =============================================================================

if __name__ == "__main__":

    main()
