"""Status 0=pending, 1=reported success, 2=password rejection, 3=file rejection. Capture actual firmware behavior."""
if __package__:
    from .common import Endpoint, show_command
else:
    from common import Endpoint, show_command

SCOPE = 'current'
REFERENCE = 'CGI/products/ITC_C80/cgi/get_import_status.py'
ENDPOINT = Endpoint(
    name='read_import_status',
    method='GET',
    path='/vb.htm',
    auth=('digest', 'basic'),
    effects='read',
    sources=('Python/ITEC/products/ITC-C80/src/config_import.py:200', 'Python/CGI/products/ITC_C80/cgi/get_import_status.py', 'Python/ITEC/products/ITC-C80/src/config_import.py'),
    query=(('', None), ('getimportstatus', None)),
    notes='Status 0=pending, 1=reported success, 2=password rejection, 3=file rejection. Capture actual firmware behavior.',
)
prepare = ENDPOINT.prepare

if __name__ == "__main__":
    show_command(ENDPOINT, 'get_import_status', scope=SCOPE)
