"""Single camera reset request; option meanings are retention flags, not steps."""
import json
from urllib.parse import urlsplit
from .transport import create_transport

RESET_PATH = '/cgi-bin/admin/admin.cgi?action=set.system.reset&format=json'
SUPPORTED_OPTIONS = (0, 1, 2, 3)
OPTION_NOTES = {
    0: 'No retention bits: reset defaults without requesting IP/user retention. Physical effect not tested.',
    1: 'Keep IP/network flag. User retention is not requested. Physical effect not tested.',
    2: 'Keep user/accounts flag. IP retention is not requested. Observed reset to 192.168.5.190 on the tested device.',
    3: 'Keep IP and user/accounts flags (1 | 2). Observed IP retained and device reboot/recovery.',
}


def validate_option(option):
    if type(option) is not int or option not in SUPPORTED_OPTIONS:
        raise ValueError('Reset option must be an integer in 0, 1, 2, 3')
    return option


def reset(base_url, option, credentials, *, transport=None):
    """Send exactly one logical ``set.system.reset`` POST and return (HTTP, text).

    Args:
        base_url: Camera origin, e.g. ``http://192.168.5.190``. No credentials,
            query, fragment or path may be embedded in this value.
        option: Integer 0, 1, 2 or 3. The device maintenance UI defines retention
            bits: KEEP_IP=1, KEEP_USER=2, KEEP_OSD=4 (KEEP_DEVICE_NAME=8 is outside
            this function's requested scope). These are not sequential stages:
            0 requests no retention; 1 keeps IP; 2 keeps users; 3 keeps both.
            Settings whose retention bit is absent may be reset. Do not describe
            2 as changing only IP, or 3 as keeping only IP.
        credentials: Explicit current (username, password) tuple; do not assume
            credentials before config import still work afterward.
        transport: Optional compatible request callable, for isolation, timing
            and tests. An omitted transport owns a temporary authenticated session.

    Evidence:
        Meanings are sourced from the tested device's maintenance Web UI flags.
        Options 2 and 3 were exercised on NC8164-FPE; option 2 returned its IP to
        192.168.5.190, while option 3 retained the work IP. Options 0 and 1 remain
        physically unverified. Default IP, mask, gateway and account behavior can
        vary with OEM/firmware; independently verify MAC, SN and settings afterward.

    Behavior:
        Body is JSON ``{"options": option}``. Return is only the HTTP reply;
        HTTP 200 does not prove reboot or completed reset. Bind target MAC/SN and
        authorize the effect before calling. This function never polls, changes IP
        again, retries a reset, stores passwords or marks an inspection OK.
        A transport exception is an unknown outcome: verify state, do not resend.
        Option 0 requests a full-default reset and can change IP/accounts.
        Option 4 (OSD retention) and other bit combinations are outside this
        requested function range and are rejected.
    """
    validate_option(option)
    origin = urlsplit(base_url)
    if (origin.scheme not in ('http','https') or not origin.hostname or
            origin.username is not None or origin.password is not None or
            origin.path not in ('','/') or origin.query or origin.fragment):
        raise ValueError('Use a camera origin without credentials, path, query or fragment')
    origin.port
    owns_transport = transport is None
    request = transport or create_transport()
    try:
        return request(base_url.rstrip('/') + RESET_PATH, 'POST',
                       json.dumps({'options': option}).encode('utf-8'),
                       {'Content-Type':'application/json'}, credentials)
    finally:
        if owns_transport and hasattr(request,'close'):
            request.close()
