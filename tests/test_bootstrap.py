"""Verify Japanese startup logs with a Korean Windows output encoding."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


class BootstrapEncodingTests(unittest.TestCase):
    def test_startup_logs_are_utf8_even_when_parent_requests_cp949(self):
        root = Path(__file__).resolve().parents[1]
        code = (
            "import sys;sys.path.insert(0,'src');import bootstrap;"
            "from unittest.mock import patch;from types import SimpleNamespace;"
            "p=patch.object(bootstrap.importlib,'import_module',return_value=SimpleNamespace(__version__='1'));"
            "p.start();assert bootstrap.check_modules()==[];print('韓国語PC・モジュール確認');"
            "assert bootstrap.os.environ['PYTHONIOENCODING']=='utf-8'"
        )
        result = subprocess.run([sys.executable, '-c', code], cwd=root,
                                env=dict(os.environ, PYTHONUTF8='0', PYTHONIOENCODING='cp949'),
                                capture_output=True, timeout=15,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
        self.assertIn('モジュール確認', result.stdout.decode('utf-8'))


if __name__ == '__main__':
    unittest.main()
