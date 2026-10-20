import json,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from app import RuntimeControl,Paused,translate,unload_model
class Stop:
 def __init__(self):self.now=0;self.waits=[]
 def wait(self,n):self.now+=n;self.waits.append(n);return False
class Response:
 def __init__(self):self.data=b'{"message":{"content":"Prevod"},"done_reason":"stop"}'
 def __enter__(self):return self
 def __exit__(self,*args):pass
 def read(self,*args):return self.data
class PerfTests(unittest.TestCase):
 def test_pacing_and_persistence(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'perf.json';r=RuntimeControl(p)
   for pct,expected in [(100,0),(50,1),(25,3)]:
    r.set(pct,'GPU');s=Stop();r.rest(1,s,clock=lambda:s.now)
    self.assertAlmostEqual(s.now,expected)
   r.set(25,'CPU');self.assertEqual(RuntimeControl(p).snapshot(),{'percent':25,'device':'CPU'})
   r.set(0,'GPU');self.assertEqual(r.snapshot()['percent'],10)
 def test_pause_interrupts_sleep(self):
  with tempfile.TemporaryDirectory() as d:
   r=RuntimeControl(Path(d)/'p.json');r.set(25,'GPU');stop=threading.Event();stop.set()
   with self.assertRaises(Paused):r.rest(1,stop)
 def test_device_change_ends_wait(self):
  with tempfile.TemporaryDirectory() as d:
   r=RuntimeControl(Path(d)/'p.json');r.set(25,'CPU');s=Stop();r.rest(10,s,clock=lambda:s.now,device='GPU');self.assertEqual(s.waits,[])
 def test_cpu_gpu_options_and_unload(self):
  with patch('app.urllib.request.urlopen',return_value=Response()) as opened:
   self.assertEqual(translate('Text','m',('English','en'),{'device':'CPU','percent':25}),'Prevod')
   body=json.loads(opened.call_args.args[0].data);self.assertEqual(body['options']['num_gpu'],0);self.assertEqual(body['options']['num_thread'],2)
   translate('Text','m',('English','en'),{'device':'GPU','percent':50})
   body=json.loads(opened.call_args.args[0].data);self.assertEqual(body['options']['num_gpu'],-1);self.assertEqual(body['options']['num_batch'],128)
   unload_model('m');body=json.loads(opened.call_args.args[0].data);self.assertEqual(body['keep_alive'],0);self.assertEqual(body['prompt'],'')
if __name__=='__main__':unittest.main()
