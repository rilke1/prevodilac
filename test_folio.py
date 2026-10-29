import json, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
import app

class FolioTests(unittest.TestCase):
 def test_readable_epub_spine_and_inline_text(self):
  with tempfile.TemporaryDirectory() as directory:
   path=Path(directory)/'book.epub'
   with zipfile.ZipFile(path,'w') as z:
    z.writestr('META-INF/container.xml','<container><rootfiles><rootfile full-path="OPS/content.opf"/></rootfiles></container>')
    z.writestr('OPS/content.opf','<package xmlns:dc="http://purl.org/dc/elements/1.1/"><metadata><dc:title>Book</dc:title><dc:creator>Author</dc:creator></metadata><manifest><item id="one" href="one.xhtml"/><item id="two" href="two.xhtml"/></manifest><spine><itemref idref="two"/><itemref idref="one"/></spine></package>')
    z.writestr('OPS/one.xhtml','<html><body><p>Last</p></body></html>')
    z.writestr('OPS/two.xhtml','<html><head><style>ignore</style></head><body><h1>First</h1><p>Hello <em>world</em> today.</p></body></html>')
   result=app.folio_document(path)
   self.assertEqual(result['title'],'Book');self.assertEqual(result['author'],'Author')
   self.assertIn('Hello world today.',result['chapters'][0]['text'])
   self.assertNotIn('ignore',result['chapters'][0]['text'])
   self.assertEqual(result['chapters'][1]['text'],'Last')
 def test_process_returns_original_and_translation(self):
  import threading
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);src=root/'a.epub'
   with zipfile.ZipFile(src,'w') as z:
    z.writestr('mimetype','application/epub+zip')
    z.writestr('META-INF/container.xml','<container><rootfile full-path="content.opf"/></container>')
    z.writestr('content.opf','<package><metadata><title>Book</title></metadata><manifest><item id="a" href="a.xhtml"/></manifest><spine><itemref idref="a"/></spine></package>')
    z.writestr('a.xhtml','<html><body><p>Hello world.</p></body></html>')
   calls=[]
   def call(cfg,action,fields=None,files=None,job=None):
    if action=='input':return src.read_bytes()
    calls.append((action,fields,files))
    return {'ok':True}
   original_book=app.Book
   def book(path,model,source):return original_book(path,model,source,root)
   with patch('app.BASE',root),patch('app.folio_call',side_effect=call),patch('app.Book',side_effect=book),patch('app.translate',side_effect=lambda text,*args:'Prevod '+text):
    app.folio_process({}, {'id':'test','lease':'lease','source':'English','filename':'a.epub'})
   self.assertEqual(calls[-1][0],'finish')
   self.assertIn('Prevod Hello world.',json.loads(calls[-1][1]['document'])['chapters'][0]['text'])
   self.assertIn('Hello world.',json.loads(calls[-1][1]['originalDocument'])['chapters'][0]['text'])
   self.assertEqual(set(calls[-1][2]),{'file','originalFile'})

 def test_device_token_sent_only_to_fixed_folio_site(self):
  class Response:
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def read(self,*args):return b'{"job":null}'
  cfg={'site':'https://evil.invalid','serviceToken':'secret-service','deviceToken':'secret-device'}
  with patch('app.folio_tls',return_value='tls'),patch('app.folio_open',return_value=Response()) as opened:
   app.folio_call(cfg,'claim')
  request=opened.call_args.args[0]
  self.assertTrue(request.full_url.startswith(app.FOLIO_SITE+'/'))
  self.assertEqual(request.get_header('X-folio-device'),'secret-device')
 def test_private_download_address_rejected(self):
  with tempfile.TemporaryDirectory() as directory:
   with patch('socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):
    with self.assertRaisesRegex(ValueError,'privatnu mrežu'):
     app.folio_download('https://example.org/a.epub',directory)
 def test_two_queues_cannot_translate_together(self):
  with tempfile.TemporaryDirectory() as directory,patch('app.BASE',Path(directory)):
   with app.TranslationGate():
    with self.assertRaises(BlockingIOError):
     with app.TranslationGate():pass
   with app.TranslationGate():pass

if __name__=='__main__':unittest.main()
