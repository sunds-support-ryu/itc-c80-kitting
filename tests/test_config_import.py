from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
"""オフライン検証。実機への書込・Resetは実行しない。"""
import base64
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import sys
from urllib.parse import urlsplit, parse_qs
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.asymmetric import rsa, padding as rsa_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

spec = importlib.util.spec_from_file_location("step3_test_module", (Path(__file__).resolve().parents[1] / "src" / "config_import.py"))
step3 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(step3)


class Camera:
    def __init__(self, outcome="OnUploadSus", serial="SN001", verification=True):
        self.outcome, self.serial, self.verification = outcome, serial, verification
        self.uploads, self.resets = 0, []
        self.written = False
        self.aes = b"1234567890123456"
        self.password = None

    def request(self, url, method="GET", body=None, headers=None, credentials=None):
        parsed = urlsplit(url)
        params = parse_qs(parsed.query)
        if parsed.path == "/challenge":
            modulus = int.from_bytes(base64.b64decode(base64.b64decode(params["PublicKey"][0])), "big")
            public = rsa.RSAPublicNumbers(3, modulus).public_key()
            encrypted = public.encrypt(self.aes, rsa_padding.PKCS1v15())
            key = base64.b64encode(encrypted.hex().encode()).decode()
            return 200, f"<key>{key}</key><keyID>123</keyID>"
        if parsed.path == "/dataloader.cgi":
            self.uploads += 1
            if self.outcome == "timeout":
                raise TimeoutError()
            decryptor = Cipher(algorithms.AES(self.aes), modes.CBC(b"0123456789ABCDEF")).decryptor()
            decoded = decryptor.update(base64.b64decode(params["encryptPwd"][0])) + decryptor.finalize()
            unpadder = padding.PKCS7(128).unpadder()
            self.password = (unpadder.update(decoded) + unpadder.finalize()).decode()
            assert b"config-content" in body and "multipart/form-data" in headers["Content-Type"]
            self.written = True
            return 200, self.outcome
        if "set.system.reset" in parsed.query:
            self.resets.append(json.loads(body))
            assert credentials == step3.VERIFY_CREDENTIALS
            return 200, "{}"
        if self.written and not self.verification:
            return 401, ""
        return 200, json.dumps({"snCode": self.serial, "model": "ITC-C80"})


class WebCamera(Camera):
    def __init__(self, code=1):
        super().__init__()
        self.code = code
        self.cleared = False

    def request(self, url, method="GET", body=None, headers=None, credentials=None):
        parsed = urlsplit(url)
        params = parse_qs(parsed.query)
        if parsed.path == "/vb.htm":
            if "setimportstatus=0" in parsed.query:
                self.cleared = True
                return 200, "UW OK setimportstatus=0"
            code = self.code if self.written else 0
            return 200, f"UW OK getimportstatus={code}"
        if parsed.path == "/challenge":
            raise AssertionError("Web import must not perform RSA handshake")
        if parsed.path == "/dataloader.cgi":
            assert self.cleared
            assert params == {"up": ["cfg"], "pwd": ["example-import-password"]}
            assert b'filename="config-no-extension"' in body
            assert b"config-content" in body
            self.uploads += 1
            self.written = True
            return 200, ""
        return super().request(url, method, body, headers, credentials)


