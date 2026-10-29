"""BookTranslate 1.0 — local EPUB translation. Python 3.10+, lxml."""
import hashlib, json, os, queue, re, sqlite3, threading, time, urllib.request, urllib.error, zipfile
from pathlib import Path
from lxml import etree as ET
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

VERSION = '1.4.0'
CACHE_VERSION = '1.0'
BASE = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'BookTranslate'
SKIP = {'script', 'style', 'code', 'pre', 'math', 'svg'}
LANGS = {'Engleski': ('English', 'en'), 'Latinski': ('Latin', 'la'), 'Francuski': ('French', 'fr'), 'Nemački': ('German', 'de'), 'Arapski': ('Arabic', 'ar')}
CYR = dict(zip('абвгдђежзијклмнопрстћуфхцчш', 'abvgdđežzijklmnoprstćufhcčš'))
CYR.update({'љ':'lj', 'њ':'nj', 'џ':'dž'})
def latin(s):
    out=[]
    for i,c in enumerate(s):
        v=CYR.get(c.lower())
        if v is None: out.append(c)
        elif c.isupper(): out.append(v.upper() if i+1<len(s) and s[i+1].isupper() else v.capitalize())
        else: out.append(v)
    return ''.join(out)

def local(tag): return ET.QName(tag).localname if isinstance(tag,str) else ''
def parse(data):
    return ET.fromstring(data, ET.XMLParser(resolve_entities=False, no_network=True, recover=False))
