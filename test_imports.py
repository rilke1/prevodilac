import tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
import pymupdf
from app import prepare_book,Book,LANGS,Paused,text_epub
class ImportTests(unittest.TestCase):
 def test_txt_and_pdf_to_readable_epub(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);txt=p/'book.txt';txt.write_text('A real paragraph with <markup> & text.\n\nSecond paragraph.',encoding='utf-8')
   out=prepare_book(txt,LANGS['Engleski'],threading.Event(),lambda s:None,p)
   b=Book(out,'m',LANGS['Engleski'],p);self.assertTrue(b.total);b.close()
   self.assertEqual(out,prepare_book(txt,LANGS['Engleski'],threading.Event(),lambda s:None,p))
   pdf=p/'book.pdf'
   with pymupdf.open() as d:
    page=d.new_page();page.insert_text((72,72),'This is a sufficiently long paragraph from a PDF document.');d.save(pdf)
   out=prepare_book(pdf,LANGS['Engleski'],threading.Event(),lambda s:None,p)
   b=Book(out,'m',LANGS['Engleski'],p);self.assertTrue(any('sufficiently' in ''.join(parts) for _,_,_,parts in b.slots));b.close()
 def test_converter_missing_and_success(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);src=p/'book.mobi';src.write_bytes(b'sample')
   with patch('app.find_tool',return_value=None):
    with self.assertRaisesRegex(RuntimeError,'Calibre'):prepare_book(src,LANGS['Engleski'],threading.Event(),lambda s:None,p)
   def convert(args,stop):text_epub([('Chapter','Converted text')],'Book',Path(args[2]))
   with patch('app.find_tool',return_value='ebook-convert'),patch('app.tool_run',side_effect=convert):
    out=prepare_book(src,LANGS['Engleski'],threading.Event(),lambda s:None,p);self.assertTrue(out.exists())
 def test_djvu_path_and_pdf_ocr(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);src=p/'book.djvu';src.write_bytes(b'djvu')
   def tools(args,stop):
    if args[0]=='ddjvu':
     with pymupdf.open() as d:d.new_page();d.save(args[-1])
    else:Path(args[2]+'.txt').write_text('Recognized scanned page text.',encoding='utf-8')
   with patch('app.find_tool',side_effect=lambda name,rel:name),patch('app.tool_run',side_effect=tools):
    out=prepare_book(src,LANGS['Engleski'],threading.Event(),lambda s:None,p)
    b=Book(out,'m',LANGS['Engleski'],p);self.assertTrue(any('Recognized' in ''.join(parts) for _,_,_,parts in b.slots));b.close()
 def test_pdf_cancel_and_unsupported(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);src=p/'a.pdf'
   with pymupdf.open() as d:d.new_page();d.save(src)
   stop=threading.Event();stop.set()
   with self.assertRaises(Paused):prepare_book(src,LANGS['Engleski'],stop,lambda s:None,p)
   src=p/'bad.exe';src.touch()
   with self.assertRaisesRegex(ValueError,'nije podržan'):prepare_book(src,LANGS['Engleski'],threading.Event(),lambda s:None,p)
if __name__=='__main__':unittest.main()
