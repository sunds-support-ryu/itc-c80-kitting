"""Selected-interface discovery, Layer-2 IP assignment and durable IP reservations.

SET/ACK framing follows ../mac_change_ip.py. Discovery announcements are retained
as raw info: uncertain fields never qualify a device for automatic relocation.
"""
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time

MAGIC = bytes.fromhex("20120610")
ETHER_TYPE = 0x0809
GET_DEVICE_INFO = b"\x00\x00"
ANNOUNCE, SET, ACK = b"\x00\x01", b"\x00\x02", b"\x00\x03"


def mac(value):
    value = re.sub(r"[:-]", "", str(value)).lower()
    if not re.fullmatch(r"[0-9a-f]{12}", value):
        raise ValueError("MACが不正です")
    return ":".join(value[i:i + 2] for i in range(0, 12, 2))


def prefix(value):
    value = str(value).strip().rstrip(".")
    parts = value.split(".")
    if len(parts) != 3:
        raise ValueError("IP前3桁を入力してください（例:192.168.0）")
    ipaddress.IPv4Address(value + ".1")
    return value


def network_config(settings, interface):
    interface = select_work_interface(settings, interface)
    pc = ipaddress.IPv4Address(interface["ip"])
    pc_network = ipaddress.IPv4Network(f"{pc}/{interface.get('prefix_length', 24)}", strict=False)
    work_prefix = prefix(settings.get("work_prefix") or str(pc).rsplit(".", 1)[0])
    work_start, work_end = int(settings.get("work_start", 150)), int(settings.get("work_end", 189))
    target_prefix = prefix(settings.get("target_prefix", "192.168.0"))
    start, end, first = (int(settings.get(key, default)) for key, default in (
        ("target_start", 215), ("target_end", 254), ("target_next", 220)))
    if not 1 <= work_start <= work_end <= 254 or not 1 <= start <= first <= end <= 254:
        raise ValueError("IP範囲・開始番号を確認してください（1～254）")
    work = [f"{work_prefix}.{n}" for n in range(work_start, work_end + 1)]
    if any(ipaddress.IPv4Address(ip) not in pc_network for ip in work):
        raise ValueError("作業IPは選択したPCネットワーク内に設定してください")
    work = [ip for ip in work if ip != str(pc) and ip not in (str(pc_network.network_address), str(pc_network.broadcast_address))]
    targets = [f"{target_prefix}.{n}" for n in list(range(first, end + 1)) + list(range(start, first))]
    mask = str(settings.get("target_mask") or "255.255.255.0")
    gateway = str(settings.get("target_gateway") or target_prefix + ".1")
    target_network = ipaddress.IPv4Network(f"{targets[0]}/{mask}", strict=False)
    if ipaddress.IPv4Address(gateway) not in target_network or gateway in (str(target_network.network_address), str(target_network.broadcast_address)):
        raise ValueError("目標ゲートウェイは目標ネットワーク内に設定してください")
    if any(ipaddress.IPv4Address(ip) not in target_network or ip in (
            str(target_network.network_address), str(target_network.broadcast_address)) for ip in targets):
        raise ValueError("目標IP範囲とサブネットマスクが一致しません")
    if str(pc) in targets or set(work).intersection(targets):
        raise ValueError("作業IP・目標IP・PCのIPを重複させないでください")
    if interface.get("gateway") in work or gateway in targets:
        raise ValueError("ゲートウェイを割当範囲から除外してください")
    return {"work": work, "targets": targets, "mask": mask, "gateway": gateway,
            "work_mask": str(pc_network.netmask), "work_gateway": interface.get("gateway") or work_prefix + ".1"}


def windows_network_details():
    if os.name != "nt":
        return {}
    script = "Get-NetIPConfiguration | ForEach-Object { [pscustomobject]@{mac=$_.NetAdapter.MacAddress;ip=@($_.IPv4Address.IPAddress);prefix=@($_.IPv4Address.PrefixLength);gateway=$_.IPv4DefaultGateway.NextHop} } | ConvertTo-Json -Compress"
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True,
                                timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        data = json.loads(result.stdout.decode("utf-8-sig", errors="replace"))
        rows = data if isinstance(data, list) else [data]
        return {mac(item["mac"]): item for item in rows if item.get("mac")}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}


