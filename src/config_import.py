'Import config after inspection using Web status or legacy RSA/AES.'
import base64
import http.cookiejar
import json
import os
import re
import time
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid

CONFIG_PASSWORD = os.environ.get("ITC_CONFIG_PASSWORD", "")
FINAL_IP = "192.168.5.190"
# Post-config verification account from the existing config writer.
VERIFY_CREDENTIALS = (os.environ.get("ITC_AFTER_CAMERA_USER", "admin"), os.environ.get("ITC_AFTER_CAMERA_PASSWORD", ""))
INFO_PATH = "/cgi-bin/admin/admin.cgi?action=get.system.information&format=json"
RESET_PATH = "/cgi-bin/admin/admin.cgi?action=set.system.reset&format=json"


class Step3Error(Exception):
    pass


def load_config(base_dir):
    folder = os.path.join(base_dir, "config file")
    if not os.path.isdir(folder):
        raise Step3Error('Config file folder missing.')
    files = sorted(os.path.join(folder, name) for name in os.listdir(folder)
                   if os.path.isfile(os.path.join(folder, name)) and not name.startswith("."))
    if len(files) != 1:
        raise Step3Error('Place exactly one config file in the config file folder.')
    with open(files[0], "rb") as source:
        content = source.read()
    if not content:
        raise Step3Error('Config file is empty.')
    return files[0], content


def request(url, method="GET", body=None, headers=None, credentials=None, cookie_jar=None):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, response_headers, newurl):
            return None
    passwords = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    passwords.add_password(None, url, *credentials)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                        urllib.request.HTTPCookieProcessor(cookie_jar),
                                        urllib.request.HTTPDigestAuthHandler(passwords),
                                        urllib.request.HTTPBasicAuthHandler(passwords))
    parsed = urllib.parse.urlsplit(url)
    request_headers = {"User-Agent": "Mozilla/5.0", "Referer": f"{parsed.scheme}://{parsed.netloc}/",
                       "X-Requested-With": "XMLHttpRequest", "X-From": "Web"}
    request_headers.update(headers or {})
    req = urllib.request.Request(url, data=body, method=method, headers=request_headers)
    try:
        with opener.open(req, timeout=30) as response:
            return response.status, response.read().decode("utf-8-sig", errors="replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", errors="replace")


def serial_number(data):
    if isinstance(data, dict):
        for key, value in data.items():
            if key.lower() in ("sncode", "sn", "serialnumber", "serial_number") and isinstance(value, str):
                return value.strip()
        for value in data.values():
            found = serial_number(value)
            if found:
                return found
    return None


def verify_identity(base, camera, credentials, transport):
    status, body = transport(base + INFO_PATH, credentials=credentials)
    if status != 200:
        raise Step3Error(f'Verification login failed HTTP={status}')
    try:
        serial = serial_number(json.loads(body))
    except ValueError:
        raise Step3Error('Identity response is not JSON.')
    if serial != camera.sn:
        raise Step3Error('SN mismatch. Do not write or reset a different device.')