def split(text, limit=2200):
    # Preserve every character and split preferentially at sentence/word boundaries.
    result=[]
    while len(text)>limit:
        options=[m.end() for m in re.finditer(r'[.!?][\s]+|\s+',text[:limit]) if m.end()>limit//2]
        n=options[-1] if options else limit
        result.append(text[:n]); text=text[n:]
    if text: result.append(text)
    return result

class TranslationLengthError(RuntimeError): pass

def translate(text, model, source, runtime=None):
    def piece(value, depth=0):
        try:
            return _translate_once(value, model, source, runtime)
        except TranslationLengthError:
            # Never reuse a truncated response. Retry the original in smaller pieces.
            if depth >= 5 or len(value) < 80:
                raise RuntimeError('Model dostiže ograničenje i na kratkom tekstu. '
                    'Prevod ovog dela nije sačuvan kao završen. Pokušaj ponovo ili proveri model.')
            middle=len(value)//2
            breaks=[m.start() for m in re.finditer(r'\s+',value)
                    if len(value)//3 <= m.start() <= 2*len(value)//3]
            cut=min(breaks,key=lambda n:abs(n-middle)) if breaks else middle
            left,right=value[:cut],value[cut:]
            separator=right[:len(right)-len(right.lstrip())]
            a=piece(left.rstrip(),depth+1)
            b=piece(right.lstrip(),depth+1)
            return a+(separator or ' ')+b
    return piece(text)

def _translate_once(text, model, source, runtime=None):
    name,code=source
    prompt=(f'You are a professional {name} ({code}) to Serbian Latin (sr-Latn) translator. '
        f'Your goal is to accurately convey the meaning and nuances of the original {name} text while adhering to Serbian grammar, vocabulary, and cultural sensitivities. '
        'Preserve all claims, names, numbers, and paragraph breaks. Do not summarize or omit content. '
        f'Produce only the Serbian Latin translation, without any additional explanations or commentary. Please translate the following {name} text into Serbian Latin:\n\n\n'+text)
    options={'temperature':0,'num_ctx':8192,'num_predict':4096}
    if runtime and runtime['device']=='CPU':options.update(num_gpu=0,num_thread=2,num_batch=64)
    else:options.update(num_gpu=-1,num_batch=128 if runtime and runtime['percent']<100 else 512)
    body=json.dumps({'model':model,'messages':[{'role':'user','content':prompt}], 'stream':False,
        'keep_alive':'10m','options':options}).encode()
    req=urllib.request.Request('http://127.0.0.1:11434/api/chat',data=body,headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=600) as r: data=json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError('Ollama: '+e.read().decode('utf-8',errors='replace')[:700]) from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError('Ollama nije dostupna ili je odgovor istekao. Pokreni Ollama, proveri model i klikni Nastavi.') from e
    if data.get('done_reason')=='length': raise TranslationLengthError('Odgovor je odsečen.')
    out=data.get('message',{}).get('content','').strip()
    if not out: raise RuntimeError('Model je vratio prazan prevod. Pokušaj ponovo.')
    return latin(out)

def prepare_output(output,original=None):
    import tempfile
    output=Path(output)
    if original is not None and output.resolve()==Path(original).resolve():
        raise ValueError('Izlaz ne sme da zameni originalnu knjigu.')
    try:
        output.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix='booktranslate-check-',dir=output.parent):pass
    except OSError as e:
        raise RuntimeError(f'Ne mogu da sačuvam knjigu u folder: {output.parent}. Proveri da folder postoji i da imaš pravo upisa. Detalj: {e}') from e
    return output

class TranslationGate:
    """One translation at a time across the desktop queue and Folio worker."""
    def __enter__(self):
        BASE.mkdir(parents=True,exist_ok=True)
        self.file=(BASE/'translation.lock').open('a+b')
        self.file.seek(0);self.file.write(b'0');self.file.flush();self.file.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.file.close();raise BlockingIOError('Drugi red trenutno prevodi knjigu.')
        return self
    def __exit__(self,*args):
        self.file.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(self.file.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            fcntl.flock(self.file.fileno(),fcntl.LOCK_UN)
        self.file.close()

class Paused(Exception): pass
class Book:
    def __init__(self,path,model,source,root=BASE):
        self.path=Path(path); self.model=model; self.source=source; self.runtime=None; self.last_device=None
        digest=hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.job=root/'projekti'/hashlib.sha256((digest+model+str(source)+CACHE_VERSION).encode()).hexdigest()[:24]
        self.job.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.job/'napredak.sqlite')
        self.db.execute('CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        self.docs={}; self.slots=[]; self.warnings=[]
        with zipfile.ZipFile(self.path) as z:
            names=z.namelist()
            if len(names)!=len(set(names)): raise ValueError('EPUB sadrži duplirane putanje.')
            if 'META-INF/encryption.xml' in names: raise ValueError('EPUB sadrži encryption.xml. Ova verzija ne obrađuje zaštićene knjige ili šifrovane resurse.')
            for name in names:
                if name.lower().endswith(('.xhtml','.html','.htm','.ncx','.opf')):
                    try: tree=parse(z.read(name))
                    except ET.XMLSyntaxError as e: raise ValueError(f'Neispravan XML u {name}: {e}') from e
                    self.docs[name]=tree
                    isopf=name.lower().endswith('.opf'); isncx=name.lower().endswith('.ncx')
                    for el in tree.iter():
                        if not isinstance(el.tag,str): continue
                        tag=local(el.tag)
                        if isopf:
                            if tag=='language': el.text='sr-Latn'
                            if tag=='title' and el.text: self.add(name,el,'text',el.text)
                            continue
                        if isncx:
                            if tag=='text' and el.text: self.add(name,el,'text',el.text)
                            continue
                        if tag=='html' or 'lang' in el.attrib or '{http://www.w3.org/XML/1998/namespace}lang' in el.attrib:
                            el.set('lang','sr-Latn'); el.set('{http://www.w3.org/XML/1998/namespace}lang','sr-Latn')
                        ancestors=[local(a.tag) for a in el.iterancestors()]
                        if tag not in SKIP and not any(a in SKIP for a in ancestors):
                            if el.text: self.add(name,el,'text',el.text)
                        if el.tail and not any(a in SKIP for a in ancestors): self.add(name,el,'tail',el.tail)
        if not self.slots: raise ValueError('Nema teksta za prevod u ovoj EPUB knjizi.')
        self.total=sum(len(parts) for _,_,_,parts in self.slots)
    def add(self,name,el,attr,text):
        if any(c.isalpha() for c in text): self.slots.append((name,el,attr,split(text)))
    def run(self,stop,notify,output):
        output=prepare_output(output,self.path)
        done=0; started=time.monotonic()
        for name,el,attr,parts in self.slots:
            translated=[]
            for part in parts:
                if stop.is_set(): raise Paused()
                key=hashlib.sha256(part.encode()).hexdigest()
                row=self.db.execute('SELECT value FROM cache WHERE key=?',(key,)).fetchone()
                if row: value=row[0]
                else:
                    lead=part[:len(part)-len(part.lstrip())]; trail=part[len(part.rstrip()):]
                    cfg=self.runtime.snapshot() if self.runtime else None
                    if cfg:
                        if (self.last_device is None and cfg['device']=='CPU') or (self.last_device is not None and self.last_device!=cfg['device']):
                            unload_model(self.model)
                        self.last_device=cfg['device']
                    tick=time.monotonic()
                    value=lead+(translate(part.strip(),self.model,self.source,cfg) if cfg else translate(part.strip(),self.model,self.source))+trail
                    active_seconds=time.monotonic()-tick
                    self.db.execute('INSERT OR REPLACE INTO cache VALUES (?,?)',(key,value)); self.db.commit()
                    if len(value)<len(part)*.4: self.warnings.append(f'Proveri mogući kratak prevod: {name}: {part[:100]}')
                    if self.runtime:self.runtime.rest(active_seconds,stop,device=cfg['device'])
                translated.append(value); done+=1
                notify(done,self.total,name,part,value,time.monotonic()-started)
            setattr(el,attr,''.join(translated))
        if stop.is_set(): raise Paused()
        output=Path(output)
        if output.resolve()==self.path.resolve(): raise ValueError('Izlaz ne sme da zameni originalnu knjigu.')
        # The folder may have disappeared during a long translation.
        prepare_output(output,self.path)
        temp=output.with_name(output.name+'.tmp')
        try:
            with zipfile.ZipFile(self.path) as src, zipfile.ZipFile(temp,'w') as dst:
                dst.writestr('mimetype',b'application/epub+zip',compress_type=zipfile.ZIP_STORED)
                for info in src.infolist():
                    if info.filename=='mimetype': continue
                    data=ET.tostring(self.docs[info.filename],encoding='utf-8',xml_declaration=True) if info.filename in self.docs else src.read(info.filename)
                    dst.writestr(info,data)
            with zipfile.ZipFile(temp) as z:
                if z.testzip(): raise ValueError('Oštećena izlazna arhiva.')
                for n in self.docs: parse(z.read(n))
            os.replace(temp,output)
        finally: temp.unlink(missing_ok=True)
        return self.warnings
    def close(self): self.db.close()

def atomic_json(path, data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    with temp.open('w',encoding='utf-8') as f:
        json.dump(data,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
    os.replace(temp,path)

# Supported source formats. Output is always EPUB.
CALIBRE_EXT={'.mobi','.azw','.azw3','.azw4','.prc','.fb2','.docx','.odt','.rtf','.html','.htm','.htmlz','.txtz','.lit','.lrf','.pdb','.pml','.pmlz','.rb','.snb','.tcr','.chm'}
IMPORT_EXT=CALIBRE_EXT|{'.epub','.pdf','.djvu','.djv','.djwu','.txt','.md','.doc','.png','.jpg','.jpeg','.tif','.tiff','.bmp','.webp','.xps'}

def find_tool(name,relative):
    import shutil
    found=shutil.which(name)
    if found:return found
    for env in ('ProgramFiles','ProgramFiles(x86)','LOCALAPPDATA'):
        for rel in relative:
            path=Path(os.environ.get(env,''))/rel
            if path.is_file():return str(path)
    return None

def tool_run(args,stop):
    import subprocess,tempfile
    # File-backed output avoids a full pipe deadlock on verbose converters.
    with tempfile.TemporaryFile() as log:
        p=subprocess.Popen(args,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        started=time.monotonic()
        try:
            while p.poll() is None:
                if stop.wait(.2):raise Paused()
                if time.monotonic()-started>3600:raise RuntimeError('Konverzija je trajala duže od jednog sata.')
            log.seek(0);output=log.read().decode('utf-8',errors='replace')
            if p.returncode:raise RuntimeError('Konverzija/OCR nije uspeo:\n'+output[-1800:])
            return output
        finally:
            if p.poll() is None:p.kill();p.wait()

def text_epub(pages,title,output):
    import html,uuid
    esc=html.escape;uid=uuid.uuid4().hex
    manifest=[];spine=[];nav=[]
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('mimetype','application/epub+zip',compress_type=zipfile.ZIP_STORED)
        z.writestr('META-INF/container.xml','<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        for i,(heading,text) in enumerate(pages):
            name=f'part{i}.xhtml';id=f'p{i}'
            paragraphs=''.join('<p>'+esc(p).replace('\n','<br/>')+'</p>' for p in re.split(r'\n\s*\n',text) if p.strip())
            z.writestr(name,f'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{esc(heading)}</title></head><body><h1>{esc(heading)}</h1>{paragraphs}</body></html>')
            manifest.append(f'<item id="{id}" href="{name}" media-type="application/xhtml+xml"/>');spine.append(f'<itemref idref="{id}"/>');nav.append(f'<li><a href="{name}">{esc(heading)}</a></li>')
        z.writestr('nav.xhtml','<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head><body><nav epub:type="toc"><ol>'+''.join(nav)+'</ol></nav></body></html>')
        z.writestr('book.opf',f'<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="uid">{uid}</dc:identifier><dc:title>{esc(title)}</dc:title><dc:language>en</dc:language><meta property="dcterms:modified">2026-01-01T00:00:00Z</meta></metadata><manifest>'+''.join(manifest)+'<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/></manifest><spine>'+''.join(spine)+'</spine></package>')

def read_text(path):
    data=Path(path).read_bytes()
    for encoding in ('utf-8-sig','utf-16','cp1252'):
        try:return data.decode(encoding)
        except UnicodeError:pass
    raise ValueError('Kodiranje teksta nije prepoznato. Sačuvaj ga kao UTF-8.')

def prepare_book(path,source,stop,notify,root=BASE):
    import tempfile,shutil
    path=Path(path);ext=path.suffix.lower()
    if not path.is_file():raise ValueError('Izvorni fajl više nije dostupan: '+str(path))
    if ext not in IMPORT_EXT:raise ValueError('Format nije podržan: '+ext+'. Konvertuj dokument u EPUB ili PDF.')
    if ext=='.epub':return path
    digest=hashlib.sha256(path.read_bytes()+str(source).encode()+b'import-v1').hexdigest()
    folder=root/'uvoz'/digest;folder.mkdir(parents=True,exist_ok=True);out=folder/'book.epub'
    if out.exists():return out
    notify('Pripremam '+path.name+'…')
    with tempfile.TemporaryDirectory(dir=folder) as td:
        stage=Path(td);result=stage/'book.epub';input_path=path
        if ext in {'.djvu','.djv','.djwu'}:
            ddjvu=find_tool('ddjvu',['DjVuLibre/ddjvu.exe','DjVuLibre/bin/ddjvu.exe'])
            if not ddjvu:raise RuntimeError('Za DjVu instaliraj DjVuLibre (ddjvu.exe) i dodaj njegov folder u PATH. Potom Ponovi izabrane.')
            input_path=stage/'scan.pdf';tool_run([ddjvu,'-format=pdf',str(path),str(input_path)],stop);ext='.pdf'
        if ext in {'.pdf','.xps','.png','.jpg','.jpeg','.tif','.tiff','.bmp','.webp'}:
            import pymupdf
            pages=[];ocr_lang={'en':'eng','la':'lat','fr':'fra','de':'deu','ar':'ara'}[source[1]]
            with pymupdf.open(input_path) as doc:
                if doc.needs_pass:raise ValueError('Dokument je zaključan lozinkom. Sačuvaj otključanu kopiju.')
                for n,page in enumerate(doc):
                    if stop.is_set():raise Paused()
                    notify(f'{path.name}: čitam stranu {n+1}/{len(doc)}…')
                    text=page.get_text('text',sort=True)
                    if len(''.join(c for c in text if c.isalpha()))<25:
                        tesseract=find_tool('tesseract',['Tesseract-OCR/tesseract.exe','Programs/Tesseract-OCR/tesseract.exe'])
                        if not tesseract:raise RuntimeError('Strana '+str(n+1)+' zahteva OCR. Instaliraj Tesseract OCR sa jezikom '+ocr_lang+' pa Ponovi izabrane. Prazne strane takođe zahtevaju OCR proveru.')
                        image=stage/'page.png';page.get_pixmap(dpi=200).save(image)
                        dest=stage/'ocr';tool_run([tesseract,str(image),str(dest),'-l',ocr_lang],stop)
                        text=dest.with_suffix('.txt').read_text(encoding='utf-8')
                    pages.append((f'Page {n+1}',text))
            if not any(t.strip() for _,t in pages):raise ValueError('Nije pronađen tekst ni posle OCR-a.')
            text_epub(pages,path.stem,result)
            (folder/'NAPOMENA.txt').write_text('PDF/DjVu/slike: tekst je izdvojen po stranama, uz OCR gde nema dovoljno teksta. Originalni raspored, slike i fusnote nisu rekonstruisani. Proveri redosled kolona i OCR greške.',encoding='utf-8')
        elif ext in {'.txt','.md'}:
            text=read_text(path)
            if not text.strip():raise ValueError('Dokument je prazan.')
            text_epub([(path.stem,text)],path.stem,result)
        else:
            if ext=='.doc':
                office=find_tool('soffice',['LibreOffice/program/soffice.exe'])
                if not office:raise RuntimeError('Za stari .doc instaliraj LibreOffice ili sačuvaj dokument kao .docx.')
                profile=(stage/'lo-profile').as_uri()
                tool_run([office,'-env:UserInstallation='+profile,'--headless','--convert-to','docx','--outdir',str(stage),str(path)],stop)
                input_path=stage/(path.stem+'.docx')
                if not input_path.exists():raise RuntimeError('LibreOffice nije napravio DOCX fajl.')
            converter=find_tool('ebook-convert',['Calibre2/ebook-convert.exe','Calibre/ebook-convert.exe'])
            if not converter:raise RuntimeError('Za '+ext+' instaliraj Calibre sa calibre-ebook.com/download_windows, zatim Ponovi izabrane. Aplikacija ga prepoznaje u standardnom folderu ili kroz PATH.')
            tool_run([converter,str(input_path),str(result)],stop)
        if stop.is_set():raise Paused()
        with zipfile.ZipFile(result) as z:
            if 'META-INF/container.xml' not in z.namelist() or z.testzip():raise ValueError('Konverzija nije napravila ispravan EPUB.')
        os.replace(result,out)
    return out


class RuntimeControl:
    """Duty-cycle pacing, not a hardware utilization cap. Safe to change from UI."""
    def __init__(self,path=BASE/'performance.json'):
        self.path=Path(path);self.lock=threading.RLock()
        cfg=json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        self.percent=max(10,min(100,int(cfg.get('percent',100))))
        self.device='CPU' if cfg.get('device')=='CPU' else 'GPU'
    def snapshot(self):
        with self.lock:return {'percent':self.percent,'device':self.device}
    def set(self,percent,device):
        with self.lock:
            self.percent=max(10,min(100,int(percent)));self.device='CPU' if device=='CPU' else 'GPU'
            atomic_json(self.path,self.snapshot())
    def rest(self,active_seconds,stop,clock=time.monotonic,device=None):
        start=clock()
        while True:
            cfg=self.snapshot()
            if device is not None and cfg['device']!=device:return
            percent=cfg['percent']
            target=active_seconds*(100/percent-1)
            remaining=target-(clock()-start)
            if remaining<=0:return
            if stop.wait(min(.2,remaining)):raise Paused()

def unload_model(model):
    payload=json.dumps({'model':model,'prompt':'','keep_alive':0,'stream':False}).encode()
    req=urllib.request.Request('http://127.0.0.1:11434/api/generate',data=payload,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=60) as r:json.load(r)


class QueueStore:
    def __init__(self,path=BASE/'red.json'):
        self.path=Path(path);self.lock=threading.RLock();self.items=[]
        if self.path.exists():
            self.items=json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(self.items,list):raise ValueError('Neispravan red knjiga.')
            for item in self.items:
                if item['state']=='running':item['state']='paused'
            self.save()
    def save(self):
        with self.lock:atomic_json(self.path,self.items)
    def snapshot(self):
        with self.lock:return [dict(i) for i in self.items]
    def update(self,id,**values):
        with self.lock:
            item=next(i for i in self.items if i['id']==id);item.update(values);self.save()
    def add(self,paths,folder,model,source):
        import uuid
        count=0
        with self.lock:
            used={Path(i['output']).resolve() for i in self.items}
            sources={Path(i['path']).resolve() for i in self.items}
            for p in map(Path,paths):
                p=p.resolve()
                if p in sources:continue
                output=Path(folder)/(p.stem+' — srpski.epub');n=2
                while output.exists() or output.resolve() in used or output.resolve()==p:
                    output=Path(folder)/(p.stem+f' — srpski ({n}).epub');n+=1
                self.items.append({'id':uuid.uuid4().hex,'path':str(p),'output':str(output.resolve()),
                    'model':model,'source':list(source),'state':'pending','progress':0,'error':''})
                sources.add(p);used.add(output.resolve());count+=1
            self.save()
        return count
    def change_output_folder(self,folder):
        folder=Path(folder).resolve()
        prepare_output(folder/'probe.epub')
        changed=0
        with self.lock:
            used={Path(i['output']).resolve() for i in self.items if i['state']=='done'}
            for item in self.items:
                if item['state']=='done':continue
                old=Path(item['output']);target=folder/old.name;n=2
                same=target.resolve()==old.resolve()
                while target.resolve() in used or target.resolve()==Path(item['path']).resolve() or (target.exists() and not same):
                    target=folder/(old.stem+f' ({n})'+old.suffix);n+=1;same=False
                item['output']=str(target);used.add(target.resolve())
                item['state']='pending';item['error']='';changed+=1
            self.save()
        return changed
    def remove(self,ids):
        with self.lock:self.items=[i for i in self.items if i['id'] not in ids];self.save()
    def run(self,stop,emit,book_factory=Book,prepare=prepare_book,runtime=None):
        try:
            with TranslationGate():
                return self._run(stop,emit,book_factory,prepare,runtime)
        except BlockingIOError:
            emit('import','Folio trenutno prevodi knjigu. Sačekaj završetak pa pokreni lokalni red.')
            emit('finished',None)
    def _run(self,stop,emit,book_factory=Book,prepare=prepare_book,runtime=None):
        while not stop.is_set():
            item=next((i for i in self.snapshot() if i['state'] in ('pending','paused')),None)
            if not item:break
            id=item['id'];book=None
            self.update(id,state='running',error='');emit('refresh',None)
            try:
                prepare_output(item['output'],item['path'])
                prepared=prepare(item['path'],tuple(item['source']),stop,lambda msg:emit('import',msg))
                book=book_factory(prepared,item['model'],tuple(item['source']))
                if isinstance(book,Book):book.runtime=runtime
                def progress(done,total,name,original,translated,elapsed):
                    emit('progress',(id,done,total,name,original,translated,elapsed))
                warnings=book.run(stop,progress,item['output'])
                if warnings:Path(item['output']+'.provera.txt').write_text('\n'.join(warnings),encoding='utf-8')
                self.update(id,state='done',progress=100)
            except Paused:
                self.update(id,state='paused');break
            except Exception as e:self.update(id,state='error',error=str(e))
            finally:
                if book:book.close()
                emit('refresh',None)
        emit('finished',None)

STATES={'pending':'Čeka','running':'Prevodi se','paused':'Pauzirano','done':'Završeno','error':'Greška'}
class App(tk.Tk):
    def __init__(self):
        super().__init__(); self.title('BookTranslate '+VERSION);self.geometry('1080x840');self.minsize(860,680)
        folio_start_worker()
        self.events=queue.Queue();self.stop=threading.Event();self.busy=False;self.runtime=RuntimeControl()
        try:self.jobs=QueueStore()
        except Exception as e:
            messagebox.showerror('Red nije učitan',f'{e}\nSačuvani red nije prepisan.');self.destroy();raise
        self.model=tk.StringVar(value='translategemma:12b');self.source=tk.StringVar(value='Engleski');self.folder=tk.StringVar(value=str(Path.home()/'Documents'))
        self.status=tk.StringVar(value='Dodaj knjige i dokumente (EPUB, MOBI, PDF, DjVu…). Red se čuva i posle zatvaranja aplikacije.')
        f=ttk.Frame(self,padding=20);f.pack(fill='both',expand=True)
        ttk.Label(f,text='BookTranslate',font=('Segoe UI',24,'bold')).pack(anchor='w')
        ttk.Label(f,text='Lokalni prevod knjiga • srpska latinica • red za obradu').pack(anchor='w',pady=(0,14))
        if os.environ.get('BOOKTRANSLATE_UPDATE_ERROR'):
            ttk.Label(f,text=os.environ['BOOKTRANSLATE_UPDATE_ERROR'],foreground='#a00000',wraplength=1000).pack(anchor='w',pady=(0,8))
        row=ttk.Frame(f);row.pack(fill='x')
        ttk.Label(row,text='Za nove knjige — jezik:').pack(side='left')
        ttk.Combobox(row,textvariable=self.source,values=list(LANGS),state='readonly',width=12).pack(side='left',padx=6)
        ttk.Label(row,text='Model:').pack(side='left');ttk.Entry(row,textvariable=self.model,width=25).pack(side='left',padx=6)
        ttk.Button(row,text='Ažuriranja',command=self.settings).pack(side='right')
        ttk.Button(row,text='Folio',command=lambda:folio_connect(self)).pack(side='right',padx=6)
        ttk.Button(row,text='Folio – isključi',command=lambda:folio_disconnect(self)).pack(side='right')
        perf=ttk.Frame(f);perf.pack(fill='x',pady=(10,0))
        cfg=self.runtime.snapshot();self.percent=tk.DoubleVar(value=cfg['percent']);self.device=tk.StringVar(value=cfg['device']);self.intensity_label=tk.StringVar(value=f"{cfg['percent']}%")
        ttk.Label(perf,text='Intenzitet prevoda:').pack(side='left')
        ttk.Scale(perf,from_=10,to=100,variable=self.percent,command=lambda v:self.intensity_label.set(f'{round(float(v))}%')).pack(side='left',fill='x',expand=True,padx=6)
        ttk.Label(perf,textvariable=self.intensity_label,width=5).pack(side='left')
        ttk.Combobox(perf,textvariable=self.device,values=['GPU','CPU'],state='readonly',width=6).pack(side='left',padx=6)
        ttk.Button(perf,text='Primeni',command=self.apply_performance).pack(side='left')
        ttk.Button(perf,text='Igranje: CPU',command=self.gaming).pack(side='left',padx=6)
        ttk.Label(f,text='Procenat određuje rad i pauze između delova; GPU tokom prevoda može dostići 100%. VRAM ostaje zauzet u GPU režimu.',wraplength=1000).pack(anchor='w',pady=(4,0))
        row=ttk.Frame(f);row.pack(fill='x',pady=10)
        ttk.Label(row,text='Izlazni folder:').pack(side='left');ttk.Entry(row,textvariable=self.folder).pack(side='left',fill='x',expand=True,padx=6)
        ttk.Button(row,text='Izaberi folder',command=self.pick_folder).pack(side='left')
        self.apply_folder=ttk.Button(row,text='Primeni folder na red',command=self.apply_output_folder);self.apply_folder.pack(side='left',padx=6)
        row=ttk.Frame(f);row.pack(fill='x',pady=(0,10))
        ttk.Button(row,text='+ Dodaj knjige',command=self.add).pack(side='left')
        self.start=ttk.Button(row,text='Pokreni / Nastavi red',command=self.go);self.start.pack(side='left',padx=6)
        self.pause=ttk.Button(row,text='Pauziraj red',command=self.pause_job,state='disabled');self.pause.pack(side='left')
        self.retry=ttk.Button(row,text='Ponovi izabrane',command=self.retry_selected);self.retry.pack(side='left',padx=6)
        self.remove=ttk.Button(row,text='Ukloni izabrane',command=self.remove_selected);self.remove.pack(side='left')
        table=ttk.Frame(f);table.pack(fill='both',expand=True)
        self.tree=ttk.Treeview(table,columns=('name','state','pct','lang'),show='headings',height=9,selectmode='extended')
        for c,label,width in [('name','Knjiga',450),('state','Status',130),('pct','Napredak',90),('lang','Izvorni jezik',130)]:
            self.tree.heading(c,text=label);self.tree.column(c,width=width)
        scroll=ttk.Scrollbar(table,command=self.tree.yview);self.tree.config(yscrollcommand=scroll.set);scroll.pack(side='right',fill='y');self.tree.pack(fill='both',expand=True)
        self.tree.bind('<<TreeviewSelect>>',self.show_selected)
        self.bar=ttk.Progressbar(f,maximum=100);self.bar.pack(fill='x',pady=(12,6))
        ttk.Label(f,textvariable=self.status,wraplength=1000).pack(anchor='w')
        self.preview=tk.Text(f,wrap='word',font=('Segoe UI',10),height=10,state='disabled');self.preview.pack(fill='both',expand=True,pady=10)
        row=ttk.Frame(f);row.pack(fill='x')
        ttk.Button(row,text='Otvori izlazni folder',command=self.open_output).pack(side='left')
        ttk.Button(row,text='Otvori napredak',command=self.open_data).pack(side='left',padx=8)
        ttk.Label(f,text='Pauza čeka trenutni deo. Knjige se prevode redom; greška jedne knjige ne briše ostale.',wraplength=1000).pack(anchor='w',pady=(10,0))
        self.refresh();self.protocol('WM_DELETE_WINDOW',self.quit_app);self.after(150,self.poll)
    def apply_performance(self):
        self.runtime.set(round(self.percent.get()),self.device.get())
        self.status.set('Podešavanje sačuvano. Primenjuje se na naredni deo. CPU režim koristi 2 niti i može znatno usporiti prevod.')
    def gaming(self):
        self.device.set('CPU');self.percent.set(25);self.intensity_label.set('25%');self.apply_performance()
    def settings(self):
        from tkinter import simpledialog
        cfgpath=BASE/'updates.json'
        cfg=json.loads(cfgpath.read_text(encoding='utf-8')) if cfgpath.exists() else {'repository':'rilke1/prevodilac','auto_update':True}
        repo=simpledialog.askstring('Automatska ažuriranja','Javni GitHub repozitorijum: vlasnik/repo\nOstavi prazno da isključiš ažuriranja.\nNove verzije se instaliraju pri sledećem pokretanju.',initialvalue=cfg.get('repository',''),parent=self)
        if repo is None:return
        repo=repo.strip().removeprefix('https://github.com/').rstrip('/')
        if repo and not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo):messagebox.showerror('Repozitorijum','Unesi vlasnik/repo.');return
        atomic_json(cfgpath,{'repository':repo,'auto_update':bool(repo)})
        messagebox.showinfo('Ažuriranja','Sačuvano. Provera će se izvršiti kada sledeći put pokreneš POKRENI.bat.' if repo else 'Automatska ažuriranja su isključena.')
    def refresh(self):
        selected=self.tree.selection();rows=self.jobs.snapshot();ids={i['id'] for i in rows}
        for id in self.tree.get_children():
            if id not in ids:self.tree.delete(id)
        for i in rows:
            vals=(Path(i['path']).name,STATES[i['state']],f"{i['progress']:.0f}%",i['source'][0])
            if self.tree.exists(i['id']):self.tree.item(i['id'],values=vals)
            else:self.tree.insert('','end',iid=i['id'],values=vals)
        self.tree.selection_set([id for id in selected if id in ids])
    def apply_output_folder(self):
        if self.busy:
            messagebox.showinfo('Izlazni folder','Prvo pauziraj red i sačekaj završetak tekućeg dela.');return
        try:count=self.jobs.change_output_folder(self.folder.get())
        except Exception as e:messagebox.showerror('Izlazni folder',str(e));return
        self.refresh();self.status.set(f'Folder je promenjen za {count} nezavršenih knjiga. Klikni Pokreni / Nastavi red. Sačuvani prevodi se ponovo koriste.')
    def pick_folder(self):
        p=filedialog.askdirectory()
        if p:self.folder.set(p)
    def add(self):
        model=self.model.get().strip()
        if not model:messagebox.showerror('Model','Unesi naziv modela.');return
        folder=Path(self.folder.get())
        if not folder.is_dir():messagebox.showerror('Folder','Izaberi postojeći izlazni folder.');return
        paths=filedialog.askopenfilenames(filetypes=[('Knjige i dokumenti',' '.join('*'+e for e in sorted(IMPORT_EXT))),('Svi fajlovi','*.*')])
        if paths:
            count=self.jobs.add(paths,folder,model,LANGS[self.source.get()]);self.refresh();self.status.set(f'Dodato: {count}. Duplikati su preskočeni. Jezik i model su sačuvani za svaku knjigu.')
    def retry_selected(self):
        if self.busy:return
        for i in self.jobs.snapshot():
            if i['id'] in self.tree.selection():self.jobs.update(i['id'],state='pending',error='')
        self.refresh()
    def remove_selected(self):
        if not self.busy:self.jobs.remove(set(self.tree.selection()));self.refresh()
    def show_selected(self,event=None):
        for i in self.jobs.snapshot():
            if i['id'] in self.tree.selection():
                self.status.set(i['error'] or i['output']);break
    def open_output(self):
        selected=[i for i in self.jobs.snapshot() if i['id'] in self.tree.selection()]
        p=Path(selected[0]['output']).parent if selected else Path(self.folder.get())
        if p.is_dir():os.startfile(str(p))
    def open_data(self):BASE.mkdir(parents=True,exist_ok=True);os.startfile(str(BASE))
    def pause_job(self):self.stop.set();self.status.set('Pauziram red posle tekućeg dela…')
    def go(self):
        if self.busy:return
        if not any(i['state'] in ('pending','paused') for i in self.jobs.snapshot()):
            self.status.set('Nema knjiga na čekanju. Za knjige sa greškom izaberi Ponovi izabrane.');return
        self.busy=True;self.stop.clear();self.start.config(state='disabled');self.pause.config(state='normal');self.retry.config(state='disabled');self.remove.config(state='disabled')
        def work():
            try:self.jobs.run(self.stop,lambda kind,data:self.events.put((kind,data)),runtime=self.runtime)
            except Exception as e:self.events.put(('fatal',str(e)));self.events.put(('finished',None))
        threading.Thread(target=work,daemon=True).start()
    def poll(self):
        latest=None;refresh=False
        while True:
            try:kind,data=self.events.get_nowait()
            except queue.Empty:break
            if kind=='progress':latest=data
            elif kind=='import':self.status.set(data)
            elif kind=='refresh':refresh=True
            elif kind=='fatal':messagebox.showerror('Red je zaustavljen',data)
            elif kind=='finished':
                latest=None;refresh=True;self.busy=False;self.start.config(state='normal');self.pause.config(state='disabled');self.retry.config(state='normal');self.remove.config(state='normal')
                rows=self.jobs.snapshot();errors=sum(i['state']=='error' for i in rows);done=sum(i['state']=='done' for i in rows)
                self.status.set(f'{"Red je pauziran" if self.stop.is_set() else "Obrada reda je završena"}. Završeno: {done}; greške: {errors}. Klikni knjigu za detalje.')
        if latest:
            id,done,total,name,original,translated,elapsed=latest;pct=100*done/total;self.bar['value']=pct
            # Progress writes are throttled to UI polling rather than every text fragment.
            self.jobs.update(id,progress=pct);refresh=True
            self.status.set(f'{done:,} / {total:,} delova • {pct:.1f}% • {name}')
            self.preview.config(state='normal');self.preview.delete('1.0','end');self.preview.insert('end','ORIGINAL\n'+original+'\n\nPREVOD\n'+translated);self.preview.config(state='disabled')
        if refresh:self.refresh()
        self.after(150,self.poll)
    def quit_app(self):
        if self.busy and not messagebox.askyesno('Zatvori','Prekidam rad? Red i završeni delovi ostaju sačuvani.'):return
        self.stop.set();self.destroy()

# Folio uses an outbound connection only; Ollama never listens on the Internet.
FOLIO_SITE='https://folio-marijo.autokomanda.chatgpt.site'

def folio_tls():
    import ssl, certifi
    return ssl.create_default_context(cafile=certifi.where())

def folio_protect(raw, decrypt=False):
    import ctypes
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_char))]
    buf=ctypes.create_string_buffer(raw)
    inp=Blob(len(raw),ctypes.cast(buf,ctypes.POINTER(ctypes.c_char)));out=Blob()
    fn=ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not fn(ctypes.byref(inp),None,None,None,None,1,ctypes.byref(out)):
        raise OSError('Windows nije mogao da zaštiti Folio pristup.')
    try:return ctypes.string_at(out.data,out.size)
    finally:ctypes.windll.kernel32.LocalFree(out.data)

def folio_config():
    return json.loads(folio_protect((BASE/'folio.dat').read_bytes(),True))

def folio_open(req):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    opener=urllib.request.build_opener(urllib.request.HTTPSHandler(context=folio_tls()),NoRedirect())
    return opener.open(req,timeout=90)

def folio_call(cfg,action,fields=None,files=None,job=None):
    import uuid
    headers={'OAI-Sites-Authorization':'Bearer '+cfg['serviceToken'],'X-Folio-Device':cfg['deviceToken']}
    url=FOLIO_SITE+'/api/computer?action='+action
    if action=='input':
        url=FOLIO_SITE+'/api/computer?id='+job['id'];headers['X-Folio-Lease']=job['lease'];body=None
    elif fields is None:
        body=b''
    else:
        boundary='folio'+uuid.uuid4().hex;chunks=[]
        for key,value in fields.items():
            chunks.append(('--'+boundary+'\r\nContent-Disposition: form-data; name="'+key+'"\r\n\r\n'+str(value)+'\r\n').encode())
        for key,path in (files or {}).items():
            # A fixed ASCII wire filename avoids header injection from book titles.
            chunks.append(('--'+boundary+'\r\nContent-Disposition: form-data; name="'+key+'"; filename="'+key+'.epub"\r\nContent-Type: application/epub+zip\r\n\r\n').encode()+Path(path).read_bytes()+b'\r\n')
        chunks.append(('--'+boundary+'--\r\n').encode());body=b''.join(chunks)
        headers['Content-Type']='multipart/form-data; boundary='+boundary
    req=urllib.request.Request(url,data=body,headers=headers,method='GET' if action=='input' else 'POST')
    try:
        with folio_open(req) as response:
            data=response.read(26*1024*1024)
            if action=='input' and len(data)>25*1024*1024:raise ValueError('Knjiga je veća od 25 MB.')
            return data if action=='input' else json.loads(data)
    except urllib.error.HTTPError as e:
        # Do not log request headers or redirect/login URLs containing credentials.
        raise RuntimeError('Folio zahtev nije uspeo (HTTP '+str(e.code)+'). Proveri vezu ili ponovo poveži računar.') from None

def folio_download(url,target_dir):
    import http.client, ipaddress, socket, urllib.parse
    class PinnedHTTPS(http.client.HTTPSConnection):
        def connect(self):
            sock=socket.create_connection((self.public_ip,443),timeout=self.timeout)
            self.sock=self._context.wrap_socket(sock,server_hostname=self.host)
    for redirect in range(6):
        u=urllib.parse.urlsplit(url)
        if u.scheme!='https' or not u.hostname or u.username or u.password or (u.port and u.port!=443):
            raise ValueError('Dozvoljeni su samo javni HTTPS linkovi.')
        addresses=socket.getaddrinfo(u.hostname,443,type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(x[4][0]).is_global for x in addresses):
            raise ValueError('Link ne sme voditi na lokalnu ili privatnu mrežu.')
        con=PinnedHTTPS(u.hostname,timeout=45,context=folio_tls());con.public_ip=addresses[0][4][0]
        try:
            con.request('GET',urllib.parse.urlunsplit(('','',u.path or '/',u.query,'')),headers={'User-Agent':'BookTranslate-Folio/1.4'})
            response=con.getresponse()
            if response.status in (301,302,303,307,308):
                url=urllib.parse.urljoin(url,response.getheader('Location',''));continue
            if response.status!=200:raise ValueError('Preuzimanje nije uspelo (HTTP '+str(response.status)+'). Dodaj fajl direktno u Folio.')
            disposition=response.getheader('Content-Disposition','')
            match=re.search(r'filename="?([^";]+)',disposition)
            name=match.group(1) if match else urllib.parse.unquote(u.path.split('/')[-1])
            ext=Path(name).suffix.lower()
            allowed={'.epub','.pdf','.txt','.mobi','.azw','.azw3','.fb2','.docx','.doc','.djvu','.djv','.rtf','.odt'}
            ctype=response.getheader('Content-Type','').lower()
            if ext not in allowed:
                ext={ 'application/epub+zip':'.epub','application/pdf':'.pdf','text/plain':'.txt'}.get(ctype.split(';')[0],'')
            if not ext or 'text/html' in ctype:
                raise ValueError('Ovo je stranica knjige, a ne fajl za preuzimanje. Dodaj direktan link do EPUB/PDF fajla ili pošalji fajl u Folio.')
            path=Path(target_dir)/('input'+ext);total=0
            with path.open('wb') as f:
                while True:
                    chunk=response.read(65536)
                    if not chunk:break
                    total+=len(chunk)
                    if total>25*1024*1024:raise ValueError('Knjiga je veća od 25 MB.')
                    f.write(chunk)
            if not total:raise ValueError('Preuzet je prazan fajl.')
            return path
        finally:con.close()
    raise ValueError('Link ima previše preusmeravanja.')

def folio_document(path):
    import posixpath, urllib.parse
    chapters=[];title=Path(path).stem;author=''
    with zipfile.ZipFile(path) as z:
        container=parse(z.read('META-INF/container.xml'))
        opath=container.xpath('string(//*[local-name()="rootfile"]/@full-path)')
        opf=parse(z.read(opath));title=opf.xpath('string(//*[local-name()="title"])') or title
        author=opf.xpath('string(//*[local-name()="creator"])')
        manifest={e.get('id'):e.get('href') for e in opf.iter() if local(e.tag)=='item'}
        for e in opf.iter():
            if local(e.tag)!='itemref':continue
            href=manifest.get(e.get('idref'))
            if not href:continue
            name=posixpath.normpath(posixpath.join(posixpath.dirname(opath),urllib.parse.unquote(href.split('#')[0])))
            root=parse(z.read(name));bits=[]
            def collect(node):
                if local(node.tag) in SKIP:return
                if node.text:bits.append(node.text)
                for child in node:
                    collect(child)
                    if child.tail:bits.append(child.tail)
                if local(node.tag) in ('p','div','h1','h2','h3','li','br'):bits.append('\n\n')
            collect(root)
            text=''.join(bits).strip()
            if text:
                heading=root.xpath('string(//*[local-name()="h1" or local-name()="h2" or local-name()="title"][1])')
                chapters.append({'title':heading.strip() or 'Poglavlje '+str(len(chapters)+1),'text':text})
    if not chapters:raise ValueError('Knjiga nema čitljiv sadržaj.')
    return {'title':title,'author':author,'chapters':chapters}

def folio_start_worker():
    import subprocess,sys
    if not (BASE/'folio.dat').exists():return
    python=Path(sys.executable);pythonw=python.with_name('pythonw.exe')
    if pythonw.exists():python=pythonw
    subprocess.Popen([str(python),str(Path(__file__).resolve()),'--folio-worker'],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))

def folio_connect(parent):
    import sys
    path=filedialog.askopenfilename(parent=parent,title='Izaberi Folio-povezivanje.json',filetypes=[('Folio povezivanje','*.json')])
    if not path:return
    try:
        cfg=json.loads(Path(path).read_text(encoding='utf-8'))
        if cfg.get('site')!=FOLIO_SITE or not all(isinstance(cfg.get(k),str) and len(cfg[k])>=32 for k in ('serviceToken','deviceToken')):
            raise ValueError('Neispravan fajl. Preuzmi novi kroz Folio → Prevod preko mog računara.')
        if os.name!='nt':raise ValueError('Automatsko povezivanje je pripremljeno za Windows.')
        BASE.mkdir(parents=True,exist_ok=True)
        (BASE/'folio.dat').write_bytes(folio_protect(json.dumps(cfg).encode()))
        # Same Windows user, no administrator access and no inbound ports.
        startup=Path(os.environ['APPDATA'])/'Microsoft/Windows/Start Menu/Programs/Startup/BookTranslate-Folio.cmd'
        python=Path(sys.executable);pythonw=python.with_name('pythonw.exe')
        if pythonw.exists():python=pythonw
        script=Path(__file__).resolve()
        startup.write_text('@echo off\r\nstart "" "'+str(python)+'" "'+str(script)+'" --folio-worker\r\n',encoding='utf-8')
        folio_start_worker()
        messagebox.showinfo('Folio','Računar je povezan. Folio radi u pozadini i posle zatvaranja ovog prozora, kao i posle sledeće prijave u Windows.\n\nRačunar može biti zaključan. Pristup je zaštićen tvojim Windows nalogom.\n\nVezu možeš isključiti dugmetom Folio – isključi.',parent=parent)
    except Exception as e:messagebox.showerror('Folio',str(e),parent=parent)

def folio_disconnect(parent):
    (BASE/'folio.dat').unlink(missing_ok=True)
    if os.environ.get('APPDATA'):
        (Path(os.environ['APPDATA'])/'Microsoft/Windows/Start Menu/Programs/Startup/BookTranslate-Folio.cmd').unlink(missing_ok=True)
    messagebox.showinfo('Folio','Veza je isključena. Tekući prevod će se zaustaviti pri sledećoj proveri.',parent=parent)

def folio_worker():
    import ctypes
    if os.name!='nt':return
    kernel=ctypes.windll.kernel32
    kernel.CreateMutexW.restype=ctypes.c_void_p
    mutex=kernel.CreateMutexW(None,False,'Local\\BookTranslateFolioWorker')
    if kernel.GetLastError()==183:
        kernel.CloseHandle(ctypes.c_void_p(mutex));return
    BASE.mkdir(parents=True,exist_ok=True)
    try:
        while (BASE/'folio.dat').exists():
            kernel.SetThreadExecutionState(0x80000001)
            try:
                try:
                    with TranslationGate():
                        cfg=folio_config();j=folio_call(cfg,'claim').get('job')
                        if j:folio_process(cfg,j)
                except BlockingIOError:pass
                time.sleep(15)
            except Exception as e:
                # Exception messages are controlled; credentials are never logged.
                (BASE/'folio.log').write_text(time.strftime('%Y-%m-%d %H:%M:%S')+' Veza sa Foliom nije uspela. Proveri internet ili ponovi povezivanje.\n',encoding='utf-8')
                time.sleep(30)
    finally:
        kernel.SetThreadExecutionState(0x80000000)
        kernel.CloseHandle(ctypes.c_void_p(mutex))

def folio_process(cfg,j):
    import ctypes
    stop=threading.Event();done=threading.Event();lease_lost=threading.Event();progress=[0]
    fields=lambda:{'id':j['id'],'lease':j['lease'],'progress':progress[0]}
    def heartbeat():
        while not done.wait(25):
            if not (BASE/'folio.dat').exists():stop.set();return
            try:folio_call(cfg,'progress',fields())
            except Exception:lease_lost.set();stop.set();return
    thread=threading.Thread(target=heartbeat,daemon=True);thread.start();book=None
    try:
        work=BASE/'folio-jobs'/j['id'];work.mkdir(parents=True,exist_ok=True)
        if j.get('url'):path=folio_download(j['url'],work)
        else:
            ext=Path(j.get('filename','book.epub')).suffix.lower()
            if ext not in {'.epub','.pdf','.txt','.mobi','.azw','.azw3','.fb2','.docx','.doc','.djvu','.djv','.rtf','.odt','.html','.htm'}:raise ValueError('Nepodržan format.')
            path=work/('input'+ext)
            if not path.exists():
                data=folio_call(cfg,'input',job=j)
                temp=path.with_suffix(path.suffix+'.tmp');temp.write_bytes(data);os.replace(temp,path)
        if stop.is_set():raise Paused()
        code={'English':'en','French':'fr','German':'de','Latin':'la','Arabic':'ar'}[j['source']]
        source=(j['source'],code)
        prepared=prepare_book(str(path),source,stop,lambda *a:None)
        original_doc=folio_document(prepared)
        book=Book(prepared,'translategemma:12b',source);book.runtime=RuntimeControl()
        out=work/'prevod.epub'
        def notify(n,total,*args):
            progress[0]=round(n/total*99)
            if os.name=='nt':ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
        book.run(stop,notify,out)
        if lease_lost.is_set():raise Paused()
        document=folio_document(out);document['title']+=' — srpski'
        payload={**fields(),'document':json.dumps(document,ensure_ascii=False)}
        files={'file':out}
        if not j.get('originalId'):
            payload['originalDocument']=json.dumps(original_doc,ensure_ascii=False);files['originalFile']=prepared
        done.set();thread.join(timeout=95)
        if lease_lost.is_set():raise Paused()
        folio_call(cfg,'progress',fields())
        folio_call(cfg,'finish',payload,files)
    except Paused:pass
    except Exception as e:
        try:folio_call(cfg,'fail',{**fields(),'error':str(e)[:800]})
        except Exception:pass
    finally:
        done.set();thread.join(timeout=2)
        if book:book.close()
        if os.name=='nt':ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)

if __name__=='__main__':
    import sys
    if '--folio-worker' in sys.argv:folio_worker()
    else:App().mainloop()