def enrich_interfaces(records):
    details = windows_network_details()
    for record in records:
        detail = details.get(mac(record["mac"]), {})
        addresses = detail.get("ip", [])
        lengths = detail.get("prefix", [])
        addresses = addresses if isinstance(addresses, list) else [addresses]
        lengths = lengths if isinstance(lengths, list) else [lengths]
        record['ipv4'] = [{'ip': address, 'prefix_length': int(lengths[index])}
                          for index, address in enumerate(addresses) if address and index < len(lengths)]
        record["prefix_verified"] = False
        # A saved interface IP can be stale after Windows network settings change.
        # Match the adapter by MAC, then refresh from its current IPv4 addresses.
        if record["ip"] not in addresses:
            candidates = [address for address in addresses if address and
                          not ipaddress.IPv4Address(address).is_link_local]
            if candidates:
                record["ip"] = candidates[0]
            elif len(addresses) == 1 and addresses[0]:
                record["ip"] = addresses[0]
        if record["ip"] in addresses:
            index = addresses.index(record["ip"])
            if index < len(lengths):
                record["prefix_length"] = lengths[index]
                record["prefix_verified"] = True
        record.setdefault("prefix_length", 24)
        gateways = detail.get("gateway") or ""
        record['gateways'] = gateways if isinstance(gateways, list) else [gateways] if gateways else []
        record["gateway"] = gateways[0] if isinstance(gateways, list) and gateways else gateways
    return records


def select_work_interface(settings, interface):
    """NIC identity stays fixed; choose one of its addresses for the work subnet."""
    result = dict(interface)
    addresses = interface.get('ipv4') or [{'ip': interface['ip'], 'prefix_length': interface.get('prefix_length', 24)}]
    requested = settings.get('work_prefix')
    if requested:
        segment = prefix(requested)
        first = ipaddress.IPv4Address(f"{segment}.{int(settings.get('work_start', 150))}")
        last = ipaddress.IPv4Address(f"{segment}.{int(settings.get('work_end', 189))}")
        candidates = [item for item in addresses if first in ipaddress.IPv4Network(f"{item['ip']}/{item['prefix_length']}", strict=False)
                      and last in ipaddress.IPv4Network(f"{item['ip']}/{item['prefix_length']}", strict=False)]
        if not candidates:
            raise ValueError('このネットワーク接続のIPから作業IPへ接続できません。Base IPを確認してください')
        candidates.sort(key=lambda item: (item['ip'].rsplit('.', 1)[0] != segment, -item['prefix_length']))
        selected = candidates[0]
    else:
        selected = next((item for item in addresses if item['ip'] == interface.get('ip')), addresses[0])
    result.update(ip=selected['ip'], prefix_length=selected['prefix_length'])
    selected_network = ipaddress.IPv4Network(f"{selected['ip']}/{selected['prefix_length']}", strict=False)
    gateways = interface.get('gateways', [interface.get('gateway', '')])
    result['gateway'] = next((gateway for gateway in gateways if gateway and ipaddress.IPv4Address(gateway) in selected_network), '')
    return result


def arp_source_ip(interface, fallback, target):
    address = ipaddress.IPv4Address(target)
    candidates = [item for item in interface.get('ipv4', []) if address in
                  ipaddress.IPv4Network(f"{item['ip']}/{item['prefix_length']}", strict=False)]
    return max(candidates, key=lambda item: item['prefix_length'])['ip'] if candidates else fallback


def parse_info(payload):
    """Return raw device info plus fields that are explicit in the response."""
    text = payload.split(b"\0", 1)[0].decode("ascii", errors="replace").strip()
    fields = {}
    parts = text.split(";")
    if len(parts) >= 16:
        try:
            ipaddress.IPv4Address(parts[0])
            port = int(parts[1])
            if not 1 <= port <= 65535:
                raise ValueError("HTTP port")
            ipaddress.IPv4Network(f"{parts[0]}/{parts[2]}", strict=False)
            ipaddress.IPv4Address(parts[3])
            ipaddress.IPv4Address(parts[4])
            fields.update(ip=parts[0], http_port=port, mask=parts[2], gateway=parts[3], dns=parts[4],
                          model=parts[5], firmware=parts[6], hardware_version=parts[7],
                          device_name=parts[8], oem_model=parts[9], device_type=parts[12],
                          active_status_raw=parts[13], sn=parts[15],
                          extra_fields={"field_10": parts[10], "field_11": parts[11], "field_14": parts[14]},
                          raw_fields=parts)
        except (ValueError, IndexError):
            pass
    for key, value in re.findall(r"(?:^|[;\n,])\s*(ip|model|sn|serial|mask|gateway|name)\s*[:=]\s*([^;\n,]+)", text, re.I):
        fields[key.lower()] = value.strip()
    if "ip" not in fields:
        candidates = re.findall(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])", text)
        for candidate in candidates[:1]:
            try:
                ipaddress.IPv4Address(candidate)
                fields["ip"] = candidate
            except ValueError:
                pass
    # Known product identifiers may be explicitly embedded in the announcement.
    if "model" not in fields:
        match = re.search(r"\b(?:NC\d{3,5}[A-Z0-9-]*|ITC-C\d+[A-Z0-9-]*)\b", text)
        if match:
            fields["model"] = match.group(0)
    return {"info": text, **fields}