def create_transport():
    import requests
    from requests.auth import HTTPBasicAuth, HTTPDigestAuth
    session = requests.Session()
    session.trust_env = False
    session.headers.update({"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest", "X-From": "Web"})
    warmed = set()
    auth_handlers = {}
    def transport(url, method="GET", body=None, headers=None, credentials=None):
        parsed = urllib.parse.urlsplit(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        session.headers["Referer"] = base + "/"
        if credentials not in auth_handlers:
            auth_handlers[credentials] = HTTPDigestAuth(*credentials)
        session.auth = auth_handlers[credentials]
        if base not in warmed:
            try:
                with session.get(base + "/", timeout=3, allow_redirects=False):
                    pass
            except requests.RequestException:
                pass
            warmed.add(base)
        try:
            response = session.request(method, url, data=body, headers=headers or {},
                                       timeout=60 if method == "POST" else 10, allow_redirects=False)
            if response.status_code == 401 and "basic" in response.headers.get("WWW-Authenticate", "").lower():
                response.close()
                response = session.request(method, url, data=body, headers=headers or {},
                                           auth=HTTPBasicAuth(*credentials), timeout=30, allow_redirects=False)
            with response:
                return response.status_code, response.content.decode("utf-8-sig", errors="replace")
        except requests.RequestException as error:
            detail = str(error)
            if credentials and credentials[1]:
                detail = detail.replace(credentials[1], "***")
            raise urllib.error.URLError(f"Camera transport failed: {type(error).__name__}: {detail}") from error
    transport.close = session.close
    return transport


def normalize_mac(value):
    value = re.sub(r"[:-]", "", str(value).strip()).lower()
    if not re.fullmatch(r"[0-9a-f]{12}", value) or value in ("000000000000", "ffffffffffff"):
        raise Step3Error('Invalid verification MAC.')
    return ":".join(value[i:i + 2] for i in range(0, 12, 2))


_settings_lock = threading.Lock()


def load_settings(base_dir):
    try:
        with open(os.path.join(base_dir, "records", "settings.json"), encoding="utf-8") as source:
            data = json.load(source)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(base_dir, changes):
    with _settings_lock:
        data = load_settings(base_dir)
        data.update(changes)
        folder = os.path.join(base_dir, "records")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "settings.json")
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as destination:
            json.dump(data, destination, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    return data


def discover_interfaces():
    from scapy.all import get_if_addr, get_if_hwaddr, get_working_ifaces
    found = []
    for interface in get_working_ifaces():
        try:
            address = get_if_addr(interface)
            if address.startswith("127."):
                continue
            found.append({"id": getattr(interface, "network_name", str(interface)),
                          "name": getattr(interface, "description", str(interface)),
                          "ip": address, "mac": normalize_mac(get_if_hwaddr(interface))})
        except (ValueError, OSError, Step3Error):
            continue
    if not found:
        raise Step3Error('No network adapter found.')
    return found


def create_ip_checker(base_dir=None):
    'Verify MAC/IP using Scapy and Npcap without transmitting an IP-change SET.'
    try:
        from scapy.all import ARP, Ether, conf, get_if_addr, get_if_hwaddr, get_working_ifaces, srp
        if os.name == "nt" and not conf.use_pcap:
            raise Step3Error('Npcap is required for MAC/IP verification.')
        settings = load_settings(base_dir) if base_dir else {}
        saved = settings.get("network_interfaces", [])
        if not saved:
            saved = discover_interfaces()
            if base_dir:
                settings = save_settings(base_dir, {"network_interfaces": saved})
        selected = settings.get("network_id", "")
        if base_dir and not selected:
            raise Step3Error('Select the MAC/IP verification adapter in settings.')
        interfaces = []
        for item in saved:
            if selected and item["id"] != selected:
                continue
            interface = item["id"]
            try:
                address = get_if_addr(interface)
                pc_mac = normalize_mac(get_if_hwaddr(interface))
                if pc_mac != item["mac"] or address == "0.0.0.0" or address.startswith("127."):
                    continue
                interfaces.append((interface, address, pc_mac))
            except (ValueError, OSError, Step3Error):
                continue
        if not interfaces:
            raise Step3Error('Saved network adapter is invalid. Rescan it in settings.')
    except ImportError:
        raise Step3Error('Scapy and Npcap are required for MAC/IP verification.')

    def checker(target_mac, target_ip):
        target_mac = normalize_mac(target_mac)
        for interface, address, pc_mac in interfaces:
            # Unicast to the original MAC: another camera on the same factory IP cannot pass.
            query = Ether(src=pc_mac, dst=target_mac) / ARP(
                op=1, hwsrc=pc_mac, psrc=address, hwdst=target_mac, pdst=target_ip)
            replies, _ = srp(query, iface=interface, timeout=1, verbose=False)
            for _, reply in replies:
                if reply.haslayer(ARP) and reply.haslayer(Ether):
                    arp = reply[ARP]
                    if (int(arp.op) == 2 and str(arp.psrc) == target_ip and
                            normalize_mac(arp.hwsrc) == target_mac and
                            normalize_mac(reply[Ether].src) == target_mac and
                            normalize_mac(arp.hwdst) == pc_mac and str(arp.pdst) == address):
                        return True
        return False
    return checker


def run(camera, base_dir, credentials, log=lambda text: None, transport=None,
        verify_timeout=90, stopped=lambda: False, ip_checker=None, final_timeout=120, reset_ip=True,
        credential_report=None):
    'Synchronous worker; never retry the upload POST.'
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.asymmetric import rsa, padding as rsa_padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    stage = 'Check config file'
    owns_transport = transport is None
    try:
        transport = transport or create_transport()
        config_password = load_settings(base_dir).get("config_import_password", CONFIG_PASSWORD)
        if not isinstance(config_password, str) or not config_password:
            raise Step3Error('Configure the config import password.')
        path, content = load_config(base_dir)
        target_mac = normalize_mac(camera.mac)
        if reset_ip:
            ip_checker = ip_checker or create_ip_checker(base_dir)
        if stopped():
            raise Step3Error('Operation cancelled')
        base = f"http://{camera.ip}"
        stage = 'Verify SN before upload'
        log(stage)
        verify_identity(base, camera, credentials, transport)
        stage = 'Import config'
        log(f"{stage} · {os.path.basename(path)}")
        status, status_body = transport(base + "/vb.htm?&getimportstatus", credentials=credentials)
        modern = status == 200 and re.search(r"OK getimportstatus=([0-3])", status_body) is not None
        if modern:
            log('Web import protocol - status verification enabled')
            status, cleared = transport(base + "/vb.htm?setimportstatus=0", credentials=credentials)
            if status != 200 or "OK" not in cleared:
                raise Step3Error('Cannot clear import status. Upload not sent.')
            status, cleared = transport(base + "/vb.htm?&getimportstatus", credentials=credentials)
            if status != 200 or not re.search(r"OK getimportstatus=0(?:\s|$)", cleared):
                raise Step3Error('Import status is not zero. Upload not sent.')
            params = {"up": "cfg", "pwd": config_password}
        else:
            log('Legacy RSA/AES import protocol')
            private = rsa.generate_private_key(public_exponent=3, key_size=1024)
            modulus = private.public_key().public_numbers().n.to_bytes(128, "big")
            public = base64.b64encode(base64.b64encode(modulus)).decode()
            status, body = transport(base + "/challenge?" + urllib.parse.urlencode({"PublicKey": public}),
                                     credentials=credentials)
            if status != 200:
                raise Step3Error(f"Handshake HTTP={status}")
            key = re.search(r"<key>(.*?)</key>", body, re.S)
            key_id = re.search(r"<keyID>(.*?)</keyID>", body, re.S)
            if not key or not key_id:
                raise Step3Error('Handshake key/keyID missing')
            encrypted = bytes.fromhex(base64.b64decode(key.group(1)).decode())
            aes = private.decrypt(encrypted, rsa_padding.PKCS1v15())
            padder = padding.PKCS7(128).padder()
            padded = padder.update(config_password.encode()) + padder.finalize()
            cipher = Cipher(algorithms.AES(aes[:16]), modes.CBC(b"0123456789ABCDEF"))
            encryptor = cipher.encryptor()
            password = base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode()
            params = {"up": "cfg", "keyId": key_id.group(1), "encryptPwd": password}
        boundary = "itc-" + uuid.uuid4().hex
        payload = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{os.path.basename(path)}"\r\n'
                   'Content-Type: application/octet-stream\r\n\r\n').encode() + content + f"\r\n--{boundary}--\r\n".encode()
        if stopped():
            raise Step3Error('Operation cancelled')
        try:
            status, body = transport(base + "/dataloader.cgi?" + urllib.parse.urlencode(params), "POST", payload,
                                     {"Content-Type": f"multipart/form-data; boundary={boundary}"}, credentials)
        except (urllib.error.URLError, TimeoutError, OSError):
            raise Step3Error('Upload response missing. Outcome unknown; no reset or resend.')
        if status != 200:
            raise Step3Error(f'Config rejected HTTP={status}')
        if modern:
            deadline = time.monotonic() + verify_timeout
            while time.monotonic() < deadline and not stopped():
                status, result = transport(base + "/vb.htm?&getimportstatus", credentials=credentials)
                match = re.search(r"OK getimportstatus=([0-3])", result) if status == 200 else None
                if match:
                    code = int(match.group(1))
                    if code in (2, 3):
                        reason = 'Import password rejected' if code == 2 else 'Config file format rejected'
                        raise Step3Error(f'getimportstatus={code} / {reason}. Reset not sent.')
                    if code == 1:
                        # Match the maintenance UI's success acknowledgement.
                        log('Import succeeded - acknowledge status')
                        try:
                            status, acknowledged = transport(base + "/vb.htm?setimportstatus=0", credentials=credentials)
                            if status in (401, 403):
                                log('Authentication changed - verify using post-config credentials')
                            elif status != 200 or "OK" not in acknowledged:
                                raise Step3Error(f'Apply acknowledgement failed after import HTTP={status}. No resend.')
                        except (urllib.error.URLError, TimeoutError, OSError):
                            # Applying config can close the connection. Verify, never repeat upload.
                            log('Apply acknowledgement closed connection - verify post-config credentials')
                        break
                time.sleep(1)
            else:
                raise Step3Error('Import success unverified. No reset or resend.')
        else:
            if "upload_failed" in body:
                raise Step3Error('Config rejected HTTP=200 / upload_failed. Reset not sent.')
            if "OnUploadSus" not in body:
                raise Step3Error('Upload success unverified. Reset not sent.')
        stage = 'Verify config application and SN'
        log(stage)
        deadline = time.monotonic() + verify_timeout
        verified_credentials = None
        last_verification_error = 'Waiting for response'
        while time.monotonic() < deadline and not stopped():
            try:
                verify_identity(base, camera, VERIFY_CREDENTIALS, transport)
                verified_credentials = VERIFY_CREDENTIALS
                break
            except Step3Error as error:
                if 'SN mismatch' in str(error):
                    raise
                if str(error) != last_verification_error:
                    log('Waiting for post-config authentication -' + str(error))
                last_verification_error = str(error)
                if modern and "HTTP=401" in str(error):
                    # Some exported configs preserve users. Only accept the original
                    # login after firmware readiness AND the same SN are confirmed.
                    try:
                        ready_status, ready_body = transport(base + "/vb.htm?&reloadflag", credentials=credentials)
                        if ready_status == 200 and "OK reloadflag" in ready_body:
                            verify_identity(base, camera, credentials, transport)
                            verified_credentials = credentials
                            log('Import and SN verified - login unchanged; continue verified credentials')
                            break
                    except (Step3Error, urllib.error.URLError, TimeoutError, OSError):
                        pass
            except (urllib.error.URLError, TimeoutError, OSError):
                last_verification_error = 'HTTP connection has not recovered after upload'
            time.sleep(1)
        if verified_credentials is None:
            raise Step3Error('Config application unverified. option=2 not sent. Last check:' + last_verification_error)
        if credential_report:
            credential_report(verified_credentials)
        if stopped():
            raise Step3Error('Operation cancelled')
        if not reset_ip:
            return True, 'Config and SN verified. Waiting for label confirmation.'
        stage = "IP Reset option=2"
        log(stage)
        status, body = transport(base + RESET_PATH, "POST", json.dumps({"options": 2}).encode(),
                                 {"Content-Type": "application/json"}, verified_credentials)
        if not 200 <= status < 300:
            raise Step3Error(f"IP Reset HTTP={status}")
        stage = f'Waiting for reboot - MAC verification -> {FINAL_IP}'
        log(stage)
        deadline = time.monotonic() + final_timeout
        while time.monotonic() < deadline and not stopped():
            if ip_checker(target_mac, FINAL_IP):
                return True, f'Reboot completed - MAC matched / IP={FINAL_IP} verified'
            time.sleep(1)
        if stopped():
            raise Step3Error('Operation cancelled. Final IP unverified.')
        raise Step3Error(f'MAC-matched{FINAL_IP}could not be verified. No reset resend.')
    except Exception as error:
        # Never log URLs containing import passwords or access credentials.
        detail = str(error) if isinstance(error, Step3Error) else type(error).__name__
        return False, f"{stage}: {detail}"
    finally:
        if owns_transport and hasattr(transport, "close"):
            transport.close()
