from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
"""Offline browser bridge checks: no camera connection or reset."""
import importlib.util
from pathlib import Path
import queue
import http.client
import threading
import tempfile
import types
import unittest
import asyncio
import time
from unittest.mock import patch

import numpy as np
from PIL import Image

spec = importlib.util.spec_from_file_location("browser_ui_tests", (Path(__file__).resolve().parents[1] / "src" / "inspection_runtime.py"))
web = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web)


class Reader:
    def __init__(self, camera=None):
        self.camera = camera
        self.frames = queue.Queue(maxsize=1)
        self.frame_rate = types.SimpleNamespace(value=lambda: 25.0)
        self.error = ""
    def start(self):
        pass
    def stop(self):
        pass
    def get_frame(self):
        return np.zeros((480, 640, 3), dtype=np.uint8)
    def snapshot(self):
        return Image.new("RGB", (640, 480), "blue")


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.storage = tempfile.TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        original_journal, original_store = web.flow.Journal, web.JobStore
        for target, name, original in ((web.flow, "Journal", original_journal), (web, "JobStore", original_store)):
            storage_patch = patch.object(target, name, side_effect=lambda base, cls=original: cls(self.storage.name))
            storage_patch.start()
            self.addCleanup(storage_patch.stop)
        self.root = web.OwnerLoop()
        with patch.object(web.engine.step3, 'load_settings', return_value={}):
            self.app = web.BrowserInspection(self.root, demo=True)
        self.app.settings_dir = self.storage.name
        self.app.demo = False
        scan_patch = patch.object(self.app, "device_scan")
        scan_patch.start()
        self.addCleanup(scan_patch.stop)
        self.camera = web.engine.CameraInfo("192.168.0.233", "SNTEST", "02:00:00:00:00:03")
        self.reader = patch.object(web.engine, "RTSPReader", Reader)
        self.reader.start()
        self.addCleanup(self.reader.stop)
        self.app.usb_reader = Reader()
        self.app.panels[0].reserve(self.camera)
        self.app.panels[0].stream_ready = True
        self.app.assign_usb_panel()
        self.app.poll()

    def tearDown(self):
        self.app.close()
        web.engine.APP_STOP.clear()

    def test_real_frame_and_fps_export_and_exterior_ok_advances(self):
        state = self.app.snapshot()
        self.assertTrue(state["panels"][0]["image"])
        self.assertEqual(state["panels"][0]["fps"], "USB  25.0 FPS")
        self.assertIn("[Enter]", state["panels"][0]["ok"])
        self.assertTrue(self.app.jpeg("0").startswith(b"\xff\xd8"))
        self.app.command({"action": "ok", "index": 0})
        self.assertTrue(self.app.panels[0].exterior_done)
        self.assertIsInstance(self.app.panels[0].reader, Reader)

    def test_exterior_ng_boxes_are_saved_before_continuing(self):
        self.app.command({"action": "ng", "index": 0})
        self.assertTrue(self.app.snapshot()["preview"]["exterior"])
        self.assertFalse(self.app.snapshot()["panels"][0]["can_ok"])
        with self.assertRaises(ValueError):
            self.app.command({"action": "save_ng", "boxes": []})
        with patch.object(self.app, "save_evidence", return_value="evidence/B-test.jpg") as save, patch.object(web.engine, "save_result") as record:
            self.app.command({"action": "save_ng", "category": "B", "boxes": [[10, 20, 100, 150]]})
            self.assertEqual(save.call_args.args[2], "B")
            self.assertEqual(save.call_args.args[1].getpixel((10, 20)), (255, 0, 0))
            self.assertEqual(record.call_args.args[1], "外観NG")
        self.assertTrue(self.app.panels[0].exterior_done)
        self.assertIsNone(self.app.preview)

    def test_rtsp_ng_finishes_immediately_and_records_reason(self):
        self.app.command({'action':'ok','index':0})
        self.app.panels[0].reader.last_status = 'RTSP NG / reconnecting'
        with patch.object(self.app, 'save_evidence', return_value='E1-test.jpg'), patch.object(web.engine, 'save_result') as record:
            self.app.poll_ir_cut()
        self.assertIsNone(self.app.panels[0].camera)
        self.assertEqual(self.app.ng_count, 1)
        self.assertIsNone(self.app.preview)
        self.assertEqual(record.call_args.kwargs['ng_reason'], 'RTSP不具合')

    def test_damage_is_distinct_reason_but_keeps_b_evidence_prefix(self):
        self.app.command({'action': 'ng', 'index': 0})
        with patch.object(self.app, 'save_evidence', return_value='B-damage.jpg') as save, patch.object(web.engine, 'save_result') as record:
            self.app.command({'action': 'save_ng', 'category': 'D', 'boxes': [[10, 20, 100, 150]]})
        self.assertEqual(save.call_args.args[2], 'B')
        self.assertEqual(record.call_args.kwargs['ng_reason'], '破損')
        self.assertTrue(self.app.panels[0].exterior_done)

    def test_manual_ng_dialog_blocks_automatic_ir_pass(self):
        self.app.command({'action': 'ok', 'index': 0})
        self.advance_rtsp()
        check = self.app.ir_checks[web.flow.mac(self.camera.mac)]
        check.stage, check.count = 'COLOR', 2
        self.app.command({'action': 'ng', 'index': 0})
        with patch.object(web.engine, 'request_step3') as request:
            self.app.poll_ir_cut()
        request.assert_not_called()
        self.assertEqual(check.stage, 'COLOR')

    def test_rtsp_ok_queues_step3_and_keeps_running(self):
        self.app.command({"action": "ok", "index": 0})
        with patch.object(self.app, 'start_config') as start:
            self.advance_rtsp()
            check = self.app.ir_checks[web.flow.mac(self.camera.mac)]
            self.assertEqual(check.stage, 'BW')
            self.assertTrue(self.app.snapshot()['panels'][0]['hide_ok'])
            self.assertEqual(self.app.panels[0].ok.cget('state'), 'disabled')
            check.stage = 'PASS'
            self.app.poll_ir_cut()
            start.assert_not_called()
            self.app.command({'action':'ok','index':0})
            start.assert_called_once_with(self.app.panels[0])

    def advance_rtsp(self):
        for now in (100, 100.5, 101):
            self.app.panels[0].reader.latest_frame_time = now
            with patch.object(web.time, 'monotonic', return_value=now):
                self.app.poll_ir_cut()

    def test_full_automatic_flow_dispatches_config_once_without_async_queue(self):
        identity = web.flow.mac(self.camera.mac)
        self.app.store.create('TEST', 1, '192.168.0.233')
        self.app.store.device(identity, 1, self.camera.sn, self.camera.ip, '192.168.0.220')
        self.app.settings.update(network_id='wired', network_interfaces=[{'id':'wired','name':'Ethernet','ip':'192.168.0.10','mac':'02:00:00:00:00:10'}])
        self.app.journal.reserve(identity, ['192.168.0.220'], lambda ip: False)
        target = '192.168.0.220'
        class Link:
            changed = False
            def arp(link, ip, mac=None): return [identity] if mac else []
            def set_ip(link, *args): link.changed = True
            def wait_ip(link, *args): return True
        self.app.link = Link()
        original_allocate = self.app.allocate_label
        def run(camera, *args, **kwargs):
            kwargs['credential_report'](('test-user','test-password'))
            return True, 'config verified'
        with patch.object(web.engine.step3, 'load_config', return_value=('config', b'config')), \
             patch.object(web.engine.step3, 'run', side_effect=run) as write, \
             patch.object(self.app, 'allocate_label', side_effect=lambda camera: original_allocate(camera, target)), \
             patch.object(web.engine, 'save_result'):
            self.app.command({'action':'ok','index':0})  # appearance
            self.advance_rtsp()
            for now, color in ((101.5,'gray'),(102,'gray'),(102.5,'gray'),(103,'blue'),(103.5,'blue'),(104,'blue')):
                reader = self.app.panels[0].reader
                reader.latest_frame_time = now
                with patch.object(reader,'snapshot',return_value=Image.new('RGB',(160,90),color)), patch.object(web.time,'monotonic',return_value=now):
                    self.app.poll_ir_cut()
            self.assertEqual(write.call_count, 0)
            self.app.command({'action':'ok','index':0})
            deadline = time.monotonic()+3
            while self.app.config_running and time.monotonic()<deadline: time.sleep(.01)
            self.app.poll()
            self.assertEqual(write.call_count, 1)
            self.assertEqual(self.app.jobs[identity]['state'], 'sticker')
            self.app.command({'action':'ok','index':0})  # user sticker confirmation
            deadline = time.monotonic()+3
            while self.app.jobs[identity]['state']=='changing' and time.monotonic()<deadline:
                self.app.poll(); time.sleep(.01)
            self.assertTrue(self.app.link.changed)
            self.assertEqual(self.app.jobs[identity]['state'], 'confirmed')
            self.assertEqual(self.app.store.job['devices'][identity.replace(':','').upper()]['results']['ir_cut'], 'OK')
            self.assertEqual(self.app.panels[0].result_state, 'OK')

    def test_settings_require_network_and_persist_inspection_options(self):
        records = [{"id": "wired", "name": "Ethernet", "ip": "192.168.0.10", "mac": "00:11:22:33:44:55"}]
        self.app.settings["network_interfaces"] = records
        with tempfile.TemporaryDirectory() as directory:
            self.app.settings_dir = directory
            web.engine.step3.save_settings(directory, {"network_interfaces": records})
            with self.assertRaises(ValueError):
                self.app.command({"action": "settings", "network": ""})
            self.app.command({"action": "settings", "network": "wired", "start": 220,
                              "end": 240, "mode": 1, "stream": "/sub"})
            saved = web.engine.step3.load_settings(directory)
            self.assertEqual(saved["network_id"], "wired")
            self.assertEqual(saved["scan_start"], 220)
            self.assertEqual(saved["display_mode"], 1)
            self.assertEqual(self.app.snapshot()["scan_end"], 240)
            self.assertIn("Ethernet", self.app.snapshot()["network"])
            with self.assertRaises(ValueError):
                self.app.command({"action": "settings", "network": "wired", "start": 250, "end": 220})
            self.assertEqual(web.engine.step3.load_settings(directory)["scan_start"], 220)

    def test_start_requires_interface_before_scanner_launch(self):
        self.app.settings.pop("network_id", None)
        with patch.object(self.app, "start_scan") as start:
            with self.assertRaises(ValueError):
                self.app.command({"action": "start"})
            start.assert_not_called()

    def test_startup_notice_only_acknowledges_or_clears_display_counts(self):
        self.app.recovery_pending = True
        self.app.ok_count, self.app.ng_count = 3, 2
        with patch.object(self.app, "start_workflow") as start, patch.object(self.app.store, "archive") as archive:
            self.app.command({"action": "startup_continue"})
            self.assertEqual((self.app.ok_count, self.app.ng_count), (3, 2))
            self.app.command({"action": "clear_counters"})
            self.assertEqual((self.app.ok_count, self.app.ng_count), (0, 0))
            start.assert_not_called()
            archive.assert_not_called()
        self.assertFalse(self.app.recovery_pending)

    def test_existing_work_ip_retries_http_without_resending_set(self):
        device = {"mac": self.camera.mac, "model": "NC8164-FPE"}
        self.app.settings["allowed_models"] = ["NC8164-FPE"]
        link = types.SimpleNamespace(wait_ip=lambda *a: True, set_ip=lambda *a: self.fail("SET resent"))
        transport = unittest.mock.Mock(side_effect=[OSError("rebooting"), (200, '{"device":1}')])
        with patch.object(web.flow, "Link", return_value=link), \
             patch.object(web.engine.step3, "create_transport", return_value=transport), \
             patch.object(web.engine, "extract_camera_info", return_value=self.camera), \
             patch.object(web.engine, "find_value", return_value="NC8164-FPE"), \
             patch.object(self.app.journal, "remember"), patch.object(web.time, "sleep"):
            self.app.onboard_device(device, self.camera.ip, {}, ("admin", "test"), False)
        self.assertEqual(transport.call_count, 2)
        self.assertIn(web.flow.mac(self.camera.mac), self.app.approved)

    def test_six_panel_mode_and_shrink_guard(self):
        self.app.command({"action": "mode", "count": 6})
        self.assertEqual(len(self.app.snapshot()["panels"]), 6)
        self.app.panels[5].processing_camera = self.camera
        with self.assertRaises(ValueError):
            self.app.command({"action": "mode", "count": 2})

    def test_completed_resume_keeps_slot_and_never_repeats_exterior(self):
        for panel in self.app.panels:
            panel.finish()
        self.app.mode_var.set(2)
        self.app.change_mode()
        identity = web.flow.mac(self.camera.mac)
        self.app.store.create("TEST", 2, "192.168.0.150")
        self.app.store.device(identity, 1, self.camera.sn, "192.168.0.233", "192.168.0.220")
        self.app.store.update(identity, status="OK", results={"appearance": "OK", "settings": "OK"})
        self.app.working[identity] = "192.168.0.150"
        self.app.workflow_events.put(("restore_label", (self.camera, "192.168.0.220", "confirmed")))
        with patch.object(self.app, "watch_removal"), patch.object(self.app, "complete_exterior") as exterior, \
             patch.object(self.app.journal, "transition"), patch.object(web.engine, "save_result"):
            self.app.poll()
        exterior.assert_not_called()
        self.assertEqual(self.app.panels[0].result_state, "OK")
        self.assertIs(self.app.panels[0].processing_camera, self.camera)

    def test_one_mac_task_error_does_not_stop_other_mac(self):
        first = web.flow.mac(self.camera.mac)
        second = "02:00:00:00:00:01"
        other = web.engine.CameraInfo("192.168.0.151", "OTHER", second)
        self.app.approved = {first, second}
        self.app.working = {first: self.camera.ip, second: other.ip}
        self.app.journal.data["devices"].update({first: {"sn": self.camera.sn}, second: {"sn": other.sn}})
        web.engine.APP_STOP.clear()
        self.addCleanup(web.engine.APP_STOP.clear)
        async def run():
            progressed = asyncio.Event()
            class Session:
                async def __aenter__(self): return self
                async def __aexit__(self, *args): pass
            async def reserve(camera): return True
            async def reset(session, camera, option):
                if web.flow.mac(camera.mac) == first:
                    raise ValueError("first device failed")
                return True
            async def reboot(session, camera): return True
            async def info(session, ip):
                progressed.set()
                return other
            async def config(session): await asyncio.Event().wait()
            with patch.object(web.engine.aiohttp, "ClientSession", return_value=Session()), \
                 patch.object(self.app, "reserve_mac_panel", side_effect=reserve), \
                 patch.object(web.engine, "reset_device", side_effect=reset), \
                 patch.object(web.engine, "wait_reboot", side_effect=reboot), \
                 patch.object(web.engine, "get_camera_info", side_effect=info), \
                 patch.object(web.engine, "step3_worker", side_effect=config), \
                 patch.object(self.app.store, "update"):
                task = asyncio.create_task(self.app.mac_scanner(150, 151))
                await asyncio.wait_for(progressed.wait(), 2)
                await asyncio.sleep(.01)
                self.assertIn(second, self.app.approved)
                self.assertNotIn(first, self.app.approved)
                web.engine.APP_STOP.set()
                await asyncio.wait_for(task, 2)
        asyncio.run(run())

    def test_access_credentials_persist_and_apply_to_both_stages(self):
        for panel in self.app.panels:
            panel.finish()
        records = [{"id": "wired", "name": "Ethernet", "ip": "192.168.0.10", "mac": "00:11:22:33:44:55"}]
        self.app.settings["network_interfaces"] = records
        with tempfile.TemporaryDirectory() as directory:
            self.app.settings_dir = directory
            web.engine.step3.save_settings(directory, {"network_interfaces": records})
            values = {"access_username": "before_user", "access_password": "before_secret",
                      "after_config_username": "after_user", "after_config_password": "after_secret"}
            self.app.command({"action": "settings", "network": "wired", **values})
            saved = web.engine.step3.load_settings(directory)
            self.assertEqual({key: saved[key] for key in values}, values)
            self.assertEqual((web.engine.USERNAME, web.engine.PASSWORD), ("before_user", "before_secret"))
            self.assertEqual(web.engine.step3.VERIFY_CREDENTIALS, ("after_user", "after_secret"))
            with self.assertRaises(ValueError):
                self.app.command({"action": "settings", "network": "wired", "access_password": "bad;value"})

    def test_sticker_confirmation_is_required_before_final_ip_change(self):
        self.app.panels[0].finish()
        self.app.panels[0].show_result("RUNING", self.camera)
        self.app.allocate_label(self.camera, "192.168.0.220")
        self.assertEqual(self.app.jobs[web.flow.mac(self.camera.mac)]["state"], "sticker")
        self.assertIn("ラベル", self.app.snapshot()["panels"][0]["message"])
        with patch.object(self.app, "finalize") as change:
            change.assert_not_called()
            self.app.command({"action": "ok", "index": 0})
            change.assert_called_once()

    def test_final_ok_holds_panel_until_mac_disappears_then_stops_blink(self):
        panel = self.app.panels[0]
        panel.finish()
        panel.show_result("RUNING", self.camera)
        self.app.allocate_label(self.camera, "192.168.0.220")
        with patch.object(web.engine, "save_result"):
            self.app.workflow_events.put(("final_ok", self.camera))
            self.app.poll()
        self.assertEqual(panel.result_state, "OK")
        self.assertIs(panel.processing_camera, self.camera)
        self.assertIn("電源", panel.video.cget("text"))
        self.app.workflow_events.put(("disconnected", self.camera))
        self.app.poll()
        self.assertIsNone(panel.processing_camera)
        self.assertIsNone(panel.blink_timer)
        self.assertNotIn("OK", panel.video.cget("text"))

    def test_usb_auto_tries_next_device_and_uses_actual_picture(self):
        attempts = []
        class Candidate(Reader):
            def __init__(self, index, name, retry):
                super().__init__()
                self.index = index
                self.done_event = threading.Event()
                if index == 1: self.done_event.set()
                attempts.append((index, retry))
            def get_frame(self):
                return super().get_frame() if self.index == 2 else None
        self.app.usb_reader = None
        self.app.usb_auto_events.put((True, [(1, "USB failed"), (2, "USB working")]))
        with patch.object(web.engine.exterior, "InspectionUSBReader", Candidate), patch.object(self.app, "persist_settings") as save:
            self.app.poll_usb_auto()
            self.app.poll_usb_auto()
            self.assertEqual(attempts, [(1, False), (2, False)])
            self.assertEqual(self.app.usb_selected, 2)
            self.assertTrue(self.app.usb_connection_seen)
            self.assertFalse(self.app.usb_search_stopped)
            save.assert_called_once_with({"usb_index": 2, "usb_name": "USB working"})

    def test_usb_all_candidates_fail_then_stop_without_repeated_enumeration(self):
        class Candidate(Reader):
            def __init__(self, index, name, retry):
                super().__init__()
                self.done_event = threading.Event()
                self.done_event.set()
            def get_frame(self): return None
        self.app.usb_reader = None
        self.app.usb_auto_events.put((True, [(1, "USB failed1"), (2, "USB failed2")]))
        with patch.object(web.engine.exterior, "InspectionUSBReader", Candidate):
            self.app.poll_usb_auto()
            self.app.poll_usb_auto()
        self.assertTrue(self.app.usb_search_stopped)
        self.assertIsNone(self.app.usb_reader)
        with patch.object(web.engine.exterior, "list_usb_camera_devices") as enumerate_devices:
            for _ in range(3): web.BrowserInspection.scan_usb(self.app)
            enumerate_devices.assert_not_called()
        self.app.command({"action": "usb_scan"})
        self.assertFalse(self.app.usb_search_stopped)

    def test_no_usb_devices_stops_and_single_pass_reader_does_not_retry(self):
        self.app.usb_reader = None
        self.app.usb_auto_events.put((True, []))
        self.app.poll_usb_auto()
        self.assertTrue(self.app.usb_search_stopped)
        capture = types.SimpleNamespace(isOpened=lambda: False, release=lambda: None)
        with patch.object(web.engine.exterior.cv2, "VideoCapture", return_value=capture, create=True) as open_device, \
             patch.object(web.engine.exterior.cv2, "CAP_DSHOW", 1, create=True):
            reader = web.engine.exterior.InspectionUSBReader(0, retry=False)
            reader.run()
            self.assertTrue(reader.done_event.is_set())
            open_device.assert_called_once()

    def test_usb_same_names_keep_the_selected_device_index(self):
        capture = types.SimpleNamespace(isOpened=lambda: False, release=lambda: None)
        with patch.object(web.engine.exterior, "list_usb_camera_devices", return_value=[(1, "USB Camera"), (2, "USB Camera")]), \
             patch.object(web.engine.exterior.cv2, "VideoCapture", return_value=capture, create=True) as open_device, \
             patch.object(web.engine.exterior.cv2, "CAP_DSHOW", 1, create=True):
            reader = web.engine.exterior.InspectionUSBReader(2, "USB Camera", retry=False)
            reader.run()
            self.assertEqual(open_device.call_args.args[0], 2)

    def test_demo_blocks_mutations(self):
        self.app.demo = True
        with self.assertRaises(ValueError):
            self.app.command({"action": "settings", "usb": 0})

    def test_ir_manual_ng_does_not_open_dialog_or_write_config(self):
        self.app.command({'action':'ok','index':0})
        self.advance_rtsp()
        with patch.object(self.app, 'start_config') as start, patch.object(self.app, 'save_evidence', return_value='E2-test.jpg'), patch.object(web.engine,'save_result') as record:
            self.app.command({'action':'ng','index':0})
        start.assert_not_called()
        self.assertIsNone(self.app.preview)
        self.assertIsNone(self.app.panels[0].camera)
        self.assertEqual(record.call_args.kwargs['ng_reason'], 'IR-CUT / 光センサー不具合')



if __name__ == "__main__":
    unittest.main()
