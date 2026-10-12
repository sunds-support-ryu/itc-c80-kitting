"""Read LPR upload policy, not an upload action. Support on NC8164-FPE is unverified."""
if __package__:
    from .common import Endpoint, show_command
else:
    from common import Endpoint, show_command

SCOPE = 'candidate'
REFERENCE = 'CGI/products/Milesight_Camera/cgi/get_lpr_upload_policy.py'
ENDPOINT = Endpoint(
    name='lpr_upload_policy',
    method='GET',
    path='/cgi-bin/operator/operator.cgi',
    auth=('basic',),
    effects='read',
    sources=('Python/CGI/PMC8266_get_dataTrans.py:107', 'Python/CGI/products/Milesight_Camera/cgi/get_lpr_upload_policy.py'),
    query=(('action', 'get.lpr.post'), ('format', 'json'), ('pmcmode', 1)),
    notes='Read LPR upload policy, not an upload action. Support on NC8164-FPE is unverified.',
)
prepare = ENDPOINT.prepare

if __name__ == "__main__":
    show_command(ENDPOINT, 'get_lpr_upload_policy', scope=SCOPE)
