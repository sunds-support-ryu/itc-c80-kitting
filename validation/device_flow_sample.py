from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
"""Explicit, one-shot technical verification; never writes production OK records."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
import threading
import sys
import time
from types import SimpleNamespace
from urllib.parse import quote

import cv2
import network_workflow as flow
import config_import as step3
from job_store import atomic

BASE = Path(__file__).resolve().parents[1]
LOCK = threading.RLock()
REPORT = BASE / "data" / "hardware_smoke.json"


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="Run authorized technical checks including config upload")
    args = parser.parse_args()
    settings = step3.load_settings(BASE)
    interface = next(r for r in flow.enrich_interfaces(settings["network_interfaces"]) if r["id"] == settings["network_id"])
    link = flow.Link(interface)
    devices = [d for d in link.discover() if flow.model_allowed(d, settings["allowed_models"])]
    devices.sort(key=lambda d: d["mac"])
    print("Discovery:", [(d["mac"], d["ip"], d.get("sn")) for d in devices], flush=True)
    if not args.run:
        return
    if len(devices) != 2:
        raise ValueError("Expected the two connected cameras; no write performed")
    before = (settings["access_username"], settings["access_password"])
    after = (settings["after_config_username"], settings["after_config_password"])
    step3.VERIFY_CREDENTIALS = after
    report = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else {"devices": {}}
    journal = flow.Journal(BASE)
    config = flow.network_config(settings, interface)
    def save(identity, **values):
        with LOCK:
            record = report["devices"].setdefault(identity, {})
            record.update(values, updated_at=datetime.now().isoformat(timespec="seconds"))
            atomic(REPORT, report)
    def run(device, work_ip):
        identity = device["mac"]
        camera = SimpleNamespace(mac=identity, sn=device["sn"], ip=work_ip)
        transport = step3.create_transport()
        private_link = flow.Link(interface)
        try:
            previous = report["devices"].get(identity, {})
            if previous.get("state") == "FINAL_VERIFIED":
                print(identity, "Completed: no SET or config upload", flush=True)
                return
            if previous.get("config_started"):
                step3.verify_identity("http://" + device["ip"], camera, after, transport)
                save(identity, state="CONFIG_VERIFIED", current_ip=device["ip"])
                print(identity, "Already uploaded: verification only", flush=True)
                return
            if device["ip"] != work_ip:
                if private_link.arp(work_ip):
                    raise ValueError("Work IP occupied")
                journal.hold_work(identity, work_ip, interface["id"])
                save(identity, old_ip=device["ip"], work_ip=work_ip, sn=camera.sn, state="SETTING_IP")
                private_link.set_ip(identity, work_ip, config["work_mask"], config["work_gateway"], before)
            if not private_link.wait_ip(identity, work_ip):
                raise ValueError("MAC/IP not confirmed")
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                try:
                    step3.verify_identity("http://" + work_ip, camera, before, transport)
                    break
                except Exception as error:
                    print(identity, "HTTP startup waiting", type(error).__name__, flush=True)
                    time.sleep(2)
            else:
                raise ValueError("Work IP HTTP startup timeout")
            save(identity, state="RESET_STARTED")
            status, body = transport("http://" + work_ip + step3.RESET_PATH, "POST",
                json.dumps({"options": 3}).encode(), {"Content-Type": "application/json"}, before)
            if not 200 <= status < 300:
                raise ValueError(f"Reset option=3 HTTP={status}")
            deadline = time.monotonic() + 90
            time.sleep(5)
            while time.monotonic() < deadline:
                try:
                    step3.verify_identity("http://" + work_ip, camera, before, transport)
                    break
                except Exception:
                    time.sleep(2)
            else:
                raise ValueError("Reset return/login timeout")
            save(identity, state="RTSP")
            capture = cv2.VideoCapture(f"rtsp://{quote(before[0], safe='')}:{quote(before[1], safe='')}@{work_ip}:554/sub",
                cv2.CAP_FFMPEG, [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000])
            try:
                success, frame = capture.read()
                if not success or frame is None:
                    raise ValueError("No RTSP frame")
                save(identity, rtsp_frame=True, frame_size=[int(frame.shape[1]), int(frame.shape[0])])
            finally:
                capture.release()
            save(identity, state="CONFIG", config_started=True)
            def tracked_transport(url, *args, **kwargs):
                if "/dataloader.cgi?" in url:
                    save(identity, config_post_sent=True)
                return transport(url, *args, **kwargs)
            success, detail = step3.run(camera, BASE, before, reset_ip=False, transport=tracked_transport,
                log=lambda message: print(identity, message, flush=True))
            save(identity, state="CONFIG_VERIFIED" if success else "ERROR", detail=detail,
                 manual_appearance="NOT_VERIFIED", manual_ir_cut="NOT_VERIFIED", sticker="NOT_VERIFIED")
            print(identity, success, detail, flush=True)
        except Exception as error:
            save(identity, state="ERROR", error=type(error).__name__ + ": " + str(error))
            print(identity, type(error).__name__, str(error), flush=True)
        finally:
            transport.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda pair: run(*pair), zip(devices, config["work"][:2])))


if __name__ == "__main__":
    main()
