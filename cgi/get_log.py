"""file means a device log path, not a PC path. Source uses Basic authentication."""
if __package__:
    from .common import Endpoint, show_command
else:
    from common import Endpoint, show_command

SCOPE = 'candidate'
REFERENCE = 'CGI/products/Milesight_Camera/cgi/get_log.py'
ENDPOINT = Endpoint(
    name='device_log',
    method='GET',
    path='/vb.htm',
    auth=('basic',),
    effects='read',
    sources=('Python/http_log.py:49', 'Python/CGI/products/Milesight_Camera/cgi/get_log.py'),
    parameters=('file',),
    required_parameters=('file',),
    notes='file means a device log path, not a PC path. Source uses Basic authentication.',
)
prepare = ENDPOINT.prepare

if __name__ == "__main__":
    show_command(ENDPOINT, 'get_log', scope=SCOPE)
