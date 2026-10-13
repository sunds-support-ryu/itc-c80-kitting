"""Standalone CGI request contracts. Builder adapted from workspace CGI/common.py."""
from dataclasses import dataclass, field
from collections.abc import Mapping
import json as json_module
from urllib.parse import quote, urlsplit, urlunsplit


@dataclass(frozen=True)
class PreparedRequest:
    method: str
    url: str = field(repr=False)
    headers: dict = field(repr=False)
    json_body: object = field(default=None, repr=False)
    data: object = field(default=None, repr=False)
    files: object = field(default=None, repr=False)
    auth: tuple[str, ...] = ()
    effects: str = "read"

    def request_kwargs(self, timeout=10):
        """Arguments for an already-authenticated requests Session; does not send."""
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("timeout must be positive")
        kwargs = {"method": self.method, "url": self.url, "headers": dict(self.headers),
                  "allow_redirects": False, "timeout": timeout}
        for name, value in (("json", self.json_body), ("data", self.data), ("files", self.files)):
            if value is not None:
                kwargs[name] = value
        return kwargs


@dataclass(frozen=True)
class Endpoint:
    name: str
    method: str
    path: str
    auth: tuple[str, ...]
    effects: str
    sources: tuple[str, ...]
    query: tuple[tuple[str, object], ...] = ()
    parameters: tuple[str, ...] = ()
    required_parameters: tuple[str, ...] = ()
    encoding: str = "none"
    required_body: tuple[str, ...] = ()
    fixed_body: tuple[tuple[str, object], ...] = ()
    headers: tuple[tuple[str, str], ...] = ()
    required_headers: tuple[str, ...] = ()
    notes: str = ""

    def prepare(self, base_url, *, params=None, body=None, files=None, headers=None):
        """Build a request from explicit input, without login, retries or network IO."""
        base = urlsplit(base_url)
        if (base.scheme not in {"http", "https"} or not base.hostname or base.username is not None
                or base.password is not None or base.query or base.fragment):
            raise ValueError("Use an http(s) base URL without credentials/query/fragment")
        base.port  # Validate malformed ports without connecting.
        if not self.path.startswith("/") or self.path.startswith("//"):
            raise ValueError("Invalid endpoint path")
        params = dict(params or {})
        fixed = dict(self.query)
        if set(params) - set(self.parameters):
            raise ValueError("Unsupported query parameter or fixed action override")
        if set(params) & set(fixed):
            raise ValueError("Fixed query parameters cannot be overridden")
        if set(self.required_parameters) - set(params):
            raise ValueError("Missing required query parameters")
        pairs = [*self.query, *params.items()]
        # None denotes a bare flag; the empty-key flag preserves legacy ?&flags.
        query = "&".join(quote(str(k), safe="") if v is None else
                         quote(str(k), safe="") + "=" + quote(str(v), safe="") for k, v in pairs)
        url = urlunsplit((base.scheme, base.netloc, base.path.rstrip("/") + self.path, query, ""))
        headers = dict(headers or {})
        if len({key.casefold() for key in headers}) != len(headers):
            raise ValueError("Duplicate header names")
        for key, value in self.headers:
            matches = [name for name in headers if name.casefold() == key.casefold()]
            if matches and headers[matches[0]] != value:
                raise ValueError("Fixed headers cannot be changed")
            if not matches:
                headers[key] = value
        if any(key.casefold() not in {name.casefold() for name in headers} for key in self.required_headers):
            raise ValueError("Missing required headers")
        json_body, data = None, None
        if self.encoding == "none":
            if body is not None or files is not None:
                raise ValueError("This endpoint has no request body")
        elif self.encoding == "xml":
            if not isinstance(body, (str, bytes)) or not body or files is not None:
                raise ValueError("Provide the already-negotiated XML login body")
            data = body.encode("utf-8") if isinstance(body, str) else body
            headers["Content-Type"] = "application/xml; charset=UTF-8"
            headers["If-Modified-Since"] = "0"
        else:
            if not isinstance(body, Mapping):
                raise ValueError("Supply a request body mapping ({} for an empty JSON body)")
            payload = dict(body)
            constants = dict(self.fixed_body)
            if any(key in payload and payload[key] != value for key, value in constants.items()):
                raise ValueError("Fixed body fields cannot be changed")
            payload.update(constants)
            if set(self.required_body) - set(payload):
                raise ValueError("Missing required body fields")
            if self.encoding == "json":
                json_body = payload
            elif self.encoding == "json-form-content-type":
                data = json_module.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            elif self.encoding in {"form", "multipart"}:
                data = payload
            else:
                raise ValueError("Unsupported body encoding")
            if self.encoding == "multipart":
                if not isinstance(files, Mapping) or "file" not in files or len(files) != 1:
                    raise ValueError("Provide exactly the multipart field 'file'; caller owns its stream")
            elif files is not None:
                raise ValueError("Only multipart endpoints accept files")
        return PreparedRequest(self.method, url, headers, json_body, data, files, self.auth, self.effects)


