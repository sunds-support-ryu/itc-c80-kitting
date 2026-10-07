from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
"""Offline persistence, restart and outbox isolation checks."""
import csv
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from job_store import JobStore, atomic


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = JobStore(self.temp.name)
        self.store.create("0012", 6, "192.168.0.150")
        self.mac = "02:00:00:00:00:02"
        self.store.device(self.mac, 1, "SN1", "192.168.5.101", "192.168.0.150")

    def test_restart_preserves_mac_steps_and_results(self):
        self.store.update(self.mac, step="IR_CUT", status="TESTING", results={"appearance": "OK"})
        restored = JobStore(self.temp.name)
        self.assertTrue(restored.unfinished())
        self.assertEqual(restored.job["devices"]["020000000002"]["results"], {"appearance": "OK"})
        restored.device("020000000002", 2, "SN1", "changed", "changed")
        self.assertEqual(len(restored.job["devices"]), 1)

    def test_duplicate_completion_does_not_duplicate_csv_or_outbox(self):
        self.store.finish(self.mac, "OK")
        JobStore(self.temp.name).finish(self.mac, "OK")
        with (self.store.root / "result.csv").open(encoding="utf-8-sig") as stream:
            self.assertEqual(len(list(csv.DictReader(stream))), 1)
        data = json.loads((self.store.root / "pending_post.json").read_text())
        self.assertEqual(len(data["records"]), 1)
        self.assertEqual(self.store.carton_count("0012"), 1)

    def test_post_failure_never_changes_local_result(self):
        self.store.finish(self.mac, "OK")
        def failing(record):
            raise TimeoutError("offline")
        self.store.deliver(failing)
        data = json.loads((self.store.root / "pending_post.json").read_text())
        self.assertEqual(data["records"][0]["retry_count"], 1)
        self.assertEqual(data["records"][0]["status"], "PENDING")
        self.assertEqual(self.store.carton_count("0012"), 1)
        self.store.deliver(lambda row: None)
        data = json.loads((self.store.root / "pending_post.json").read_text())
        self.assertEqual(data["records"][0]["status"], "SENT")

    def test_new_job_archives_old_job(self):
        self.store.create("0013", 2, "192.168.0.150")
        backups = list((self.store.root / "history").glob("*incomplete.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text())["carton"], "0012")

    def test_failed_replace_keeps_valid_previous_json(self):
        previous = self.store.path.read_bytes()
        with patch("job_store.os.replace", side_effect=OSError("crash")):
            with self.assertRaises(OSError):
                atomic(self.store.path, {"incomplete": True})
        self.assertEqual(self.store.path.read_bytes(), previous)

    def test_transient_permission_denial_retries_atomic_replace(self):
        import job_store
        original = job_store.os.replace
        calls = []
        def replace(source, destination):
            calls.append(source)
            if len(calls) < 3:
                raise PermissionError("sync lock")
            return original(source, destination)
        with patch("job_store.os.replace", side_effect=replace), patch("job_store.time.sleep"):
            self.store.update(self.mac, step="RTSP")
        self.assertEqual(len(calls), 3)
        self.assertFalse(self.store.dirty)

    def test_long_permission_denial_keeps_pending_state_and_later_flushes(self):
        with patch("job_store.os.replace", side_effect=PermissionError("locked")), patch("job_store.time.sleep"):
            self.store.update(self.mac, step="RTSP")
        self.assertTrue(self.store.dirty)
        self.assertEqual(self.store.job["devices"]["020000000002"]["step"], "RTSP")
        self.store.flush_pending()
        self.assertFalse(self.store.dirty)
        restored = JobStore(self.temp.name)
        self.assertEqual(restored.job["devices"]["020000000002"]["step"], "RTSP")

    def test_ng_does_not_count_as_carton_ok(self):
        self.store.finish(self.mac, "NG")
        self.assertEqual(self.store.carton_count("0012"), 0)

    def test_rekit_preserves_ng_history_and_uses_new_record_id(self):
        self.store.finish(self.mac, 'NG')
        old_id = self.store.job['devices']['020000000002']['record_id']
        self.store.forget_device(self.mac)
        device = self.store.device(self.mac, 1, 'SN1', '192.168.0.150', '192.168.0.220')
        self.assertNotEqual(device['record_id'], old_id)
        self.store.update(self.mac, results={'ir_on':'OK','ir_off':'OK'})
        self.store.finish(self.mac, 'OK')
        with (self.store.root/'result.csv').open(encoding='utf-8-sig') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual([row['result'] for row in rows], ['NG','OK'])
        self.assertEqual(rows[-1]['ir_off'], 'OK')

    def test_existing_csv_gains_ir_columns_without_losing_history(self):
        with (self.store.root/'result.csv').open('w',encoding='utf-8-sig',newline='') as stream:
            writer = csv.DictWriter(stream,fieldnames=['record_id','result','carton'])
            writer.writeheader();writer.writerow({'record_id':'old','result':'NG','carton':'0012'})
        self.store.finish(self.mac, 'OK')
        with (self.store.root/'result.csv').open(encoding='utf-8-sig') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows[0]['record_id'], 'old')
        self.assertIn('ir_on', rows[1])

    def test_user_retry_corrects_existing_record_without_duplicate_append(self):
        self.store.finish(self.mac, "ERROR")
        self.store.finish(self.mac, "OK")
        with (self.store.root / "result.csv").open(encoding="utf-8-sig") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["result"], "OK")
        self.assertEqual(self.store.carton_count("0012"), 1)

    def test_slow_external_post_does_not_lock_production_state(self):
        self.store.finish(self.mac, "OK")
        entered, release = threading.Event(), threading.Event()
        def slow_sender(row):
            entered.set()
            release.wait(3)
        worker = threading.Thread(target=self.store.deliver, args=(slow_sender,))
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            done = threading.Event()
            def save_step():
                self.store.update(self.mac, step="DONE")
                done.set()
            saver = threading.Thread(target=save_step)
            saver.start()
            self.assertTrue(done.wait(1), "POST blocks production state")
            saver.join(2)
        finally:
            release.set()
            worker.join(3)
