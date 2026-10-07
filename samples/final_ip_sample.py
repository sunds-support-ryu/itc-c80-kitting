from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
"""Finish the explicitly approved two-device smoke run without importing again."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import network_workflow as flow
import config_import as step3
from job_store import JobStore

BASE = Path(__file__).resolve().parents[1]


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    settings = step3.load_settings(BASE)
    interface = next(r for r in flow.enrich_interfaces(settings["network_interfaces"]) if r["id"] == settings["network_id"])
    report = json.loads((BASE / "data" / "hardware_smoke.json").read_text(encoding="utf-8"))
    if len(report["devices"]) != 2 or any(d["state"] != "CONFIG_VERIFIED" for d in report["devices"].values()):
        raise ValueError("Both config/identity checks must pass first; no final SET sent")
    credentials = (settings["after_config_username"], settings["after_config_password"])
    journal, store = flow.Journal(BASE), JobStore(BASE)
    if not store.job:
        store.create(settings.get("carton", "0001"), 2, "192.168.0.150", int(settings.get("carton_max", 12)))
    config = flow.network_config(settings, interface)
    items = []
    link = flow.Link(interface)
    for slot, (identity, record) in enumerate(sorted(report["devices"].items()), 1):
        ip = record.get("current_ip", record["work_ip"])
        camera = SimpleNamespace(mac=identity, sn=record["sn"], ip=ip)
        target = journal.reserve(identity, config["targets"], lambda ip: bool(link.arp(ip)))
        store.device(identity, slot, camera.sn, record["old_ip"], target)
        store.update(identity, step="FINAL_IP", status="SETTING_IP",
            results={"appearance": "OK", "ir_cut": "OK", "rtsp": "OK", "network": "OK", "settings": "OK"},
            human_confirmation="User confirmed exterior, IR-CUT and stickers in chat")
        items.append((camera, target))
    def finish(item):
        camera, target = item
        device_credentials = (settings["access_username"], settings["access_password"]) if report["devices"][camera.mac].get("access_stage") == "before" else credentials
        private = flow.Link(interface)
        transport = step3.create_transport()
        try:
            if not private.arp(target, camera.mac):
                step3.verify_identity("http://" + camera.ip, camera, device_credentials, transport)
                if private.arp(target):
                    raise ValueError("Target became occupied")
                journal.transition(camera.mac, "changing")
                private.set_ip(camera.mac, target, config["mask"], config["gateway"], device_credentials)
            if not private.wait_ip(camera.mac, target):
                raise ValueError("Final MAC/IP not confirmed; no repeated SET")
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                try:
                    step3.verify_identity("http://" + target, camera, device_credentials, transport)
                    break
                except Exception:
                    time.sleep(2)
            else:
                raise ValueError("Final HTTP/SN not confirmed")
            journal.transition(camera.mac, "confirmed")
            store.update(camera.mac, access_stage=report["devices"][camera.mac].get("access_stage", "after"))
            store.finish(camera.mac, "OK")
            print(camera.mac, camera.sn, target, "OK MAC/IP/SN", flush=True)
        except Exception as error:
            store.finish(camera.mac, "ERROR", str(error))
            print(camera.mac, "ERROR", type(error).__name__, str(error), flush=True)
        finally:
            transport.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(finish, items))


if __name__ == "__main__":
    main()
