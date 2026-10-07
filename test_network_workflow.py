"""Offline IP planning and reservation checks; never sends network packets."""
import importlib.util
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import types

spec = importlib.util.spec_from_file_location("workflow_tests", Path(__file__).with_name("network_workflow.py"))
flow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flow)


class NetworkTests(unittest.TestCase):
    def test_refresh_saved_ip_from_current_adapter(self):
        record = {"mac": "02:00:00:00:00:10", "ip": "192.168.5.102"}
        details = {record["mac"]: {"ip": ["192.168.0.100"], "prefix": [24],
                                   "gateway": "192.168.0.1"}}
        with patch.object(flow, "windows_network_details", return_value=details):
            result = flow.enrich_interfaces([record])[0]
        self.assertEqual(result["ip"], "192.168.0.100")
        self.assertTrue(result["prefix_verified"])
        self.assertEqual(result["prefix_length"], 24)
        self.assertEqual(result["gateway"], "192.168.0.1")

    def test_ambiguous_current_ips_are_not_guessed(self):
        record = {"mac": "02:00:00:00:00:10", "ip": "192.168.5.102"}
        details = {record["mac"]: {"ip": ["192.168.0.100", "192.168.1.100"],
                                   "prefix": [24, 24]}}
        with patch.object(flow, "windows_network_details", return_value=details):
            self.assertFalse(flow.enrich_interfaces([record])[0]["prefix_verified"])

    def setUp(self):
        self.interface = {"ip": "192.168.23.33", "prefix_length": 24, "gateway": "192.168.23.1"}

    def test_work_network_auto_and_target_defaults(self):
        config = flow.network_config({}, self.interface)
        self.assertEqual(config["work"][0], "192.168.23.150")
        self.assertEqual(config["targets"][0], "192.168.0.220")
        self.assertEqual(config["mask"], "255.255.255.0")
        self.assertEqual(config["gateway"], "192.168.0.1")

    def test_reject_unreachable_work_pool_overlaps_and_bad_mask_gateway(self):
        for settings in ({"work_prefix": "192.168.0"},
                         {"target_prefix": "192.168.23", "target_next": 150, "target_start": 150},
                         {"target_mask": "255.255.255.248"},
                         {"target_gateway": "192.168.9.1"},
                         {"target_start": 230, "target_next": 220}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                flow.network_config(settings, self.interface)

    def test_pc_address_excluded_from_work_pool(self):
        self.interface["ip"] = "192.168.23.160"
        self.assertNotIn("192.168.23.160", flow.network_config({}, self.interface)["work"])

    def test_target_reservations_are_sequential_and_survive_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = flow.Journal(folder)
            candidates = ["192.168.0.220", "192.168.0.221", "192.168.0.222"]
            self.assertEqual(journal.reserve("mac1", candidates, lambda ip: False), candidates[0])
            self.assertEqual(journal.reserve("mac2", candidates, lambda ip: False), candidates[1])
            reopened = flow.Journal(folder)
            self.assertEqual(reopened.reserve("mac1", candidates, lambda ip: True), candidates[0])
            reopened.transition("mac1", "confirmed")
            self.assertEqual(flow.Journal(folder).data["assignments"]["mac1"]["state"], "confirmed")
            self.assertEqual(reopened.reserve("mac3", candidates, lambda ip: False), candidates[2])

    def test_occupied_target_skipped_and_full_pool_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = flow.Journal(folder)
            choices = ["192.168.0.220", "192.168.0.221"]
            self.assertEqual(journal.reserve("mac1", choices, lambda ip: ip.endswith("220")), choices[1])
            with self.assertRaises(ValueError):
                journal.reserve("mac2", choices, lambda ip: ip.endswith("220"))

    def test_target_cursor_wraps_and_each_record_advances_once(self):
        config = flow.network_config({"target_start": 220, "target_end": 222, "target_next": 222}, self.interface)
        self.assertEqual(config["targets"], ["192.168.0.222", "192.168.0.220", "192.168.0.221"])
        with tempfile.TemporaryDirectory() as folder:
            journal = flow.Journal(folder)
            self.assertEqual(journal.advance_counter("one", 220, 222, 222), 220)
            self.assertIsNone(flow.Journal(folder).advance_counter("one", 220, 222, 220))
            self.assertEqual(journal.advance_counter("two", 220, 222, 220), 221)

    def test_new_job_can_reuse_history_ip_but_checks_current_occupancy(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = flow.Journal(folder)
            journal.reserve("old", ["192.168.0.220"], lambda ip: False)
            journal.begin_job()
            self.assertEqual(journal.reserve("new", ["192.168.0.220", "192.168.0.221"], lambda ip: ip.endswith("220")), "192.168.0.221")

    def test_multiple_devices_never_receive_same_reserved_ip(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = flow.Journal(folder)
            result = []
            choices = [f"192.168.0.{n}" for n in range(220, 224)]
            threads = [threading.Thread(target=lambda n=n: result.append(journal.reserve(str(n), choices, lambda ip: False))) for n in range(4)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
            self.assertEqual(set(result), set(choices))

    def test_info_raw_preserved_explicit_fields_parsed(self):
        result = flow.parse_info(b"ip=192.168.5.190;model=NC8164-FPE;sn=SN001\0padding")
        self.assertEqual(result["model"], "NC8164-FPE")
        self.assertEqual(result["ip"], "192.168.5.190")
        self.assertEqual(result["sn"], "SN001")
        self.assertIn("model=", result["info"])
        self.assertNotIn("model", flow.parse_info(b"unidentified device"))

    def fake_link(self, packets):
        class Ether:
            def __init__(self, **values): self.__dict__.update(values)
            def __truediv__(self, payload):
                self.payload = payload
                return self
        class Raw:
            def __init__(self, payload): self.payload = payload
        class ARP:
            def __init__(self, **values): self.__dict__.update(values)
        class Packet:
            def __init__(self, source, payload):
                self.eth = types.SimpleNamespace(src=source, payload=payload)
            def __getitem__(self, layer): return self.eth
        class Sniffer:
            def __init__(self, **kwargs): pass
            def start(self): pass
            def stop(self): return packets
        link = flow.Link.__new__(flow.Link)
        link.Ether, link.Raw, link.ARP, link.AsyncSniffer = Ether, Raw, ARP, Sniffer
        link.pc_mac, link.pc_ip = "00:11:22:33:44:55", "192.168.0.100"
        link.id, link.interface = "wired", {"prefix_length": 24}
        link.stopped = lambda: False
        link.srp = lambda *args, **kwargs: ([], [])
        sent = []
        link.sendp = lambda packet, **kwargs: sent.append(packet)
        return link, sent, Packet

    def test_discovery_sends_get_info_and_caches_valid_ack_info(self):
        packets = []
        link, sent, Packet = self.fake_link(packets)
        target = "02:00:00:00:00:03"
        payload = flow.MAGIC + flow.ACK + bytes.fromhex("001122334455") + b"\x01ip=192.168.5.190;model=NC8164-FPE"
        packets.append(Packet(target, payload))
        with patch.object(flow.time, "sleep"):
            devices = link.discover()
        self.assertEqual(devices[0]["mac"], target)
        self.assertEqual(devices[0]["model"], "NC8164-FPE")
        self.assertEqual(sent[0].payload.payload, (flow.MAGIC + flow.GET_DEVICE_INFO).ljust(246, b"\0"))
        self.assertEqual(len(sent), 1)

    def test_ip_set_is_one_shot_and_ack_must_match_both_macs(self):
        packets = []
        link, sent, Packet = self.fake_link(packets)
        target = "02:00:00:00:00:03"
        good = flow.MAGIC + flow.ACK + bytes.fromhex("001122334455") + b"\x01ip=192.168.0.220"
        packets.append(Packet("1c:c3:16:55:5a:3b", good))
        with patch.object(flow.time, "sleep"):
            result = link.set_ip(target, "192.168.0.220", "255.255.255.0", "192.168.0.1", ("admin", "test"))
        self.assertIsNone(result)
        self.assertEqual(len(sent), 1)
        payload = sent[0].payload.payload
        self.assertEqual(payload[:6], flow.MAGIC + flow.SET)
        self.assertEqual(payload[6:12], bytes.fromhex(target.replace(":", "")))
        self.assertEqual(len(payload), 246)
        packets[:] = [Packet(target, good)]
        with patch.object(flow.time, "sleep"):
            result = link.set_ip(target, "192.168.0.220", "255.255.255.0", "192.168.0.1", ("admin", "test"))
        self.assertEqual(result["ip"], "192.168.0.220")

    def test_corrupt_reservation_record_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            records = Path(folder) / "records"
            records.mkdir()
            (records / "network_workflow.json").write_text("not-json", encoding="utf-8")
            with self.assertRaises(ValueError):
                flow.Journal(folder)

    def test_captured_device_info_model_and_oem_are_distinct(self):
        info = b"192.168.5.101;80;255.255.255.0;192.168.5.1;192.168.1.1;MS-C8164-FPE;61.8.1.4-r7-c18;V1.0;Network Camera;NC8164-FPE;1513;0;IPCAM;1;1;EXAMPLE-SN-001"
        parsed = flow.parse_info(info.ljust(240, b"\0"))
        self.assertEqual(parsed["model"], "MS-C8164-FPE")
        self.assertEqual(parsed["oem_model"], "NC8164-FPE")
        self.assertEqual(parsed["http_port"], 80)
        self.assertEqual(parsed["sn"], "EXAMPLE-SN-001")
        self.assertEqual(parsed["firmware"], "61.8.1.4-r7-c18")
        self.assertEqual(parsed["device_type"], "IPCAM")
        self.assertTrue(flow.model_allowed(parsed, ["NC8164-FPE"]))
        self.assertTrue(flow.model_allowed(parsed, ["MS-C8164-FPE"]))
        self.assertFalse(flow.model_allowed(parsed, ["other"]))

    def test_get_device_info_response_reads_offset_six(self):
        packets = []
        link, sent, Packet = self.fake_link(packets)
        body = b"192.168.5.140;80;255.255.255.0;192.168.5.1;192.168.5.1;MS-C8164-FPE;FW;HW;Network Camera;NC8164-FPE;1516;1;IPCAM;1;1;SN002"
        packets.append(Packet("02:00:00:00:00:02", (flow.MAGIC + flow.ANNOUNCE + body).ljust(246, b"\0")))
        with patch.object(flow.time, "sleep"):
            devices = link.discover()
        self.assertEqual(devices[0]["ip"], "192.168.5.140")
        self.assertEqual(devices[0]["oem_model"], "NC8164-FPE")


if __name__ == "__main__":
    unittest.main()
