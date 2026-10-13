"""Read actual user roles/permission masks; discovered from the device user-management UI."""
if __package__:
    from .common import Endpoint, show_command
else:
    from common import Endpoint, show_command

SCOPE = 'observed_device_frontend'
ENDPOINT = Endpoint(
    name='get_system_user', method='GET', path='/cgi-bin/admin/admin.cgi',
    auth=('digest', 'basic'), effects='read',
    sources=('Device firmware Web UI: API module 8593, get.system.user; user management ADMIN=0/OPERATOR=1/VIEWER=2',),
    query=(('action','get.system.user'),('format','json')),
    notes='Read userList roles and permission masks. Do not infer viewer restrictions from its name alone.',
)
prepare = ENDPOINT.prepare

if __name__ == '__main__':
    show_command(ENDPOINT, 'get_users', scope=SCOPE)
