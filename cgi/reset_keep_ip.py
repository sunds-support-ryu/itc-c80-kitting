'Compatibility selector for option 3: keep IP and users (retention bits 1 OR 2).'
if __package__:
    from .common import show_command
    from .reset import reset_endpoint
else:
    from common import show_command
    from reset import reset_endpoint

SCOPE = 'current'
REFERENCE = 'CGI/products/ITC_C80/cgi/reset_keep_ip.py'
ENDPOINT = reset_endpoint(3, name='reset_keep_ip', sources=('Python/ITEC/products/ITC-C80/src/inspection_engine.py:602', 'Python/CGI/products/ITC_C80/cgi/reset_keep_ip.py', 'Python/ITEC/products/ITC-C80/src/config_import.py'))
prepare = ENDPOINT.prepare

if __name__ == "__main__":
    show_command(ENDPOINT, 'reset_keep_ip', scope=SCOPE)
