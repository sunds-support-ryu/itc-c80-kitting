"""GET physically moves PTZ. ipncptz value is gotopreset=<preset>. Authentication remains unverified in reference."""
if __package__:
    from .common import Endpoint, show_command
else:
    from common import Endpoint, show_command

SCOPE = 'candidate'
REFERENCE = 'CGI/products/Milesight_Camera/cgi/goto_preset.py'
ENDPOINT = Endpoint(
    name='ptz_preset',
    method='GET',
    path='/vb.htm',
    auth=('unknown',),
    effects='control',
    sources=('Python SDK/apps/ws301_camera/src/camera_controller.py:95', 'Python/CGI/products/Milesight_Camera/cgi/goto_preset.py'),
    query=(('', None),),
    parameters=('ipncptz',),
    required_parameters=('ipncptz',),
    notes='GET physically moves PTZ. ipncptz value is gotopreset=<preset>. Authentication remains unverified in reference.',
)
prepare = ENDPOINT.prepare

if __name__ == "__main__":
    show_command(ENDPOINT, 'goto_preset', scope=SCOPE)
