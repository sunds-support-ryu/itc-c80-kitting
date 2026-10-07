"""検査OK後の設定書込。Web状態API対応機はpwd方式、旧機種はRSA/AES方式を使用。"""
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
# 既存config_writer.pyで使用する、設定適用後の確認用アカウント。
VERIFY_CREDENTIALS = (os.environ.get("ITC_AFTER_CAMERA_USER", "admin"), os.environ.get("ITC_AFTER_CAMERA_PASSWORD", ""))
INFO_PATH = "/cgi-bin/admin/admin.cgi?action=get.system.information&format=json"
RESET_PATH = "/cgi-bin/admin/admin.cgi?action=set.system.reset&format=json"


class Step3Error(Exception):
    pass


def load_config(base_dir):
    folder = os.path.join(base_dir, "config file")
    if not os.path.isdir(folder):
        raise Step3Error("config fileフォルダがありません。")
    files = sorted(os.path.join(folder, name) for name in os.listdir(folder)
                   if os.path.isfile(os.path.join(folder, name)) and not name.startswith("."))
    if len(files) != 1:
        raise Step3Error("config fileには設定ファイルを1個だけ置いてください。")
    with open(files[0], "rb") as source:
        content = source.read()
    if not content:
        raise Step3Error("設定ファイルが空です。")
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
        raise Step3Error(f"確認ログイン失敗 HTTP={status}")
    try:
        serial = serial_number(json.loads(body))
    except ValueError:
        raise Step3Error("確認応答がJSONではありません。")
    if serial != camera.sn:
        raise Step3Error("SN不一致。別の機器には書込・Resetしません。")


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
        raise Step3Error("確認用MACが不正です。")
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
        raise Step3Error("ネットワーク接続が見つかりません。")
    return found


