"""HTTP CGI authentication/transport, independent of import/reset sequencing."""
import urllib.error
import urllib.parse
import urllib.request

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


