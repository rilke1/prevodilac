import json, tempfile, threading, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
from app import Book, translate

class Response:
    def __init__(self,data): self.data=data
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def read(self): return json.dumps(self.data).encode()

class LengthTests(unittest.TestCase):
    def responder(self,calls):
        def respond(request,**kwargs):
            body=json.loads(request.data)
            text=body['messages'][0]['content'].split('\n\n\n',1)[1]
            calls.append((text,body['options']))
            if len(text)>160:
                return Response({'done_reason':'length','message':{'content':'ODSECENO'}})
            return Response({'done_reason':'stop','message':{'content':text}})
        return respond

    def test_recursive_split_preserves_text_and_settings(self):
        text=('First sentence. Second sentence.\n\n'*25).strip();calls=[]
        with patch('app.urllib.request.urlopen',side_effect=self.responder(calls)):
            result=translate(text,'test',('English','en'),{'device':'CPU','percent':25})
        self.assertEqual(result,text)
        self.assertGreater(len(calls),3)
        self.assertTrue(all(options['num_gpu']==0 for _,options in calls))
        self.assertNotIn('ODSECENO',result)

    def test_permanent_length_error_is_bounded(self):
        with patch('app.urllib.request.urlopen',return_value=Response({'done_reason':'length'})) as opened:
            with self.assertRaisesRegex(RuntimeError,'kratkom tekstu'):
                translate('word '*440,'test',('English','en'))
        self.assertLessEqual(opened.call_count,6)

    def test_single_epub_and_resume_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);src=root/'original.epub';out=root/'translated.epub'
            text=('First sentence. Second sentence. '*25).strip();calls=[]
            with zipfile.ZipFile(src,'w') as z:
                z.writestr('mimetype','application/epub+zip')
                z.writestr('text.xhtml','<html><body><p>'+text+'</p></body></html>')
            with patch('app.urllib.request.urlopen',side_effect=self.responder(calls)):
                for attempt in range(2):
                    book=Book(src,'test',('English','en'),root)
                    book.run(threading.Event(),lambda *args:None,out);book.close()
                    if attempt==0: first_count=len(calls)
                self.assertEqual(len(calls),first_count)
            with zipfile.ZipFile(out) as z:
                self.assertIn(text,z.read('text.xhtml').decode())
                self.assertNotIn('ODSECENO',z.read('text.xhtml').decode())
            self.assertEqual(len(list(root.glob('*.epub'))),2)

    def test_connection_error_is_not_split(self):
        import urllib.error
        with patch('app.urllib.request.urlopen',side_effect=urllib.error.URLError('offline')) as opened:
            with self.assertRaisesRegex(RuntimeError,'Ollama nije dostupna'):
                translate('word '*440,'test',('English','en'))
        self.assertEqual(opened.call_count,1)

if __name__=='__main__': unittest.main()
