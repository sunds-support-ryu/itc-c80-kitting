"""Return is a binary snapshot; validate image decoding and actual content, not JSON."""
if __package__:
    from .common import Endpoint, show_command
else:
    from common import Endpoint, show_command

SCOPE = 'candidate'
REFERENCE = 'CGI/products/Milesight_Camera/cgi/get_snapshot.py'
ENDPOINT = Endpoint(
    name='snapshot',
    method='GET',
    path='/snapshot.cgi',
    auth=('digest',),
    effects='read',
    sources=('Python/camera_snapshot.py:62', 'Python/CGI/products/Milesight_Camera/cgi/get_snapshot.py'),
    query=(('mainstream', None),),
    notes='Return is a binary snapshot; validate image decoding and actual content, not JSON.',
)
prepare = ENDPOINT.prepare

if __name__ == "__main__":
    show_command(ENDPOINT, 'get_snapshot', scope=SCOPE)
