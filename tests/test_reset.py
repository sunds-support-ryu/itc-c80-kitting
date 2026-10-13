"""One shared reset function; no physical resets during tests."""
from pathlib import Path
import sys
import json
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from camera_cgi.reset import reset, validate_option


class ResetTests(unittest.TestCase):
    def test_all_four_options_use_one_post_and_preserve_option_value(self):
        for option in (0,1,2,3):
            calls=[]
            def transport(*args):calls.append(args);return 200,'accepted'
            reply=reset('http://192.0.2.10',option,('fixture-user','fixture-password'),transport=transport)
            self.assertEqual(reply,(200,'accepted'))
            self.assertEqual(len(calls),1)
            self.assertEqual(calls[0][1],'POST')
            self.assertEqual(json.loads(calls[0][2]),{'options':option})
            self.assertTrue(calls[0][0].endswith('?action=set.system.reset&format=json'))

    def test_invalid_options_rejected_before_transport(self):
        for option in (4,5,8,-1,True,False,'2',2.0,None):
            with self.assertRaises(ValueError):validate_option(option)

    def test_lost_response_is_not_retried(self):
        calls=[]
        def transport(*args):calls.append(args);raise TimeoutError('lost response')
        with self.assertRaises(TimeoutError):reset('http://192.0.2.10',3,('fixture','fixture'),transport=transport)
        self.assertEqual(len(calls),1)

    def test_embedded_credentials_and_redirect_style_origins_rejected(self):
        for url in ('http://user:password@device','http://device/path','http://device?query=1','file:///tmp'):
            with self.assertRaises(ValueError):reset(url,1,('fixture','fixture'),transport=lambda *args:None)


if __name__=='__main__':unittest.main()
