"""GET changes state. Used before upload and as acknowledgement after success; test those contexts separately."""
if __package__:
    from .common import Endpoint, show_command
else:
    from common import Endpoint, show_command

SCOPE = 'current'
REFERENCE = 'CGI/products/ITC_C80/cgi/clear_import_status.py'
ENDPOINT = Endpoint(
    name='clear_import_status',
    method='GET',
    path='/vb.htm',
    auth=('digest', 'basic'),
    effects='config',
    sources=('Python/ITEC/products/ITC-C80/src/config_import.py:261', 'Python/CGI/products/ITC_C80/cgi/clear_import_status.py', 'Python/ITEC/products/ITC-C80/src/config_import.py'),
    query=(('setimportstatus', 0),),
    notes='GET changes state. Used before upload and as acknowledgement after success; test those contexts separately.',
)
prepare = ENDPOINT.prepare

if __name__ == "__main__":
    show_command(ENDPOINT, 'clear_import_status', scope=SCOPE)