def create_ip_checker(base_dir=None):
    """MAC変更ツール同様、Npcap/Scapyで二層確認。IP変更SETは送信しない。"""
    try:
        from scapy.all import ARP, Ether, conf, get_if_addr, get_if_hwaddr, get_working_ifaces, srp
        if os.name == "nt" and not conf.use_pcap:
            raise Step3Error("MAC/IP確認にはNpcapが必要です。")
        settings = load_settings(base_dir) if base_dir else {}
        saved = settings.get("network_interfaces", [])
        if not saved:
            saved = discover_interfaces()
            if base_dir:
                settings = save_settings(base_dir, {"network_interfaces": saved})
        selected = settings.get("network_id", "")
        if base_dir and not selected:
            raise Step3Error("設定でMAC/IP確認用のネットワーク接続を指定してください。")
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
            raise Step3Error("保存済みネットワーク接続が無効です。設定から再検索してください。")
    except ImportError:
        raise Step3Error("MAC/IP確認にはScapyとNpcapが必要です。")

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
    """同期処理。Step2は専用workerからto_threadで呼ぶ。書込POSTは再試行しない。"""
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.asymmetric import rsa, padding as rsa_padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    stage = "設定ファイル確認"
    owns_transport = transport is None
    try:
        transport = transport or create_transport()
        config_password = load_settings(base_dir).get("config_import_password", CONFIG_PASSWORD)
        if not isinstance(config_password, str) or not config_password:
            raise Step3Error("configファイルの導入パスワードを設定してください")
        path, content = load_config(base_dir)
        target_mac = normalize_mac(camera.mac)
        if reset_ip:
            ip_checker = ip_checker or create_ip_checker(base_dir)
        if stopped():
            raise Step3Error("処理中止")
        base = f"http://{camera.ip}"
        stage = "書込前のSN確認"
        log(stage)
        verify_identity(base, camera, credentials, transport)
        stage = "設定書込"
        log(f"{stage} · {os.path.basename(path)}")
        status, status_body = transport(base + "/vb.htm?&getimportstatus", credentials=credentials)
        modern = status == 200 and re.search(r"OK getimportstatus=([0-3])", status_body) is not None
        if modern:
            log("Web導入プロトコル · 状態確認あり")
            status, cleared = transport(base + "/vb.htm?setimportstatus=0", credentials=credentials)
            if status != 200 or "OK" not in cleared:
                raise Step3Error("導入状態を初期化できません。書込未実行。")
            status, cleared = transport(base + "/vb.htm?&getimportstatus", credentials=credentials)
            if status != 200 or not re.search(r"OK getimportstatus=0(?:\s|$)", cleared):
                raise Step3Error("導入状態が0ではありません。書込未実行。")
            params = {"up": "cfg", "pwd": config_password}
        else:
            log("旧RSA/AES導入プロトコル")
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
                raise Step3Error("Handshake key/keyIDなし")
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
            raise Step3Error("処理中止")
        try:
            status, body = transport(base + "/dataloader.cgi?" + urllib.parse.urlencode(params), "POST", payload,
                                     {"Content-Type": f"multipart/form-data; boundary={boundary}"}, credentials)
        except (urllib.error.URLError, TimeoutError, OSError):
            raise Step3Error("書込応答なし。結果不明のためReset未実行・再送なし。")
        if status != 200:
            raise Step3Error(f"設定書込拒否 HTTP={status}")
        if modern:
            deadline = time.monotonic() + verify_timeout
            while time.monotonic() < deadline and not stopped():
                status, result = transport(base + "/vb.htm?&getimportstatus", credentials=credentials)
                match = re.search(r"OK getimportstatus=([0-3])", result) if status == 200 else None
                if match:
                    code = int(match.group(1))
                    if code in (2, 3):
                        reason = "導入パスワード検証拒否" if code == 2 else "設定ファイル形式拒否"
                        raise Step3Error(f"getimportstatus={code} / {reason}。Reset未実行。")
                    if code == 1:
                        # Match the maintenance UI's success acknowledgement.
                        log("導入成功 · 状態確認完了通知")
                        try:
                            status, acknowledged = transport(base + "/vb.htm?setimportstatus=0", credentials=credentials)
                            if status in (401, 403):
                                log("認証が切り替わりました · 書込後認証で確認")
                            elif status != 200 or "OK" not in acknowledged:
                                raise Step3Error(f"導入成功後の適用通知失敗 HTTP={status}。再送なし。")
                        except (urllib.error.URLError, TimeoutError, OSError):
                            # Applying config can close the connection. Verify, never repeat upload.
                            log("適用通知後の接続終了 · 書込後認証で確認")
                        break
                time.sleep(1)
            else:
                raise Step3Error("導入状態の成功を確認できません。Reset未実行・再送なし。")
        else:
            if "upload_failed" in body:
                raise Step3Error("設定書込拒否 HTTP=200 / upload_failed。Reset未実行。")
            if "OnUploadSus" not in body:
                raise Step3Error("書込成功を確認できません。Reset未実行。")
        stage = "設定適用・SN確認"
        log(stage)
        deadline = time.monotonic() + verify_timeout
        verified_credentials = None
        last_verification_error = "応答待ち"
        while time.monotonic() < deadline and not stopped():
            try:
                verify_identity(base, camera, VERIFY_CREDENTIALS, transport)
                verified_credentials = VERIFY_CREDENTIALS
                break
            except Step3Error as error:
                if "SN不一致" in str(error):
                    raise
                if str(error) != last_verification_error:
                    log("書込後認証の確認待ち · " + str(error))
                last_verification_error = str(error)
                if modern and "HTTP=401" in str(error):
                    # Some exported configs preserve users. Only accept the original
                    # login after firmware readiness AND the same SN are confirmed.
                    try:
                        ready_status, ready_body = transport(base + "/vb.htm?&reloadflag", credentials=credentials)
                        if ready_status == 200 and "OK reloadflag" in ready_body:
                            verify_identity(base, camera, credentials, transport)
                            verified_credentials = credentials
                            log("導入成功・SN一致 · ログイン情報は変更されていません。確認済み認証を継続")
                            break
                    except (Step3Error, urllib.error.URLError, TimeoutError, OSError):
                        pass
            except (urllib.error.URLError, TimeoutError, OSError):
                last_verification_error = "書込後のHTTP接続未復帰"
            time.sleep(1)
        if verified_credentials is None:
            raise Step3Error("設定適用を確認できません。option=2は未実行。最終確認: " + last_verification_error)
        if credential_report:
            credential_report(verified_credentials)
        if stopped():
            raise Step3Error("処理中止")
        if not reset_ip:
            return True, "設定書込・SN確認完了。ラベル貼付待ち。"
        stage = "IP Reset option=2"
        log(stage)
        status, body = transport(base + RESET_PATH, "POST", json.dumps({"options": 2}).encode(),
                                 {"Content-Type": "application/json"}, verified_credentials)
        if not 200 <= status < 300:
            raise Step3Error(f"IP Reset HTTP={status}")
        stage = f"再起動待ち · MAC確認 → {FINAL_IP}"
        log(stage)
        deadline = time.monotonic() + final_timeout
        while time.monotonic() < deadline and not stopped():
            if ip_checker(target_mac, FINAL_IP):
                return True, f"再起動完了 · MAC一致 / IP={FINAL_IP} 確認済み"
            time.sleep(1)
        if stopped():
            raise Step3Error("処理中止。最終IP未確認。")
        raise Step3Error(f"MAC一致の{FINAL_IP}を確認できません。Reset再送なし。")
    except Exception as error:
        # 導入パスワード入りURLや認証情報をログに含めない。
        detail = str(error) if isinstance(error, Step3Error) else type(error).__name__
        return False, f"{stage}: {detail}"
    finally:
        if owns_transport and hasattr(transport, "close"):
            transport.close()
