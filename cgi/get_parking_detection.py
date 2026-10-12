"""Read parking detection configuration. Support on NC8164-FPE is unverified."""
if __package__:
    from .common import Endpoint, show_command
else:
    from common import Endpoint, show_command

SCOPE = 'candidate'
REFERENCE = 'CGI/products/Milesight_Camera/cgi/get_parking_detection.py'
ENDPOINT = Endpoint(
    name='parking_detection',
    method='GET',
    path='/cgi-bin/operator/operator.cgi',
    auth=('basic',),
    effects='read',
    sources=('Python/CGI/PMC8266_get_info.py:102', 'Electron/M-sight ProMAX/electron/camera-pit.cjs:10', 'Python/CGI/products/Milesight_Camera/cgi/get_parking_detection.py'),
    query=(('action', 'get.parkingspace.detection'), ('format', 'json')),
    notes='Read parking detection configuration. Support on NC8164-FPE is unverified.',
)
prepare = ENDPOINT.prepare

if __name__ == "__main__":
    show_command(ENDPOINT, 'get_parking_detection', scope=SCOPE)
