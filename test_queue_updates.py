import hashlib, io, json, tempfile, threading, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
from app import QueueStore, Paused, LANGS
from updater import install_blob, update, choose_active

class FakeBook:
    calls=[];closed=[];pause=False
    def __init__(self,path,model,source):
        self.path=path;self.calls.append((path,model,source))
        if Path(path).stem=='bad':raise ValueError('test greška')
    def run(self,stop,notify,output):
        notify(1,2,'test','original','prevod',1)
        if self.pause:stop.set();raise Paused()
        Path(output).write_text('prevod');return []
    def close(self):self.closed.append(self.path)

class QueueTests(unittest.TestCase):
    def setUp(self):FakeBook.calls=[];FakeBook.closed=[];FakeBook.pause=False
    def test_queue_continues_after_error_and_restores(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);q=QueueStore(p/'red.json')
            books=[p/n for n in ('one.epub','bad.epub','two.epub')]
            self.assertEqual(q.add(books,p,'m',LANGS['Engleski']),3)
            self.assertEqual(q.add(books,p,'m',LANGS['Engleski']),0)
            q.run(threading.Event(),lambda *a:None,FakeBook,lambda path,*args:path)
            self.assertEqual([i['state'] for i in q.snapshot()],['done','error','done'])
            self.assertEqual([Path(c[0]).name for c in FakeBook.calls],['one.epub','bad.epub','two.epub'])
            restored=QueueStore(q.path);restored.run(threading.Event(),lambda *a:None,FakeBook,lambda path,*args:path)
            self.assertEqual(len(FakeBook.calls),3)
    def test_pause_stops_whole_queue(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);q=QueueStore(p/'red.json');q.add([p/'a.epub',p/'b.epub'],p,'m',LANGS['Engleski'])
            FakeBook.pause=True;q.run(threading.Event(),lambda *a:None,FakeBook,lambda path,*args:path)
            self.assertEqual([i['state'] for i in q.snapshot()],['paused','pending'])
            FakeBook.pause=False;q=QueueStore(q.path);q.run(threading.Event(),lambda *a:None,FakeBook,lambda path,*args:path)
            self.assertEqual([i['state'] for i in q.snapshot()],['done','done'])
    def test_crash_recovery_and_name_collision(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);q=QueueStore(p/'red.json');(p/'a — srpski.epub').touch()
            q.add([p/'one'/'a.epub',p/'two'/'a.epub'],p,'m',LANGS['Engleski'])
            rows=q.snapshot();self.assertNotEqual(rows[0]['output'],rows[1]['output'])
            self.assertTrue(rows[0]['output'].endswith('(2).epub'))
            q.update(rows[0]['id'],state='running')
            self.assertEqual(QueueStore(q.path).snapshot()[0]['state'],'paused')
            # Invalid persistent data must not get silently replaced.
            q.path.write_text('BROKEN')
            with self.assertRaises(Exception):QueueStore(q.path)
            self.assertEqual(q.path.read_text(),'BROKEN')

def archive(ver='1.2.0',extra=None):
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:
        z.writestr('app.py',f"VERSION = '{ver}'\n")
        z.writestr('requirements.txt','lxml>=5.3,<7\n');z.writestr('VERSION',ver)
        if extra:z.writestr(extra,'bad')
    return b.getvalue()
class UpdateTests(unittest.TestCase):
    def test_integrity_and_traversal(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'versions'/'1.2.0';blob=archive()
            with self.assertRaises(ValueError):install_blob(blob,'0'*64,'1.2.0',p)
            self.assertFalse(p.exists())
            bad=archive(extra='../escape')
            with self.assertRaises(ValueError):install_blob(bad,hashlib.sha256(bad).hexdigest(),'1.2.0',p)
            self.assertFalse(p.exists());self.assertFalse((Path(t)/'escape').exists())
            def fail(s):raise RuntimeError('pip failed')
            with self.assertRaises(RuntimeError):install_blob(blob,hashlib.sha256(blob).hexdigest(),'1.2.0',p,fail)
            self.assertFalse(p.exists())
            install_blob(blob,hashlib.sha256(blob).hexdigest(),'1.2.0',p)
            self.assertTrue((p/'app.py').exists())
    def test_end_to_end_update_and_preserve_user_data(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);root=p/'bundled';root.mkdir();(root/'VERSION').write_text('1.1.0');(root/'app.py').write_text('')
            base=p/'data';base.mkdir();(base/'red.json').write_text('user queue')
            (base/'updates.json').write_text(json.dumps({'auto_update':True,'repository':'owner/repo'}))
            blob=archive();digest=hashlib.sha256(blob).hexdigest();prefix='https://github.com/owner/repo/releases/download/v1.2.0/'
            release={'tag_name':'v1.2.0','assets':[{'name':n,'browser_download_url':prefix+n} for n in ('BookTranslate-update.zip','BookTranslate-update.sha256')]}
            responses={'https://api.github.com/repos/owner/repo/releases/latest':json.dumps(release).encode(),prefix+'BookTranslate-update.zip':blob,prefix+'BookTranslate-update.sha256':digest.encode()}
            active,ver=update(base,root,responses.__getitem__,lambda p:None)
            self.assertEqual(ver,'1.2.0');self.assertEqual(active,base/'versions'/'1.2.0')
            self.assertEqual((base/'red.json').read_text(),'user queue');self.assertEqual(choose_active(base,root),(active,ver))
            # A future bundled version takes priority over an older downloaded one.
            (root/'VERSION').write_text('1.3.0');self.assertEqual(choose_active(base,root),(root,'1.3.0'))
    def test_disabled_does_not_contact_network(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);(p/'VERSION').write_text('1.1.0');(p/'updates.json').write_text('{"auto_update": false}')
            def forbidden(url):raise AssertionError('network')
            self.assertEqual(update(p,p,forbidden), (p,'1.1.0'))
if __name__=='__main__':unittest.main()
