"""Contract and isolated runner checks. Never contacts a camera."""
import base64
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'cgi'
spec = importlib.util.spec_from_file_location('camera_cgi_probes', FOLDER / '__init__.py', submodule_search_locations=[str(FOLDER)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)


def load(name):
    import importlib
    return importlib.import_module('camera_cgi_probes.' + name)


class CGIProbeTests(unittest.TestCase):
    def test_all_catalogued_commands_import_without_requests(self):
        import requests
        items = json.loads((FOLDER / 'inventory.json').read_text(encoding='utf-8'))
        with patch.object(requests, 'Session', side_effect=AssertionError('Unexpected network call')):
            for item in items:
                self.assertEqual(load(item['id']).ENDPOINT.method, item['method'])
        self.assertEqual(sum(i['scope'] == 'current' for i in items), 9)
        self.assertEqual(len(items), 17)

    def test_bare_query_flags_and_fixed_reset_options_are_preserved(self):
        base = 'http://192.0.2.10'
        self.assertTrue(load('get_import_status').prepare(base).url.endswith('/vb.htm?&getimportstatus'))
        self.assertTrue(load('get_reload_flag').prepare(base).url.endswith('/vb.htm?&reloadflag'))
        for name, value in [('reset_ip', 2), ('reset_keep_ip', 3)]:
            module = load(name)
            self.assertEqual(module.prepare(base, body={}).json_body, {'options': value})
            with self.assertRaises(ValueError):
                module.prepare(base, body={'options': 0})

    def test_snapshot_and_upload_have_distinct_body_contracts(self):
        with self.assertRaises(ValueError):
            load('upload_config').prepare('http://192.0.2.10', params={'pwd': 'test-only'}, body={}, files={})
        req = load('upload_config').prepare('http://192.0.2.10', params={'pwd': 'test-only'}, body={}, files={'file': ('c.bin', b'config', 'application/octet-stream')})
        self.assertEqual(req.method, 'POST')
        self.assertFalse(req.request_kwargs()['allow_redirects'])
        self.assertTrue(load('get_snapshot').prepare('http://192.0.2.10').url.endswith('?mainstream'))

    def test_source_contract_comparison_has_no_technical_differences(self):
        workspace = next((path for path in ROOT.parents if (path / 'CGI/products').is_dir()), None)
        if workspace is None:
            self.skipTest('Workspace reference CGI library is not installed on this PC')
        report = load('compare').compare(workspace, FOLDER)
        self.assertTrue(all(row['technical_match'] for row in report['comparison']))

    def test_http_200_does_not_verify_writes_or_accept_false_status(self):
        common = load('common')
        for name in ('reset_ip', 'reset_keep_ip', 'upload_config', 'upload_firmware', 'set_tcp'):
            reply = common.inspect_reply(name, 200, b'OK')
            self.assertFalse(reply['physical_effect_verified'])
            self.assertEqual(reply['interpretation'], 'http_accepted_effect_unverified')
        self.assertEqual(common.inspect_reply('get_import_status', 200, b'NOT OK getimportstatus=1')['interpretation'], 'unclassified_reply')
        for status in range(4):
            reply = common.inspect_reply('get_import_status', 200, ('UW OK getimportstatus=' + str(status)).encode())
            self.assertEqual(reply['import_status'], status)

    def test_preview_is_offline_and_masks_import_password(self):
        import requests
        output = io.StringIO()
        args = ['upload_config.py', '--base-url', 'http://192.0.2.10']
        with patch.object(sys, 'argv', args), patch.dict('os.environ', {'ITC_CONFIG_PASSWORD': 'fixture-import-secret'}), \
                patch.object(requests, 'Session', side_effect=AssertionError('Preview must be offline')), contextlib.redirect_stdout(output):
            load('common').show_command(load('upload_config').ENDPOINT, 'upload_config')
        self.assertNotIn('fixture-import-secret', output.getvalue())
        self.assertFalse(json.loads(output.getvalue())['plan']['executed'])

    def test_upload_timeout_is_unknown_and_never_retried(self):
        import requests
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / 'config.bin'
            config.write_bytes(b'test fixture')
            record = Path(temporary) / 'result.json'
            args = ['upload_config.py', '--base-url', 'http://192.0.2.10', '--execute', '--file', str(config), '--record', str(record)]
            with patch.object(sys, 'argv', args), patch.dict('os.environ', {'ITC_CAMERA_PASSWORD': 'fixture-access-secret', 'ITC_CONFIG_PASSWORD': 'fixture-import-secret'}), \
                    patch.object(requests.Session, 'request', side_effect=requests.Timeout('fixture-import-secret')) as request, contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit):
                    load('common').show_command(load('upload_config').ENDPOINT, 'upload_config')
            self.assertEqual(request.call_count, 1)
            text = record.read_text(encoding='utf-8')
            self.assertNotIn('fixture-import-secret', text)
            self.assertNotIn('fixture-access-secret', text)
            self.assertEqual(json.loads(text)['outcome'], 'unknown')

    def test_legacy_crypto_matches_rsa_and_aes_contract(self):
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        crypto = load('legacy_crypto')
        private, params = crypto.create_challenge()
        self.assertEqual(len(base64.b64decode(base64.b64decode(params['PublicKey']))), 128)
        aes = b'0123456789abcdef'
        cipher = private.public_key().encrypt(aes, rsa_padding.PKCS1v15())
        xml = '<key>' + base64.b64encode(cipher.hex().encode()).decode() + '</key><keyID>test-id</keyID>'
        params = crypto.encrypt_import_password(private, xml, 'fixture-password')
        decrypt = Cipher(algorithms.AES(aes), modes.CBC(b'0123456789ABCDEF')).decryptor()
        padded = decrypt.update(base64.b64decode(params['encryptPwd'])) + decrypt.finalize()
        unpad = padding.PKCS7(128).unpadder()
        self.assertEqual(unpad.update(padded) + unpad.finalize(), b'fixture-password')
        self.assertEqual(params['keyId'], 'test-id')

    def test_legacy_session_creation_sends_only_challenge_and_masks_tokens(self):
        import requests
        from urllib.parse import parse_qs, urlsplit
        from cryptography.hazmat.primitives.asymmetric import rsa, padding as rsa_padding
        def response(**kwargs):
            self.assertEqual(kwargs['method'], 'GET')
            self.assertEqual(urlsplit(kwargs['url']).path, '/challenge')
            public = parse_qs(urlsplit(kwargs['url']).query)['PublicKey'][0]
            modulus = int.from_bytes(base64.b64decode(base64.b64decode(public)), 'big')
            encrypted = rsa.RSAPublicNumbers(3, modulus).public_key().encrypt(b'0123456789abcdef', rsa_padding.PKCS1v15())
            body = '<key>' + base64.b64encode(encrypted.hex().encode()).decode() + '</key><keyID>fixture-session-id</keyID>'
            result = requests.Response()
            result.status_code = 200
            result._content = body.encode()
            result._content_consumed = True
            return result
        with tempfile.TemporaryDirectory() as temporary:
            tokens = Path(temporary) / 'session.json'
            record = Path(temporary) / 'result.json'
            output = io.StringIO()
            args = ['get_challenge.py', '--base-url', 'http://192.0.2.10', '--execute', '--session-file', str(tokens), '--record', str(record)]
            with patch.object(sys, 'argv', args), patch.dict('os.environ', {'ITC_CAMERA_PASSWORD':'fixture-access-secret','ITC_CONFIG_PASSWORD':'fixture-config-secret'}), \
                    patch.object(requests.Session, 'request', side_effect=response) as request, contextlib.redirect_stdout(output):
                load('common').show_command(load('get_challenge').ENDPOINT, 'get_challenge')
            self.assertEqual(request.call_count, 1)
            self.assertEqual(json.loads(tokens.read_text())['keyId'], 'fixture-session-id')
            self.assertNotIn('fixture-session-id', output.getvalue())
            self.assertNotIn('fixture-config-secret', record.read_text())


if __name__ == '__main__':
    unittest.main()
