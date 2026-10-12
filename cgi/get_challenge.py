"""PublicKey is double Base64 of a 128-byte RSA modulus; response contains XML key/keyID. Session state may change."""
if __package__:
    from .common import Endpoint, show_command
else:
    from common import Endpoint, show_command

SCOPE = 'current'
REFERENCE = 'CGI/products/Milesight_Camera/cgi/get_challenge.py'
ENDPOINT = Endpoint(
    name='legacy_challenge',
    method='GET',
    path='/challenge',
    auth=('digest', 'basic'),
    effects='session',
    sources=('Python/ITEC/legacy/itec/upload_config.py:147', 'Python/ITEC/legacy/itc-c80/config_writer.py:61', 'Python/CGI/products/Milesight_Camera/cgi/get_challenge.py', 'Python/ITEC/products/ITC-C80/src/config_import.py'),
    parameters=('PublicKey',),
    required_parameters=('PublicKey',),
    notes='PublicKey is double Base64 of a 128-byte RSA modulus; response contains XML key/keyID. Session state may change.',
)
prepare = ENDPOINT.prepare

if __name__ == "__main__":
    show_command(ENDPOINT, 'get_challenge', scope=SCOPE)