def inspect_reply(command, status, content, content_type=''):
    """Interpret one reply; never equate HTTP acceptance with a physical effect."""
    import re
    result = {'http_ok': 200 <= status < 300, 'interpretation': 'unclassified_reply',
              'physical_effect_verified': False}
    if not result['http_ok']:
        result['interpretation'] = 'http_error'
        return result
    text = content.decode('utf-8-sig', errors='replace')
    if command == 'get_import_status':
        match = re.fullmatch(r'(?:UW\s+)?OK\s+getimportstatus=([0-3])\s*', text)
        if match:
            code = int(match.group(1))
            result.update(import_status=code, interpretation=[
                'pending_or_idle', 'import_reported_success', 'import_password_rejected',
                'import_file_rejected'][code])
    elif command == 'clear_import_status':
        result['interpretation'] = 'status_clear_acknowledged' if text.strip() == 'OK' else 'unclassified_reply'
    elif command == 'get_reload_flag':
        if re.match(r'^(?:UW\s+)?OK\s+reloadflag(?:\s|=|$)', text.strip()):
            result['interpretation'] = 'readiness_hint_only'
    elif command == 'get_challenge':
        if re.search(r'<key>.+?</key>', text, re.S) and re.search(r'<keyID>.+?</keyID>', text, re.S):
            result['interpretation'] = 'handshake_fields_present'
    elif command in ('upload_config', 'upload_firmware', 'reset_ip', 'reset_keep_ip', 'set_tcp', 'goto_preset', 'unlock_upgrade'):
        result['interpretation'] = 'http_accepted_effect_unverified'
        if command == 'upload_config':
            # Current device Web UI ignores this HTML body and polls import status.
            # Some replies contain upload_failed_tips even when status becomes 1.
            result['html_warning_present'] = 'upload_failed' in text
        elif 'upload_failed' in text:
            result['interpretation'] = 'upload_rejected'
    elif command == 'upload_config_legacy':
        result['interpretation'] = ('upload_rejected' if 'upload_failed' in text else
                                    'upload_marker_present_effect_unverified' if 'OnUploadSus' in text else 'unclassified_reply')
    elif command == 'get_snapshot':
        try:
            from PIL import Image
            import io
            with Image.open(io.BytesIO(content)) as picture:
                size = picture.size
                picture.verify()
            result.update(interpretation='image_decoded', image_size=size)
        except Exception:
            result['interpretation'] = 'not_a_valid_image'
    elif command in ('get_info', 'get_lpr_upload_policy', 'get_parking_detection'):
        try:
            decoded = json_module.loads(text)
            result['interpretation'] = 'json_received' if isinstance(decoded, (dict, list)) else 'unexpected_json_shape'
        except ValueError:
            result['interpretation'] = 'invalid_json'
    elif command == 'get_log':
        result['interpretation'] = 'log_payload_received_format_unverified'
    return result


