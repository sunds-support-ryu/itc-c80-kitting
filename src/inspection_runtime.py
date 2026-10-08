"""Headless inspection runtime shared by the native desktop window.

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
        self.config_dispatched = set()
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
        self.rtsp_counts = {}
        self.rtsp_sample_times = {}
        self.rtsp_started = {}
        super().__init__(root)
        if not self.settings.get('post_url'):
            self.settings['post_url'] = gas_post.DEFAULT_POST_URL
        self.settings.setdefault('post_enabled', False)
        self.panels.extend(engine.CameraPanel(self, self.panel_area, i) for i in range(4, 6))
        self.protection_logged = set()
        for device_mac, record in self.journal.data["active_work"].items():
            self.working[device_mac] = record["ip"]
            console_log(f'[MAC protection restored] MAC={device_mac} IP={record['ip']} IP change prohibited')
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
            self.network_label.config(text=f'No network adapter selected. Select an adapter in settings. Saved adapters: {count}items')
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
            raise ValueError('Cannot hide an occupied camera panel')
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
            if check.stage == 'PURPLE':
                self.store.update(identity, step='CONFIG', results={'ir_cut': 'OK', 'ir_cut_method': 'manual'})
                self.start_config(panel)
            else:
                raise ValueError('RTSP verification in progress')
            return
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
            now = time.monotonic()
            if check.stage == 'COVER':
                self.rtsp_started.setdefault(identity, now)
                if 'NG' in getattr(panel.reader, 'last_status', '') or now - self.rtsp_started[identity] >= 15:
                    self.finish_ng_direct(panel, 'E1', 'RTSP defect', 'rtsp')
                    continue
                if image is None:
                    self.rtsp_counts[identity] = 0
                elif fresh and now - self.rtsp_sample_times.get(identity, -float('inf')) >= .5:
                    self.rtsp_sample_times[identity] = now
                    self.rtsp_counts[identity] = self.rtsp_counts.get(identity, 0) + 1
                    if self.rtsp_counts[identity] >= 3:
                        check.start(now)
                        self.store.update(identity, step='IR_CUT_BW', results={'rtsp': 'OK', 'ir_cut': None})
                        console_log(f'[RTSP auto OK -> IR-CUT started] MAC={identity}')
                if fresh:
                    self.ir_frame_times[identity] = stamp
            if check.stage in ('BW', 'COLOR'):
                if fresh or image is None or time.monotonic() - check.started > 90:
                    check.feed(frame, time.monotonic())
                if fresh:
                    self.ir_frame_times[identity] = stamp
            if check.stage == 'PASS':
                check.stage = 'PURPLE'
                self.store.update(identity, step='IR_CUT_CONFIRM', results={'light_sensor': 'OK'})
            messages = {'COVER': 'Verifying RTSP connection.',
                        'BW': 'Operation 1: cover the camera. Waiting for automatic black-and-white confirmation.',
                        'COLOR': 'Operation 2: remove the cover. Waiting for automatic color confirmation.',
                        'PURPLE': 'Check for purple areas. No purple: OK / purple present: NG.',
                        'TIMEOUT': 'IR-CUT / light sensor switching unverified. Press NG.'}
            panel.status.config(text=('RTSP · ' if check.stage == 'COVER' else 'Purple check -' if check.stage == 'PURPLE' else 'IR-CUT / light sensor -') + messages[check.stage])
            panel.status_detail.config(text=messages[check.stage])
            panel.show_message(messages[check.stage], '#d97706' if check.stage == 'BW' else '#2563eb' if check.stage == 'COLOR' else engine.COLOR_GREEN)
            panel.ok.config(text='OK - import config', state='normal' if check.stage == 'PURPLE' else 'disabled')
            panel.ng.config(text='RTSP NG' if check.stage == 'COVER' else 'IR-CUT NG' if check.stage == 'PURPLE' else 'IR-CUT / light sensor NG', state='normal' if not self.ng_preview_open else 'disabled')
            if self.mode_count == 1:
                if panel.ok.cget('state') == 'normal':
                    panel.ok.config(text=panel.ok.cget('text') + ' [Enter]')
                if panel.ng.cget('state') == 'normal':
                    panel.ng.config(text=panel.ng.cget('text') + ' [Esc]')
            if check.stage != previous:
                self.store.update(identity, step='IR_CUT_' + check.stage)
                console_log(f'[IR-CUT] MAC={identity} {previous}→{check.stage} {check.metrics}')

    def finish_ng_direct(self, panel, category, reason, result_key):
        reason_code = {'rtsp': 'RTSP_FAILURE', 'light_sensor': 'IR_CUT_LIGHT_SENSOR_FAILURE', 'ir_cut': 'IR_CUT_PURPLE_DEFECT'}.get(result_key, 'OTHER_FAILURE')
        camera = panel.camera
        if camera is None:
            return
        identity = flow.mac(camera.mac)
        image = panel.reader.snapshot() if panel.reader else None
        if image is None:
            image = engine.exterior.evidence_store.connection_failure_image(camera, reason)
        evidence = ''
        try:
            evidence = self.save_evidence(camera, image, category)
        except OSError as error:
            console_log(f'[NG evidence save pending] MAC={identity} {error}')
        self.store.update(identity, ng_reason_code=reason_code, results={result_key: 'NG'})
        self.store.finish(identity, 'NG', reason)
        engine.save_result(camera, 'NG', '-', ng_reason=reason, evidence_path=evidence)
        self.approved.discard(identity)
        self.ng_count += 1
        panel.finish('NG')
        panel.show_result('NG', camera)
        panel.last_camera = camera
        panel.ok.config(state='disabled')
        panel.ng.config(state='disabled')
        panel.status.config(text='NG · ' + reason)
        panel.show_message('NG · ' + reason + '\nDisconnect and proceed to the next camera', engine.COLOR_RED)
        if self.flow_started:
            self.watch_removal(camera, camera.ip, discover=True)
        console_log(f'[NG completed] MAC={identity} {reason}')

    def start_config(self, panel):
        if self.demo:
            return
        camera = panel.camera
        if camera is None:
            raise ValueError('No camera selected for config import')
        identity = flow.mac(camera.mac)
        if identity in self.config_dispatched:
            return
        self.config_dispatched.add(identity)
        self.config_running.add(identity)
        panel.finish('Waiting for config import')
        panel.show_result('RUNING', camera)
        panel.ok.config(state='disabled')
        panel.ng.config(state='disabled')
        console_log(f'[IR-CUT OK -> config dispatched] MAC={identity} IP={camera.ip}')
        def execute():
            try:
                engine.step3.load_config(self.settings_dir)
                self.store.update(identity, step='CONFIG', config_uncertain=True)
                success, detail = engine.step3.run(camera, self.settings_dir,
                    self.camera_credentials.get(identity, (engine.USERNAME, engine.PASSWORD)),
                    log=lambda text: (console_log(f'[config] MAC={identity} {text}'),
                        engine.event_queue.put(('step3_progress', camera.mac, text))),
                    stopped=engine.APP_STOP.is_set, reset_ip=False,
                    credential_report=lambda credentials: self.camera_credentials.__setitem__(identity, credentials))
                self.store.update(identity, step='STICKER' if success else 'CONFIG',
                    access_stage='before' if self.camera_credentials.get(identity) == (engine.USERNAME, engine.PASSWORD) else 'after',
                    config_uncertain=not success and 'rejected' not in detail,
                    results={'settings': 'OK' if success else 'NG'})
                self.workflow_events.put(('config_ready' if success else 'config_failed', (camera, detail)))
            except Exception as error:
                self.workflow_events.put(('config_failed', (camera, str(error))))
            finally:
                self.config_running.discard(identity)
        threading.Thread(target=execute, daemon=True, name='config-' + identity.replace(':', '')).start()

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
            raise ValueError('Select a network adapter')
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
                if allow_moves:
                    planned = flow.network_config(self.settings, interface)
                    pool = planned['work'][:self.mode_count]
                    discovered.sort(key=lambda device: (0 if device.get('ip') in pool else 2 if device.get('ip') in planned['targets'] else 1, device.get('ip', '')))
                for device in discovered:
                    if engine.APP_STOP.is_set():
                        break
                    device_mac = flow.mac(device["mac"])
                    held = self.journal.data["active_work"].get(device_mac)
                    if held and device_mac not in self.protection_logged:
                        console_log(f'[MAC protection] MAC={device_mac} IP={held['ip']} SET resend prohibited')
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
                            self.workflow_events.put(("onboarding_failed", (camera, state.get("error", 'Previous result restored'))))
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
                        console_log(f'[MAC protection] MAC={device_mac} work IP={work_ip} is outside Base IP range; modification prohibited')
                        continue
                    if work_ip is None:
                        if len(used) >= self.mode_var.get():
                            continue
                        if self.store.job and len(self.store.job["devices"]) >= self.store.job.get("expected_count", self.mode_count):
                            continue
                        if device.get('ip') in config['work'][:self.mode_count] and device['ip'] not in used:
                            work_ip = device['ip']
                        else:
                            work_ip = next((ip for ip in config["work"][:self.mode_count] if ip not in used and not link.arp(ip)), None)
                    if work_ip is None:
                        console_log(f'[Work IP waiting] MAC={device_mac} no free slot; continue other MACs')
                        continue
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
                    raise ValueError('Config application unknown. Check camera Web settings before confirming application. No resend')
                camera = engine.CameraInfo(work_ip, record.get("sn", device.get("sn", "")), device_mac)
                engine.step3.verify_identity(f"http://{work_ip}", camera, engine.step3.VERIFY_CREDENTIALS, transport)
                self.store.update(device_mac, config_uncertain=False, results={"settings": "OK"})
                record = {**record, "results": {**record.get("results", {}), "settings": "OK"}}
            if record.get("results", {}).get("settings") == "OK":
                credentials = self.camera_credentials.get(device_mac, engine.step3.VERIFY_CREDENTIALS)
            if needs_move:
                # Never resend SET after an uncertain response on subsequent scans.
                if not link.arp(work_ip, device_mac):
                    engine.send_log(f'[Work IP] MAC={device_mac} → {work_ip}')
                    link.set_ip(device_mac, work_ip, config["work_mask"], config["work_gateway"], credentials)
            if not link.wait_ip(device_mac, work_ip):
                raise ValueError('Work IP change unverified')
            self.store.update(device_mac, status="CONNECTING", step="HTTP", results={"network": "OK"})
            deadline = time.monotonic() + 60
            last_error = 'Waiting for HTTP'
            while time.monotonic() < deadline and not engine.APP_STOP.is_set():
                try:
                    status, body = transport(f"http://{work_ip}" + engine.INFO_PATH, credentials=credentials)
                    if status in (401, 403):
                        raise ValueError(f'HTTP={status} verify pre-config access credentials')
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
                    last_error = f'HTTP={status} MAC/model unverified'
                except OSError as error:
                    last_error = str(error)
                console_log(f'[Work IP HTTP waiting] MAC={device_mac} IP={work_ip} {last_error}')
                time.sleep(2)
            raise ValueError(f'IP={work_ip} HTTP verification failed: {last_error}')
        except Exception as error:
            self.store.update(device_mac, status="ERROR", step="CONNECTING", error=str(error))
            self.workflow_events.put(("onboarding_failed", (engine.CameraInfo(work_ip, device.get("sn", ""), device_mac), str(error))))
            engine.send_log(f'[Work IP Error] MAC={device_mac} IP={work_ip} {error}')
        finally:
            if "transport" in locals() and hasattr(transport, "close"):
                transport.close()
            self.onboarding.discard(device_mac)

    def start_workflow(self):
        if self.recovery_pending:
            raise ValueError('Choose whether to resume the previous job or start a new one')
        interface = self.selected_interface()
        if interface is None or not self.settings.get("allowed_models"):
            raise ValueError('Configure the network adapter and allowed models')
        if not getattr(self, "resuming", False) and any(job.get("state") in ("sticker", "changing") for job in self.journal.data["assignments"].values()):
            raise ValueError('Unfinished IP allocations exist. Check the journal before starting')
        interface = flow.enrich_interfaces([dict(interface)])[0]
        interface = flow.select_work_interface(self.settings, interface)
        console_log(f"[Network] {interface}")
        if os.name == "nt" and not interface.get("prefix_verified"):
            raise ValueError('PC subnet information unavailable. Rescan network adapters')
        records = [interface if item["id"] == interface["id"] else item for item in self.settings["network_interfaces"]]
        if not self.persist_settings({"network_interfaces": records}):
            raise ValueError('Network journal save failed')
        config = flow.network_config(self.settings, interface)
        config["work"] = config["work"][:self.mode_count]
        if len(config["work"]) < self.mode_count:
            raise ValueError('Configure enough consecutive work IPs from Base IP')
        if not self.store.job:
            carton = self.settings.get("carton", "0001")
            maximum = int(self.settings.get("carton_max", 12))
            if self.store.carton_count(carton) >= maximum:
                raise ValueError(f'This carton has reached{maximum}cameras. Switch to the next carton')
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
        self.flow_started = True
        if not self.engine_started:
            engine.scanner_main = self.mac_scanner
            self.start_scan()
            self.engine_started = True
        else:
            self.scan_started = True
        if getattr(self, 'resuming', False) and self.store.job:
            for identity, record in self.store.job['devices'].items():
                device_mac = flow.mac(identity)
                assignment = self.journal.data['assignments'].get(device_mac, {})
                if assignment.get('state') == 'confirmed':
                    saved_camera = engine.CameraInfo(assignment['ip'], record['sn'], device_mac)
                    self.watch_removal(saved_camera, assignment['ip'], 'disconnected')
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
                                        self.workflow_events.put(("config_ready", (camera, 'Config already applied - resume')))
                                    else:
                                        if record.get("results", {}).get("preparation") != "OK":
                                            self.store.update(identity, step="RESET", status="TESTING")
                                            if not await engine.reset_device(session, camera, 3):
                                                raise ValueError('Pre-inspection reset failed')
                                            if not await engine.wait_reboot(session, camera):
                                                raise ValueError('Reboot recovery unverified')
                                            self.store.update(identity, results={"preparation": "OK"})
                                        verified = await engine.get_camera_info(session, ip)
                                        if not verified or flow.mac(verified.mac) != identity or verified.sn != camera.sn:
                                            raise ValueError('Work IP MAC/SN unverified')
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
                                    console_log(f'[Camera Task released] MAC={identity}')
                            tasks[identity] = asyncio.create_task(monitor())
                            generations[identity] = self.task_generation.get(identity, 0)
                    for identity, task in list(tasks.items()):
                        if identity not in self.approved:
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                            tasks.pop(identity)
                    await asyncio.sleep(.2)
            finally:
                for task in tasks.values():
                    task.cancel()
                await asyncio.gather(*tasks.values(), return_exceptions=True)
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
                panel.video.config(text='Attach the label', fg=engine.COLOR_BLUE)
                panel.status.config(text='Waiting for label confirmation')
                panel.status_detail.config(text=f'Target IP for this camera: {target}\nAfter attaching the label, press Label attached')
                panel.ok.config(text='Label attached - change IP', state="normal")
                panel.ng.config(state="disabled")
        self.update_counter()

    def finalize(self, job):
        camera, target = job["camera"], job["target"]
        device_mac = flow.mac(camera.mac)
        if job["state"] != "sticker":
            raise ValueError('IP change in progress')
        config = flow.network_config(self.settings, self.selected_interface())
        # MAC identity and destination occupancy are checked again at the moment of confirmation.
        def change():
            try:
                if not self.link.arp(camera.ip, device_mac):
                    raise ValueError('Target MAC not verified at work IP. IP unchanged')
                occupied = self.link.arp(target)
                if any(found != device_mac for found in occupied):
                    raise ValueError('Target IP is occupied by another device. IP unchanged')
                self.journal.transition(device_mac, "changing")
                self.link.set_ip(device_mac, target, config["mask"], config["gateway"], self.camera_credentials.get(device_mac, engine.step3.VERIFY_CREDENTIALS))
                if not self.link.wait_ip(device_mac, target):
                    raise ValueError('Final MAC/IP unverified. No repeated IP change')
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
                panel.status_detail.config(text=f'IP change and MAC verification -> {target}')
        threading.Thread(target=change, daemon=True).start()

    def refresh_shortcut_labels(self):
        super().refresh_shortcut_labels()
        for panel in self.panels:
            if panel.processing_camera:
                job = self.jobs.get(flow.mac(panel.processing_camera.mac))
                if job and job["state"] == "sticker":
                    panel.ok.config(text='Label attached - change IP', state="normal")
            if panel.camera and panel.exterior_done:
                check = self.ir_checks.get(flow.mac(panel.camera.mac))
                if check:
                    panel.ok.config(text='OK - import config', state='normal' if check.stage == 'PURPLE' else 'disabled')
                    panel.ng.config(text=('RTSP NG' if check.stage == 'COVER' else 'IR-CUT NG' if check.stage == 'PURPLE' else 'IR-CUT / light sensor NG') + (' [Esc]' if self.mode_count == 1 and panel.ng.cget('state') == 'normal' else ''))

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
                        console_log(f'[MAC monitor] MAC={identity} {error}')
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
                    self.log('[Network Error]' + value)
                if self.flow_started:
                    self.root.after(3000, self.device_scan)
            elif kind == "reserve_slot":
                camera, future = value
                identity = flow.mac(camera.mac)
                self.ir_checks[identity] = ir_cut_check.Check()
                self.rtsp_counts.pop(identity, None)
                self.rtsp_sample_times.pop(identity, None)
                self.rtsp_started.pop(identity, None)
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
                        console_log(f'[Sequence] OK counted - next starting IP={self.settings.get('target_prefix', '192.168.0')}.{next_number}')
                engine.save_result(camera, "OK", 'Final MAC/IP verified' + job["target"])
                for panel in self.panels:
                    if panel.processing_camera and flow.mac(panel.processing_camera.mac) == flow.mac(camera.mac):
                        panel.show_result("OK", camera)
                        panel.processing_camera = camera  # Hold the slot until the MAC disappears.
                        panel.last_camera = camera
                        panel.ok.config(state="disabled")
                        panel.ng.config(state="disabled")
                        panel.video.config(text='OK\nTurn off power')
                        panel.status_detail.config(text='MAC matched -' + job["target"])
                self.header_status.config(text='Completed - waiting for power off')
            elif kind == "watch_error":
                camera, detail = value
                for panel in self.panels:
                    if panel.processing_camera and flow.mac(panel.processing_camera.mac) == flow.mac(camera.mac):
                        panel.status_detail.config(text='Rechecking MAC monitoring -' + detail)
            elif kind == "ng_disconnected":
                self.ir_checks.pop(flow.mac(value.mac), None)
                self.ir_frame_times.pop(flow.mac(value.mac), None)
                record = self.store.job["devices"].get(value.mac.replace(":", "").upper(), {}) if self.store.job else {}
                if record.get("status") == "ERROR":
                    self.store.finish(value.mac, "ERROR", record.get("error", ""))
                self.journal.release_work(value.mac)
                console_log(f'[MAC protection released] MAC={value.mac} power off verified')
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
                console_log(f'[MAC protection released] MAC={camera.mac} power off verified')
                self.working.pop(flow.mac(camera.mac), None)
                self.approved.discard(flow.mac(camera.mac))
                identity = flow.mac(camera.mac)
                self.jobs.setdefault(identity, {'camera': camera, 'target': camera.ip})['state'] = 'disconnected'
                self.store.finish(identity, 'OK')
                for panel in self.panels:
                    if panel.processing_camera and flow.mac(panel.processing_camera.mac) == flow.mac(camera.mac):
                        panel.finish()
                        panel.show_message('Disconnection verified\nWaiting for the next camera')
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
                    console_log('[History save pending]' + str(error))

    def scan_usb(self):
        if self.demo or self.usb_search_stopped or self.usb_cycle_active or self.usb_scanning:
            return
        self.usb_cycle_active = True
        self.usb_scanning = True
        self.usb_scan_time = time.monotonic()
        self.usb_label.config(text='Searching USB cameras...')
        def search():
            try:
                self.usb_auto_events.put((True, engine.exterior.list_usb_camera_devices()))
            except Exception as error:
                self.usb_auto_events.put((False, str(error)))
        threading.Thread(target=search, daemon=True).start()

    def connect_usb(self):
        index = int(self.usb_entry.get())
        if index not in self.usb_devices:
            raise ValueError('Rescan USB cameras')
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
            self.usb_label.config(text='USB disconnected - stopped after checking all candidates')
            return
        index, name = self.usb_candidates[self.usb_candidate_position]
        self.usb_candidate_position += 1
        self.usb_selected = index
        self.usb_connection_seen = False
        self.usb_attempt_time = time.monotonic()
        self.usb_reader = engine.exterior.InspectionUSBReader(index, name, retry=False)
        self.usb_reader.start()
        self.usb_label.config(text=f'USB connection verification {self.usb_candidate_position}/{len(self.usb_candidates)} · {name}')

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
                self.usb_label.config(text='USB discovery stopped -' + devices)
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
                self.usb_label.config(text='USB connected -' + self.usb_devices.get(self.usb_selected, ""))
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
                self.usb_label.config(text='USB discovery stopped - camera response timeout')

    def scan_network(self):
        if not self.demo:
            super().scan_network()

    def show_usb_status(self):
        # Browser sidebar replaces the separate native status window.
        self.usb_status_window = None

    def begin_ng(self, index):
        if self.ng_preview_open:
            raise ValueError('Close the NG confirmation window.')
        panel = self.panels[index]
        if panel.camera and panel.exterior_done:
            identity = flow.mac(panel.camera.mac)
            check = self.ir_checks.get(identity)
            stage = check.stage if check else 'COVER'
            key = 'rtsp' if stage == 'COVER' else 'ir_cut' if stage == 'PURPLE' else 'light_sensor'
            reason = {'rtsp': 'RTSP defect', 'light_sensor': 'IR-CUT / light sensor defect', 'ir_cut': 'IR-CUT defect'}[key]
            self.finish_ng_direct(panel, 'E1' if key == 'rtsp' else 'E2', reason, key)
            return
        if panel.camera is None:
            raise ValueError('No camera available.')
        exterior = not panel.exterior_done
        if exterior:
            if self.usb_owner != index or self.usb_reader is None:
                raise ValueError('Waiting for USB appearance inspection.')
            image = self.usb_reader.snapshot()
        else:
            if panel.reader is None:
                raise ValueError('Waiting for video verification.')
            image = panel.reader.snapshot()
        if image is None and exterior:
            raise ValueError('No USB image available.')
        connection_failure = image is None
        if connection_failure:
            image = engine.exterior.evidence_store.connection_failure_image(
                panel.camera, getattr(panel.reader, "last_status", 'No RTSP frames'))
        self.preview = {"index": index, "camera": panel.camera, "image": image.copy(),
                        "exterior": exterior, "connection_failure": connection_failure}
        self.ng_preview_open = True
        self.refresh_shortcut_labels()

    def complete_ng(self, category, reason, boxes, discard=False):
        preview = self.preview
        if preview is None:
            raise ValueError('No NG image available.')
        index, camera, image = preview["index"], preview["camera"], preview["image"]
        panel = self.panels[index]
        if panel.camera is not camera:
            raise ValueError('Inspection target changed.')
        if preview["exterior"]:
            if not boxes:
                raise ValueError('Draw a box around the affected area.')
            output = image.copy()
            draw = ImageDraw.Draw(output)
            for box in boxes:
                if len(box) != 4 or any(not isinstance(v, (int, float)) for v in box):
                    raise ValueError('Invalid annotation box.')
                x1, y1, x2, y2 = box
                if not (0 <= x1 < x2 <= image.width and 0 <= y1 < y2 <= image.height):
                    raise ValueError('Annotation box is outside the image.')
                draw.rectangle(box, outline="red", width=max(2, image.width // 300))
            if category not in ('B', 'D'):
                raise ValueError('Select appearance defect or damage')
            appearance_reason = 'Appearance defect' if category == 'B' else 'Damage'
            path = self.save_evidence(camera, output, 'B')
            self.store.update(camera.mac, results={"appearance": "NG", "appearance_category": appearance_reason}, step="PREPARE")
            engine.save_result(camera, 'Appearance NG', "-", ng_reason=appearance_reason, evidence_path=path)
            self.complete_exterior(index, "NG")
        else:
            if preview["connection_failure"]:
                category, reason = "E1", 'RTSP unavailable'
            elif category == "E2":
                reason = 'IR-CUT defect'
            elif category == "Z" and str(reason).strip():
                reason = str(reason).strip()
            else:
                raise ValueError('Check NG category and reason.')
            path = "" if discard else self.save_evidence(camera, image, category)
            if category == 'E2':
                self.store.update(camera.mac, results={'ir_cut': 'NG'})
            self.store.update(camera.mac, ng_reason_code={"E1": "RTSP_FAILURE", "E2": "IR_CUT_PURPLE_DEFECT", "Z": "OTHER_FAILURE"}.get(category, "OTHER_FAILURE"))
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
            raise ValueError('POST in progress')
        url = self.settings.get("post_url", "")
        if not url:
            self.post_last_attempt = time.monotonic()
            raise ValueError('Save a POST URL in settings')
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
                    console_log(f'[GAS response] record_id={row['record_id']} {json.dumps(received, ensure_ascii=False)}')
                    return received
                if test:
                    payload = {"type": "test", "record_id": "test-" + secrets.token_hex(8), "message": "ITC Camera Inspection connection test"}
                    self.post_response = gas_post.send(url, payload)
                else:
                    self.store.deliver(sender)
                    data = json.loads((self.store.root / "pending_post.json").read_text(encoding="utf-8"))
                    pending = [row for row in data["records"] if row["status"] != "SENT"]
                    if pending:
                        self.post_error = pending[-1].get("last_error", 'Waiting to send')
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
            raise ValueError('Hardware operations are disabled in demo mode.')
        if action == "post_settings":
            url = str(body.get("url", "")).strip()
            enabled = body.get("enabled") is True
            if url:
                gas_post.validate_url(url)
            elif enabled:
                raise ValueError('Enter a POST URL')
            if not self.persist_settings({"post_url": url, "post_enabled": enabled}):
                raise ValueError('POST settings save failed')
            self.post_last_attempt = 0
        elif action in ("post_retry", "post_test"):
            self.post_records(test=action == "post_test")
        elif action in ("startup_continue", "clear_counters"):
            self.recovery_pending = False
            if action == "clear_counters":
                self.ok_count = self.ng_count = 0
                self.update_counter()
            console_log('[Startup notice] Acknowledged; no camera operations' if action == "startup_continue" else '[Counters] Cleared display counts; history and MAC protection retained')
        elif action == "job_resume":
            if not self.recovery_pending:
                raise ValueError('No job available to resume')
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
                raise ValueError('Cannot switch jobs while running')
            self.store.archive("incomplete")
            self.journal.begin_job()
            self.recovery_pending = False
            console_log('[Job] Archived previous job; waiting for a new job')
        elif action == "job_setup":
            if self.flow_started:
                raise ValueError('Configure carton and Base IP before starting inspection')
            import ipaddress
            base_ip = str(ipaddress.IPv4Address(body.get("base_ip", "")))
            carton = str(body.get("carton", "")).strip()
            if not __import__('re').fullmatch(r"[0-9A-Za-z_-]{1,32}", carton):
                raise ValueError('Enter a carton number')
            start = int(base_ip.rsplit(".", 1)[1])
            if start + self.mode_count - 1 > 254 or start < 1:
                raise ValueError('Cannot reserve consecutive work IPs from Base IP')
            maximum = int(body.get("carton_max", 12))
            if maximum < 1:
                raise ValueError('Carton limit must be at least 1')
            if not self.persist_settings({"carton": carton, "carton_max": maximum,
                "work_prefix": base_ip.rsplit(".", 1)[0], "work_start": start,
                "work_end": start + self.mode_count - 1}):
                raise ValueError('Job settings save failed')
        elif action == 'rekit_device':
            identity = flow.mac(body['mac'])
            if identity in self.config_running or identity in self.onboarding or self.jobs.get(identity, {}).get('state') in ('sticker', 'changing') or any(
                    p.camera and flow.mac(p.camera.mac) == identity for p in self.panels):
                raise ValueError('This MAC is under inspection. Rekit after completion')
            self.store.forget_device(identity)
            self.journal.forget_device(identity)
            self.approved.discard(identity)
            self.working.pop(identity, None)
            self.jobs.pop(identity, None)
            self.config_dispatched.discard(identity)
            self.ir_checks.pop(identity, None)
            self.rtsp_counts.pop(identity, None)
            self.rtsp_started.pop(identity, None)
            self.task_generation[identity] = self.task_generation.get(identity, 0) + 1
            console_log(f'[Rekit] MAC={identity} cache released - history retained')
            self.device_scan()
        elif action == "config_confirmed":
            identity = flow.mac(body["mac"])
            if identity in self.config_running or identity in self.onboarding:
                raise ValueError('This MAC is being processed')
            self.store.update(identity, config_uncertain=False, results={"settings": "OK"})
            self.command({"action": "retry_device", "mac": identity})
        elif action == "retry_device":
            identity = flow.mac(body["mac"])
            if identity in self.onboarding or identity in self.config_running:
                raise ValueError('This MAC is being processed')
            self.task_generation[identity] = self.task_generation.get(identity, 0) + 1
            self.ir_checks.pop(identity, None)
            self.config_dispatched.discard(identity)
            self.rtsp_counts.pop(identity, None)
            self.rtsp_sample_times.pop(identity, None)
            self.rtsp_started.pop(identity, None)
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
                raise ValueError('Demo mode.')
            if not self.settings.get("network_id"):
                raise ValueError('Select the MAC/IP verification adapter in settings.')
            self.resuming = bool(self.store.job)
            self.start_workflow()
        elif action in ("select", "ok", "ng"):
            index = int(body.get("index", 0))
            if not 0 <= index < self.mode_count:
                raise ValueError('Invalid camera number.')
            self.select_panel(index)
            if action == "ok" and self.panels[index].processing_camera:
                job = self.jobs.get(flow.mac(self.panels[index].processing_camera.mac))
                if job:
                    self.finalize(job)
                    return
            if action != "select":
                if self.ng_preview_open:
                    raise ValueError('Close the NG confirmation window.')
                button = self.panels[index].ok if action == "ok" else self.panels[index].ng
                if button.cget("state") != "normal":
                    raise ValueError('This action is not available yet.')
                if action == "ok":
                    self.mark_ok(index)
                else:
                    self.begin_ng(index)
        elif action == "mode":
            count = int(body["count"])
            if count not in (1, 2, 4, 6):
                raise ValueError('Invalid panel count.')
            if self.flow_started and count != self.mode_count:
                raise ValueError('Change work mode before starting inspection')
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
                raise ValueError('USB discovery in progress. Wait for completion.')
            if self.usb_reader is not None:
                done = getattr(self.usb_reader, "done_event", None)
                if self.usb_search_stopped and done is not None and not done.is_set():
                    raise ValueError('Previous USB camera is still waiting. Reconnect it before retrying.')
                self.usb_reader.stop()
                self.usb_reader = None
            self.usb_search_stopped = False
            self.usb_cycle_active = False
            self.usb_scanning = False
            self.scan_usb()
        elif action == "target_next":
            if self.flow_started or any(p.processing_camera for p in self.panels):
                raise ValueError('Set starting IP before inspection')
            value = int(body.get("value", 0))
            if not int(self.settings.get("target_start", 215)) <= value <= int(self.settings.get("target_end", 254)):
                raise ValueError('Starting IP must be within the target range')
            if not self.persist_settings({"target_next": value}):
                raise ValueError('Starting IP save failed')
        elif action == "settings":
            network = body.get("network", "")
            if not network or network not in [item["id"] for item in self.settings.get("network_interfaces", [])]:
                raise ValueError('Select a network adapter for MAC/IP verification.')
            start = int(body.get("start", self.start_entry.get()))
            end = int(body.get("end", self.end_entry.get()))
            if not 0 <= start <= end <= 255:
                raise ValueError('IP range must be 0-255 and start must not exceed end.')
            mode = int(body.get("mode", self.mode_count))
            stream = body.get("stream", engine.RTSP_PATH)
            if mode not in (1, 2, 4, 6) or stream not in ("/main", "/sub"):
                raise ValueError('Invalid display mode or stream.')
            if self.scan_started and (start != int(self.start_entry.get()) or end != int(self.end_entry.get()) or stream != engine.RTSP_PATH):
                raise ValueError('Restart before changing the scan range or stream.')
            if any(p.camera or p.processing_camera for p in self.panels[mode:]):
                raise ValueError('Finish cameras 2-4 before switching to single-panel mode.')
            if network != self.settings.get("network_id") and (self.flow_started or any(p.processing_camera for p in self.panels)):
                raise ValueError('Cannot change adapter during inspection. Restart before changing it.')
            usb = body.get("usb")
            if usb is not None and int(usb) not in self.usb_devices:
                raise ValueError('Rescan USB cameras.')
            interface = next(item for item in self.settings["network_interfaces"] if item["id"] == network)
            extended = {key: body.get(key, self.settings.get(key)) for key in (
                "work_prefix", "work_start", "work_end", "target_prefix", "target_start", "target_end", "target_next",
                "target_mask", "target_gateway", "allowed_models", "access_username", "access_password",
                "after_config_username", "after_config_password", "config_import_password", "post_url", "post_enabled", "ui_language") if body.get(key, self.settings.get(key)) is not None}
            if extended.get('ui_language', 'en') not in ('en', 'ja'):
                raise ValueError('Unsupported UI language')
            if extended.get("post_url"):
                extended["post_url"] = gas_post.validate_url(str(extended["post_url"]).strip())
            if extended.get("post_enabled") and not extended.get("post_url"):
                raise ValueError('Enter a POST URL')
            if "config_import_password" in extended and (not isinstance(extended["config_import_password"], str) or
                    not extended["config_import_password"] or "\x00" in extended["config_import_password"]):
                raise ValueError('Enter the config import password')
            for key in ("access_username", "access_password", "after_config_username", "after_config_password"):
                if key in extended and (not isinstance(extended[key], str) or not extended[key] or
                                        any(c in extended[key] for c in (";", "\r", "\n", "\x00"))):
                    raise ValueError('Enter access credentials (no semicolons or line breaks)')
            if any(p.processing_camera for p in self.panels) and any(
                    key in extended and self.settings.get(key) != extended[key] for key in
                    ("access_username", "access_password", "after_config_username", "after_config_password", "config_import_password")):
                raise ValueError('Cannot change credentials during inspection')
            if "allowed_models" in extended:
                known_models = {model for item in self.journal.data["devices"].values() for model in flow.device_models(item)}
                known_models.update(self.settings.get("allowed_models", []))
                if not isinstance(extended["allowed_models"], list) or any(model not in known_models for model in extended["allowed_models"]):
                    raise ValueError('Select allowed models from discovered devices')
            proposed = {**self.settings, **extended}
            if any(key.startswith("target_") or key.startswith("work_") for key in extended):
                flow.network_config(proposed, interface)
            if self.flow_started and any(self.settings.get(key) != value for key, value in extended.items() if key not in ("post_url", "post_enabled")):
                raise ValueError('Cannot change network allocation conditions after starting inspection')
            changes = {**extended, "network_id": network, "scan_start": start, "scan_end": end,
                       "display_mode": mode, "rtsp_path": stream}
            if usb is not None:
                changes.update(usb_index=int(usb), usb_name=self.usb_devices[int(usb)])
            if not self.persist_settings(changes):
                raise ValueError('Settings save failed')
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
            raise ValueError('Unknown action.')
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
                "hide_ok": bool(panel.camera and panel.exterior_done and panel.reader and self.ir_checks.get(flow.mac(panel.camera.mac)) and self.ir_checks[flow.mac(panel.camera.mac)].stage != 'PURPLE'),
                "operation_color": {'BW': '#d97706', 'COLOR': '#2563eb'}.get(self.ir_checks[flow.mac(camera.mac)].stage, '') if camera and flow.mac(camera.mac) in self.ir_checks else '',
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
                "network_busy": self.network_scanning, "usb_busy": self.usb_scanning,
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
