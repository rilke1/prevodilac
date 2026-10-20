import tempfile, unittest, zipfile, threading
from pathlib import Path
from unittest.mock import patch
from lxml import etree as ET
from app import Book, Paused, latin, split
class Tests(unittest.TestCase):
 def test_roundtrip_resume(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td); src=root/'a.epub'; out=root/'b.epub'
   with zipfile.ZipFile(src,'w') as z:
    z.writestr('mimetype','application/epub+zip')
    z.writestr('META-INF/container.xml','<container/>')
    z.writestr('OEBPS/a.xhtml','<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Book</title><style>p {color:red}</style></head><body><h1 id="a">Chapter</h1><p>Hello <em>world</em> today.</p><a href="#a">Go</a><img src="x.png"/></body></html>')
    z.writestr('OEBPS/a.opf','<package xmlns:dc="http://purl.org/dc/elements/1.1/"><metadata><dc:title>Book</dc:title><dc:language>en</dc:language><dc:creator>Author</dc:creator></metadata></package>')
    z.writestr('OEBPS/x.png',b'image')
   original=src.read_bytes();stop=threading.Event();calls=[]
   def fake(t,*a): calls.append(t);return 'Prevod '+t
   with patch('app.translate',fake):
    b=Book(src,'test',('English','en'),root)
    def notify(*a):stop.set()
    with self.assertRaises(Paused):b.run(stop,notify,out)
    b.close(); first=list(calls);stop.clear()
    b=Book(src,'test',('English','en'),root);b.run(stop,lambda *a:None,out);b.close()
    self.assertEqual(calls.count(first[0]),1)
    count=len(calls)
    b=Book(src,'test',('English','en'),root);b.run(stop,lambda *a:None,out);b.close()
    self.assertEqual(len(calls),count)
   self.assertEqual(src.read_bytes(),original)
   with zipfile.ZipFile(out) as z:
    self.assertEqual(z.infolist()[0].filename,'mimetype');self.assertEqual(z.infolist()[0].compress_type,0)
    self.assertEqual(z.read('OEBPS/x.png'),b'image')
    tree=ET.fromstring(z.read('OEBPS/a.xhtml'))
    self.assertEqual(tree.xpath('string(//*[local-name()="a"]/@href)'),'#a')
    self.assertEqual(tree.xpath('string(//*[local-name()="style"])'),'p {color:red}')
    opf=ET.fromstring(z.read('OEBPS/a.opf'))
    self.assertEqual(opf.xpath('string(//*[local-name()="language"])'),'sr-Latn')
    self.assertEqual(opf.xpath('string(//*[local-name()="creator"])'),'Author')
   b=Book(src,'test',('English','en'),root)
   with self.assertRaises(ValueError):b.run(stop,lambda *a:None,src)
   b.close();self.assertEqual(src.read_bytes(),original)
 def test_split_latin(self):
  s='A long sentence. '*1000;self.assertEqual(''.join(split(s)),s)
  self.assertTrue(all(len(x)<=2200 for x in split(s)))
  self.assertEqual(latin('Љубав и Његош'),'Ljubav i Njegoš')
if __name__=='__main__':unittest.main()
