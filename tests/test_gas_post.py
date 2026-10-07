from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
"""Offline response contract tests, no external HTTP."""
import json
import unittest
from unittest.mock import Mock
import gas_post


class Response:
    def __init__(self, body, status=202):
        self.status_code = status
        self.body = body.encode()
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def iter_content(self, size): return iter([self.body])


class Session:
    def __init__(self, response): self.post = Mock(return_value=response)
    def __enter__(self): return self
    def __exit__(self, *args): pass


class PostTests(unittest.TestCase):
    def send(self, body, status=202):
        self.session = Session(Response(body, status))
        return gas_post.send('https://script.google.com/macros/s/example/exec',
                             {'record_id': 'record-1'}, session_factory=lambda: self.session)

    def test_business_ack_success_does_not_require_http_200(self):
        result = self.send('{"ok":true,"record_id":"record-1"}', 202)
        self.assertEqual(result['http_status'], 202)
        self.assertTrue(self.session.post.call_args.kwargs['allow_redirects'])

    def test_http_200_with_rejection_is_failure_and_preserves_reply(self):
        with self.assertRaises(gas_post.PostError) as failure:
            self.send('{"ok":false,"record_id":"record-1","error":"rejected"}', 200)
        self.assertEqual(failure.exception.response['body']['error'], 'rejected')

    def test_wrong_record_id_and_login_html_are_failures(self):
        for body in ('{"ok":true,"record_id":"other"}', '<html>Sign in</html>', '{"ok":"true","record_id":"record-1"}'):
            with self.subTest(body=body), self.assertRaises(gas_post.PostError):
                self.send(body, 200)

    def test_url_must_be_http_without_embedded_credentials(self):
        for url in ('file:///secret', 'https://user:password@example.com/', ''):
            with self.subTest(url=url), self.assertRaises(ValueError):
                gas_post.validate_url(url)
