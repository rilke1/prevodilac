import ssl,unittest
from unittest.mock import patch
from updater import tls_context,fetch
class Response:
 def __enter__(self):return self
 def __exit__(self,*args):pass
 def read(self,n):return b'ok'
class TLSTests(unittest.TestCase):
 def test_does_not_load_windows_default_store(self):
  with patch.object(ssl.SSLContext,'load_default_certs',side_effect=ssl.SSLError('ASN1')):
   ctx=tls_context()
   self.assertEqual(ctx.verify_mode,ssl.CERT_REQUIRED)
   self.assertTrue(ctx.check_hostname)
   self.assertGreater(len(ctx.get_ca_certs()),0)
 def test_fetch_supplies_verified_context(self):
  with patch('updater.urllib.request.urlopen',return_value=Response()) as opened:
   self.assertEqual(fetch('https://api.github.com/repos/a/b/releases/latest'),b'ok')
   ctx=opened.call_args.kwargs['context'];self.assertTrue(ctx.check_hostname)
   self.assertEqual(ctx.verify_mode,ssl.CERT_REQUIRED)
  with self.assertRaises(ValueError):fetch('http://example.com')
if __name__=='__main__':unittest.main()
