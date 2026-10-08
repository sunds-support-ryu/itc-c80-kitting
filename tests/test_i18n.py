"""Language resources and English backend boundaries; no real camera access."""
import ast
import contextlib
import io
from pathlib import Path
import re
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from i18n import Translator
from locales.en import MESSAGES as EN
from locales.ja import MESSAGES as JA


class LocalizationTests(unittest.TestCase):
    def test_language_packs_cover_same_keys_and_named_placeholders(self):
        self.assertEqual(set(EN), set(JA))
        for key in EN:
            self.assertEqual(set(re.findall(r'\{(field\d+|stage|detail)\}', EN[key])),
                             set(re.findall(r'\{(field\d+|stage|detail)\}', JA[key])), key)

    def test_english_reference_and_backend_literals_are_not_japanese(self):
        pattern = re.compile('[\u3040-\u30ff\u4e00-\u9fff]')
        for key, value in EN.items():
            self.assertFalse(pattern.search(value), key)
        for source in (Path(__file__).resolve().parents[1] / 'src').glob('*.py'):
            for node in ast.walk(ast.parse(source.read_text(encoding='utf-8'))):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    self.assertFalse(pattern.search(node.value), f'{source.name}:{node.lineno}')

    def test_japanese_ui_does_not_change_english_backend_message(self):
        t = Translator('ja')
        source = 'Operation 1: cover the camera. Waiting for automatic black-and-white confirmation.'
        self.assertIn('蓋', t.text(source))
        self.assertIn('Operation 1', source)
        self.assertEqual(t.text('Camera record: THIRD-PARTY-DATA'), 'Camera record: THIRD-PARTY-DATA')

    def test_unsupported_language_and_missing_translation_fall_back_to_english(self):
        self.assertEqual(Translator('ko').text('Start inspection'), 'Start inspection')
        t = Translator('ja')
        t.messages = {}
        self.assertEqual(t.text('Start inspection'), 'Start inspection')

    def test_templated_message_preserves_device_identity(self):
        t = Translator('ja')
        translated = t.text('Target IP for this camera: 192.168.0.220')
        self.assertIn('192.168.0.220', translated)


if __name__ == '__main__':
    unittest.main()
