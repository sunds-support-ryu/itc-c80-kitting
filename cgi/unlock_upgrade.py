"""GET changes upgrade restriction. Test actual effect on this model before any firmware update."""
if __package__:
    from .common import Endpoint, show_command
else:
    from common import Endpoint, show_command

SCOPE = 'candidate'
REFERENCE = 'CGI/products/Milesight_Camera/cgi/unlock_upgrade.py'
ENDPOINT = Endpoint(
    name='unlock_upgrade',
    method='GET',
    path='/cgi-bin/viewer/viewer.cgi',
    auth=('digest',),
    effects='firmware',
    sources=('Python/ITEC/upgrade_fw.py:62', 'Python/CGI/products/Milesight_Camera/cgi/unlock_upgrade.py'),
    query=(('action', 'set.system.demotion.limit'), ('value', 0)),
    notes='GET changes upgrade restriction. Test actual effect on this model before any firmware update.',
)
prepare = ENDPOINT.prepare

if __name__ == "__main__":
    show_command(ENDPOINT, 'unlock_upgrade', scope=SCOPE)
