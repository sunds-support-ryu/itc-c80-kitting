"""Read readiness hint. It cannot prove config application, identity or successful login by itself."""
if __package__:
    from .common import Endpoint, show_command
else:
    from common import Endpoint, show_command

SCOPE = 'current'
REFERENCE = 'CGI/products/ITC_C80/cgi/get_reload_flag.py'
ENDPOINT = Endpoint(
    name='read_reload_flag',
    method='GET',
    path='/vb.htm',
    auth=('digest', 'basic'),
    effects='read',
    sources=('Python/ITEC/products/ITC-C80/src/config_import.py:295', 'Python/CGI/products/ITC_C80/cgi/get_reload_flag.py', 'Python/ITEC/products/ITC-C80/src/config_import.py'),
    query=(('', None), ('reloadflag', None)),
    notes='Read readiness hint. It cannot prove config application, identity or successful login by itself.',
)
prepare = ENDPOINT.prepare

if __name__ == "__main__":
    show_command(ENDPOINT, 'get_reload_flag', scope=SCOPE)
