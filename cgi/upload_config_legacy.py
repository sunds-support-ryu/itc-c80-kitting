"""Requires keyId and encryptPwd from the same RSA/AES handshake. Never substitute plaintext pwd."""
if __package__:
    from .common import Endpoint, show_command
else:
    from common import Endpoint, show_command

SCOPE = 'current'
REFERENCE = 'CGI/products/Milesight_Camera/cgi/upload_config_legacy.py'
ENDPOINT = Endpoint(
    name='upload_config_legacy',
    method='POST',
    path='/dataloader.cgi',
    auth=('digest', 'basic'),
    effects='config',
    sources=('Python/ITEC/legacy/itec/upload_config.py:216', 'Python/ITEC/legacy/itc-c80/config_writer.py:61', 'Python/CGI/products/Milesight_Camera/cgi/upload_config_legacy.py', 'Python/ITEC/products/ITC-C80/src/config_import.py'),
    query=(('up', 'cfg'),),
    parameters=('keyId', 'encryptPwd'),
    required_parameters=('keyId', 'encryptPwd'),
    encoding='multipart',
    notes='Requires keyId and encryptPwd from the same RSA/AES handshake. Never substitute plaintext pwd.',
)
prepare = ENDPOINT.prepare

if __name__ == "__main__":
    show_command(ENDPOINT, 'upload_config_legacy', scope=SCOPE)
