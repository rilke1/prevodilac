import tempfile,threading,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
from app import Book,prepare_output,text_epub,LANGS
class OutputTests(unittest.TestCase):
 def test_missing_parent_export_and_cached_retry(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);source=root/'source.epub';text_epub([('Title','Hello world')],'Book',source)
   output=root/'missing'/'Documents'/'translated.epub'
   with patch('app.translate',side_effect=lambda text,*args:'Prevod '+text) as translate:
    b=Book(source,'m',LANGS['Engleski'],root);b.run(threading.Event(),lambda *args:None,output);b.close()
    self.assertTrue(output.is_file());self.assertIsNone(zipfile.ZipFile(output).testzip())
    count=translate.call_count
    output.unlink();output.parent.rmdir()
    b=Book(source,'m',LANGS['Engleski'],root);b.run(threading.Event(),lambda *args:None,output);b.close()
    self.assertEqual(translate.call_count,count);self.assertTrue(output.is_file())
 def test_bad_folder_detected_before_translation(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);source=root/'source.epub';text_epub([('Title','Text')],'Book',source)
   blocked=root/'a-file';blocked.write_text('keep')
   with patch('app.translate') as translate:
    b=Book(source,'m',LANGS['Engleski'],root)
    with self.assertRaisesRegex(RuntimeError,'Ne mogu da sačuvam'):b.run(threading.Event(),lambda *args:None,blocked/'out.epub')
    b.close();translate.assert_not_called()
   self.assertEqual(blocked.read_text(),'keep')
if __name__=='__main__':unittest.main()