class Step3Tests(unittest.TestCase):
    def run_camera(self, device, timeout=.01, ip_checker=lambda mac, ip: True, reset_ip=True):
        camera = types.SimpleNamespace(ip="192.168.0.230", sn="SN001", mac="02:00:00:00:00:03")
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "records").mkdir()
            (Path(directory) / "records/settings.json").write_text(json.dumps({"config_import_password": "example-import-password"}))
            folder = Path(directory) / "config file"
            folder.mkdir()
            (folder / "config-no-extension").write_bytes(b"config-content")
            return step3.run(camera, directory, ("admin", "old-login"), transport=device.request,
                             verify_timeout=timeout, ip_checker=ip_checker, final_timeout=.01, reset_ip=reset_ip)

    def test_import_password_and_reset_option_two(self):
        device = Camera()
        success, detail = self.run_camera(device)
        self.assertTrue(success, detail)
        self.assertEqual(device.password, "example-import-password")
        self.assertEqual(device.uploads, 1)
        self.assertEqual(device.resets, [{"options": 2}])

    def test_rejection_unknown_and_lost_response_never_reset_or_retry(self):
        for outcome in ("upload_failed", "<script>function OnUploadSus(){};alert(upload_failed_tips);</script>", "unknown", "timeout"):
            with self.subTest(outcome=outcome):
                device = Camera(outcome=outcome)
                self.assertFalse(self.run_camera(device)[0])
                self.assertEqual(device.uploads, 1)
                self.assertEqual(device.resets, [])

    def test_wrong_serial_prevents_upload(self):
        device = Camera(serial="OTHER")
        self.assertFalse(self.run_camera(device)[0])
        self.assertEqual(device.uploads, 0)
        self.assertEqual(device.resets, [])

    def test_verification_failure_prevents_reset(self):
        device = Camera(verification=False)
        self.assertFalse(self.run_camera(device)[0])
        self.assertEqual(device.resets, [])

    def test_web_import_uses_pwd_and_status_then_reset(self):
        device = WebCamera()
        success, detail = self.run_camera(device)
        self.assertTrue(success, detail)
        self.assertEqual(device.uploads, 1)
        self.assertEqual(device.resets, [{"options": 2}])

    def test_import_with_retained_login_uses_verified_original_credentials(self):
        device = WebCamera()
        original = device.request
        def request(url, method="GET", body=None, headers=None, credentials=None):
            if "reloadflag" in url:
                return 200, "UW OK reloadflag"
            if device.written and urlsplit(url).path == "/cgi-bin/admin/admin.cgi" and credentials == step3.VERIFY_CREDENTIALS:
                return 401, ""
            return original(url, method, body, headers, credentials)
        success, detail = self.run_camera(types.SimpleNamespace(request=request), reset_ip=False)
        self.assertTrue(success, detail)
        self.assertEqual(device.uploads, 1)
        self.assertEqual(device.resets, [])

    def test_saved_import_password_is_used_in_legacy_encryption(self):
        device = Camera()
        with patch.object(step3, "load_settings", return_value={"config_import_password": "custom-import-secret"}):
            success, detail = self.run_camera(device)
        self.assertTrue(success, detail)
        self.assertEqual(device.password, "custom-import-secret")

    def test_saved_import_password_is_used_in_web_upload(self):
        device = WebCamera()
        original = device.request
        def transport(url, *args, **kwargs):
            if "/dataloader.cgi" in url:
                self.assertEqual(parse_qs(urlsplit(url).query)["pwd"], ["custom-import-secret"])
                url = url.replace("custom-import-secret", "example-import-password")
            return original(url, *args, **kwargs)
        with patch.object(step3, "load_settings", return_value={"config_import_password": "custom-import-secret"}):
            success, detail = self.run_camera(types.SimpleNamespace(request=transport))
        self.assertTrue(success, detail)

    def test_web_pending_password_and_file_errors_do_not_reset(self):
        for code in (0, 2, 3):
            with self.subTest(code=code):
                device = WebCamera(code)
                self.assertFalse(self.run_camera(device)[0])
                self.assertEqual(device.uploads, 1)
                self.assertEqual(device.resets, [])

    def test_final_mac_ip_confirmation_is_required_after_reset(self):
        calls = []
        def checker(mac, ip):
            calls.append((mac, ip))
            return False
        device = WebCamera()
        success, detail = self.run_camera(device, ip_checker=checker)
        self.assertFalse(success)
        self.assertIn("192.168.5.190", detail)
        self.assertEqual(calls, [("02:00:00:00:00:03", "192.168.5.190")])
        self.assertEqual(device.resets, [{"options": 2}])

    def test_layer_two_probe_rejects_other_mac_and_other_ip(self):
        class Layer:
            def __init__(self, **kwargs): self.__dict__.update(kwargs)
            def __truediv__(self, other): return (self, other)
        class Ether(Layer): pass
        class ARP(Layer): pass
        class Reply:
            def __init__(self, mac, ip):
                self.layers = {Ether: Ether(src=mac), ARP: ARP(op=2, psrc=ip, hwsrc=mac,
                    hwdst="00:11:22:33:44:55", pdst="192.168.0.10")}
            def haslayer(self, layer): return layer in self.layers
            def __getitem__(self, layer): return self.layers[layer]
        target = "02:00:00:00:00:03"
        replies = []
        api = types.ModuleType("scapy.all")
        api.ARP, api.Ether = ARP, Ether
        api.conf = types.SimpleNamespace(use_pcap=True)
        api.get_if_addr = lambda iface: "192.168.0.10"
        api.get_if_hwaddr = lambda iface: "00:11:22:33:44:55"
        api.get_working_ifaces = lambda: ["wired"]
        api.srp = lambda *args, **kwargs: ([(None, reply) for reply in replies], [])
        with patch.dict(sys.modules, {"scapy": types.ModuleType("scapy"), "scapy.all": api}):
            checker = step3.create_ip_checker()
            replies[:] = [Reply("1c:c3:16:55:5a:3b", step3.FINAL_IP)]
            self.assertFalse(checker(target, step3.FINAL_IP))
            replies[:] = [Reply(target, "192.168.0.233")]
            self.assertFalse(checker(target, step3.FINAL_IP))
            replies[:] = [Reply(target, step3.FINAL_IP)]
            self.assertTrue(checker(target, step3.FINAL_IP))
            with tempfile.TemporaryDirectory() as directory:
                step3.save_settings(directory, {"network_interfaces": [{"id": "wired", "name": "Ethernet",
                    "ip": "192.168.0.10", "mac": "00:11:22:33:44:55"}], "network_id": "wired"})
                with patch.object(step3, "discover_interfaces", side_effect=AssertionError("Must reuse cache")):
                    cached = step3.create_ip_checker(directory)
                    self.assertTrue(cached(target, step3.FINAL_IP))


    def test_settings_persist_and_merge_network_and_usb(self):
        with tempfile.TemporaryDirectory() as directory:
            step3.save_settings(directory, {"network_id": "wired", "network_interfaces": []})
            step3.save_settings(directory, {"usb_index": 2, "usb_name": "USB Camera"})
            stored = step3.load_settings(directory)
            self.assertEqual(stored["network_id"], "wired")
            self.assertEqual(stored["usb_name"], "USB Camera")
            self.assertTrue((Path(directory) / "records" / "settings.json").is_file())

    def test_config_only_returns_before_ip_reset_for_sticker_workflow(self):
        device = WebCamera()
        success, detail = self.run_camera(device, reset_ip=False)
        self.assertTrue(success, detail)
        self.assertEqual(device.uploads, 1)
        self.assertEqual(device.resets, [])

    def test_config_selection_rejects_multiple_files(self):
        with tempfile.TemporaryDirectory() as directory:
            step3.save_settings(directory, {"config_import_password": "example-import-password"})
            folder = Path(directory) / "config file"
            folder.mkdir()
            (folder / "a").write_bytes(b"a")
            (folder / "b").write_bytes(b"b")
            with self.assertRaises(step3.Step3Error):
                step3.load_config(directory)


if __name__ == "__main__":
    unittest.main()
