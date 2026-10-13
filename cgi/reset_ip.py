'Compatibility selector for option 2: keep users; reset unretained settings including IP.'
if __package__:
    from .common import show_command
    from .reset import reset_endpoint
else:
    from common import show_command
    from reset import reset_endpoint

SCOPE = 'current'
REFERENCE = 'CGI/products/ITC_C80/cgi/reset_ip.py'
ENDPOINT = reset_endpoint(2, name='reset_ip', sources=('Python/ITEC/products/ITC-C80/src/config_import.py:316', 'Python/CGI/products/ITC_C80/cgi/reset_ip.py', 'Python/ITEC/products/ITC-C80/src/config_import.py'))
prepare = ENDPOINT.prepare

if __name__ == "__main__":
    show_command(ENDPOINT, 'reset_ip', scope=SCOPE)
