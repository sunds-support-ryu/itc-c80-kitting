"""Durable MAC-keyed jobs, final CSV and an independent delivery outbox."""
import csv
from datetime import datetime
import json
import os
from pathlib import Path
import re
import threading
import time
import logging
import uuid

TERMINAL = {"OK", "NG", "ERROR"}


def key(value):
    value = re.sub(r"[:-]", "", value).upper()
    if not re.fullmatch(r"[0-9A-F]{12}", value):
        raise ValueError("MACが不正です")
    return value


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    for attempt in range(7):
        try:
            os.replace(temporary, path)
            break
        except PermissionError:
            if attempt == 6:
                raise
            time.sleep(.05 * (attempt + 1))


class JobStore:
    fields = ["timestamp", "record_id", "carton", "camera_slot", "mac", "sn", "old_ip", "new_ip",
              "result", "appearance", "network", "rtsp", "ir_cut", "settings", "tool_version"]

    def __init__(self, base):
        self.root = Path(base) / "data"
        self.path = self.root / "current_job.json"
        self.lock = threading.RLock()
        self.post_lock = threading.RLock()
        self.delivery_lock = threading.Lock()
        self.save_error = ""
        self.dirty = False
        self.pending_results = {}
        self.job = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else None
        self.root.mkdir(parents=True, exist_ok=True)
        if not (self.root / "pending_post.json").exists():
            atomic(self.root / "pending_post.json", {"records": []})

    def save(self):
        self.job["updated_at"] = datetime.now().isoformat(timespec="seconds")
        try:
            atomic(self.path, self.job)
            self.dirty = False
            self.save_error = ""
        except PermissionError as error:
            self.dirty = True
            self.save_error = str(error)
            logging.getLogger("camera_inspection").error("作業状態の保存待ち: %s", error)

    def flush_pending(self):
        with self.lock:
            if self.dirty and self.job:
                self.save()
            for identity, values in list(self.pending_results.items()):
                self.finish(identity, *values)

    def unfinished(self):
        return bool(self.job and (not self.job.get("devices") or any(
            d["status"] not in TERMINAL for d in self.job["devices"].values())))

    def archive(self, status):
        with self.lock:
            if self.job:
                name = datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_carton" + self.job["carton"] + "_" + status + ".json"
                atomic(self.root / "history" / name, self.job)
                if self.path.exists():
                    self.path.unlink()
                self.job = None

    def create(self, carton, mode, base_ip, maximum=12):
        if not re.fullmatch(r"[0-9A-Za-z_-]{1,32}", carton):
            raise ValueError("Carton番号を入力してください（英数字・ハイフン）")
        with self.lock:
            if self.job:
                self.archive("incomplete" if self.unfinished() else "completed")
            self.job = {"job_id": uuid.uuid4().hex, "carton": carton, "mode": mode,
                        "base_ip": base_ip, "max_devices": maximum, "devices": {}}
            self.save()

    def device(self, mac, slot, sn, old_ip, new_ip):
        with self.lock:
            identity = key(mac)
            if identity not in self.job["devices"]:
                self.job["devices"][identity] = {"camera_slot": slot, "sn": sn, "old_ip": old_ip,
                    "new_ip": new_ip, "status": "WAITING", "step": "DISCOVERY", "results": {},
                    "record_id": self.job["job_id"] + "-" + self.job["carton"] + "-" + identity}
                self.save()
            return self.job["devices"][identity]

    def update(self, mac, **changes):
        with self.lock:
            if not self.job or key(mac) not in self.job["devices"]:
                return
            device = self.job["devices"][key(mac)]
            results = changes.pop("results", {})
            device["results"].update(results)
            device.update(changes)
            self.save()

    def finish(self, mac, result, detail=""):
        with self.lock:
            self.pending_results[key(mac)] = (result, detail)
            try:
                self._finish(mac, result, detail)
                self.pending_results.pop(key(mac), None)
            except PermissionError as error:
                self.save_error = str(error)
                logging.getLogger("camera_inspection").error("履歴の保存待ち: %s", error)

    def _finish(self, mac, result, detail=""):
        with self.lock:
            if not self.job or key(mac) not in self.job["devices"]:
                return
            self.update(mac, status=result, step="DONE", error=detail)
            device = self.job["devices"][key(mac)]
            row = {"timestamp": datetime.now().isoformat(timespec="seconds"), "record_id": device["record_id"],
                   "carton": self.job["carton"], "mac": key(mac), "result": result, "tool_version": "2.0.0",
                   **{field: device.get(field, "") for field in ("camera_slot", "sn", "old_ip", "new_ip")},
                   **device["results"]}
            path = self.root / "result.csv"
            rows = []
            if path.exists():
                with path.open(encoding="utf-8-sig", newline="") as stream:
                    rows = list(csv.DictReader(stream))
            if not any(r["record_id"] == row["record_id"] for r in rows):
                with path.open("a", encoding="utf-8-sig", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=self.fields, extrasaction="ignore")
                    if not rows:
                        writer.writeheader()
                    writer.writerow(row)
                    stream.flush()
                    os.fsync(stream.fileno())
            else:
                # A user-requested retry corrects the same formal record, without an append.
                for index, previous in enumerate(rows):
                    if previous["record_id"] == row["record_id"] and previous["result"] != result:
                        rows[index] = row
                        temporary = path.with_name(path.name + ".tmp")
                        with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
                            writer = csv.DictWriter(stream, fieldnames=self.fields, extrasaction="ignore")
                            writer.writeheader()
                            writer.writerows(rows)
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.replace(temporary, path)
                        break
            self.queue_post(row)

    def queue_post(self, row):
        with self.post_lock:
            path = self.root / "pending_post.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            if not any(r["record_id"] == row["record_id"] for r in data["records"]):
                data["records"].append({**row, "status": "PENDING", "retry_count": 0,
                                       "last_error": "", "last_retry": ""})
                atomic(path, data)
            else:
                for index, old in enumerate(data["records"]):
                    if old["record_id"] == row["record_id"] and old["result"] != row["result"]:
                        data["records"][index] = {**row, "status": "PENDING", "retry_count": 0,
                                                  "last_error": "", "last_retry": ""}
                        atomic(path, data)
                        break

    def deliver(self, sender):
        """Optional future POST adapter. Failure never changes production results."""
        with self.delivery_lock:
            path = self.root / "pending_post.json"
            with self.post_lock:
                data = json.loads(path.read_text(encoding="utf-8"))
            for row in data["records"]:
                if row["status"] == "SENT":
                    continue
                row["retry_count"] += 1
                row["last_retry"] = datetime.now().isoformat(timespec="seconds")
                self.delivery_status(row, "SENDING")
                try:
                    received = sender(dict(row))
                    row["last_response"] = received
                    row.update(status="SENT", last_error="")
                except Exception as error:
                    row["last_error"] = str(error)
                    row["last_response"] = getattr(error, "response", None)
                self.delivery_status(row, row["status"])
                with self.post_lock:
                    latest = json.loads(path.read_text(encoding="utf-8"))
                    for index, pending in enumerate(latest["records"]):
                        if pending["record_id"] == row["record_id"] and pending.get("result") == row.get("result"):
                            latest["records"][index] = row
                            break
                    atomic(path, latest)

    def delivery_status(self, row, status):
        with self.lock:
            device = self.job["devices"].get(key(row["mac"])) if self.job else None
            if device and device["record_id"] == row["record_id"]:
                self.update(row["mac"], post_status=status)

    def carton_count(self, carton):
        path = self.root / "result.csv"
        if not path.exists():
            return 0
        with path.open(encoding="utf-8-sig", newline="") as stream:
            return sum(r["carton"] == carton and r["result"] == "OK" for r in csv.DictReader(stream))
