"""Multipart firmware file; X-From Web and Referer required. Verify version and identity after reboot. Do not resend."""
if __package__:
    from .common import Endpoint, show_command
else:
    from common import Endpoint, show_command

SCOPE = 'candidate'
REFERENCE = 'CGI/products/Milesight_Camera/cgi/upload_firmware.py'
ENDPOINT = Endpoint(
    name='upload_firmware',
    method='POST',
    path='/update.cgi',
    auth=('digest',),
    effects='firmware',
    sources=('Python/ITEC/upgrade_fw.py:106', 'Electron/UG Kitting Tool/src/milesightCgi.js:91', 'Python/CGI/products/Milesight_Camera/cgi/upload_firmware.py'),
    encoding='multipart',
    headers=(('x-from', 'Web'),),
    required_headers=('Referer',),
    notes='Multipart firmware file; X-From Web and Referer required. Verify version and identity after reboot. Do not resend.',
)
prepare = ENDPOINT.prepare

if __name__ == "__main__":
    show_command(ENDPOINT, 'upload_firmware', scope=SCOPE)