def show_command(endpoint, command_id, scope='current'):
    """No arguments: contract only. --execute: exactly one logical CGI request."""
    import argparse
    from dataclasses import asdict
    from datetime import datetime, timezone
    import getpass
    import hashlib
    import os
    from pathlib import Path
    import sys
    if sys.stdout is not None and hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
    parser = argparse.ArgumentParser(description=endpoint.notes)
    parser.add_argument('--base-url', help='Example: http://192.168.0.150 (no embedded credentials)')
    parser.add_argument('--execute', action='store_true', help='Send this single CGI request; no workflow, reset or upload retries')
    parser.add_argument('--params-file', type=Path, help='UTF-8 JSON query-parameter mapping')
    parser.add_argument('--body-file', type=Path, help='UTF-8 JSON request-body mapping')
    parser.add_argument('--file', type=Path, help='Config/firmware binary for multipart field file')
    parser.add_argument('--user', help='Username; defaults to the selected saved profile')
    parser.add_argument('--profile', choices=('before', 'after'), default='before')
    parser.add_argument('--auth', choices=('digest', 'basic'), help='Explicitly choose authentication when comparing firmware behavior')
    parser.add_argument('--timeout', type=float, default=15)
    parser.add_argument('--record', type=Path, help='Override local evidence JSON output; default data/cgi_validation/')
    parser.add_argument('--session-file', type=Path, help='get_challenge only: generate RSA key in memory and save derived keyId/encryptPwd locally')
    parser.add_argument('--save-binary', type=Path, help='get_snapshot only: save the received snapshot bytes locally')
    args = parser.parse_args()
    contract = {'command': command_id, 'scope': scope, **asdict(endpoint)}
    if not args.base_url:
        if args.execute:
            parser.error('--execute requires --base-url')
        print(json_module.dumps(contract, ensure_ascii=False, indent=2))
        return
    params = json_module.loads(args.params_file.read_text(encoding='utf-8-sig')) if args.params_file else {}
    body = json_module.loads(args.body_file.read_text(encoding='utf-8-sig')) if args.body_file else {}
    if not isinstance(params, dict) or not isinstance(body, dict):
        parser.error('Parameter and body files must contain JSON objects')
    if args.body_file and endpoint.encoding == 'none':
        parser.error('This endpoint does not accept a request body')
    if args.file and endpoint.encoding != 'multipart':
        parser.error('--file is only supported for multipart endpoints')
    settings = {}
    try:
        settings = json_module.loads((Path(__file__).resolve().parents[1] / 'records/settings.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        pass
    secrets = []
    private = None
    import_password = os.environ.get('ITC_CONFIG_PASSWORD') or settings.get('config_import_password', '')
    if args.session_file:
        if command_id != 'get_challenge' or not args.execute or 'PublicKey' in params:
            parser.error('--session-file requires get_challenge --execute with no explicit PublicKey')
        if not import_password:
            import_password = getpass.getpass('Config import password for legacy encryption: ')
        if __package__:
            from .legacy_crypto import create_challenge
        else:
            from legacy_crypto import create_challenge
        private, generated = create_challenge()
        params.update(generated)
        secrets.append(import_password)
    if args.save_binary and command_id != 'get_snapshot':
        parser.error('--save-binary is only supported for get_snapshot')
    if 'pwd' in endpoint.parameters and 'pwd' not in params:
        password = os.environ.get('ITC_CONFIG_PASSWORD') or settings.get('config_import_password', '')
        if args.execute and not password:
            password = getpass.getpass('Config import password: ')
        if password:
            params['pwd'] = password
    for key in ('pwd', 'encryptPwd', 'keyId'):
        if params.get(key):
            secrets.append(str(params[key]))
    if not args.execute:
        for key in endpoint.required_parameters:
            params.setdefault(key, '<' + key + '>')
        for key in endpoint.required_body:
            body.setdefault(key, '<' + key + '>')
    files = None
    if endpoint.encoding == 'multipart':
        if args.execute and not args.file:
            parser.error('Multipart execution requires --file')
        payload = args.file.read_bytes() if args.file else b''
        if args.execute and not payload:
            parser.error('Upload file is empty')
        files = {'file': (args.file.name if args.file else 'sample.bin', payload, 'application/octet-stream')}
    origin = urlsplit(args.base_url)
    headers = {'Referer': args.base_url.rstrip('/') + '/', 'Origin': urlunsplit((origin.scheme, origin.netloc, '', '', '')), 'X-From': 'Web',
               'X-Requested-With': 'XMLHttpRequest', 'User-Agent': 'Mozilla/5.0'}
    prepared = endpoint.prepare(args.base_url, params=params,
                                body=body if endpoint.encoding != 'none' else None,
                                files=files, headers=headers)
    def redact(value):
        text = str(value)
        from urllib.parse import quote
        for secret in secrets:
            if secret:
                text = text.replace(quote(secret, safe=''), '<redacted>').replace(secret, '<redacted>')
        return text
    report = {'command': command_id, 'scope': scope, 'effect': endpoint.effects,
              'timestamp_utc': datetime.now(timezone.utc).isoformat(),
              'method': prepared.method, 'url': redact(prepared.url), 'json_body': prepared.json_body,
              'file': {'name': args.file.name if args.file else None, 'bytes': len(files['file'][1]),
                       'sha256': hashlib.sha256(files['file'][1]).hexdigest()} if files else None,
              'executed': False, 'physical_effect_verified': False}
    if not args.execute:
        print(json_module.dumps({'contract': contract, 'plan': report}, ensure_ascii=False, indent=2))
        return
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    auth = args.auth or next((kind for kind in endpoint.auth if kind in ('digest', 'basic')), None)
    if auth is None:
        parser.error('Reference authentication is unknown. Specify --auth explicitly for this test')
    if 'unknown' not in endpoint.auth and auth not in endpoint.auth:
        parser.error('Authentication scheme is outside the reference contract')
    user_key, password_key = ('access_username', 'access_password') if args.profile == 'before' else ('after_config_username', 'after_config_password')
    username = args.user or settings.get(user_key, 'admin')
    password = os.environ.get('ITC_CAMERA_PASSWORD') or settings.get(password_key, '')
    if not password:
        password = getpass.getpass('Camera access password: ')
    secrets.append(password)
    report.update(executed=True, auth=auth, profile=args.profile, logical_requests=1)
    import requests
    from requests.auth import HTTPBasicAuth, HTTPDigestAuth
    with requests.Session() as session:
        session.trust_env = False
        session.auth = (HTTPDigestAuth if auth == 'digest' else HTTPBasicAuth)(username, password)
        try:
            with session.request(**prepared.request_kwargs(timeout=args.timeout), stream=True) as response:
                chunks, size = [], 0
                for chunk in response.iter_content(8192):
                    size += len(chunk)
                    if size > 4_000_000:
                        raise ValueError('Response exceeded the 4MB diagnostic limit')
                    chunks.append(chunk)
                content = b''.join(chunks)
                report.update(http_status=response.status_code, content_type=response.headers.get('Content-Type', ''),
                              response_bytes=len(content), response_sha256=hashlib.sha256(content).hexdigest(),
                              reply=inspect_reply(command_id, response.status_code, content, response.headers.get('Content-Type', '')))
                if private is not None and report['reply']['interpretation'] == 'handshake_fields_present':
                    if __package__:
                        from .legacy_crypto import encrypt_import_password
                    else:
                        from legacy_crypto import encrypt_import_password
                    tokens = encrypt_import_password(private, content.decode('utf-8-sig'), import_password)
                    secrets.extend(str(value) for value in tokens.values())
                    args.session_file.parent.mkdir(parents=True, exist_ok=True)
                    args.session_file.write_text(json_module.dumps(tokens, indent=2), encoding='utf-8')
                    report['legacy_session_file'] = str(args.session_file)
                if args.save_binary and report['reply']['interpretation'] == 'image_decoded':
                    args.save_binary.parent.mkdir(parents=True, exist_ok=True)
                    args.save_binary.write_bytes(content)
                    report['snapshot_file'] = str(args.save_binary)
                if command_id != 'get_snapshot':
                    report['response_text'] = redact(content.decode('utf-8-sig', errors='replace'))
        except (requests.RequestException, OSError, ValueError) as error:
            report.update(error_type=type(error).__name__, error=redact(error),
                          outcome='unknown' if endpoint.effects != 'read' else 'read_failed')
    output = args.record or Path(__file__).resolve().parents[1] / 'data/cgi_validation' / (
        datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '_' + command_id + '.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(redact(json_module.dumps(report, ensure_ascii=False, indent=2)), encoding='utf-8')
    print(redact(json_module.dumps(report, ensure_ascii=False, indent=2)))
    print('Evidence saved:', output)
    if report.get('error') or not report.get('reply', {}).get('http_ok'):
        raise SystemExit(1)

