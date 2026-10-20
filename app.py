"""BookTranslate 1.0 — local EPUB translation. Python 3.10+, lxml."""
import hashlib, json, os, queue, re, sqlite3, threading, time, urllib.request, urllib.error, zipfile
from pathlib import Path
from lxml import etree as ET
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

VERSION = '1.1.0'
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

def translate(text, model, source):
    name,code=source
    prompt=(f'You are a professional {name} ({code}) to Serbian Latin (sr-Latn) translator. '
        f'Your goal is to accurately convey the meaning and nuances of the original {name} text while adhering to Serbian grammar, vocabulary, and cultural sensitivities. '
        'Preserve all claims, names, numbers, and paragraph breaks. Do not summarize or omit content. '
        f'Produce only the Serbian Latin translation, without any additional explanations or commentary. Please translate the following {name} text into Serbian Latin:\n\n\n'+text)
    body=json.dumps({'model':model,'messages':[{'role':'user','content':prompt}], 'stream':False,
        'keep_alive':'10m','options':{'temperature':0,'num_ctx':8192,'num_predict':4096}}).encode()
    req=urllib.request.Request('http://127.0.0.1:11434/api/chat',data=body,headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=600) as r: data=json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError('Ollama: '+e.read().decode('utf-8',errors='replace')[:700]) from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError('Ollama nije dostupna ili je odgovor istekao. Pokreni Ollama, proveri model i klikni Nastavi.') from e
    if data.get('done_reason')=='length': raise RuntimeError('Prevod je dostigao ograničenje dužine. Deo nije sačuvan kao završen.')
    out=data.get('message',{}).get('content','').strip()
    if not out: raise RuntimeError('Model je vratio prazan prevod. Pokušaj ponovo.')
    return latin(out)

class Paused(Exception): pass
class Book:
    def __init__(self,path,model,source,root=BASE):
        self.path=Path(path); self.model=model; self.source=source
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
                    value=lead+translate(part.strip(),self.model,self.source)+trail
                    self.db.execute('INSERT OR REPLACE INTO cache VALUES (?,?)',(key,value)); self.db.commit()
                    if len(value)<len(part)*.4: self.warnings.append(f'Proveri mogući kratak prevod: {name}: {part[:100]}')
                translated.append(value); done+=1
                notify(done,self.total,name,part,value,time.monotonic()-started)
            setattr(el,attr,''.join(translated))
        if stop.is_set(): raise Paused()
        output=Path(output)
        if output.resolve()==self.path.resolve(): raise ValueError('Izlaz ne sme da zameni originalnu knjigu.')
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
    def remove(self,ids):
        with self.lock:self.items=[i for i in self.items if i['id'] not in ids];self.save()
    def run(self,stop,emit,book_factory=Book):
        while not stop.is_set():
            item=next((i for i in self.snapshot() if i['state'] in ('pending','paused')),None)
            if not item:break
            id=item['id'];book=None
            self.update(id,state='running',error='');emit('refresh',None)
            try:
                book=book_factory(item['path'],item['model'],tuple(item['source']))
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
        self.events=queue.Queue();self.stop=threading.Event();self.busy=False
        try:self.jobs=QueueStore()
        except Exception as e:
            messagebox.showerror('Red nije učitan',f'{e}\nSačuvani red nije prepisan.');self.destroy();raise
        self.model=tk.StringVar(value='translategemma:12b');self.source=tk.StringVar(value='Engleski');self.folder=tk.StringVar(value=str(Path.home()/'Documents'))
        self.status=tk.StringVar(value='Dodaj više EPUB knjiga. Red se čuva i posle zatvaranja aplikacije.')
        f=ttk.Frame(self,padding=20);f.pack(fill='both',expand=True)
        ttk.Label(f,text='BookTranslate',font=('Segoe UI',24,'bold')).pack(anchor='w')
        ttk.Label(f,text='Lokalni prevod knjiga • srpska latinica • red za obradu').pack(anchor='w',pady=(0,14))
        row=ttk.Frame(f);row.pack(fill='x')
        ttk.Label(row,text='Za nove knjige — jezik:').pack(side='left')
        ttk.Combobox(row,textvariable=self.source,values=list(LANGS),state='readonly',width=12).pack(side='left',padx=6)
        ttk.Label(row,text='Model:').pack(side='left');ttk.Entry(row,textvariable=self.model,width=25).pack(side='left',padx=6)
        ttk.Button(row,text='Ažuriranja',command=self.settings).pack(side='right')
        row=ttk.Frame(f);row.pack(fill='x',pady=10)
        ttk.Label(row,text='Izlazni folder:').pack(side='left');ttk.Entry(row,textvariable=self.folder).pack(side='left',fill='x',expand=True,padx=6)
        ttk.Button(row,text='Izaberi folder',command=self.pick_folder).pack(side='left')
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
    def pick_folder(self):
        p=filedialog.askdirectory()
        if p:self.folder.set(p)
    def add(self):
        model=self.model.get().strip()
        if not model:messagebox.showerror('Model','Unesi naziv modela.');return
        folder=Path(self.folder.get())
        if not folder.is_dir():messagebox.showerror('Folder','Izaberi postojeći izlazni folder.');return
        paths=filedialog.askopenfilenames(filetypes=[('EPUB knjige','*.epub')])
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
            try:self.jobs.run(self.stop,lambda kind,data:self.events.put((kind,data)))
            except Exception as e:self.events.put(('fatal',str(e)));self.events.put(('finished',None))
        threading.Thread(target=work,daemon=True).start()
    def poll(self):
        latest=None;refresh=False
        while True:
            try:kind,data=self.events.get_nowait()
            except queue.Empty:break
            if kind=='progress':latest=data
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

if __name__=='__main__': App().mainloop()
