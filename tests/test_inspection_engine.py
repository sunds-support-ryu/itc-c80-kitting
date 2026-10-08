from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
"""Headless regression tests; no physical camera resets or video connections."""
import asyncio
import csv
import importlib.util
from pathlib import Path
import queue
import sys
import types
import tempfile
from PIL import Image
import unittest
from unittest.mock import patch
import numpy as np

class Widget:
    def __init__(self, *args, **kwargs):
        self.text = ""
        self.options = kwargs
    def __getattr__(self, name):
        return lambda *args, **kwargs: None
    def config(self, **kwargs):
        self.options.update(kwargs)
    def cget(self, key):
        return self.options.get(key)
    configure = config
    def insert(self, index, text):
        self.text += text
    def get(self, *args):
        return self.text
    def delete(self, *args):
        self.text = ""
    def index(self, *args):
        return "1.0"
    def winfo_width(self):
        return 960
    def winfo_height(self):
        return 540
    def place_configure(self, **kwargs):
        self.options.update(kwargs)

class Variable:
    def __init__(self, value=False):
        self.value = value
    def get(self):
        return self.value
    def set(self, value):
        self.value = value

class Reader:
    def __init__(self, camera):
        self.camera = camera
        self.frames = queue.Queue(maxsize=1)
        self.stopped = False
    def start(self):
        pass
    def snapshot(self):
        return Image.new("RGB", (1920, 1080), "red")
    def stop(self):
        self.stopped = True

class USBReader:
    error = ""
    def __init__(self):
        self.available = True
        self.stopped = False
    def snapshot(self):
        return Image.new("RGB", (640, 480), "red") if self.available else None
    def get_frame(self):
        return np.zeros((480, 640, 3), dtype=np.uint8) if self.available else None
    def stop(self):
        self.stopped = True

