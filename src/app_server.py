"""Local HTML interface. Existing inspection engine owns all camera operations.

WidgetState maps the engine's UI writes to browser state; no hidden Tk window.
All commands and engine callbacks execute on one owner thread.
"""
import argparse
import importlib.util
import io
import json
import logging
import os
from pathlib import Path
import queue
import secrets
import threading
import time
import types
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from job_store import JobStore
import gas_post
import ir_cut_check
import numpy as np

from PIL import Image, ImageDraw

from app_paths import APP_ROOT, CODE_DIR
BASE = APP_ROOT
spec = importlib.util.spec_from_file_location("web_inspection_engine", CODE_DIR / "inspection_engine.py")
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)

_flow_spec = importlib.util.spec_from_file_location("camera_network_workflow", CODE_DIR / "network_workflow.py")
flow = importlib.util.module_from_spec(_flow_spec)
_flow_spec.loader.exec_module(flow)
_original_interfaces = engine.step3.discover_interfaces
engine.step3.discover_interfaces = lambda: flow.enrich_interfaces(_original_interfaces())

LOG = logging.getLogger("camera_inspection")


def console_log(text):
    text = str(text)
    for secret in (engine.PASSWORD, engine.step3.CONFIG_PASSWORD,
                   engine.step3.VERIFY_CREDENTIALS[1]):
        if secret:
            text = text.replace(secret, "***")
    LOG.info(text)


_original_log = engine.InspectionUI.log
_original_panel_log = engine.CameraPanel.add_log


def _console_ui_log(self, text):
    console_log(text)
    return _original_log(self, text)


def _console_panel_log(self, text):
    camera = getattr(self, "processing_camera", None) or getattr(self, "last_camera", None)
    console_log(f"[Camera {camera.ip if camera else '-'}] {text}")
    return _original_panel_log(self, text)


engine.InspectionUI.log = _console_ui_log
engine.CameraPanel.add_log = _console_panel_log


class WidgetState:
    def __init__(self, *args, **kwargs):
        self.options = kwargs
        self.text = ""
        self.image = None
        self.visible = True

    def config(self, **kwargs):
        self.options.update(kwargs)
    configure = config

    def cget(self, key):
        return self.options.get(key)

    def insert(self, index, text):
        self.text += str(text)

    def delete(self, *args):
        self.text = ""

    def get(self):
        return self.text

    def index(self, *args):
        return str(self.text.count("\n") + 1) + ".0"

    def place(self, **kwargs):
        self.visible = True

    def place_forget(self):
        self.visible = False

    def winfo_width(self):
        return 960

    def winfo_height(self):
        return 540

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *args, **kwargs: None


class Variable:
    def __init__(self, value=False):
        self.value = value
    def get(self):
        return self.value
    def set(self, value):
        self.value = value


class OwnerLoop(WidgetState):
    def __init__(self):
        super().__init__()
        self.timers = {}
        self.sequence = 0
        self.closed = False
    def after(self, ms, callback):
        self.sequence += 1
        self.timers[self.sequence] = (time.monotonic() + ms / 1000, callback)
        return self.sequence
    def after_cancel(self, key):
        self.timers.pop(key, None)
    def tick(self):
        now = time.monotonic()
        due = [key for key, (when, _) in self.timers.items() if when <= now]
        for key in due:
            timer = self.timers.pop(key, None)
            if timer:
                timer[1]()
    def destroy(self):
        self.closed = True
        self.timers.clear()


def ui_error(title, message, **kwargs):
    raise ValueError(str(message))


engine.tk = types.SimpleNamespace(**{name: WidgetState for name in (
    "Frame", "Label", "Entry", "Button", "Text", "Scrollbar", "Toplevel",
    "Radiobutton", "Checkbutton")}, IntVar=Variable, BooleanVar=Variable)
engine.ImageTk = types.SimpleNamespace(PhotoImage=lambda image=None, **kwargs: image.copy())
engine.messagebox = types.SimpleNamespace(showerror=ui_error, showwarning=ui_error)


