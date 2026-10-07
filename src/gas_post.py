"""GAS JSON acknowledgement transport; HTTP status alone never confirms receipt."""
import requests
from urllib.parse import urlsplit

DEFAULT_POST_URL = 'https://script.google.com/macros/s/AKfycbzYGHxJ4K7OOKWComQMW04850znk-UEuECrDNHlcaeDXiwuCTZJD-HNQFgNcGwvBo5h/exec'


class PostError(ValueError):
    def __init__(self, message, response=None):
        super().__init__(message)
        self.response = response


def validate_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("HTTP / HTTPS のPOST URLを入力してください")
    return url


def send(url, payload, session_factory=requests.Session):
    validate_url(url)
    with session_factory() as session:
        session.trust_env = False
        with session.post(url, json=payload, timeout=(5, 20), allow_redirects=True, stream=True) as response:
            chunks, length = [], 0
            for chunk in response.iter_content(4096):
                length += len(chunk)
                if length > 65536:
                    raise PostError("GAS応答が大きすぎます（64KB超）")
                chunks.append(chunk)
            raw = b"".join(chunks).decode("utf-8-sig", errors="replace")
            import json
            try:
                body = json.loads(raw)
            except ValueError:
                raise PostError("GAS応答がJSONではありません（ログイン画面・デプロイ設定を確認）",
                                {"http_status": response.status_code, "body": raw[:2000]})
            received = {"http_status": response.status_code, "body": body}
            if not isinstance(body, dict) or body.get("ok") is not True:
                raise PostError("GAS側が受信成功を確認していません", received)
            if body.get("record_id") != payload["record_id"]:
                raise PostError("GAS応答のrecord_idが一致しません", received)
            return received
