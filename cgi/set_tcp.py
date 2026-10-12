"""IP settings write. Full IPv4/IPv6 fields required by source. Timeout means unknown, not success. Current tool instead uses L2 MAC SET."""
if __package__:
    from .common import Endpoint, show_command
else:
    from common import Endpoint, show_command

SCOPE = 'candidate'
REFERENCE = 'CGI/products/Milesight_Camera/cgi/set_tcp.py'
ENDPOINT = Endpoint(
    name='set_tcp_network',
    method='POST',
    path='/cgi-bin/admin/admin.cgi',
    auth=('digest',),
    effects='network',
    sources=('Python/ITEC/change_ip.py:39', 'Python/CGI/products/Milesight_Camera/cgi/set_tcp.py'),
    query=(('action', 'set.network.tcp'), ('format', 'json')),
    encoding='json',
    required_body=('mtuByte', 'ipv4Ipaddress', 'ipv4DhcpEnable', 'ipv4netmask', 'ipv4gateway', 'dns0', 'ipv6Mode', 'ipv6Ipaddress', 'ipv6netmask', 'ipv6gateway'),
    notes='IP settings write. Full IPv4/IPv6 fields required by source. Timeout means unknown, not success. Current tool instead uses L2 MAC SET.',
)
prepare = ENDPOINT.prepare

if __name__ == "__main__":
    show_command(ENDPOINT, 'set_tcp', scope=SCOPE)
