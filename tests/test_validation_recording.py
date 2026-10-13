"""Private .py evidence and target-only transport checks; no real camera access."""
import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'validation'))
from recording import Recorder, read_literal


class ValidationRecordingTests(unittest.TestCase):
    def test_timestamps_durations_and_secrets_are_stored_as_literal_python(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            recorder = Recorder(folder, ['fixture-password'])
            recorder.call('get_info', 'sample', lambda: {'url':'http://device/?pwd=fixture-password','password':'fixture-password'})
            data = read_literal(Path(folder)/'validation_record.py', 'REPORT')
            event = data['events'][0]
            self.assertIn('utc', event['started'])
            self.assertIn('+09:00', event['started']['jst'])
            self.assertGreaterEqual(event['duration_ms'], 0)
            self.assertNotIn('fixture-password', (Path(folder)/'validation_record.py').read_text())
            self.assertTrue((Path(folder)/'get_info.py').exists())

    def test_latch_survives_restart_and_prevents_repeat(self):
        with tempfile.TemporaryDirectory() as folder:
            Recorder(folder).latch('upload_config')
            with self.assertRaises(ValueError): Recorder(folder).latch('upload_config')

    def test_target_link_sends_vendor_frames_only_to_target_mac(self):
        import module_validation as module
        from scapy.all import Ether, Raw
        sent = []
        def initialize(link, interface, **kwargs):
            link.Ether=Ether
            link.sendp=lambda packet, **kw:sent.append(packet)
        with patch.object(module.network.Link, '__init__', initialize):
            link=module.TargetLink({},'02:00:00:00:00:01')
            link.sendp(Ether(dst='ff:ff:ff:ff:ff:ff')/Raw(b'test'))
        self.assertEqual(sent[0].dst,'02:00:00:00:00:01')

    def test_hardware_cli_is_plan_only_without_execute(self):
        import module_validation as module
        with patch.object(sys,'argv',['module_validation.py','--run','unused']), \
                patch.object(module,'Validator',side_effect=AssertionError('Unexpected hardware access')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            module.main()
        self.assertIn('"executed": false', output.getvalue())

    def test_recorded_serial_binding_cannot_be_silently_changed(self):
        import module_validation as module
        private={'config_import_password':'fixture','viewer_user':'fixture','viewer_password':'fixture','target_mac':'02:00:00:00:00:01'}
        settings={'network_id':'wired','network_interfaces':[{'id':'wired'}]}
        record=SimpleNamespace(data={'metadata':{'target':{'sn':'OLD'}}})
        with patch.object(module.cfg,'load_settings',return_value=settings), patch.object(module,'Recorder',return_value=record), \
                patch.object(module,'TargetLink'), patch.object(module.Validator,'locate',return_value={'sn':'NEW'}):
            with self.assertRaises(ValueError):module.Validator(private,'unused')

    def test_tcp_write_cannot_leave_authorized_subnet(self):
        import module_validation as module
        validator=module.Validator.__new__(module.Validator)
        validator.private={'test_ip_b':'192.168.0.230'}
        validator.guard_identity=lambda:None
        validator.link=SimpleNamespace(arp=lambda ip:False)
        with self.assertRaises(ValueError):validator.tcp()


if __name__ == '__main__': unittest.main()
