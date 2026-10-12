"""Read system information as JSON; compare SN with the physical camera label."""
if __package__:
    from .common import Endpoint, show_command
else:
    from common import Endpoint, show_command

SCOPE = 'current'
REFERENCE = 'CGI/products/ITC_C80/cgi/get_info.py'
ENDPOINT = Endpoint(
    name='system_information_repair',
    method='GET',
    path='/cgi-bin/admin/admin.cgi',
    auth=('digest', 'basic'),
    effects='read',
    sources=('Python/ITEC/products/ITC-C80/src/config_import.py:75', 'Python/CGI/products/ITC_C80/cgi/get_info.py', 'Python/ITEC/products/ITC-C80/src/config_import.py'),
    query=(('action', 'get.system.information'), ('format', 'json')),
    notes='Read system information as JSON; compare SN with the physical camera label.',
)
prepare = ENDPOINT.prepare

if __name__ == "__main__":
    show_command(ENDPOINT, 'get_info', scope=SCOPE)
