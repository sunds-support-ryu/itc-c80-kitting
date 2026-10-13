"""Authorized phased camera validation. Secrets and measured records stay in data/.

Default phase is read-only baseline. Modifying phases require --execute and
are latched before dispatch. Target identity is bound by L2 MAC and HTTP SN.
"""
import argparse
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import config_import as cfg
import network_workflow as network
from validation_recording import Recorder, read_literal

spec = importlib.util.spec_from_file_location('hardware_validation_cgi', Path(__file__).with_name('__init__.py'), submodule_search_locations=[str(Path(__file__).parent)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)


def endpoint(name):
    import importlib
    return importlib.import_module('hardware_validation_cgi.' + name).ENDPOINT


class TargetLink(network.Link):
    """Direct vendor L2 requests only to the bound camera MAC, never another camera."""
    def __init__(self, interface, target_mac):
        super().__init__(interface, verify_ip=False)
        original = self.sendp
        def directed(packet, **kwargs):
            packet = packet.copy()
            packet[self.Ether].dst = target_mac
            return original(packet, **kwargs)
        self.sendp = directed


class Validator:
    def __init__(self, private, folder):
        self.private = private
        self.settings = cfg.load_settings(ROOT)
        self.profiles = {
            'admin_before': (self.settings.get('access_username', 'admin'), self.settings.get('access_password', '')),
            'admin_after': (self.settings.get('after_config_username', 'admin'), self.settings.get('after_config_password', '')),
            'viewer_test': (private['viewer_user'], private['viewer_password'])}
        secrets = [private['config_import_password'], *[value[1] for value in self.profiles.values()]]
        self.record = Recorder(folder, secrets)
        interface = next(item for item in self.settings['network_interfaces'] if item['id'] == self.settings['network_id'])
        self.mac = network.mac(private['target_mac'])
        self.link = TargetLink(interface, self.mac)
        self.sessions = {}
        self.device = self.locate()
        previous=self.record.data['metadata'].get('target',{})
        if previous.get('sn') and previous['sn'] != self.device.get('sn'):
            raise ValueError('Recorded target SN changed; use a separately authorized validation run')
        if (self.device.get('oem_model') or self.device.get('model')) != 'NC8164-FPE':
            raise ValueError('Modifying validation is bound to NC8164-FPE only')
        self.sn = self.device['sn']
        self.record.data['metadata'].update(target=self.device, pc_ip=self.link.pc_ip,
            config_file=Path(private['config_file']).name,
            config_sha256=hashlib.sha256((ROOT / private['config_file']).read_bytes()).hexdigest())
        self.record.save()

    def locate(self):
        devices = self.record.call('l2_discovery', 'identify_bound_mac_only',
            lambda:[device for device in self.link.discover() if network.mac(device['mac'])==self.mac])
        selected = [device for device in devices if network.mac(device['mac']) == self.mac]
        if len(selected) != 1:
            raise ValueError('Target MAC must be found exactly once')
        if not selected[0]['ip'].startswith('192.168.5.'):
            raise ValueError('Test writes are restricted to the authorized 192.168.5.x network')
        return selected[0]

    def request(self, name, context, profile=None, params=None, body=None, content=None, auth='digest', browser_style=False):
        import requests
        from requests.auth import HTTPBasicAuth, HTTPDigestAuth
        profile = profile or self.record.data['metadata'].get('verified_admin', 'admin_before')
        credentials = self.profiles[profile]
        key = (profile, auth)
        if key not in self.sessions:
            session = requests.Session()
            session.trust_env = False
            session.auth = (HTTPBasicAuth if auth == 'basic' else HTTPDigestAuth)(*credentials)
            self.sessions[key] = session
        contract = endpoint(name)
        files = {'file': (Path(self.private['config_file']).name, content, 'application/octet-stream')} if content is not None else None
        base = 'http://' + self.device['ip']
        request = contract.prepare(base, params=params, body=(body or {}) if contract.encoding != 'none' else None,
                                   files=files, headers={'User-Agent':'Mozilla/5.0','Referer':base+'/','Origin':base,'X-From':'Web','X-Requested-With':'XMLHttpRequest'})
        if browser_style and name=='upload_config':
            from dataclasses import replace
            request=replace(request,url=request.url.replace('%40','@'))
        def send():
            begin=time.perf_counter_ns()
            with self.sessions[key].request(**request.request_kwargs(timeout=10 if name not in ('upload_config','upload_config_legacy') else 60),stream=True) as response:
                headers_received=time.perf_counter_ns()
                raw = response.content
                body_received=time.perf_counter_ns()
                text = raw.decode('utf-8-sig', errors='replace')
                try:
                    parsed = json.loads(text)
                except ValueError:
                    parsed = None
                return {'http_status':response.status_code,'auth_profile':profile,'auth_scheme':auth,
                    'auth_http_attempts':len(response.history)+1, 'method':request.method,
                    'http_headers_ms':round((headers_received-begin)/1_000_000,3),
                    'http_complete_ms':round((body_received-begin)/1_000_000,3),
                    'body_transfer_ms':round((body_received-headers_received)/1_000_000,3),
                    'url':request.url, 'response_bytes':len(raw),'response_sha256':hashlib.sha256(raw).hexdigest(),
                    'content_type':response.headers.get('Content-Type',''),
                    'json_response':parsed, 'response_text':text[:12000] if name!='get_snapshot' else '',
                    'reply':__import__('hardware_validation_cgi.common', fromlist=['inspect_reply']).inspect_reply(name,response.status_code,raw),
                    '_raw':raw}
        return self.record.call(name, context, send)

    def admin(self):
        for profile in ('admin_before','admin_after'):
            try:
                result = self.request('get_info','admin_identity_'+profile,profile)
                if result['http_status']==200 and cfg.serial_number(result['json_response']) == self.sn:
                    self.record.data['metadata']['verified_admin']=profile
                    self.record.mark_effect('get_info',{'sn_matched':self.sn,'profile':profile,'ip':self.device['ip']})
                    return profile
            except Exception:
                raise ValueError('HTTP identity service not ready; wait before trying another account')
        raise ValueError('Neither saved admin profile can verify the target SN')

    def baseline(self):
        profile = self.admin()
        for name in ('get_info','get_import_status','get_reload_flag'):
            for sample in range(3):
                self.request(name,'baseline_sample_'+str(sample),profile)
        for actor in (profile,'viewer_test'):
            for auth in ('digest','basic'):
                self.request('get_info','access_matrix_before_config',actor,auth=auth)
                self.request('get_snapshot','snapshot_access_before_config',actor,auth=auth)
        for name,params,auth in [('get_log',{'file':'/mnt/nand/err_log.txt'},'basic'),('get_lpr_upload_policy',{},'basic'),('get_parking_detection',{},'basic')]:
            try:
                self.request(name,'read_only_capability',profile,params=params,auth=auth)
            except Exception:
                pass
        try:
            cfg.load_config(ROOT)
        except cfg.Step3Error as error:
            self.record.data['metadata']['production_config_selection']=str(error)
        self.record.data['pending']=[{'module':'upload_firmware','reason':'Await matching firmware from user; remind user'},
            {'module':'unlock_upgrade','reason':'Deferred together with firmware test'},
            {'module':'goto_preset','reason':'Fixed-lens model; physical PTZ capability not established'},
            {'module':'ir_cut_light_sensor','reason':'Requires operator cover/remove actions; not a CGI'}]
        self.record.save()

    def guard_identity(self):
        self.device = self.locate()
        if self.device['sn'] != self.sn:
            raise ValueError('L2 SN changed')
        self.admin()

    def move_l2(self, target):
        self.guard_identity()
        if target == self.device['ip']:
            return
        if not target.startswith('192.168.5.') or self.link.arp(target):
            raise ValueError('Target IP outside allowed range or already occupied')
        profile=self.record.data['metadata']['verified_admin']
        self.record.call('l2_set_ip','move_to_'+target,lambda:self.link.set_ip(self.mac,target,self.private['mask'],self.private['gateway'],self.profiles[profile]))
        self.device['ip']=target
        if not self.link.wait_ip(self.mac,target):
            raise ValueError('MAC/IP not verified after L2 change; no resend')
        self.record.mark_effect('l2_set_ip',{'mac':self.mac,'ip':target})
        self.wait_recovery('l2_http',expected_ip=target)

    def wait_recovery(self, name, timeout=90, expected_ip=None):
        start=time.perf_counter()
        deadline=time.monotonic()+timeout
        errors=[]
        while time.monotonic()<deadline:
            try:
                self.device=self.locate()
                if self.device['sn']!=self.sn:
                    raise ValueError('Identity changed')
                profile=self.admin()
                if expected_ip and self.device['ip']!=expected_ip:
                    raise ValueError('Unexpected resulting IP')
                self.record.mark_effect(name+'_recovery',{'recovered_after_ms':round((time.perf_counter()-start)*1000,3),
                    'device':self.device,'admin_profile':profile})
                return
            except Exception as error:
                errors.append(type(error).__name__)
                time.sleep(1)
        raise ValueError('Recovery unverified; no resend. Errors: '+','.join(errors[-4:]))

    def reset(self, name):
        self.guard_identity()
        original=self.device['ip']
        self.record.latch(name)
        try:
            self.request(name,'single_reset_dispatch')
        except Exception:
            pass
        offline_seen=False
        for attempt in range(10):
            if not self.link.arp(original,self.mac):
                offline_seen=True
                break
            time.sleep(.25)
        self.wait_recovery(name,expected_ip=original if name=='reset_keep_ip' else None)
        changed=self.device['ip']!=original
        evidence={'original_ip':original,'resulting_device':self.device,'offline_observed':offline_seen,'ip_changed':changed}
        if offline_seen or changed:
            self.record.mark_effect(name,evidence)
        self.record.finish_phase(name,evidence)

    def config(self, legacy=False, browser_style=False):
        name='upload_config_legacy' if legacy else 'upload_config'
        phase='upload_config_browser_url' if browser_style else name
        self.guard_identity()
        self.record.latch(phase)
        self.request('clear_import_status','before_'+name)
        status=self.request('get_import_status','verify_clear_before_'+name)
        if status['reply'].get('import_status')!=0:
            raise ValueError('Import status not zero; upload not sent')
        params={'pwd':self.private['config_import_password']}
        if legacy:
            from legacy_crypto import create_challenge,encrypt_import_password
            private,challenge=create_challenge()
            response=self.request('get_challenge','legacy_handshake',params=challenge)
            params=encrypt_import_password(private,response['_raw'].decode('utf-8-sig'),self.private['config_import_password'])
        content=(ROOT/self.private['config_file']).read_bytes()
        self.request(name,'browser_url_compatibility' if browser_style else 'single_upload_dispatch',params=params,content=content,browser_style=browser_style)
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            result=self.request('get_import_status','poll_'+name)
            code=result['reply'].get('import_status')
            if code in (2,3):
                raise ValueError('Config import rejected with status '+str(code))
            if code==1:
                break
            time.sleep(1)
        else:
            raise ValueError('Config outcome unverified; no resend')
        self.request('clear_import_status','acknowledge_'+name)
        time.sleep(15)  # Device maintenance UI waits 15 seconds before readiness polling.
        self.wait_recovery(phase,timeout=180)
        viewer=self.request('get_snapshot','viewer_account_after_'+name,'viewer_test')
        if viewer['reply']['interpretation']!='image_decoded':
            raise ValueError('New viewer account could not retrieve a snapshot')
        self.record.mark_effect(name,{'viewer_account_verified':self.private['viewer_user'],'sn':self.sn,'device':self.device})
        self.request('get_info','viewer_admin_boundary_after_'+name,'viewer_test')
        self.record.finish_phase(phase,{'viewer_verified':True,'device':self.device})

    def tcp(self):
        self.guard_identity()
        target=self.private['test_ip_b']
        if not target.startswith('192.168.5.') or self.link.arp(target):
            raise ValueError('TCP target already occupied')
        self.record.latch('set_tcp')
        body={'mtuByte':1500,'ipv4Ipaddress':target,'ipv4DhcpEnable':0,'ipv4netmask':self.private['mask'],
              'ipv4gateway':self.private['gateway'],'dns0':self.private['gateway'],'ipv6Mode':0,
              'ipv6Ipaddress':'','ipv6netmask':0,'ipv6gateway':''}
        try:
            self.request('set_tcp','single_network_write',body=body)
        except Exception:
            pass
        self.wait_recovery('set_tcp',expected_ip=target)
        self.record.finish_phase('set_tcp',{'device':self.device})

    def close(self):
        for session in self.sessions.values():
            session.close()


def main():
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings',type=Path,default=ROOT/'data/module_validation/private_settings.py')
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--phase',choices=('baseline','l2','reset_keep_ip','modern_config','modern_browser_config','legacy_config','tcp','reset_ip'),default='baseline')
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    if not args.execute:
        print(json.dumps({'phase':args.phase,'run':str(args.run),'executed':False,
            'note':'Plan only. --execute is required for all hardware requests.'}))
        return
    private=read_literal(args.settings,'SETTINGS')
    validator=Validator(private,args.run)
    try:
        if args.phase=='baseline':validator.baseline()
        elif args.phase=='l2':
            validator.record.latch('l2');validator.move_l2(private['test_ip_a']);validator.record.finish_phase('l2',{'device':validator.device})
        elif args.phase=='modern_config':validator.config()
        elif args.phase=='modern_browser_config':validator.config(browser_style=True)
        elif args.phase=='legacy_config':validator.config(legacy=True)
        elif args.phase=='tcp':validator.tcp()
        else:validator.reset(args.phase)
    except Exception as error:
        key={'modern_config':'upload_config','modern_browser_config':'upload_config_browser_url','legacy_config':'upload_config_legacy','tcp':'set_tcp'}.get(args.phase,args.phase)
        if key in validator.record.data['phases']:
            validator.record.data['phases'][key].update(state='UNVERIFIED_NO_RESEND',error=validator.record.safe(str(error)))
            validator.record.save()
        raise
    finally:
        validator.close()


if __name__=='__main__':main()