def device_models(device):
    return {str(device[key]).strip() for key in ("model", "oem_model", "http_model") if device.get(key)}


def model_allowed(device, allowed):
    return bool(device_models(device).intersection(allowed))


class Journal:
    def __init__(self, base_dir):
        self.path = Path(base_dir) / "records" / "network_workflow.json"
        self.lock = threading.RLock()
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.data = {"devices": {}, "assignments": {}}
        except (ValueError, OSError):
            raise ValueError("IP割当記録を読めません。記録を確認してください")
        self.data.setdefault("devices", {})
        self.data.setdefault("assignments", {})
        self.data.setdefault("active_work", {})
        self.data.setdefault("used_targets", [])
        self.data.setdefault("counter_records", {})

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def remember(self, device):
        with self.lock:
            old = self.data["devices"].get(device["mac"], {})
            self.data["devices"][device["mac"]] = {**old, **device, "last_seen": time.time()}
            self.save()

    def reserve(self, device_mac, candidates, occupied):
        with self.lock:
            assignments = self.data["assignments"]
            if device_mac in assignments:
                return assignments[device_mac]["ip"]
            used = {entry["ip"] for entry in assignments.values()}
            for ip in candidates:
                if ip in used or occupied(ip):
                    continue
                assignments[device_mac] = {"ip": ip, "state": "sticker", "time": time.time()}
                self.save()
                return ip
            raise ValueError("目標IPの空きがありません")

    def hold_work(self, device_mac, ip, interface_id):
        with self.lock:
            device_mac = mac(device_mac)
            old = self.data["active_work"].get(device_mac)
            if old and (old["ip"] != ip or old["interface_id"] != interface_id):
                raise ValueError("実行中MACは別の作業IPへ変更できません")
            self.data["active_work"][device_mac] = old or {
                "ip": ip, "interface_id": interface_id, "time": time.time()}
            self.save()

    def begin_job(self):
        with self.lock:
            self.data["used_targets"] = sorted(set(self.data["used_targets"]) |
                {entry["ip"] for entry in self.data["assignments"].values()})
            self.data["assignments"] = {}
            self.save()

    def advance_counter(self, record_id, start, end, current):
        """Durably compute one cursor increment per final production record."""
        with self.lock:
            counters = self.data.setdefault("counter_records", {})
            if isinstance(counters, list):
                counters = self.data["counter_records"] = {}
            if record_id in counters:
                return None
            value = start if current >= end else current + 1
            counters[record_id] = value
            self.save()
            return value

    def release_work(self, device_mac):
        with self.lock:
            self.data["active_work"].pop(mac(device_mac), None)
            self.save()

    def forget_device(self, device_mac):
        with self.lock:
            identity = mac(device_mac)
            self.data['active_work'].pop(identity, None)
            self.data['assignments'].pop(identity, None)
            self.save()

    def transition(self, device_mac, state):
        with self.lock:
            self.data["assignments"][device_mac]["state"] = state
            self.save()


