"""Standalone config diagnostics. Default is read-only; --write uploads once.

Uses the selected NIC and saved credentials. Reports never contain passwords.
An attempted upload is latched on disk so rerunning cannot resend it.
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import argparse
import json
import logging
from types import SimpleNamespace
from urllib.parse import urlsplit
import config_import as cfg
import network_workflow as flow
from app_paths import APP_ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ip', required=True)
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    folder = APP_ROOT / 'data' / 'config_debug'
    folder.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s',
                        handlers=[logging.StreamHandler(), logging.FileHandler(folder / 'debug.log', encoding='utf-8')])
    log = logging.info
    settings = cfg.load_settings(APP_ROOT)
    interface = next(i for i in settings['network_interfaces'] if i['id'] == settings['network_id'])
    link = flow.Link(interface, verify_ip=False)
    found = [d for d in link.discover() if d['ip'] == args.ip]
    if len(found) != 1:
        raise SystemExit('Selected IP must resolve to exactly one L2 camera.')
    device = found[0]
    camera = SimpleNamespace(ip=args.ip, mac=device['mac'], sn=device['sn'])
    if not link.arp(camera.ip, camera.mac):
        raise SystemExit('MAC/IP ARP verification failed.')
    report_path = folder / (camera.mac.replace(':', '') + '.json')
    report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {}
    def save():
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    report.update(ip=camera.ip, mac=camera.mac, sn=camera.sn)
    transport = cfg.create_transport()
    before = (settings.get('access_username', 'admin'), settings.get('access_password', ''))
    after = (settings.get('after_config_username', 'admin'), settings.get('after_config_password', ''))
    cfg.VERIFY_CREDENTIALS = after
    accepted = []
    try:
        for name, credentials in [('before', before), ('after', after)]:
            try:
                cfg.verify_identity('http://' + camera.ip, camera, credentials, transport)
                accepted.append((name, credentials))
                log('AUTH %s OK / SN matched', name)
            except Exception as error:
                log('AUTH %s FAIL %s', name, str(error) if isinstance(error, cfg.Step3Error) else type(error).__name__)
        report['auth_valid'] = [name for name, _ in accepted]
        path, content = cfg.load_config(APP_ROOT)
        log('CONFIG %s bytes=%d password_configured=%s', Path(path).name, len(content), bool(settings.get('config_import_password')))
        log('CAMERA IP=%s MAC=%s SN=%s', camera.ip, camera.mac, camera.sn)
        save()
        if not accepted:
            raise SystemExit('Neither saved account can access this camera. Upload not sent.')
        if not args.write:
            status, body = transport('http://' + camera.ip + '/vb.htm?&getimportstatus', credentials=accepted[0][1])
            log('IMPORT STATUS HTTP=%d %s', status, body.strip()[:200])
            return
        if report.get('upload_attempted'):
            raise SystemExit('Previous upload attempted. No resend; use read-only diagnostics.')
        def traced(url, method='GET', body=None, headers=None, credentials=None):
            if method == 'POST':
                report['upload_attempted'] = True
                save()
            route = urlsplit(url).path
            log('REQUEST %s %s bytes=%d', method, route, len(body or b''))
            status, response = transport(url, method, body, headers, credentials)
            log('RESPONSE %s HTTP=%d%s', route, status, ' ' + response.strip()[:120] if route == '/vb.htm' else '')
            return status, response
        success, detail = cfg.run(camera, APP_ROOT, accepted[0][1], transport=traced, log=log,
                                  reset_ip=False, credential_report=lambda c: report.update(access_stage='after' if c == after else 'before'))
        report.update(success=success, detail=detail)
        save()
        log('RESULT success=%s %s', success, detail)
        if not success:
            raise SystemExit(1)
    finally:
        transport.close()


if __name__ == '__main__':
    main()
