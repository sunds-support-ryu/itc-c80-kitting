"""options=2. Determine actual reset IP/mask/gateway and credential behavior. Do not assume factory IP."""
if __package__:
    from .common import Endpoint, show_command
else:
    from common import Endpoint, show_command

SCOPE = 'current'
REFERENCE = 'CGI/products/ITC_C80/cgi/reset_ip.py'
ENDPOINT = Endpoint(
    name='reset_ip',
    method='POST',
    path='/cgi-bin/admin/admin.cgi',
    auth=('digest', 'basic'),
    effects='reset',
    sources=('Python/ITC-C80/ITC-C80修復/src/config_import.py:373', 'Python/CGI/products/ITC_C80/cgi/reset_ip.py', 'Python/ITC-C80/ITC-C80修復/src/config_import.py'),
    query=(('action', 'set.system.reset'), ('format', 'json')),
    encoding='json',
    fixed_body=(('options', 2),),
    notes='options=2. Determine actual reset IP/mask/gateway and credential behavior. Do not assume factory IP.',
)
prepare = ENDPOINT.prepare

if __name__ == "__main__":
    show_command(ENDPOINT, 'reset_ip', scope=SCOPE)