class Link:
    def __init__(self, interface, stopped=lambda: False, verify_ip=True):
        from scapy.all import ARP, Ether, Raw, AsyncSniffer, sendp, srp, get_if_addr, get_if_hwaddr
        self.ARP, self.Ether, self.Raw = ARP, Ether, Raw
        self.AsyncSniffer, self.sendp, self.srp = AsyncSniffer, sendp, srp
        self.interface, self.stopped = interface, stopped
        self.id = interface["id"]
        self.pc_mac = mac(get_if_hwaddr(self.id))
        self.pc_ip = interface['ip'] if verify_ip else get_if_addr(self.id)
        if self.pc_mac != mac(interface["mac"]):
            raise ValueError("選択したネットワーク接続のMACが変更されています。ネットワークを再検索してください")

    def arp(self, ip, target_mac=None):
        source_ip = arp_source_ip(getattr(self, 'interface', {}), self.pc_ip, ip)
        packet = self.Ether(src=self.pc_mac, dst=target_mac or "ff:ff:ff:ff:ff:ff") / self.ARP(
            op=1, hwsrc=self.pc_mac, psrc=source_ip, hwdst=target_mac or "00:00:00:00:00:00", pdst=ip)
        replies, _ = self.srp(packet, iface=self.id, timeout=.6, verbose=False)
        found = []
        for _, reply in replies:
            if reply.haslayer(self.ARP) and reply.haslayer(self.Ether):
                arp = reply[self.ARP]
                source = mac(arp.hwsrc)
                if (int(arp.op) == 2 and str(arp.psrc) == ip and mac(reply[self.Ether].src) == source
                        and str(arp.pdst) == source_ip and mac(arp.hwdst) == self.pc_mac
                        and (target_mac is None or source == target_mac)):
                    found.append(source)
        return found

    def discover(self):
        devices = {}
        sniffer = self.AsyncSniffer(iface=self.id, filter="ether proto 0x0809", store=True)
        sniffer.start()
        try:
            time.sleep(.15)
            # Smart Tools Get DeviceInfo: exactly 6 header bytes + 240 zero bytes.
            query = (MAGIC + GET_DEVICE_INFO).ljust(246, b"\0")
            self.sendp(self.Ether(src=self.pc_mac, dst="ff:ff:ff:ff:ff:ff", type=ETHER_TYPE) /
                       self.Raw(query), iface=self.id, verbose=False)
            time.sleep(1)
        finally:
            packets = sniffer.stop()
        for packet in packets:
            source = mac(packet[self.Ether].src)
            raw = bytes(packet[self.Ether].payload)
            if source == self.pc_mac or raw[:4] != MAGIC:
                continue
            if raw[4:6] == ACK and len(raw) >= 13 and mac(raw[6:12].hex()) == self.pc_mac:
                info = parse_info(raw[13:])
            elif raw[4:6] == ANNOUNCE:
                # Get DeviceInfo responses: 00 01, semicolon fields start at offset 6.
                info = parse_info(raw[6:])
            else:
                continue
            if info.get("info"):
                devices[source] = {"mac": source, **info}
        return list(devices.values())

    def set_ip(self, device_mac, ip, mask, gateway, credentials, name="Network Camera"):
        device_mac = mac(device_mac)
        ipaddress.IPv4Address(ip)
        ipaddress.IPv4Network(f"{ip}/{mask}", strict=False)
        ipaddress.IPv4Address(gateway)
        values = [ip, "80", mask, gateway, gateway, name, *credentials]
        if any(";" in str(value) for value in values):
            raise ValueError("ネットワーク設定にセミコロンは使用できません")
        payload = MAGIC + SET + bytes.fromhex(device_mac.replace(":", "")) + ";".join(values).encode("ascii")
        if len(payload) > 246:
            raise ValueError("IP変更パケットが長すぎます")
        sniffer = self.AsyncSniffer(iface=self.id, filter="ether proto 0x0809", store=True)
        sniffer.start()
        try:
            time.sleep(.15)
            if self.stopped():
                raise ValueError("処理中止")
            self.sendp(self.Ether(src=self.pc_mac, dst="ff:ff:ff:ff:ff:ff", type=ETHER_TYPE) /
                       self.Raw(payload.ljust(246, b"\0")), iface=self.id, verbose=False)
            time.sleep(1)
        finally:
            packets = sniffer.stop()
        for packet in packets:
            if mac(packet[self.Ether].src) != device_mac:
                continue
            raw = bytes(packet[self.Ether].payload)
            if raw[:6] == MAGIC + ACK and len(raw) >= 13 and mac(raw[6:12].hex()) == self.pc_mac:
                if raw[12] != 1:
                    raise ValueError("カメラがIP変更を拒否しました")
                return parse_info(raw[13:])
        # Lost ACK is uncertain: caller checks actual MAC/IP, never resends blindly.
        return None

    def wait_ip(self, device_mac, ip, timeout=40):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self.stopped():
            if self.arp(ip, device_mac):
                return True
            time.sleep(.5)
        return False
