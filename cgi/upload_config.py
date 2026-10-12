"""Multipart field file; query pwd is the export/import password. HTTP 200 alone is not application success."""
if __package__:
    from .common import Endpoint, show_command
else:
    from common import Endpoint, show_command

SCOPE = 'current'
REFERENCE = 'CGI/products/ITC_C80/cgi/upload_config.py'
ENDPOINT = Endpoint(
    name='upload_config_modern',
    method='POST',
    path='/dataloader.cgi',
    auth=('digest', 'basic'),
    effects='config',
    sources=('Python/ITC-C80/ITC-C80修復/src/config_import.py:267', 'Electron/UG Kitting Tool/src/milesightCgi.js:170', 'Python/CGI/products/ITC_C80/cgi/upload_config.py', 'Python/ITC-C80/ITC-C80修復/src/config_import.py'),
    query=(('up', 'cfg'),),
    parameters=('pwd',),
    required_parameters=('pwd',),
    encoding='multipart',
    notes='Multipart field file; query pwd is the export/import password. HTTP 200 alone is not application success.',
)
prepare = ENDPOINT.prepare

if __name__ == "__main__":
    show_command(ENDPOINT, 'upload_config', scope=SCOPE)
