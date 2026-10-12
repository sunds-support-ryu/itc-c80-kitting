"""options=3. Verify reboot and retention of IP, mask, gateway and credentials separately."""
if __package__:
    from .common import Endpoint, show_command
else:
    from common import Endpoint, show_command

SCOPE = 'current'
REFERENCE = 'CGI/products/ITC_C80/cgi/reset_keep_ip.py'
ENDPOINT = Endpoint(
    name='reset_keep_ip',
    method='POST',
    path='/cgi-bin/admin/admin.cgi',
    auth=('digest', 'basic'),
    effects='reset',
    sources=('Python/ITEC/products/ITC-C80/src/inspection_engine.py:605', 'Python/CGI/products/ITC_C80/cgi/reset_keep_ip.py', 'Python/ITEC/products/ITC-C80/src/config_import.py'),
    query=(('action', 'set.system.reset'), ('format', 'json')),
    encoding='json',
    fixed_body=(('options', 3),),
    notes='options=3. Verify reboot and retention of IP, mask, gateway and credentials separately.',
)
prepare = ENDPOINT.prepare

if __name__ == "__main__":
    show_command(ENDPOINT, 'reset_keep_ip', scope=SCOPE)
