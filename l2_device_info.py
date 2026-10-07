"""Read-only Milesight Smart Tools Get DeviceInfo via EtherType 0x0809.

Use saved interface, or pass --interface with a Scapy/Npcap interface ID.
No IP configuration or SET packet is sent.
"""
import argparse
import importlib.util
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, BASE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interface", help="Scapy/Npcap interface ID; defaults to saved interface")
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()
    network = load_module("l2_info_network", "network_workflow.py")
    settings_api = load_module("l2_info_settings", "step3.py")
    settings = settings_api.load_settings(str(BASE))
    interface_id = args.interface or settings.get("network_id")
    if not interface_id:
        parser.error("Select an interface in settings, or pass --interface")
    interface = next((item for item in settings.get("network_interfaces", []) if item["id"] == interface_id), None)
    if interface is None:
        interface = next((item for item in settings_api.discover_interfaces() if item["id"] == interface_id), None)
    if interface is None:
        parser.error("Interface not found; refresh interface settings")
    devices = network.Link(interface, verify_ip=False).discover()
    if not args.no_cache:
        journal = network.Journal(str(BASE))
        for device in devices:
            journal.remember({**device, "interface_id": interface_id})
    print(json.dumps(devices, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