class InspectionTests(unittest.TestCase):
    def test_legacy_japanese_csv_rows_are_not_translated(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'legacy.csv'
            previous=['2026-01-01','OLD-SN','02:00:00:00:00:01','192.168.0.150','外観NG','-']
            with path.open('w',encoding='utf-8-sig',newline='') as file:
                writer=csv.writer(file);writer.writerow(['日時','SN','MAC','IP','結果','Reset']);writer.writerow(previous)
            with patch.object(self.m,'CSV_FILE',str(path)):
                self.m.save_result(self.m.CameraInfo('192.168.0.151','NEW-SN','02:00:00:00:00:02'),'OK','-')
            with path.open(encoding='utf-8-sig',newline='') as file:rows=list(csv.reader(file))
            self.assertEqual(rows[1],previous+['',''])
            self.assertEqual(rows[0][0],'Timestamp')

    def setUp(self):
        spec = importlib.util.spec_from_file_location("inspection_test_module", (Path(__file__).resolve().parents[1] / "src" / "inspection_engine.py"))
        self.m = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {name: types.ModuleType(name) for name in ("aiohttp", "cv2")}):
            spec.loader.exec_module(self.m)
        self.m.PASSWORD = "example-login-password"
        self.m.tk = types.SimpleNamespace(**{name: Widget for name in
            ("Frame", "Label", "Checkbutton", "Radiobutton", "Entry", "Button", "Text", "Scrollbar", "Toplevel")},
            BooleanVar=Variable, IntVar=Variable)
        self.m.RTSPReader = Reader
        image_patch = patch.object(self.m.ImageTk, "PhotoImage", side_effect=lambda *args, **kwargs: object())
        image_patch.start()
        self.addCleanup(image_patch.stop)
        scan_patch = patch.object(self.m.InspectionUI, "scan_usb")
        scan_patch.start()
        self.addCleanup(scan_patch.stop)
        settings_patch = patch.object(self.m.step3, "load_settings", return_value={})
        settings_patch.start()
        self.addCleanup(settings_patch.stop)
        self.saved_settings = {}
        def save_settings(directory, changes):
            self.saved_settings.update(changes)
            return dict(self.saved_settings)
        save_patch = patch.object(self.m.step3, "save_settings", side_effect=save_settings)
        save_patch.start()
        self.addCleanup(save_patch.stop)
        self.app = self.m.InspectionUI(Widget())
        self.app.preview_ng = lambda camera, image: "discard"

    def test_default_range_mode_and_shortcut_label(self):
        self.assertEqual(self.app.mode_count, 4)
        self.assertEqual(self.app.mode_var.get(), 4)
        self.assertEqual(self.app.start_entry.get(), "215")
        self.assertEqual(self.app.end_entry.get(), "254")
        self.assertEqual(self.app.panels[0].ok.options["text"], "OK")
        self.app.select_panel(2)
        self.assertEqual(self.app.panels[2].ok.options["text"], "OK")
        self.assertEqual(self.app.panels[0].ok.options["text"], "OK")

    def fill_four(self):
        self.app.mode_var.set(4)
        self.app.change_mode()
        cameras = [self.m.CameraInfo(f"192.168.0.{230+i}", f"SN{i}", f"MAC{i}") for i in range(4)]
        for camera in cameras:
            self.m.inspection_queue.put(camera)
        self.app.poll()
        self.app.usb_reader = USBReader()
        for index in range(4):
            self.app.mark_ok(index)  # Step1外観OKがStep2を開く。
        return cameras

    def test_four_slots_independent_decisions_and_replacement(self):
        cameras = self.fill_four()
        self.assertEqual([p.camera for p in self.app.panels], cameras)
        self.assertEqual(len({id(p.reader.frames) for p in self.app.panels}), 4)
        with patch.object(self.m, "request_step3") as reset, patch.object(self.m, "save_result") as save:
            self.app.mark_ng(1)
            reset.assert_not_called()
            save.assert_called_once_with(cameras[1], "NG", "-")
            self.app.mark_ok(2)
            self.app.mark_ok(2)  # repeated click cannot reset twice
            reset.assert_called_once_with(cameras[2])
            self.assertIs(self.app.panels[0].camera, cameras[0])
            self.assertIs(self.app.panels[3].camera, cameras[3])
            replacement = self.m.CameraInfo("192.168.0.240", "NEW", "MACNEW")
            self.m.inspection_queue.put(replacement)
            self.app.poll()
            self.assertIs(self.app.panels[1].camera, replacement)
        self.assertEqual((self.app.ok_count, self.app.ng_count), (0, 1))

    def test_enter_targets_selected_panel_and_mode_guard(self):
        cameras = self.fill_four()
        self.app.select_panel(3)
        with patch.object(self.m, "request_step3") as reset:
            self.app.enter_ok(types.SimpleNamespace(widget=self.app.start_entry))
            reset.assert_not_called()
            self.app.enter_ok()
            self.app.escape_ng()
            reset.assert_not_called()
            self.assertIs(self.app.panels[3].camera, cameras[3])
        self.app.mode_var.set(1)
        self.app.change_mode()
        self.assertEqual(self.app.mode_count, 4)
        self.app.panels[1].finish()
        self.app.panels[2].finish()
        self.app.panels[3].finish()
        self.app.mode_var.set(1)
        self.app.change_mode()
        self.assertEqual(self.app.mode_count, 1)
        self.assertEqual(self.app.selected_panel, 0)

    def test_exterior_enter_and_escape_respect_stage_input_and_preview(self):
        cameras = [self.m.CameraInfo(f"192.168.0.{230+i}", f"SN{i}", f"MAC{i}") for i in range(2)]
        for camera in cameras:
            self.m.inspection_queue.put(camera)
        self.app.usb_reader = USBReader()
        self.app.poll()
        self.assertIn("[Enter]", self.app.panels[0].ok.options["text"])
        self.assertIn("[Esc]", self.app.panels[0].ng.options["text"])
        self.app.enter_ok(types.SimpleNamespace(widget=self.app.usb_entry))
        self.assertFalse(self.app.panels[0].exterior_done)
        with patch.object(self.m, "request_step3") as reset:
            self.app.enter_ok()
            self.assertTrue(self.app.panels[0].exterior_done)
            reset.assert_not_called()
        self.assertNotIn("[Enter]", self.app.panels[0].ok.options["text"])
        self.app.poll()  # USB ownership and enabled controls refresh for the next camera.
        with patch.object(self.app, "mark_ng") as ng:
            self.app.ng_preview_open = True
            self.app.escape_ng()
            ng.assert_not_called()
            self.app.ng_preview_open = False
            self.app.escape_ng(types.SimpleNamespace(widget=self.app.start_entry))
            ng.assert_not_called()
            self.app.escape_ng()
            ng.assert_called_once_with(1)
            ng.reset_mock()
            self.app.select_panel(0)
            self.app.escape_ng()
            ng.assert_not_called()

    def test_usb_shortcuts_follow_picture_buttons_modal_and_focus(self):
        self.m.inspection_queue.put(self.m.CameraInfo("192.168.0.230", "SN", "MAC"))
        self.app.usb_reader = USBReader()
        self.app.usb_reader.available = False
        self.app.poll()
        panel = self.app.panels[0]
        self.assertIn("Step1", panel.video.options["text"])
        self.assertNotIn("[Enter]", panel.ok.options["text"])
        with patch.object(self.app, "mark_ok") as ok:
            self.app.enter_ok()
            ok.assert_not_called()
        self.app.usb_reader.available = True
        self.app.poll()
        self.assertIn("[Enter]", panel.ok.options["text"])
        self.app.ng_preview_open = True
        self.app.refresh_shortcut_labels()
        self.assertNotIn("[Esc]", panel.ng.options["text"])
        self.app.ng_preview_open = False
        with patch.object(self.app.root, "focus_get", return_value=self.app.usb_entry):
            self.app.refresh_shortcut_labels()
            self.assertNotIn("[Enter]", panel.ok.options["text"])
        self.app.refresh_shortcut_labels()
        self.assertIn("[Enter]", panel.ok.options["text"])

    def test_black_panel_shows_waiting_reconnect_and_step3_progress(self):
        self.assertEqual(self.app.panels[0].video.options["text"], 'Waiting for camera')
        cameras = self.fill_four()
        panel = self.app.panels[0]
        self.m.event_queue.put(("stream_status", cameras[0].sn, 'RTSP NG / reconnecting...'))
        self.app.poll()
        self.assertIn("reconnecting", panel.video.options["text"])
        with patch.object(self.m, "request_step3"):
            self.app.mark_ok(0)
        self.m.event_queue.put(("step3_progress", cameras[0].sn, "IP Reset option=2"))
        self.app.poll()
        self.assertEqual(panel.video.options["text"], "RUNING")
        self.assertIn("IP Reset", panel.status_detail.options["text"])
        self.assertNotIn("[Enter]", panel.ok.options["text"])

    def test_fps_counts_new_frames_and_expires_after_stall(self):
        meter = self.m.exterior.FrameRate()
        with patch.object(self.m.exterior.time, "monotonic", return_value=10.0) as clock:
            meter.record()
            clock.return_value = 10.04
            meter.record()
            clock.return_value = 10.08
            meter.record()
            self.assertAlmostEqual(meter.value(), 25.0)
            # Reading the same frame repeatedly does not increase the counter.
            self.assertAlmostEqual(meter.value(), 25.0)
            clock.return_value = 11.2
            self.assertEqual(meter.value(), 0.0)
            meter.reset()
            self.assertEqual(meter.value(), 0.0)

    def test_fps_label_uses_reader_meter_for_usb_and_rtsp(self):
        self.app.usb_reader = USBReader()
        meter = types.SimpleNamespace(value=lambda: 24.5)
        self.app.usb_reader.frame_rate = meter
        self.m.inspection_queue.put(self.m.CameraInfo("192.168.0.230", "SN", "MAC"))
        self.app.poll()
        panel = self.app.panels[0]
        self.assertEqual(panel.fps_label.options["text"], "USB  24.5 FPS")
        self.app.mark_ok(0)
        panel.reader.frame_rate = meter
        self.app.poll()
        self.assertEqual(panel.fps_label.options["text"], "RTSP  24.5 FPS")

    def test_network_discovery_is_recorded_and_displayed(self):
        records = [{"id": "wired", "name": "Ethernet", "ip": "192.168.0.10", "mac": "00:11:22:33:44:55"}]
        self.app.network_events.put((True, records))
        self.app.poll()
        self.assertEqual(self.saved_settings["network_interfaces"], records)
        self.assertIn("Ethernet", self.app.network_label.options["text"])
        self.assertIn("00:11:22:33:44:55", self.app.network_label.options["text"])

    def test_usb_selected_by_saved_name_after_index_changes(self):
        self.app.settings["usb_name"] = "USB saved camera"
        self.app.usb_events.put(("devices", [(1, "USB other camera"), (3, "USB saved camera")]))
        with patch.object(self.m.exterior, "InspectionUSBReader") as reader:
            reader.return_value.get_frame.return_value = None
            self.app.poll()
            reader.assert_called_once_with(3, "USB saved camera")
        self.assertEqual(self.saved_settings["usb_name"], "USB saved camera")
        self.assertEqual(self.saved_settings["usb_index"], 3)

    def test_reset_payload_and_monitor_initial_option(self):
        async def check():
            self.m.reset_semaphore = asyncio.Semaphore(1)
            self.m.device_lock = asyncio.Lock()
            camera = self.m.CameraInfo("192.168.0.233", "SN", "MAC")
            with patch.object(self.m, "camera_http_request", return_value=(200, "{}")) as request:
                self.assertTrue(await self.m.reset_device(None, camera, 3))
                self.assertEqual(request.call_args.args[2], {"options": 3})
                self.assertFalse(await self.m.reset_device(None, camera, 0))
                self.assertEqual(request.call_count, 1)
            calls = []
            order = []
            async def info(*args): return camera
            async def claim(*args): return True
            async def reserve(*args):
                order.append("exterior")
                return True
            async def reset(session, camera, option):
                order.append("reset")
                calls.append(option)
                self.m.APP_STOP.set()
                return False
            async def no_sleep(*args): pass
            with patch.object(self.m, "get_camera_info", info), patch.object(self.m, "claim_camera", claim), patch.object(self.m, "reset_device", reset), patch.object(self.m, "reserve_reboot_panel", reserve), patch.object(asyncio, "sleep", no_sleep):
                await self.m.monitor_ip(None, camera.ip)
            self.assertEqual(calls, [3])
            self.assertEqual(order, ["exterior", "reset"])
        asyncio.run(check())

    def test_short_status_separates_stream_error_from_ng(self):
        cameras = self.fill_four()
        self.m.event_queue.put(("stream_status", cameras[0].sn, 'Video NG / reconnecting...'))
        self.app.poll()
        self.assertEqual(self.app.panels[0].status.options["text"], 'Video interrupted - reconnecting')
        self.assertEqual(self.app.ng_count, 0)
        self.app.log("[Waiting for reboot] 192.168.0.233")
        self.assertIn('Waiting for reboot', self.app.panels[3].log_box.text)
        self.assertNotIn("option=", self.app.panels[3].log_box.text)
        self.assertFalse(hasattr(type(self.app), "ok_button"))
        self.assertNotIn("log_box", self.app.__dict__)
        self.app.mark_ng(1)
        self.assertIn("NG · SN1", self.app.panels[1].status.options["text"])

    def test_reboot_reservation_keeps_window_and_blocks_early_decision(self):
        async def check():
            self.app.mode_var.set(4)
            self.app.change_mode()
            cameras = [self.m.CameraInfo(f"192.168.0.{230+i}", f"BOOT{i}", "MAC") for i in range(5)]
            futures = [asyncio.get_running_loop().create_future() for _ in cameras]
            for camera, future in zip(cameras, futures):
                self.m.event_queue.put(("camera_reboot", camera, future))
            self.app.poll()
            await asyncio.sleep(0)
            self.assertTrue(all(not f.done() for f in futures[:4]))
            self.assertFalse(futures[4].done())
            self.assertIsNone(self.app.panels[0].reader)
            self.assertEqual(self.app.panels[0].ok.options["state"], "disabled")
            with patch.object(self.m, "request_step3") as reset:
                self.app.mark_ok(0)
                reset.assert_not_called()
            self.app.log("[Offline] 192.168.0.230")
            self.assertIn('Rebooting', self.app.panels[0].log_box.text)
            self.assertNotIn('Rebooting', self.app.panels[1].log_box.text)
            self.m.event_queue.put(("camera_ready", cameras[0]))
            self.app.poll()
            self.assertIsNone(self.app.panels[0].reader)  # RTSP準備完了だけでは外観検査を飛ばせない。
            self.app.usb_reader = USBReader()
            self.app.mark_ok(0)
            await asyncio.sleep(0)
            self.assertTrue(futures[0].result())
            self.assertIs(self.app.panels[0].reader.camera, cameras[0])
            self.assertIs(self.app.panels[1].camera, cameras[1])
            self.m.event_queue.put(("camera_failed", cameras[1].sn))
            self.app.poll()
            await asyncio.sleep(0)
            self.assertIs(self.app.panels[1].camera, cameras[4])
            self.assertFalse(futures[1].result())
            self.assertFalse(futures[4].done())
            self.app.mark_ok(1)
            await asyncio.sleep(0)
            self.assertTrue(futures[4].result())
        asyncio.run(check())

    def test_ng_evidence_cancel_save_and_save_failure(self):
        cameras = self.fill_four()
        with patch.object(self.m, "save_result") as result, patch.object(self.m, "request_step3") as reset:
            self.app.preview_ng = lambda camera, image: "cancel"
            self.app.mark_ng(0)
            self.assertIs(self.app.panels[0].camera, cameras[0])
            result.assert_not_called()
            self.assertFalse(self.app.ng_preview_open)
            self.app.preview_ng = lambda camera, image: "save"
            with tempfile.TemporaryDirectory() as directory, patch.object(self.m, "APP_ROOT", Path(directory)):
                self.app.mark_ng(0)
                paths = list(Path(directory).glob("evidence/*/Z/Z-SN0_*.png"))
                self.assertEqual(len(paths), 1)
                with Image.open(paths[0]) as image:
                    self.assertEqual(image.size, (1920, 1080))
                    self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))
                self.app.save_evidence(cameras[0], Image.new("RGB", (10, 10)))
                self.assertEqual(len(list(Path(directory).glob("evidence/*/Z/Z-SN0_*.png"))), 2)
            result.assert_called_once()
            self.assertEqual(result.call_args.args, (cameras[0], "NG", "-"))
            recorded_path = Path(result.call_args.kwargs["evidence_path"])
            self.assertFalse(recorded_path.is_absolute())
            self.assertEqual(recorded_path.resolve(), paths[0].resolve())
            reset.assert_not_called()
            with patch.object(self.app, "save_evidence", side_effect=OSError("disk full")), patch.object(self.m.messagebox, "showerror"):
                self.app.mark_ng(1)
            self.assertIs(self.app.panels[1].camera, cameras[1])
            self.assertEqual(self.app.ng_count, 1)
            self.assertFalse(self.app.ng_preview_open)

    def test_ng_without_frame_requires_confirmation(self):
        self.fill_four()
        with patch.object(self.app.panels[0].reader, "snapshot", return_value=None), patch.object(self.m, "save_result") as result:
            with patch.object(self.m.messagebox, "askyesno", return_value=False):
                self.app.mark_ng(0)
                result.assert_not_called()
            with patch.object(self.m.messagebox, "askyesno", return_value=True), patch.object(self.app, "save_evidence", return_value="E1-test.png") as save:
                self.app.mark_ng(0)
                result.assert_called_once_with(self.app.panels[0].last_camera, "NG", "-",
                                               ng_reason='RTSP unavailable', evidence_path="E1-test.png")
                self.assertEqual(save.call_args.args[2], "E1")

    def test_ng_reasons_are_saved_without_reset(self):
        cameras = self.fill_four()
        with patch.object(self.m, "save_result") as result, patch.object(self.m, "request_step3") as reset, \
                patch.object(self.app, "save_evidence", return_value="evidence.png"):
            for index, reason in enumerate(('IR-CUT defect', "映像にノイズ")):
                self.app.preview_ng = lambda camera, image, reason=reason: ("save", reason)
                self.app.mark_ng(index)
                result.assert_called_with(cameras[index], "NG", "-", ng_reason=reason, evidence_path="evidence.png")
            reset.assert_not_called()

    def test_exterior_is_mandatory_and_usb_owner_is_serial(self):
        cameras = [self.m.CameraInfo(f"192.168.0.{230+i}", f"SN{i}", f"MAC{i}") for i in range(2)]
        for camera in cameras:
            self.m.inspection_queue.put(camera)
        self.app.poll()
        self.assertEqual(self.app.usb_owner, 0)
        self.assertTrue(all(p.reader is None for p in self.app.panels[:2]))
        with patch.object(self.m, "request_step3") as reset:
            self.app.mark_ok(0)  # USBなし
            self.assertFalse(self.app.panels[0].exterior_done)
            self.app.usb_reader = USBReader()
            self.app.usb_reader.available = False
            self.app.mark_ok(0)  # 切断中
            self.assertFalse(self.app.panels[0].exterior_done)
            self.app.usb_reader.available = True
            self.app.mark_ok(1)  # 別枠を誤操作
            self.assertFalse(self.app.panels[1].exterior_done)
            self.app.mark_ok(0)
            self.assertTrue(self.app.panels[0].exterior_done)
            self.assertIs(self.app.panels[0].reader.camera, cameras[0])
            self.assertEqual(self.app.usb_owner, 1)
            reset.assert_not_called()

    def test_exterior_ng_uses_usb_evidence_and_never_final_resets(self):
        camera = self.m.CameraInfo("192.168.0.230", "SN0", "AA:BB:CC:DD:EE:FF")
        self.m.inspection_queue.put(camera)
        self.app.poll()
        self.app.usb_reader = USBReader()
        with patch.object(self.m.exterior, "preview_exterior_evidence", return_value=None) as preview, \
                patch.object(self.m, "save_result") as result, patch.object(self.m, "request_step3") as reset:
            self.app.mark_ng(0)
            self.assertIs(self.app.panels[0].camera, camera)
            result.assert_not_called()
            preview.return_value = "B-AA-BB-CC-DD-EE-FF.jpg"
            self.app.mark_ng(0)
            result.assert_called_once_with(camera, 'Appearance NG', "-", ng_reason='Appearance defect', evidence_path="B-AA-BB-CC-DD-EE-FF.jpg")
            self.assertIs(self.app.panels[0].camera, camera)
            self.assertTrue(self.app.panels[0].exterior_done)
            self.assertIs(self.app.panels[0].reader.camera, camera)
            self.assertEqual(self.app.ng_count, 0)
            reset.assert_not_called()
            self.assertFalse(self.app.ng_preview_open)

    def test_exterior_full_resolution_boxes_csv_and_no_overwrite(self):
        camera = self.m.CameraInfo("192.168.0.230", "SN0", "AA:BB:CC:DD:EE:FF")
        image = Image.new("RGB", (1920, 1080), "white")
        with tempfile.TemporaryDirectory() as directory:
            csv_file = str(Path(directory) / "results.csv")
            first = self.m.exterior.save_exterior_evidence(camera, image, [(100, 100, 500, 500)], directory, csv_file)
            second = self.m.exterior.save_exterior_evidence(camera, image, [], directory, csv_file)
            self.assertNotEqual(first, second)
            self.assertTrue(Path(first).name.startswith("B-"))
            with Image.open(first) as saved:
                self.assertEqual(saved.size, (1920, 1080))
                red, green, blue = saved.getpixel((100, 200))
                self.assertGreater(red, green + 50)
            with open(csv_file, newline="", encoding="utf-8-sig") as source:
                rows = list(csv.reader(source))
            self.assertEqual(rows[1][1:3], [camera.sn, camera.mac])
            self.assertEqual(rows[1][-1], "1")

    def test_exterior_editor_maps_display_boxes_to_source_coordinates(self):
        widgets = []
        class EditorWidget(Widget):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.bindings = {}
                widgets.append(self)
            def bind(self, name, action):
                self.bindings[name] = action
            def create_rectangle(self, *args, **kwargs):
                return 1
        editor = self.m.exterior
        camera = self.m.CameraInfo("192.168.0.230", "SN0", "AA:BB:CC:DD:EE:FF")
        image = Image.new("RGB", (1920, 1080), "white")
        def interact(window):
            canvas = next(w for w in widgets if "<B1-Motion>" in w.bindings)
            canvas.bindings["<Button-1>"](types.SimpleNamespace(x=50, y=25))
            canvas.bindings["<ButtonRelease-1>"](types.SimpleNamespace(x=200, y=125))
            next(w for w in widgets if w.options.get("text") == 'Confirm and save').options["command"]()
        fake_tk = types.SimpleNamespace(**{name: EditorWidget for name in ("Toplevel", "Label", "Canvas", "Frame", "Button")})
        with patch.object(editor, "tk", fake_tk), \
                patch.object(editor, "save_exterior_evidence", return_value="evidence.jpg") as save, \
                patch.object(self.app.root, "wait_window", side_effect=interact):
            path = editor.preview_exterior_evidence(self.app.root, camera, image)
            self.assertEqual(path, "evidence.jpg")
            save.assert_called_once_with(camera, image, [(100, 50, 400, 250)])

    def test_usb_reader_rejects_stale_and_stopped_frames(self):
        import time
        reader = self.m.exterior.InspectionUSBReader(2)
        reader.frame = np.zeros((4, 4, 3), dtype=np.uint8)
        reader.frame_time = time.monotonic() - 3
        self.assertIsNone(reader.snapshot())
        reader.frame_time = time.monotonic()
        self.assertIsNotNone(reader.snapshot())
        reader.stop()
        self.assertIsNone(reader.snapshot())

    def test_evidence_categories_names_and_relative_csv_link(self):
        camera = self.m.CameraInfo("192.168.0.230", "SN001", "AA:BB:CC:DD:EE:FF")
        store = self.m.exterior.evidence_store
        with tempfile.TemporaryDirectory() as directory:
            for category in ("B", "E1", "E2", "Z"):
                path = Path(store.save_image(directory, category, camera, Image.new("RGB", (16, 16))))
                self.assertEqual(path.parent.name, category)
                self.assertRegex(path.parent.parent.name, r"^\d{4}-\d{2}-\d{2}$")
                self.assertRegex(path.name, rf"^{category}-SN001_AA-BB-CC-DD-EE-FF_\d{{8}}_\d{{6}}_\d{{6}}\.png$")
            csv_file = Path(directory) / "records" / "inspection.csv"
            with patch.object(self.m, "CSV_FILE", str(csv_file)):
                self.m.save_result(camera, "NG", "-", ng_reason='Other', evidence_path=str(path))
            with csv_file.open(newline="", encoding="utf-8-sig") as source:
                rows = list(csv.reader(source))
            self.assertEqual((csv_file.parent / rows[1][-1]).resolve(), path.resolve())

    def test_manual_other_keeps_z_category_even_with_ir_cut_text(self):
        cameras = self.fill_four()
        self.app.preview_ng = lambda camera, image: ("save", 'IR-CUT defect', "Z")
        with patch.object(self.app, "save_evidence", return_value="Z-image.png") as save, patch.object(self.m, "save_result"):
            self.app.mark_ng(0)
            self.assertIs(save.call_args.args[0], cameras[0])
            self.assertEqual(save.call_args.args[2], "Z")

    def test_records_migration_preserves_existing_destination(self):
        store = self.m.exterior.evidence_store
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "camera_inspection.csv").write_text("legacy", encoding="utf-8")
            store.organize_records(directory)
            destination = root / "records" / "camera_inspection.csv"
            self.assertEqual(destination.read_text(encoding="utf-8"), "legacy")
            (root / "camera_inspection.csv").write_text("second", encoding="utf-8")
            store.organize_records(directory)
            self.assertEqual(destination.read_text(encoding="utf-8"), "legacy")
            archived = list((root / "records").glob("camera_inspection_legacy_*.csv"))
            self.assertEqual(len(archived), 1)
            self.assertEqual(archived[0].read_text(encoding="utf-8"), "second")

    def test_usb_auto_selects_external_camera_by_enumerated_name(self):
        self.app.usb_events.put(("devices", [(0, "Integrated Camera"),
                                             (1, "Lenovo Virtual Camera"), (2, "USB 2.0 Camera")]))
        with patch.object(self.m.exterior, "InspectionUSBReader") as reader:
            reader.return_value.get_frame.return_value = None
            self.app.poll()
            reader.assert_called_once_with(2, "USB 2.0 Camera")
            reader.return_value.start.assert_called_once()
            self.assertEqual(self.app.usb_entry.get(), "2")

    def test_usb_status_updates_without_inspection_and_reopens_without_stopping_reader(self):
        self.app.usb_reader = USBReader()
        self.app.usb_selected = 2
        self.app.usb_devices = {2: "USB 2.0 Camera"}
        self.app.update_usb_status(self.app.usb_reader.get_frame())
        self.assertIn("video available", self.app.usb_state_label.options["text"])
        self.assertIn("USB 2.0 Camera", self.app.usb_device_label.options["text"])
        self.assertIn("640 × 480", self.app.usb_detail_label.options["text"])
        self.assertIn('idle', self.app.usb_detail_label.options["text"])
        self.app.usb_reader.available = False
        self.app.usb_reader.error = 'Selected USB camera unavailable - waiting for reconnection'
        self.app.update_usb_status(None)
        self.assertIn("No frames", self.app.usb_state_label.options["text"])
        self.app.hide_usb_status()
        self.assertIsNone(self.app.usb_status_window)
        self.assertFalse(self.app.usb_reader.stopped)
        self.app.show_usb_status()
        self.assertIsNotNone(self.app.usb_status_window)
        self.assertIn("No frames", self.app.usb_state_label.options["text"])

    def test_exterior_ng_continues_preparation_and_assigns_usb_to_next_device(self):
        async def check():
            camera = self.m.CameraInfo("192.168.0.230", "SN0", "AA:BB:CC:DD:EE:FF")
            future = asyncio.get_running_loop().create_future()
            self.m.event_queue.put(("camera_reboot", camera, future))
            self.app.poll()
            self.app.usb_reader = USBReader()
            with patch.object(self.m.exterior, "preview_exterior_evidence", return_value="evidence.jpg"), \
                    patch.object(self.m, "save_result"), patch.object(self.m, "reset_device") as reset:
                self.app.mark_ng(0)
                await asyncio.sleep(0)
                self.assertTrue(future.result())
                reset.assert_not_called()
            self.m.event_queue.put(("camera_ready", camera))
            self.app.poll()
            self.assertIs(self.app.panels[0].reader.camera, camera)
            replacement = self.m.CameraInfo(camera.ip, "NEW", camera.mac)
            next_future = asyncio.get_running_loop().create_future()
            self.m.event_queue.put(("camera_reboot", replacement, next_future))
            self.app.poll()
            self.assertIs(self.app.panels[1].camera, replacement)
            self.assertEqual(self.app.usb_owner, 1)
            self.assertFalse(next_future.done())
        asyncio.run(check())

    def test_step3_worker_records_result_and_ui_counts_only_completion(self):
        async def check():
            camera = self.m.CameraInfo("192.168.0.230", "SN3", "AA:BB:CC:DD:EE:FF")
            self.m.STEP3_QUEUE = asyncio.Queue()
            self.m.STEP3_QUEUE.put_nowait(camera)
            self.app.panels[0].last_camera = camera
            def completed(*args, **kwargs):
                self.m.APP_STOP.set()
                return True, "設定書込・IP Reset要求成功"
            with patch.object(self.m.step3, "run", side_effect=completed) as run, \
                    patch.object(self.m, "save_result") as save, patch.object(self.m, "reset_device") as reset:
                await self.m.step3_worker(None)
                run.assert_called_once()
                save.assert_called_once_with(camera, "OK", "Step3 OK")
                reset.assert_not_called()
                self.assertEqual(self.app.ok_count, 0)
                self.app.poll()
                self.assertEqual(self.app.ok_count, 1)
                self.assertEqual(self.app.panels[0].status.options["text"], "Step3 · OK")
        asyncio.run(check())

    def test_single_window_rtsp_shortcuts_and_four_window_block(self):
        self.fill_four()
        self.app.select_panel(0)
        with patch.object(self.app, "mark_ok") as ok, patch.object(self.app, "mark_ng") as ng:
            self.app.enter_ok()
            self.app.escape_ng()
            ok.assert_not_called()
            ng.assert_not_called()
            for panel in self.app.panels[1:]:
                panel.finish()
            self.app.mode_var.set(1)
            self.app.change_mode()
            self.assertIn("[Enter]", self.app.panels[0].ok.options["text"])
            self.assertIn("[Esc]", self.app.panels[0].ng.options["text"])
            self.app.enter_ok()
            self.app.escape_ng()
            ok.assert_called_once_with(0)
            ng.assert_called_once_with(0)

    def test_step3_running_holds_slot_and_ok_blinks_until_next_camera(self):
        cameras = self.fill_four()
        panel = self.app.panels[0]
        replacement = self.m.CameraInfo("192.168.0.240", "NEW", "MACNEW")
        with patch.object(self.m, "request_step3"):
            self.app.mark_ok(0)
        self.assertEqual(panel.video.options["text"], "RUNING")
        self.assertIs(panel.processing_camera, cameras[0])
        self.m.inspection_queue.put(replacement)
        self.app.poll()
        self.assertIsNone(panel.camera)
        self.assertIs(panel.processing_camera, cameras[0])
        self.m.event_queue.put(("step3_result", cameras[0], True, "complete"))
        self.app.poll()
        self.assertEqual(panel.video.options["text"], 'Reboot completed\nOK')
        self.assertIsNone(panel.camera)  # 少なくとも2秒は完了表示を保持。
        panel.blink_ok()
        self.assertEqual(panel.video.options["fg"], self.m.COLOR_VIDEO)
        panel.blink_ok()
        self.assertEqual(panel.video.options["fg"], self.m.COLOR_GREEN)
        panel.result_hold_until = 0
        self.app.poll()
        self.assertIs(panel.camera, replacement)
        self.assertIsNone(panel.result_state)

    def test_ng_reason_csv_migrates_existing_results(self):
        camera = self.m.CameraInfo("192.168.0.230", "SN0", "MAC0")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.csv"
            with path.open("w", newline="", encoding="utf-8-sig") as target:
                csv.writer(target).writerows([
                    ['Timestamp', "SN", "MAC", "IP", 'Result', 'Reset'],
                    ["old", "SN1", "MAC1", "192.168.0.231", "OK", "OK"]])
            with patch.object(self.m, "CSV_FILE", str(path)):
                self.m.save_result(camera, "NG", "-", ng_reason='IR-CUT defect')
                self.m.save_result(camera, "NG", "-", ng_reason="ノイズ, 色異常")
            with path.open(newline="", encoding="utf-8-sig") as source:
                rows = list(csv.reader(source))
            self.assertEqual(rows[0][-2:], ['NG reason', 'Evidence'])
            self.assertEqual(rows[1], ["old", "SN1", "MAC1", "192.168.0.231", "OK", "OK", "", ""])
            self.assertEqual(rows[2][-2], 'IR-CUT defect')
            self.assertEqual(rows[3][-2], "ノイズ, 色異常")

    def test_debug_toggle_redacts_password_and_close_stops_all(self):
        self.fill_four()
        readers = [panel.reader for panel in self.app.panels]
        self.app.debug_var.set(True)
        self.app.toggle_debug()
        self.m.send_debug("password=" + self.m.PASSWORD)
        self.app.poll()
        self.assertNotIn(self.m.PASSWORD, self.app.debug_box.text)
        self.assertIn("password=***", self.app.debug_box.text)
        self.app.hide_debug()
        self.assertFalse(self.m.DEBUG_ENABLED.is_set())
        self.app.close()
        self.assertTrue(all(reader.stopped for reader in readers))

if __name__ == "__main__":
    unittest.main()
