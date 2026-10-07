"""Offline GitHub assets, hash failure, rollback and protected user files."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import yaml
import launcher_core as core


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.names = ['step0.py', 'web_ui.py', 'web/index.html']
        self.entries, self.assets, self.responses = [], [], {}
        for name in self.names:
            dst = self.root / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(b'old')
            asset = name.replace('/', '__')
            content = ('new-' + name).encode()
            self.entries.append({'path': name, 'asset': asset, 'version': '2.1.0',
                                 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()})
            url = 'https://example.com/' + asset
            self.assets.append({'name': asset, 'browser_download_url': url, 'url': url})
            self.responses[url] = content
        self.assets.append({'name': 'update-manifest.yaml', 'url': 'https://example.com/manifest', 'browser_download_url': 'https://example.com/manifest'})
        self.manifest = {'schema': 1, 'version': '2.1.0', 'entrypoint': 'step0.py', 'files': self.entries}
        self.updater = core.Updater(self.root, 'owner/repo', log=lambda text: None)
        self.addCleanup(self.updater.close)
        def fetch(url, *args):
            if '/releases/latest' in url: return json.dumps({'assets': self.assets}).encode()
            if url.endswith('/manifest'): return yaml.safe_dump(self.manifest).encode()
            return self.responses[url]
        self.updater.fetch = fetch

    def test_success_downloads_code_and_keeps_production_files(self):
        records = self.root / 'records' / 'settings.json'
        records.parent.mkdir()
        records.write_text('preserve')
        self.updater.update()
        self.assertEqual((self.root / 'step0.py').read_bytes(), b'new-step0.py')
        self.assertEqual(records.read_text(), 'preserve')
        self.assertTrue((self.root / 'data/installed_versions.yaml').exists())
        self.assertFalse((self.root / 'data/updates/transaction.yaml').exists())

    def test_hash_failure_never_replaces_any_code(self):
        self.responses['https://example.com/web_ui.py'] = b'corrupt'
        with self.assertRaises(ValueError): self.updater.update()
        for name in self.names: self.assertEqual((self.root / name).read_bytes(), b'old')

    def test_manifest_cannot_write_production_settings(self):
        self.entries[0]['path'] = 'records/settings.json'
        with self.assertRaises(ValueError): self.updater.update()

    def test_replace_error_rolls_back_already_replaced_code(self):
        original = core.os.replace
        def replace(source, destination):
            if Path(destination) == self.root / 'web_ui.py': raise PermissionError('locked')
            return original(source, destination)
        with patch.object(core.os, 'replace', side_effect=replace), self.assertRaises(PermissionError):
            self.updater.update()
        for name in self.names: self.assertEqual((self.root / name).read_bytes(), b'old')

    def test_interrupted_update_recovers_before_network_check(self):
        backup = self.root / 'data/update_backups/interrupted'
        backup.mkdir(parents=True)
        (backup / 'step0.py').write_bytes(b'old')
        (self.root / 'step0.py').write_bytes(b'partially replaced')
        core.save_yaml(self.root / 'data/updates/transaction.yaml',
            {'backup': 'data/update_backups/interrupted', 'files': [{'path': 'step0.py', 'existed': True}]})
        self.updater.fetch = lambda *a: (_ for _ in ()).throw(OSError('offline'))
        with self.assertRaises(OSError): self.updater.update()
        self.assertEqual((self.root / 'step0.py').read_bytes(), b'old')