class BrowserInspection(engine.InspectionUI):
    def __init__(self, root, demo=False):
        self.demo = demo
        self.usb_auto_events = queue.Queue()
        self.usb_search_stopped = False
        self.usb_cycle_active = False
        self.usb_candidates = []
        self.usb_candidate_position = 0
        self.usb_attempt_time = 0
        self.usb_connection_seen = False
        self.preview = None
        self.jpeg_cache = {}
        self.workflow_events = queue.Queue()
        self.jobs = {}
        self.working = {}
        self.onboarding = set()
        self.config_running = set()
        self.camera_credentials = {}
        self.task_generation = {}
        self.approved = set()
        self.discovery_busy = False
        self.discovery_error = ""
        self.discovery_current = []
        self.flow_started = False
        self.link = None
        self.journal = flow.Journal(BASE)
        self.store = JobStore(BASE)
        self.recovery_pending = bool(self.store.job) if not demo else False
        self.slot_macs = {}
        self.engine_started = False
        self.recovered_config = set()
        self.restored_errors = set()
        self.removal_watchers = set()
        self.storage_retry_time = 0
        self.storage_retry_active = False
        self.archive_retry_time = 0
        self.post_busy = False
        self.post_last_attempt = 0
        self.post_response = None
        self.post_error = ""
        self.ir_checks = {}
        self.ir_frame_times = {}
        super().__init__(root)
        if not self.settings.get('post_url'):
            self.settings['post_url'] = gas_post.DEFAULT_POST_URL
        self.settings.setdefault('post_enabled', False)
        self.panels.extend(engine.CameraPanel(self, self.panel_area, i) for i in range(4, 6))
        self.protection_logged = set()
        for device_mac, record in self.journal.data["active_work"].items():
            self.working[device_mac] = record["ip"]
            console_log(f"[MAC保護 復元] MAC={device_mac} IP={record['ip']} 改IP禁止")
        self.apply_access_settings()
        for widget, value in ((self.start_entry, self.settings.get("work_start", 150)),
                              (self.end_entry, self.settings.get("work_end", 189))):
            widget.delete(0, "end")
            widget.insert(0, str(value))
        self.mode_var.set(self.settings.get("display_mode", 4))
        self.change_mode()
        engine.RTSP_PATH = self.settings.get("rtsp_path", "/sub") if self.settings.get("rtsp_path") in ("/main", "/sub") else "/sub"
        if self.settings.get("network_id") and not self.demo:
            self.root.after(0, self.device_scan)

    def update_network_label(self):
        if not self.settings.get("network_id"):
            count = len(self.settings.get("network_interfaces", []))
            self.network_label.config(text=f"ネットワーク未指定\n設定で接続先を選択してください\n検索・記録済み: {count}件")
        else:
            super().update_network_label()

    def apply_access_settings(self):
        engine.step3.CONFIG_PASSWORD = self.settings.get("config_import_password", "")
        engine.USERNAME = self.settings.get("access_username", "admin")
        engine.PASSWORD = self.settings.get("access_password", "")
        engine.step3.VERIFY_CREDENTIALS = (
            self.settings.get("after_config_username", "itc_cam"),
            self.settings.get("after_config_password", ""))
        if self.store.job:
            for identity, record in self.store.job["devices"].items():
                self.camera_credentials[flow.mac(identity)] = (engine.USERNAME, engine.PASSWORD) if record.get("access_stage") == "before" else engine.step3.VERIFY_CREDENTIALS

    def change_mode(self):
        count = int(self.mode_var.get())
        if count not in (1, 2, 4, 6):
            count = 4
        if any(p.processing_camera or p.camera for p in self.panels[count:]):
            raise ValueError("使用中のカメラ枠は非表示にできません")
        self.mode_count = count
        self.mode_var.set(count)
        self.select_panel(min(self.selected_panel, count - 1))

    def mark_exterior_ok(self, index):
        camera = self.panels[index].camera
        if camera:
            self.store.update(camera.mac, results={"appearance": "OK"}, step="PREPARE", status="TESTING")
        return super().mark_exterior_ok(index)

    def mark_ok(self, index=None):
        index = self.selected_panel if index is None else index
        panel = self.panels[index]
        if panel.camera and panel.exterior_done and panel.reader:
            identity = flow.mac(panel.camera.mac)
            check = self.ir_checks.setdefault(identity, ir_cut_check.Check())
            if check.stage in ('COVER', 'TIMEOUT'):
                check.start(time.monotonic())
                self.store.update(identity, step='IR_CUT_BW', results={'ir_cut': None})
                console_log(f'[IR-CUT 開始] MAC={identity}')
                return
            if check.stage != 'PASS':
                raise ValueError('IR-CUT自動確認中です。NGは手動で選択できます')
        return super().mark_ok(index)

    def poll_ir_cut(self):
        if self.ng_preview_open:
            return
        for panel in self.panels[:self.mode_count]:
            if not panel.camera or not panel.exterior_done or not panel.reader:
                continue
            identity = flow.mac(panel.camera.mac)
            check = self.ir_checks.setdefault(identity, ir_cut_check.Check())
            image = panel.reader.snapshot()
            stamp = getattr(panel.reader, 'latest_frame_time', None)
            fresh = stamp is not None and stamp != self.ir_frame_times.get(identity)
            frame = np.asarray(image) if image is not None and fresh else None
            previous = check.stage
            if check.stage in ('BW', 'COLOR'):
                if fresh or image is None or time.monotonic() - check.started > 90:
                    check.feed(frame, time.monotonic())
                if fresh:
                    self.ir_frame_times[identity] = stamp
            if check.stage == 'PASS':
                self.store.update(identity, step='CONFIG', results={'rtsp': 'OK', 'ir_cut': 'OK'})
                panel.add_log('IR-CUT 自動OK · 黒白→カラー確認')
                super().mark_ok(panel.index)
                continue
            messages = {'COVER': '蓋を被ってください。', 'BW': '黒白への切替を確認中…',
                        'COLOR': '蓋を外してください。', 'TIMEOUT': '切替を確認できません。NG または再試行してください。'}
            panel.status.config(text='IR-CUT · ' + messages[check.stage])
            panel.status_detail.place(relx=.5, rely=.82, anchor='center', relwidth=.92)
            panel.status_detail.config(text=messages[check.stage] + ('\n確認 ' + str(check.count) + '/3' if check.stage in ('BW', 'COLOR') else ''))
            panel.ok.config(text='蓋を被せた · 開始' if check.stage == 'COVER' else '再試行' if check.stage == 'TIMEOUT' else '自動確認中',
                            state='normal' if check.stage in ('COVER', 'TIMEOUT') and image is not None else 'disabled')
            panel.ng.config(text='IR-CUT NG', state='normal' if not self.ng_preview_open else 'disabled')
            if self.mode_count == 1:
                if panel.ok.cget('state') == 'normal':
                    panel.ok.config(text=panel.ok.cget('text') + ' [Enter]')
                if panel.ng.cget('state') == 'normal':
                    panel.ng.config(text='IR-CUT NG [Esc]')
            if check.stage != previous:
                self.store.update(identity, step='IR_CUT_' + check.stage)
                console_log(f'[IR-CUT] MAC={identity} {previous}→{check.stage} {check.metrics}')

    def selected_interface(self):
        selected = self.settings.get("network_id")
        return next((item for item in self.settings.get("network_interfaces", []) if item["id"] == selected), None)

    def device_scan(self):
        if self.flow_started and self.store.job and self.store.job["devices"] and all(
                d["status"] in ("OK", "NG", "ERROR") for d in self.store.job["devices"].values()) and len(self.jobs) >= len(self.store.job["devices"]):
            # Completed devices stay under ARP removal monitoring; no new L2 cycle.
            return
        if self.discovery_busy or self.demo:
            return
        interface = self.selected_interface()
        if interface is None:
            raise ValueError("ネットワーク接続を指定してください")
        self.discovery_busy = True
        self.discovery_error = ""
        allow_moves = self.flow_started
        console_log(f"[L2 scan] interface={interface['name']} PC={interface['ip']} MAC={interface['mac']} move={allow_moves}")
        def scan():
            try:
                link = self.link if self.flow_started else flow.Link(interface, engine.APP_STOP.is_set, verify_ip=False)
                transport = engine.step3.create_transport()
                credentials = (engine.USERNAME, engine.PASSWORD)
                discovered = link.discover()
                console_log(f"[L2 scan] found={len(discovered)}")
                for device in discovered:
                    console_log(f"[Device] MAC={device.get('mac')} IP={device.get('ip')} model={device.get('model')} OEM={device.get('oem_model')} SN={device.get('sn')}")
                # Cached devices still answer directed ARP even when announcements are unavailable.
                known = list(self.journal.data["devices"].values())
                for cached in known:
                    if cached.get("ip") and cached["mac"] not in [d["mac"] for d in discovered] and link.arp(cached["ip"], cached["mac"]):
                        discovered.append(dict(cached))
                for device in discovered:
                    if engine.APP_STOP.is_set():
                        break
                    device_mac = flow.mac(device["mac"])
                    held = self.journal.data["active_work"].get(device_mac)
                    if held and device_mac not in self.protection_logged:
                        console_log(f"[MAC保護] MAC={device_mac} IP={held['ip']} SET再送禁止")
                        self.protection_logged.add(device_mac)
                    if held and held["interface_id"] != interface["id"]:
                        continue
                    if device_mac == interface["mac"]:
                        continue
                    recorded = self.journal.data["assignments"].get(device_mac, {})
                    state = self.store.job["devices"].get(device_mac.replace(":", "").upper(), {}) if self.store.job else {}
                    if getattr(self, "resuming", False) and allow_moves and device_mac not in self.jobs and recorded.get("state") in ("sticker", "changing", "confirmed"):
                        known = self.store.job["devices"].get(device_mac.replace(":", "").upper(), {}) if self.store.job else {}
                        current_ip = recorded["ip"] if recorded["state"] in ("changing", "confirmed") else self.working.get(device_mac)
                        if current_ip and link.arp(current_ip, device_mac):
                            camera = engine.CameraInfo(current_ip, known.get("sn", device.get("sn", "")), device_mac)
                            self.workflow_events.put(("restore_label", (camera, recorded["ip"], recorded["state"])))
                        continue
                    if state.get("status") in ("OK", "NG", "ERROR"):
                        if getattr(self, "resuming", False) and state.get("status") != "OK" and device_mac not in self.restored_errors:
                            self.restored_errors.add(device_mac)
                            camera = engine.CameraInfo(device.get("ip", state["new_ip"]), state["sn"], device_mac)
                            self.workflow_events.put(("onboarding_failed", (camera, state.get("error", "前回の結果を復元"))))
                        continue
                    if recorded.get("state") in ("confirmed", "disconnected"):
                        continue
                    if device.get("ip") and device_mac not in self.working and not (device.get("model") and device.get("oem_model") and device.get("sn")):
                        try:
                            status, body = transport(f"http://{device['ip']}" + engine.INFO_PATH, credentials=credentials)
                            if status == 200:
                                data = json.loads(body)
                                camera = engine.extract_camera_info(device["ip"], data)
                                model = engine.find_value(data, {"model", "modelname", "devicemodel"})
                                if camera and flow.mac(camera.mac) == device_mac and model:
                                    device.setdefault("model", str(model).strip())
                                    device.update(http_model=str(model).strip(), sn=camera.sn, http_info=body)
                                    device.setdefault("info", body)
                        except (ValueError, OSError):
                            pass
                    if device.get("info") or device.get("model"):
                        self.journal.remember({**device, "interface_id": interface["id"]})
                    if not allow_moves or device_mac in self.approved or device_mac in self.onboarding or device_mac in self.jobs:
                        continue
                    if not flow.model_allowed(device, self.settings.get("allowed_models", [])):
                        continue
                    config = flow.network_config(self.settings, interface)
                    used = set(self.working.values())
                    work_ip = self.working.get(device_mac)
                    if work_ip and work_ip not in config["work"][:self.mode_count]:
                        console_log(f"[MAC保護] MAC={device_mac} 作業IP={work_ip} は現在のBase IP範囲外。変更禁止")
                        continue
                    if work_ip is None:
                        if len(used) >= self.mode_var.get():
                            continue
                        if self.store.job and len(self.store.job["devices"]) >= self.store.job.get("expected_count", self.mode_count):
                            continue
                        work_ip = next((ip for ip in config["work"][:self.mode_count] if ip not in used and not link.arp(ip)), None)
                    if work_ip is None:
                        raise ValueError("作業IPの空きがありません")
                    needs_move = device_mac not in self.working and device.get("ip") != work_ip
                    self.journal.hold_work(device_mac, work_ip, interface["id"])
                    slot = config["work"].index(work_ip) + 1
                    self.slot_macs[device_mac] = slot - 1
                    if self.store.job:
                        self.store.device(device_mac, slot, device.get("sn", ""), device.get("ip", ""), work_ip)
                        self.store.update(device_mac, status="SETTING_IP" if needs_move else "CONNECTING", step="IP")
                    self.working[device_mac] = work_ip
                    self.onboarding.add(device_mac)
                    threading.Thread(target=self.onboard_device,
                        args=(dict(device), work_ip, config, credentials, needs_move),
                        daemon=True).start()
                self.workflow_events.put(("discovered", discovered))
            except Exception as error:
                self.workflow_events.put(("discovery_error", str(error)))
            finally:
                if "transport" in locals() and hasattr(transport, "close"):
                    transport.close()
        threading.Thread(target=scan, daemon=True).start()

    def onboard_device(self, device, work_ip, config, credentials, needs_move):
        device_mac = flow.mac(device["mac"])
        try:
            link = flow.Link(self.selected_interface(), engine.APP_STOP.is_set)
            transport = engine.step3.create_transport()
            record = self.store.job["devices"].get(device_mac.replace(":", "").upper(), {}) if self.store.job else {}
            if record.get("config_uncertain") and record.get("results", {}).get("settings") != "OK":
                if tuple(credentials) == tuple(engine.step3.VERIFY_CREDENTIALS):
                    raise ValueError("config適用状態が不明です。カメラWebで設定を確認してから「設定適用確認済み」を押してください。再送なし")
                camera = engine.CameraInfo(work_ip, record.get("sn", device.get("sn", "")), device_mac)
                engine.step3.verify_identity(f"http://{work_ip}", camera, engine.step3.VERIFY_CREDENTIALS, transport)
                self.store.update(device_mac, config_uncertain=False, results={"settings": "OK"})
                record = {**record, "results": {**record.get("results", {}), "settings": "OK"}}
            if record.get("results", {}).get("settings") == "OK":
                credentials = self.camera_credentials.get(device_mac, engine.step3.VERIFY_CREDENTIALS)
            if needs_move:
                # Never resend SET after an uncertain response on subsequent scans.
                if not link.arp(work_ip, device_mac):
                    engine.send_log(f"[作業IP] MAC={device_mac} → {work_ip}")
                    link.set_ip(device_mac, work_ip, config["work_mask"], config["work_gateway"], credentials)
            if not link.wait_ip(device_mac, work_ip):
                raise ValueError("作業IPへの変更未確認")
            self.store.update(device_mac, status="CONNECTING", step="HTTP", results={"network": "OK"})
            deadline = time.monotonic() + 60
            last_error = "HTTP待機"
            while time.monotonic() < deadline and not engine.APP_STOP.is_set():
                try:
                    status, body = transport(f"http://{work_ip}" + engine.INFO_PATH, credentials=credentials)
                    if status in (401, 403):
                        raise ValueError(f"HTTP={status} config書込前のアクセス認証を確認してください")
                    data = json.loads(body) if status == 200 else {}
                    camera = engine.extract_camera_info(work_ip, data) if data else None
                    model = engine.find_value(data, {"model", "modelname", "devicemodel"})
                    if camera and flow.mac(camera.mac) == device_mac and flow.model_allowed(
                            {**device, "http_model": model}, self.settings.get("allowed_models", [])):
                        self.journal.remember({**device, "ip": work_ip, "sn": camera.sn, "http_model": model})
                        self.approved.add(device_mac)
                        self.store.update(device_mac, sn=camera.sn, status="TESTING", step="APPEARANCE")
                        self.workflow_events.put(("onboarded", camera))
                        return
                    last_error = f"HTTP={status} MAC/モデル未確認"
                except OSError as error:
                    last_error = str(error)
                console_log(f"[作業IP HTTP待機] MAC={device_mac} IP={work_ip} {last_error}")
                time.sleep(2)
            raise ValueError(f"IP={work_ip} HTTP確認失敗: {last_error}")
        except Exception as error:
            self.store.update(device_mac, status="ERROR", step="CONNECTING", error=str(error))
            self.workflow_events.put(("onboarding_failed", (engine.CameraInfo(work_ip, device.get("sn", ""), device_mac), str(error))))
            engine.send_log(f"[作業IP Error] MAC={device_mac} IP={work_ip} {error}")
        finally:
            if "transport" in locals() and hasattr(transport, "close"):
                transport.close()
            self.onboarding.discard(device_mac)

    def start_workflow(self):
        if self.recovery_pending:
            raise ValueError("前回作業の再開または新しい作業を選択してください")
        interface = self.selected_interface()
        if interface is None or not self.settings.get("allowed_models"):
            raise ValueError("ネットワーク接続と許可するモデルを設定してください")
        if not getattr(self, "resuming", False) and any(job.get("state") in ("sticker", "changing") for job in self.journal.data["assignments"].values()):
            raise ValueError("未完了のIP割当記録があります。記録を確認してから開始してください")
        interface = flow.enrich_interfaces([dict(interface)])[0]
        console_log(f"[Network] {interface}")
        if os.name == "nt" and not interface.get("prefix_verified"):
            raise ValueError("PCのサブネット情報を確認できません。ネットワークを再検索してください")
        records = [interface if item["id"] == interface["id"] else item for item in self.settings["network_interfaces"]]
        if not self.persist_settings({"network_interfaces": records}):
            raise ValueError("ネットワーク記録保存失敗")
        config = flow.network_config(self.settings, interface)
        config["work"] = config["work"][:self.mode_count]
        if len(config["work"]) < self.mode_count:
            raise ValueError("Base IPから必要台数の連続IPを設定してください")
        if not self.store.job:
            carton = self.settings.get("carton", "0001")
            maximum = int(self.settings.get("carton_max", 12))
            if self.store.carton_count(carton) >= maximum:
                raise ValueError(f"このCartonは{maximum}台に達しました。次のCartonに変更してください")
            self.store.create(carton, self.mode_count, config["work"][0], maximum)
            self.store.job["expected_count"] = min(self.mode_count, maximum - self.store.carton_count(carton))
            self.store.save()
            self.journal.begin_job()
        engine.step3.load_config(self.settings_dir)
        self.link = flow.Link(interface, engine.APP_STOP.is_set)
        engine.NETWORK = config["work"][0].rsplit(".", 1)[0] + "."
        for widget, value in ((self.start_entry, config["work"][0].rsplit(".", 1)[1]),
                              (self.end_entry, config["work"][-1].rsplit(".", 1)[1])):
            widget.delete(0, "end")
            widget.insert(0, value)
        original_info = engine.get_camera_info
        async def allowed_info(session, ip):
            camera = await original_info(session, ip)
            if camera and flow.mac(camera.mac) in self.approved and self.working.get(flow.mac(camera.mac)) == ip:
                return camera
            return None
        engine.get_camera_info = allowed_info
        async def config_worker(session):
            import asyncio
            async def execute(camera):
                identity = flow.mac(camera.mac)
                if identity in self.config_running:
                    return
                self.config_running.add(identity)
                try:
                    self.store.update(camera.mac, step="CONFIG", config_uncertain=True, results={"rtsp": "OK"})
                    success, detail = await asyncio.to_thread(engine.step3.run, camera, self.settings_dir,
                        (engine.USERNAME, engine.PASSWORD),
                        lambda text: engine.event_queue.put(("step3_progress", camera.mac, text)),
                        stopped=engine.APP_STOP.is_set, reset_ip=False,
                        credential_report=lambda credentials: self.camera_credentials.__setitem__(identity, credentials))
                    if success:
                        self.store.update(identity, access_stage="before" if self.camera_credentials.get(identity) == (engine.USERNAME, engine.PASSWORD) else "after")
                    self.store.update(camera.mac, step="STICKER" if success else "CONFIG",
                                      config_uncertain=not success and "拒否" not in detail,
                                      results={"settings": "OK" if success else "NG"})
                    self.workflow_events.put(("config_ready" if success else "config_failed", (camera, detail)))
                except Exception as error:
                    self.workflow_events.put(("config_failed", (camera, str(error))))
                finally:
                    self.config_running.discard(identity)
            tasks = set()
            try:
                while not engine.APP_STOP.is_set():
                    try:
                        camera = await asyncio.wait_for(engine.STEP3_QUEUE.get(), timeout=1)
                    except asyncio.TimeoutError:
                        continue
                    task = asyncio.create_task(execute(camera))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
            finally:
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
        engine.step3_worker = config_worker
        self.flow_started = True
        if not self.engine_started:
            engine.scanner_main = self.mac_scanner
            self.start_scan()
            self.engine_started = True
        else:
            self.scan_started = True
        self.device_scan()

    async def mac_scanner(self, start_ip, end_ip):
        """Only monitor MACs already placed and verified, never sweep an IP range."""
        import asyncio
        engine.SCANNER_LOOP = asyncio.get_running_loop()
        engine.STEP3_QUEUE = asyncio.Queue()
        engine.http_semaphore = asyncio.Semaphore(6)
        engine.reset_semaphore = asyncio.Semaphore(6)
        engine.device_lock = asyncio.Lock()
        tasks = {}
        generations = {}
        async with engine.aiohttp.ClientSession() as session:
            config_task = asyncio.create_task(engine.step3_worker(session))
            try:
                while not engine.APP_STOP.is_set():
                    for identity, task in list(tasks.items()):
                        if task.done() or generations.get(identity) != self.task_generation.get(identity, 0):
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                            tasks.pop(identity)
                    for identity in list(self.approved):
                        if identity in tasks:
                            continue
                        ip = self.working.get(identity)
                        if ip:
                            async def monitor(identity=identity, ip=ip):
                                try:
                                    cached = self.journal.data["devices"].get(identity, {})
                                    camera = engine.CameraInfo(ip, cached.get("sn", ""), identity)
                                    if not await self.reserve_mac_panel(camera):
                                        return
                                    record = self.store.job["devices"].get(identity.replace(":", "").upper(), {}) if self.store.job else {}
                                    if record.get("results", {}).get("settings") == "OK":
                                        self.workflow_events.put(("config_ready", (camera, "設定適用済み・再開")))
                                    else:
                                        if record.get("results", {}).get("preparation") != "OK":
                                            self.store.update(identity, step="RESET", status="TESTING")
                                            if not await engine.reset_device(session, camera, 3):
                                                raise ValueError("検査前Resetに失敗")
                                            if not await engine.wait_reboot(session, camera):
                                                raise ValueError("Reset後の復帰未確認")
                                            self.store.update(identity, results={"preparation": "OK"})
                                        verified = await engine.get_camera_info(session, ip)
                                        if not verified or flow.mac(verified.mac) != identity or verified.sn != camera.sn:
                                            raise ValueError("作業IPのMAC/SNを確認できません")
                                        self.store.update(identity, step="RTSP", status="TESTING")
                                        engine.event_queue.put(("camera_ready", camera))
                                    while identity in self.approved and not engine.APP_STOP.is_set():
                                        await asyncio.sleep(.2)
                                except Exception as error:
                                    console_log(f"[Camera Task Error] MAC={identity} {error}")
                                    self.store.update(identity, status="ERROR", step="TASK", error=str(error))
                                    self.approved.discard(identity)
                                    self.workflow_events.put(("onboarding_failed", (camera, str(error))))
                                finally:
                                    console_log(f"[Camera Task 解除] MAC={identity}")
                            tasks[identity] = asyncio.create_task(monitor())
                            generations[identity] = self.task_generation.get(identity, 0)
                    for identity, task in list(tasks.items()):
                        if identity not in self.approved:
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                            tasks.pop(identity)
                    await asyncio.sleep(.2)
            finally:
                for task in [*tasks.values(), config_task]:
                    task.cancel()
                await asyncio.gather(*tasks.values(), config_task, return_exceptions=True)
                engine.SCANNER_LOOP = None

    async def reserve_mac_panel(self, camera):
        import asyncio
        future = asyncio.get_running_loop().create_future()
        self.workflow_events.put(("reserve_slot", (camera, future)))
        try:
            while not engine.APP_STOP.is_set():
                try:
                    return await asyncio.wait_for(asyncio.shield(future), .5)
                except asyncio.TimeoutError:
                    continue
            return False
        finally:
            if not future.done():
                future.cancel()

    def allocate_label(self, camera, target=None):
        if target is None:
            def reserve():
                try:
                    config = flow.network_config(self.settings, self.selected_interface())
                    ip = self.journal.reserve(flow.mac(camera.mac), config["targets"], lambda address: bool(self.link.arp(address)))
                    self.workflow_events.put(("label_ready", (camera, ip)))
                except Exception as error:
                    self.workflow_events.put(("config_failed", (camera, str(error))))
            threading.Thread(target=reserve, daemon=True).start()
            return
        self.jobs[flow.mac(camera.mac)] = {"camera": camera, "target": target, "state": "sticker"}
        self.store.update(camera.mac, new_ip=target, step="STICKER", status="TESTING")
        for panel in self.panels:
            if panel.processing_camera and flow.mac(panel.processing_camera.mac) == flow.mac(camera.mac):
                panel.video.config(text="ラベルを貼ってください", fg=engine.COLOR_BLUE)
                panel.status.config(text="ラベル貼付待ち")
                panel.status_detail.config(text=f"このカメラの目標IP: {target}\n貼付後に「貼付完了」を押してください")
                panel.ok.config(text="貼付完了 · IP変更", state="normal")
                panel.ng.config(state="disabled")
        self.update_counter()

    def finalize(self, job):
        camera, target = job["camera"], job["target"]
        device_mac = flow.mac(camera.mac)
        if job["state"] != "sticker":
            raise ValueError("IP変更処理中です")
        config = flow.network_config(self.settings, self.selected_interface())
        # MAC identity and destination occupancy are checked again at the moment of confirmation.
        def change():
            try:
                if not self.link.arp(camera.ip, device_mac):
                    raise ValueError("作業IPで対象MACを確認できません。IP未変更")
                occupied = self.link.arp(target)
                if any(found != device_mac for found in occupied):
                    raise ValueError("目標IPが他の機器に使用されています。IP未変更")
                self.journal.transition(device_mac, "changing")
                self.link.set_ip(device_mac, target, config["mask"], config["gateway"], self.camera_credentials.get(device_mac, engine.step3.VERIFY_CREDENTIALS))
                if not self.link.wait_ip(device_mac, target):
                    raise ValueError("最終MAC/IP未確認。IP変更再送なし")
                self.journal.transition(device_mac, "confirmed")
                self.workflow_events.put(("final_ok", camera))
                misses = 0
                while not engine.APP_STOP.is_set():
                    time.sleep(1)
                    try:
                        present = self.link.arp(target, device_mac)
                    except Exception as error:
                        misses = 0
                        self.workflow_events.put(("watch_error", (camera, str(error))))
                        time.sleep(2)
                        continue
                    misses = 0 if present else misses + 1
                    if misses >= 3:
                        self.journal.transition(device_mac, "disconnected")
                        self.workflow_events.put(("disconnected", camera))
                        break
            except Exception as error:
                self.workflow_events.put(("final_failed", (camera, str(error))))
        job["state"] = "changing"
        self.store.update(camera.mac, step="FINAL_IP", status="SETTING_IP", new_ip=target)
        for panel in self.panels:
            if panel.processing_camera and flow.mac(panel.processing_camera.mac) == flow.mac(camera.mac):
                panel.ok.config(state="disabled")
                panel.video.config(text="RUNING")
                panel.status_detail.config(text=f"IP変更・MAC確認 → {target}")
        threading.Thread(target=change, daemon=True).start()

    def refresh_shortcut_labels(self):
        super().refresh_shortcut_labels()
        for panel in self.panels:
            if panel.processing_camera:
                job = self.jobs.get(flow.mac(panel.processing_camera.mac))
                if job and job["state"] == "sticker":
                    panel.ok.config(text="貼付完了 · IP変更", state="normal")
            if panel.camera and panel.exterior_done:
                check = self.ir_checks.get(flow.mac(panel.camera.mac))
                if check and check.stage != 'PASS':
                    label = '蓋を被せた · 開始' if check.stage == 'COVER' else '再試行' if check.stage == 'TIMEOUT' else '自動確認中'
                    panel.ok.config(text=label + (' [Enter]' if self.mode_count == 1 and panel.ok.cget('state') == 'normal' else ''))
                    panel.ng.config(text='IR-CUT NG' + (' [Esc]' if self.mode_count == 1 and panel.ng.cget('state') == 'normal' else ''))

    def watch_removal(self, camera, ip, event="ng_disconnected", discover=False):
        identity = flow.mac(camera.mac)
        if identity in self.removal_watchers:
            return
        self.removal_watchers.add(identity)
        def watch():
            misses = 0
            try:
                link = flow.Link(self.selected_interface(), engine.APP_STOP.is_set, verify_ip=False)
                while not engine.APP_STOP.is_set():
                    try:
                        present = bool(link.arp(ip, identity))
                        if not present and discover:
                            present = any(flow.mac(d["mac"]) == identity for d in link.discover())
                        misses = 0 if present else misses + 1
                        if misses >= 3:
                            self.workflow_events.put((event, camera))
                            return
                    except Exception as error:
                        misses = 0
                        console_log(f"[MAC監視] MAC={identity} {error}")
                    time.sleep(1)
            finally:
                self.removal_watchers.discard(identity)
        threading.Thread(target=watch, daemon=True).start()

    def poll(self):
        if not self.demo and not self.post_busy and self.settings.get("post_enabled") and self.settings.get("post_url") and time.monotonic() - self.post_last_attempt >= 60:
            self.post_records()
        if time.monotonic() - self.storage_retry_time > 3 and not self.storage_retry_active:
            self.storage_retry_time = time.monotonic()
            if self.store.dirty or self.store.pending_results:
                self.storage_retry_active = True
                def retry_storage():
                    try:
                        self.store.flush_pending()
                    finally:
                        self.storage_retry_active = False
                threading.Thread(target=retry_storage, daemon=True).start()
        self.poll_usb_auto()
        while True:
            try:
                kind, value = self.workflow_events.get_nowait()
            except queue.Empty:
                break
            if kind != "discovered":
                console_log(f"[Workflow {kind}] {value}")
            if kind in ("discovered", "discovery_error"):
                self.discovery_busy = False
                if kind == "discovered":
                    self.discovery_current = value or []
                if kind == "discovery_error":
                    self.discovery_error = value
                    self.log("[ネットワーク Error] " + value)
                if self.flow_started:
                    self.root.after(3000, self.device_scan)
            elif kind == "reserve_slot":
                camera, future = value
                identity = flow.mac(camera.mac)
                self.ir_checks[identity] = ir_cut_check.Check()
                self.ir_frame_times.pop(identity, None)
                record = self.store.job['devices'].get(identity.replace(':', '').upper(), {}) if self.store.job else {}
                if record.get('results', {}).get('ir_cut') == 'OK':
                    self.ir_checks[identity].stage = 'PASS'
                index = self.slot_macs.get(flow.mac(camera.mac), 0)
                panel = self.panels[index]
                if not future.done() and not panel.camera and not panel.processing_camera:
                    panel.reserve(camera, future)
                    self.select_panel(self.selected_panel)
                elif not future.done() and engine.SCANNER_LOOP:
                    engine.SCANNER_LOOP.call_soon_threadsafe(engine.resolve_panel_future, future, False)
            elif kind == "config_ready":
                try:
                    self.allocate_label(value[0])
                except Exception as error:
                    self.workflow_events.put(("config_failed", (value[0], str(error))))
            elif kind == "label_ready":
                self.allocate_label(value[0], value[1])
            elif kind == "onboarding_failed":
                camera, detail = value
                self.store.finish(camera.mac, "ERROR", detail)
                index = self.slot_macs.get(flow.mac(camera.mac), 0)
                panel = self.panels[index]
                panel.show_result("NG", camera)
                panel.processing_camera = camera
                panel.status_detail.config(text=detail)
                self.watch_removal(camera, camera.ip, discover=True)
            elif kind == "restore_label":
                camera, target, state = value
                identity = flow.mac(camera.mac)
                record = self.store.job["devices"].get(identity.replace(":", "").upper(), {}) if self.store.job else {}
                work_ip = self.working.get(identity)
                base_ip = self.store.job.get("base_ip", "") if self.store.job else ""
                index = int(record.get("camera_slot", 1)) - 1
                if work_ip and base_ip and work_ip.rsplit(".", 1)[0] == base_ip.rsplit(".", 1)[0]:
                    index = int(work_ip.rsplit(".", 1)[1]) - int(base_ip.rsplit(".", 1)[1])
                panel = self.panels[index] if 0 <= index < self.mode_count else None
                if panel:
                    panel.show_result("RUNING", camera)
                    panel.processing_camera = camera
                    self.allocate_label(camera, target)
                    if state in ("changing", "confirmed"):
                        self.journal.transition(flow.mac(camera.mac), "confirmed")
                        self.workflow_events.put(("final_ok", camera))
                        self.watch_removal(camera, target, "disconnected")
            elif kind in ("config_failed", "final_failed"):
                camera, detail = value
                self.store.finish(camera.mac, "NG", detail)
                engine.save_result(camera, "NG", detail)
                self.ng_count += 1
                self.watch_removal(camera, camera.ip, discover=True)
                for panel in self.panels:
                    if panel.processing_camera and flow.mac(panel.processing_camera.mac) == flow.mac(camera.mac):
                        panel.show_result("NG", camera)
                        panel.status_detail.config(text=detail)
            elif kind == "final_ok":
                camera = value
                job = self.jobs[flow.mac(camera.mac)]
                job["state"] = "confirmed"
                camera.ip = job["target"]
                self.ok_count += 1
                self.store.update(camera.mac, new_ip=job["target"])
                self.store.finish(camera.mac, "OK")
                record = self.store.job["devices"].get(flow.mac(camera.mac).replace(":", "").upper()) if self.store.job else None
                if record:
                    next_number = self.journal.advance_counter(record["record_id"],
                        int(self.settings.get("target_start", 215)), int(self.settings.get("target_end", 254)),
                        int(self.settings.get("target_next", 220)))
                    if next_number is not None:
                        self.persist_settings({"target_next": next_number})
                        console_log(f"[連番] OKカウント · 次の開始IP={self.settings.get('target_prefix', '192.168.0')}.{next_number}")
                engine.save_result(camera, "OK", "最終MAC/IP確認 " + job["target"])
                for panel in self.panels:
                    if panel.processing_camera and flow.mac(panel.processing_camera.mac) == flow.mac(camera.mac):
                        panel.show_result("OK", camera)
                        panel.processing_camera = camera  # Hold the slot until the MAC disappears.
                        panel.last_camera = camera
                        panel.ok.config(state="disabled")
                        panel.ng.config(state="disabled")
                        panel.video.config(text="OK\n電源を切ってください")
                        panel.status_detail.config(text="MAC一致 · " + job["target"])
                self.header_status.config(text="● 完了 · 断電待ち")
            elif kind == "watch_error":
                camera, detail = value
                for panel in self.panels:
                    if panel.processing_camera and flow.mac(panel.processing_camera.mac) == flow.mac(camera.mac):
                        panel.status_detail.config(text="MAC監視を再確認中 · " + detail)
            elif kind == "ng_disconnected":
                self.ir_checks.pop(flow.mac(value.mac), None)
                self.ir_frame_times.pop(flow.mac(value.mac), None)
                record = self.store.job["devices"].get(value.mac.replace(":", "").upper(), {}) if self.store.job else {}
                if record.get("status") == "ERROR":
                    self.store.finish(value.mac, "ERROR", record.get("error", ""))
                self.journal.release_work(value.mac)
                console_log(f"[MAC保護 解除] MAC={value.mac} 断電確認")
                self.working.pop(flow.mac(value.mac), None)
                self.approved.discard(flow.mac(value.mac))
                for panel in self.panels:
                    if panel.processing_camera and flow.mac(panel.processing_camera.mac) == flow.mac(value.mac):
                        panel.finish()
            elif kind == "disconnected":
                camera = value
                self.ir_checks.pop(flow.mac(camera.mac), None)
                self.ir_frame_times.pop(flow.mac(camera.mac), None)
                self.journal.release_work(camera.mac)
                console_log(f"[MAC保護 解除] MAC={camera.mac} 断電確認")
                self.working.pop(flow.mac(camera.mac), None)
                self.approved.discard(flow.mac(camera.mac))
                self.jobs[flow.mac(camera.mac)]["state"] = "disconnected"
                for panel in self.panels:
                    if panel.processing_camera and flow.mac(panel.processing_camera.mac) == flow.mac(camera.mac):
                        panel.finish()
                        panel.show_message("取外し確認\n次のカメラ待ち")
        super().poll()
        self.poll_ir_cut()
        for panel in self.panels[:self.mode_count]:
            camera = panel.camera or panel.processing_camera
            if camera and self.store.job:
                record = self.store.job["devices"].get(camera.mac.replace(":", "").upper())
                if record:
                    if record["camera_slot"] != panel.index + 1:
                        self.store.update(camera.mac, camera_slot=panel.index + 1)
                    if panel.reader and record["results"].get("rtsp") is None and panel.video.image:
                        self.store.update(camera.mac, step="IR_CUT", results={"rtsp": "OK"})
                    if panel.camera is not None and record["results"].get("appearance") and not panel.exterior_done:
                        self.complete_exterior(panel.index, record["results"]["appearance"])
        if self.store.job and len(self.store.job["devices"]) >= self.store.job.get("expected_count", self.mode_count) and all(
                d["status"] in ("OK", "NG", "ERROR") for d in self.store.job["devices"].values()):
            if not self.working and not self.onboarding and not self.store.dirty and not self.store.pending_results and time.monotonic() - self.archive_retry_time > 3:
                self.archive_retry_time = time.monotonic()
                try:
                    self.store.archive("completed")
                    self.flow_started = False
                    self.scan_started = False
                except PermissionError as error:
                    self.store.save_error = str(error)
                    console_log("[履歴保存待ち] " + str(error))

    def scan_usb(self):
        if self.demo or self.usb_search_stopped or self.usb_cycle_active or self.usb_scanning:
            return
        self.usb_cycle_active = True
        self.usb_scanning = True
        self.usb_scan_time = time.monotonic()
        self.usb_label.config(text="USB Cameraを検索中...")
        def search():
            try:
                self.usb_auto_events.put((True, engine.exterior.list_usb_camera_devices()))
            except Exception as error:
                self.usb_auto_events.put((False, str(error)))
        threading.Thread(target=search, daemon=True).start()

    def connect_usb(self):
        index = int(self.usb_entry.get())
        if index not in self.usb_devices:
            raise ValueError("USB Cameraを再検索してください")
        preferred = [(index, self.usb_devices[index])]
        self.usb_candidates = preferred + [(i, name) for i, name in self.usb_devices.items() if i != index and "virtual" not in name.lower()]
        self.usb_candidate_position = 0
        self.usb_search_stopped = False
        self.usb_cycle_active = True
        self.usb_connection_seen = False
        self.try_next_usb()

    def try_next_usb(self):
        if self.usb_reader is not None:
            self.usb_reader.stop()
            self.usb_reader = None
        if self.usb_candidate_position >= len(self.usb_candidates):
            self.usb_cycle_active = False
            self.usb_search_stopped = True
            self.usb_selected = None
            self.usb_label.config(text="USB未接続 · 全候補を確認して停止")
            return
        index, name = self.usb_candidates[self.usb_candidate_position]
        self.usb_candidate_position += 1
        self.usb_selected = index
        self.usb_connection_seen = False
        self.usb_attempt_time = time.monotonic()
        self.usb_reader = engine.exterior.InspectionUSBReader(index, name, retry=False)
        self.usb_reader.start()
        self.usb_label.config(text=f"USB接続確認 {self.usb_candidate_position}/{len(self.usb_candidates)} · {name}")

    def poll_usb_auto(self):
        try:
            success, devices = self.usb_auto_events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.usb_scanning = False
            if not success:
                self.usb_cycle_active = False
                self.usb_search_stopped = True
                self.usb_label.config(text="USB検索停止 · " + devices)
                return
            self.usb_cycle_active = True
            self.usb_devices = dict(devices)
            preferred = self.settings.get("usb_name")
            candidates = [(i, name) for i, name in devices if "virtual" not in name.lower() and " ir " not in name.lower()]
            self.usb_candidates = sorted(candidates, key=lambda item: (item[1] != preferred, "usb" not in item[1].lower(), item[0]))
            self.usb_candidate_position = 0
            self.try_next_usb()
        if not self.usb_cycle_active or self.usb_reader is None:
            return
        frame = self.usb_reader.get_frame()
        if frame is not None:
            if not self.usb_connection_seen:
                self.usb_connection_seen = True
                self.persist_settings({"usb_index": self.usb_selected, "usb_name": self.usb_devices.get(self.usb_selected)})
                self.usb_label.config(text="USB接続済み · " + self.usb_devices.get(self.usb_selected, ""))
            return
        done = getattr(self.usb_reader, "done_event", None)
        if self.usb_connection_seen:
            self.usb_attempt_time = time.monotonic()
            self.usb_connection_seen = False
            self.usb_candidates = [(i, name) for i, name in self.usb_devices.items() if "virtual" not in name.lower()]
            self.usb_candidate_position = 0
        if done is not None and done.is_set():
            self.try_next_usb()
        elif time.monotonic() - self.usb_attempt_time > 8:
            self.usb_reader.stop()
            if done is not None and done.is_set():
                self.try_next_usb()
            else:
                self.usb_cycle_active = False
                self.usb_search_stopped = True
                self.usb_label.config(text="USB検索停止 · カメラ応答タイムアウト")

    def scan_network(self):
        if not self.demo:
            super().scan_network()

    def show_usb_status(self):
        # Browser sidebar replaces the separate native status window.
        self.usb_status_window = None

    def begin_ng(self, index):
        if self.ng_preview_open:
            raise ValueError("NG確認画面を閉じてください。")
        panel = self.panels[index]
        if panel.camera is None:
            raise ValueError("カメラがありません。")
        exterior = not panel.exterior_done
        if exterior:
            if self.usb_owner != index or self.usb_reader is None:
                raise ValueError("USB外観確認待ちです。")
            image = self.usb_reader.snapshot()
        else:
            if panel.reader is None:
                raise ValueError("映像確認待ちです。")
            image = panel.reader.snapshot()
        if image is None and exterior:
            raise ValueError("USB映像がありません。")
        connection_failure = image is None
        if connection_failure:
            image = engine.exterior.evidence_store.connection_failure_image(
                panel.camera, getattr(panel.reader, "last_status", "RTSP映像なし"))
        self.preview = {"index": index, "camera": panel.camera, "image": image.copy(),
                        "exterior": exterior, "connection_failure": connection_failure}
        self.ng_preview_open = True
        self.refresh_shortcut_labels()

    def complete_ng(self, category, reason, boxes, discard=False):
        preview = self.preview
        if preview is None:
            raise ValueError("NG画像がありません。")
        index, camera, image = preview["index"], preview["camera"], preview["image"]
        panel = self.panels[index]
        if panel.camera is not camera:
            raise ValueError("検査対象が変更されました。")
        if preview["exterior"]:
            if not boxes:
                raise ValueError("問題箇所を囲んでください。")
            output = image.copy()
            draw = ImageDraw.Draw(output)
            for box in boxes:
                if len(box) != 4 or any(not isinstance(v, (int, float)) for v in box):
                    raise ValueError("画枠が不正です。")
                x1, y1, x2, y2 = box
                if not (0 <= x1 < x2 <= image.width and 0 <= y1 < y2 <= image.height):
                    raise ValueError("画枠が画像範囲外です。")
                draw.rectangle(box, outline="red", width=max(2, image.width // 300))
            if category not in ('B', 'D'):
                raise ValueError('外観不具合 または 破損 を選択してください')
            appearance_reason = '外観不具合' if category == 'B' else '破損'
            path = self.save_evidence(camera, output, 'B')
            self.store.update(camera.mac, results={"appearance": "NG", "appearance_category": appearance_reason}, step="PREPARE")
            engine.save_result(camera, "外観NG", "-", ng_reason=appearance_reason, evidence_path=path)
            self.complete_exterior(index, "NG")
        else:
            if preview["connection_failure"]:
                category, reason = "E1", "RTSP接続不可"
            elif category == "E2":
                reason = "IR-CUT不具合"
            elif category == "Z" and str(reason).strip():
                reason = str(reason).strip()
            else:
                raise ValueError("NG分類と理由を確認してください。")
            path = "" if discard else self.save_evidence(camera, image, category)
            if category == 'E2':
                self.store.update(camera.mac, results={'ir_cut': 'NG'})
            self.store.finish(camera.mac, "NG", reason)
            engine.save_result(camera, "NG", "-", ng_reason=reason, evidence_path=path)
            engine.mark_completed(camera.sn)
            self.ng_count += 1
            panel.add_log("NG · " + reason)
            panel.finish("NG")
            if self.flow_started:
                self.watch_removal(camera, camera.ip, discover=True)
        self.preview = None
        self.ng_preview_open = False
        self.select_panel(self.selected_panel)
        self.update_counter()

    def post_records(self, test=False):
        if self.post_busy:
            raise ValueError("POST送信中です")
        url = self.settings.get("post_url", "")
        if not url:
            self.post_last_attempt = time.monotonic()
            raise ValueError("設定でPOST URLを保存してください")
        gas_post.validate_url(url)
        self.post_busy = True
        self.post_last_attempt = time.monotonic()
        self.post_error = ""
        def send_records():
            try:
                def sender(row):
                    payload = {key: row.get(key, "") for key in self.store.fields}
                    payload["type"] = "camera_result"
                    received = gas_post.send(url, payload)
                    self.post_response = received
                    console_log(f"[GAS応答] record_id={row['record_id']} {json.dumps(received, ensure_ascii=False)}")
                    return received
                if test:
                    payload = {"type": "test", "record_id": "test-" + secrets.token_hex(8), "message": "ITC Camera Inspection connection test"}
                    self.post_response = gas_post.send(url, payload)
                else:
                    self.store.deliver(sender)
                    data = json.loads((self.store.root / "pending_post.json").read_text(encoding="utf-8"))
                    pending = [row for row in data["records"] if row["status"] != "SENT"]
                    if pending:
                        self.post_error = pending[-1].get("last_error", "送信待ち")
                        self.post_response = pending[-1].get("last_response")
            except Exception as error:
                self.post_error = str(error)
                self.post_response = getattr(error, "response", None)
                console_log("[POST Error] " + str(error))
            finally:
                self.post_busy = False
        threading.Thread(target=send_records, daemon=True).start()

    def command(self, body):
        action = body.get("action")
        if self.demo and action not in ("select", "mode", "debug", "close", "cancel_ng"):
            raise ValueError("デモ表示中のため実機操作はできません。")
        if action == "post_settings":
            url = str(body.get("url", "")).strip()
            enabled = body.get("enabled") is True
            if url:
                gas_post.validate_url(url)
            elif enabled:
                raise ValueError("POST URLを入力してください")
            if not self.persist_settings({"post_url": url, "post_enabled": enabled}):
                raise ValueError("POST設定保存失敗")
            self.post_last_attempt = 0
        elif action in ("post_retry", "post_test"):
            self.post_records(test=action == "post_test")
        elif action in ("startup_continue", "clear_counters"):
            self.recovery_pending = False
            if action == "clear_counters":
                self.ok_count = self.ng_count = 0
                self.update_counter()
            console_log("[起動通知] 確認 · カメラ操作なし" if action == "startup_continue" else "[計数] OK/NG表示をクリア · 履歴とMAC保護は保持")
        elif action == "job_resume":
            if not self.recovery_pending:
                raise ValueError("再開する作業がありません")
            job = self.store.job
            self.persist_settings({"carton": job["carton"], "display_mode": job["mode"],
                "work_prefix": job["base_ip"].rsplit(".", 1)[0],
                "work_start": int(job["base_ip"].rsplit(".", 1)[1]),
                "work_end": int(job["base_ip"].rsplit(".", 1)[1]) + int(job["mode"]) - 1})
            self.mode_var.set(job["mode"])
            self.change_mode()
            self.recovery_pending = False
            self.resuming = True
            self.start_workflow()
            for identity, record in job["devices"].items():
                device_mac = flow.mac(identity)
                assignment = self.journal.data["assignments"].get(device_mac, {})
                if assignment.get("state") == "disconnected":
                    self.journal.release_work(device_mac)
                    self.working.pop(device_mac, None)
                elif record["status"] in ("NG", "ERROR"):
                    camera = engine.CameraInfo(record["new_ip"], record["sn"], device_mac)
                    self.watch_removal(camera, record["new_ip"], discover=True)
        elif action == "job_new":
            if self.flow_started:
                raise ValueError("実行中は新しい作業へ変更できません")
            self.store.archive("incomplete")
            self.journal.begin_job()
            self.recovery_pending = False
            console_log("[Job] 前回作業をhistoryへ保存。新規作業待機")
        elif action == "job_setup":
            if self.flow_started:
                raise ValueError("検査開始前にCartonとBase IPを設定してください")
            import ipaddress
            base_ip = str(ipaddress.IPv4Address(body.get("base_ip", "")))
            carton = str(body.get("carton", "")).strip()
            if not __import__('re').fullmatch(r"[0-9A-Za-z_-]{1,32}", carton):
                raise ValueError("Carton番号を入力してください")
            start = int(base_ip.rsplit(".", 1)[1])
            if start + self.mode_count - 1 > 254 or start < 1:
                raise ValueError("Base IPから台数分の連続IPを確保できません")
            maximum = int(body.get("carton_max", 12))
            if maximum < 1:
                raise ValueError("Carton最大台数は1以上")
            if not self.persist_settings({"carton": carton, "carton_max": maximum,
                "work_prefix": base_ip.rsplit(".", 1)[0], "work_start": start,
                "work_end": start + self.mode_count - 1}):
                raise ValueError("作業設定の保存失敗")
        elif action == "config_confirmed":
            identity = flow.mac(body["mac"])
            if identity in self.config_running or identity in self.onboarding:
                raise ValueError("このMACの処理は実行中です")
            self.store.update(identity, config_uncertain=False, results={"settings": "OK"})
            self.command({"action": "retry_device", "mac": identity})
        elif action == "retry_device":
            identity = flow.mac(body["mac"])
            if identity in self.onboarding or identity in self.config_running:
                raise ValueError("このMACの処理は実行中です")
            self.task_generation[identity] = self.task_generation.get(identity, 0) + 1
            self.ir_checks.pop(identity, None)
            self.ir_frame_times.pop(identity, None)
            self.approved.discard(identity)
            self.jobs.pop(identity, None)
            for panel in self.panels:
                camera = panel.processing_camera or panel.camera or panel.last_camera
                if camera and flow.mac(camera.mac) == identity:
                    panel.finish()
                    if engine.SCANNER_LOOP:
                        import asyncio
                        asyncio.run_coroutine_threadsafe(engine.release_camera(camera.sn), engine.SCANNER_LOOP)
            self.store.update(identity, status="CONNECTING", step="HTTP")
            self.device_scan()
        elif action == "start":
            if self.demo:
                raise ValueError("デモ表示中です。")
            if not self.settings.get("network_id"):
                raise ValueError("設定でMAC/IP確認用のネットワーク接続を選択してください。")
            self.resuming = bool(self.store.job)
            self.start_workflow()
        elif action in ("select", "ok", "ng"):
            index = int(body.get("index", 0))
            if not 0 <= index < self.mode_count:
                raise ValueError("カメラ番号が不正です。")
            self.select_panel(index)
            if action == "ok" and self.panels[index].processing_camera:
                job = self.jobs.get(flow.mac(self.panels[index].processing_camera.mac))
                if job:
                    self.finalize(job)
                    return
            if action != "select":
                if self.ng_preview_open:
                    raise ValueError("NG確認画面を閉じてください。")
                button = self.panels[index].ok if action == "ok" else self.panels[index].ng
                if button.cget("state") != "normal":
                    raise ValueError("この操作はまだ使用できません。")
                if action == "ok":
                    self.mark_ok(index)
                else:
                    self.begin_ng(index)
        elif action == "mode":
            count = int(body["count"])
            if count not in (1, 2, 4, 6):
                raise ValueError("表示台数が不正です。")
            if self.flow_started and count != self.mode_count:
                raise ValueError("作業モードは検査開始前に変更してください")
            self.mode_var.set(count)
            self.change_mode()
        elif action == "cancel_ng":
            self.preview = None
            self.ng_preview_open = False
        elif action == "save_ng":
            self.complete_ng(body.get("category"), body.get("reason", ""), body.get("boxes", []), body.get("discard") is True)
        elif action == "device_scan":
            self.device_scan()
        elif action == "network_scan":
            self.scan_network()
        elif action == "usb_scan":
            if self.usb_scanning:
                raise ValueError("USB Camera検索中です。完了までお待ちください。")
            if self.usb_reader is not None:
                done = getattr(self.usb_reader, "done_event", None)
                if self.usb_search_stopped and done is not None and not done.is_set():
                    raise ValueError("前のUSBカメラが応答待ちです。カメラを抜き差ししてから再試行してください。")
                self.usb_reader.stop()
                self.usb_reader = None
            self.usb_search_stopped = False
            self.usb_cycle_active = False
            self.usb_scanning = False
            self.scan_usb()
        elif action == "target_next":
            if self.flow_started or any(p.processing_camera for p in self.panels):
                raise ValueError("開始IPは検査開始前に設定してください")
            value = int(body.get("value", 0))
            if not int(self.settings.get("target_start", 215)) <= value <= int(self.settings.get("target_end", 254)):
                raise ValueError("開始IPは目標IP範囲内で指定してください")
            if not self.persist_settings({"target_next": value}):
                raise ValueError("開始IPの保存失敗")
        elif action == "settings":
            network = body.get("network", "")
            if not network or network not in [item["id"] for item in self.settings.get("network_interfaces", [])]:
                raise ValueError("MAC/IP確認に使用するネットワーク接続を選択してください。")
            start = int(body.get("start", self.start_entry.get()))
            end = int(body.get("end", self.end_entry.get()))
            if not 0 <= start <= end <= 255:
                raise ValueError("IP範囲は0～255、開始IP≤終了IPで設定してください。")
            mode = int(body.get("mode", self.mode_count))
            stream = body.get("stream", engine.RTSP_PATH)
            if mode not in (1, 2, 4, 6) or stream not in ("/main", "/sub"):
                raise ValueError("表示モード・ストリームが不正です。")
            if self.scan_started and (start != int(self.start_entry.get()) or end != int(self.end_entry.get()) or stream != engine.RTSP_PATH):
                raise ValueError("検索開始後のIP範囲・ストリーム変更は、再起動後に行ってください。")
            if any(p.camera or p.processing_camera for p in self.panels[mode:]):
                raise ValueError("カメラ2～4の検査完了後に1台表示へ変更してください。")
            if network != self.settings.get("network_id") and (self.flow_started or any(p.processing_camera for p in self.panels)):
                raise ValueError("検査中はネットワークを変更できません。再起動後に変更してください。")
            usb = body.get("usb")
            if usb is not None and int(usb) not in self.usb_devices:
                raise ValueError("USB Cameraを再検索してください。")
            interface = next(item for item in self.settings["network_interfaces"] if item["id"] == network)
            extended = {key: body.get(key, self.settings.get(key)) for key in (
                "work_prefix", "work_start", "work_end", "target_prefix", "target_start", "target_end", "target_next",
                "target_mask", "target_gateway", "allowed_models", "access_username", "access_password",
                "after_config_username", "after_config_password", "config_import_password", "post_url", "post_enabled") if body.get(key, self.settings.get(key)) is not None}
            if extended.get("post_url"):
                extended["post_url"] = gas_post.validate_url(str(extended["post_url"]).strip())
            if extended.get("post_enabled") and not extended.get("post_url"):
                raise ValueError("POST URLを入力してください")
            if "config_import_password" in extended and (not isinstance(extended["config_import_password"], str) or
                    not extended["config_import_password"] or "\x00" in extended["config_import_password"]):
                raise ValueError("configファイルの導入パスワードを入力してください")
            for key in ("access_username", "access_password", "after_config_username", "after_config_password"):
                if key in extended and (not isinstance(extended[key], str) or not extended[key] or
                                        any(c in extended[key] for c in (";", "\r", "\n", "\x00"))):
                    raise ValueError("アクセス認証を入力してください（セミコロン・改行は使用不可）")
            if any(p.processing_camera for p in self.panels) and any(
                    key in extended and self.settings.get(key) != extended[key] for key in
                    ("access_username", "access_password", "after_config_username", "after_config_password", "config_import_password")):
                raise ValueError("カメラ検査中はアクセス認証を変更できません")
            if "allowed_models" in extended:
                known_models = {model for item in self.journal.data["devices"].values() for model in flow.device_models(item)}
                known_models.update(self.settings.get("allowed_models", []))
                if not isinstance(extended["allowed_models"], list) or any(model not in known_models for model in extended["allowed_models"]):
                    raise ValueError("検出済みのモデルから許可するモデルを選択してください")
            proposed = {**self.settings, **extended}
            if any(key.startswith("target_") or key.startswith("work_") for key in extended):
                flow.network_config(proposed, interface)
            if self.flow_started and any(self.settings.get(key) != value for key, value in extended.items() if key not in ("post_url", "post_enabled")):
                raise ValueError("検査開始後のネットワーク割当条件は変更できません")
            changes = {**extended, "network_id": network, "scan_start": start, "scan_end": end,
                       "display_mode": mode, "rtsp_path": stream}
            if usb is not None:
                changes.update(usb_index=int(usb), usb_name=self.usb_devices[int(usb)])
            if not self.persist_settings(changes):
                raise ValueError("設定保存失敗")
            self.apply_access_settings()
            self.update_network_label()
            for widget, value in ((self.start_entry, start), (self.end_entry, end)):
                widget.delete(0, "end")
                widget.insert(0, str(value))
            engine.RTSP_PATH = stream
            self.mode_var.set(mode)
            self.change_mode()
            if usb is not None and (int(usb) != self.usb_selected or self.usb_reader is None):
                self.usb_entry.delete(0, "end")
                self.usb_entry.insert(0, str(usb))
                self.connect_usb()
            if not self.flow_started:
                self.device_scan()
        elif action == "debug":
            engine.DEBUG_ENABLED.set() if body.get("enabled") else engine.DEBUG_ENABLED.clear()
        elif action == "close":
            self.close()
        else:
            raise ValueError("不明な操作です。")
        self.refresh_shortcut_labels()

    def snapshot(self):
        panels = []
        for panel in self.panels[:self.mode_count]:
            camera = panel.camera or panel.processing_camera or (panel.last_camera if panel.result_state else None)
            panels.append({"index": panel.index, "title": panel.title.cget("text"),
                "status": panel.status.cget("text"), "message": panel.video.cget("text"),
                "color": panel.video.cget("fg"), "result": panel.result_state,
                "detail": panel.status_detail.cget("text") if panel.status_detail.visible else "",
                "ir_prompt": panel.status_detail.cget("text") if camera and flow.mac(camera.mac) in self.ir_checks and self.ir_checks[flow.mac(camera.mac)].stage != 'PASS' and panel.reader else '',
                "fps": panel.fps_label.cget("text") if panel.fps_label.visible else "",
                "image": panel.video.image is not None, "selected": self.selected_panel == panel.index,
                "ok": panel.ok.cget("text"), "ng": panel.ng.cget("text"),
                "can_ok": panel.ok.cget("state") == "normal" and not self.ng_preview_open and not self.demo,
                "can_ng": panel.ng.cget("state") == "normal" and not self.ng_preview_open and not self.demo,
                "camera": {"ip": camera.ip, "sn": camera.sn, "mac": camera.mac,
                           "model": self.journal.data["devices"].get(flow.mac(camera.mac), {}).get("model", ""),
                           "oem_model": self.journal.data["devices"].get(flow.mac(camera.mac), {}).get("oem_model", "")} if camera else None,
                "logs": list(panel.log_lines)})
        reader = self.usb_reader
        preview = None if self.preview is None else {key: self.preview[key] for key in ("index", "exterior", "connection_failure")}
        return {"panels": panels, "mode": self.mode_count, "selected": self.selected_panel,
                "header": self.header_status.cget("text"), "started": self.scan_started,
                "counter": self.counter_label.cget("text"), "network": self.network_label.cget("text"),
                "post": {"busy": self.post_busy, "error": self.post_error, "response": self.post_response},
                "storage_error": self.store.save_error,
                "job": self.store.job, "recovery_pending": self.recovery_pending, "carton_ok": self.store.carton_count(self.settings.get("carton", "0001")),
                "settings": self.settings, "usb_devices": self.usb_devices,
                "scan_start": int(self.start_entry.get()), "scan_end": int(self.end_entry.get()),
                "work_prefix": self.settings.get("work_prefix") or (self.selected_interface() or {"ip": "192.168.0.1"})["ip"].rsplit(".", 1)[0],
                "stream": engine.RTSP_PATH,
                "devices": list({**self.journal.data["devices"], **{d["mac"]: {**self.journal.data["devices"].get(d["mac"], {}), **d} for d in self.discovery_current}}.values()),
                "models": sorted({model for item in self.journal.data["devices"].values() for model in flow.device_models(item)}),
                "discovery_busy": self.discovery_busy, "discovery_error": self.discovery_error,
                "config_files": sorted(p.name for p in (BASE / "config file").glob("*") if p.is_file() and not p.name.startswith(".")),
                "usb_status": self.usb_label.cget("text"),
                "usb_stopped": self.usb_search_stopped,
                "usb_index": self.usb_selected, "preview": preview, "debug": list(self.debug_history)[-100:], "demo": self.demo}

    def jpeg(self, key):
        image = self.preview["image"] if key == "preview" and self.preview else (
            self.panels[int(key)].video.image if key != "preview" else None)
        if image is None:
            return None
        previous = self.jpeg_cache.get(key)
        if previous and previous[0] is image:
            return previous[1]
        data = io.BytesIO()
        image.save(data, format="JPEG", quality=80)
        self.jpeg_cache[key] = (image, data.getvalue())
        return data.getvalue()


class Runtime:
    def __init__(self, demo=False):
        self.demo = demo
        self.requests = queue.Queue()
        self.ready = threading.Event()
        self.error = None
        self.root = None
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        if not self.ready.wait(10):
            raise RuntimeError("Interface startup timed out")
        if self.error:
            raise self.error

    def run(self):
        try:
            self.root = OwnerLoop()
            if not self.demo:
                engine.exterior.evidence_store.organize_records(os.path.relpath(BASE))
            self.app = BrowserInspection(self.root, self.demo)
            if self.demo:
                camera = engine.CameraInfo("192.168.0.233", "DEMO0001", "02:00:00:00:00:03")
                self.app.panels[0].show_result("RUNING", camera)
                self.app.panels[0].last_camera = camera
                if not self.app.journal.data["devices"]:
                    self.app.journal.data["devices"] = {camera.mac: {"mac": camera.mac, "ip": camera.ip,
                        "model": "MS-C8164-FPE", "oem_model": "NC8164-FPE", "sn": camera.sn,
                        "info": "DEMO · MS-C8164-FPE / NC8164-FPE"}}
                self.app.allocate_label(camera, "192.168.0.220")
            self.ready.set()
            while not self.root.closed:
                for _ in range(40):
                    try:
                        kind, argument, answer = self.requests.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        result = self.app.snapshot() if kind == "state" else self.app.jpeg(argument) if kind == "image" else self.app.command(argument)
                        answer.put((True, result))
                    except Exception as error:
                        console_log(f"[Runtime error] {kind}: {error}")
                        answer.put((False, str(error)))
                self.root.tick()
                time.sleep(.005)
        except Exception as error:
            console_log(f"[Runtime stopped] {error}")
            import traceback
            console_log(traceback.format_exc())
            self.error = error
            self.ready.set()

    def call(self, kind, argument=None):
        answer = queue.Queue(maxsize=1)
        self.requests.put((kind, argument, answer))
        success, result = answer.get(timeout=10)
        if not success:
            raise ValueError(result)
        return result


def make_handler(runtime, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def respond(self, status, data, mime):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass
        def valid_host(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"
        def do_GET(self):
            if not self.valid_host():
                return self.respond(403, b"Invalid host", "text/plain")
            path = urlsplit(self.path).path
            try:
                if path == "/":
                    page = (CODE_DIR / "web" / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", token)
                    self.respond(200, page.encode(), "text/html; charset=utf-8")
                elif path == "/api/state":
                    self.respond(200, json.dumps(runtime.call("state"), ensure_ascii=False).encode(), "application/json; charset=utf-8")
                elif path == "/api/gas-example":
                    self.respond(200, (BASE / "examples" / "gas_receiver.gs").read_bytes(), "text/plain; charset=utf-8")
                elif path.startswith("/api/image/"):
                    key = path.rsplit("/", 1)[-1]
                    if key not in ("0", "1", "2", "3", "4", "5", "preview"):
                        raise ValueError("Invalid image")
                    data = runtime.call("image", key)
                    self.respond(200 if data else 204, data or b"", "image/jpeg")
                else:
                    self.respond(404, b"Not found", "text/plain")
            except Exception:
                self.respond(503, b"Interface unavailable", "text/plain")
        def do_POST(self):
            origin = self.headers.get("Origin")
            expected = f"http://127.0.0.1:{self.server.server_port}"
            if not self.valid_host() or self.headers.get("X-Inspection-Token") != token or origin not in (None, expected):
                return self.respond(403, b"Forbidden", "text/plain")
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if urlsplit(self.path).path != "/api/command" or not 0 < length <= 65536:
                    raise ValueError("Invalid request")
                body = json.loads(self.rfile.read(length))
                console_log(f"[UI command] {body.get('action', '?')}")
                runtime.call("command", body)
                self.respond(200, b'{"ok":true}', "application/json")
            except Exception as error:
                self.respond(400, json.dumps({"error": str(error)}, ensure_ascii=False).encode(), "application/json; charset=utf-8")
    return Handler


def main():
    import sys
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    from logging.handlers import RotatingFileHandler
    log_dir = BASE / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s",
                        datefmt="%H:%M:%S", handlers=[logging.StreamHandler(),
                            RotatingFileHandler(log_dir / "tool.log", maxBytes=5_000_000,
                                                backupCount=5, encoding="utf-8")])
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    runtime = Runtime(args.demo)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(runtime, secrets.token_hex(24)))
    server.timeout = .5
    url = f"http://127.0.0.1:{server.server_port}/"
    print("Camera Inspection: " + url, flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        while not runtime.root.closed:
            server.handle_request()
    except KeyboardInterrupt:
        runtime.call("command", {"action": "close"})
    finally:
        server.server_close()


if __name__ == "__main__":
    from instance_lock import InstanceLock
    with InstanceLock(BASE / 'data' / 'application.lock'):
        main()
