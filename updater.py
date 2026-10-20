"""Stable launcher. Update only between sessions; user data stays in LOCALAPPDATA."""
import hashlib, io, json, os, re, runpy, shutil, subprocess, sys, tempfile, urllib.request, zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent
BASE=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'BookTranslate'
FILES={'app.py','requirements.txt','VERSION'}
MAX_BYTES=10*1024*1024

def version(text):
    if not re.fullmatch(r'\d+\.\d+\.\d+',text):raise ValueError('Neispravna verzija')
    return tuple(map(int,text.split('.')))
def load(path,default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default
def atomic(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.tmp')
    with tmp.open('w',encoding='utf-8') as f:json.dump(obj,f);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)
def fetch(url):
    if not url.startswith('https://'):raise ValueError('Ažuriranje zahteva HTTPS.')
    req=urllib.request.Request(url,headers={'User-Agent':'BookTranslate-Updater','Accept':'application/vnd.github+json'})
    with urllib.request.urlopen(req,timeout=20) as r:
        data=r.read(MAX_BYTES+1)
    if len(data)>MAX_BYTES:raise ValueError('Paket je prevelik.')
    return data

def install_blob(blob,digest,ver,destination,dependency_check=None):
    version(ver)
    if not re.fullmatch(r'[a-f0-9]{64}',digest) or hashlib.sha256(blob).hexdigest()!=digest:raise ValueError('SHA-256 provera nije uspela.')
    destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.exists():raise ValueError('Direktorijum te verzije već postoji.')
    with tempfile.TemporaryDirectory(dir=destination.parent,prefix='staging-') as td:
        stage=Path(td)
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            if len(z.namelist())!=len(FILES) or set(z.namelist())!=FILES:raise ValueError('Neočekivani fajlovi u paketu.')
            if sum(i.file_size for i in z.infolist())>MAX_BYTES:raise ValueError('Paket je prevelik.')
            for name in FILES:(stage/name).write_bytes(z.read(name))
        if (stage/'VERSION').read_text(encoding='utf-8').strip()!=ver:raise ValueError('Verzija paketa se ne slaže.')
        source=(stage/'app.py').read_text(encoding='utf-8');compile(source,'app.py','exec')
        m=re.search(r"^VERSION\s*=\s*['\"]([^'\"]+)",source,re.M)
        if not m or m.group(1)!=ver:raise ValueError('Verzija aplikacije se ne slaže.')
        if dependency_check:dependency_check(stage)
        shutil.copytree(stage,destination)

def deps(stage):
    subprocess.run([sys.executable,'-m','pip','install','-r',str(stage/'requirements.txt')],check=True,timeout=300)
    subprocess.run([sys.executable,'-c','import lxml.etree; import tkinter'],check=True,timeout=30)

def choose_active(base=BASE,root=ROOT):
    bundled=(root/'VERSION').read_text(encoding='utf-8').strip();version(bundled)
    pointer=load(base/'active_version.json',{})
    old=pointer.get('version','')
    if old:
        version(old);path=base/'versions'/old
        if version(old)>version(bundled) and (path/'app.py').is_file():return path,old
    return root,bundled

def update(base=BASE,root=ROOT,fetcher=fetch,dependency_check=deps):
    current,ver=choose_active(base,root)
    cfg=load(base/'updates.json',{'auto_update':True,'repository':'rilke1/prevodilac'})
    if not cfg.get('auto_update') or not cfg.get('repository'):return current,ver
    repo=cfg['repository']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo):raise ValueError('Neispravan GitHub repozitorijum.')
    release=json.loads(fetcher(f'https://api.github.com/repos/{repo}/releases/latest'))
    if release.get('draft') or release.get('prerelease'):return current,ver
    new=release['tag_name'].removeprefix('v')
    if version(new)<=version(ver):return current,ver
    assets={a['name']:a for a in release.get('assets',[])}
    archive=assets['BookTranslate-update.zip'];sha=assets['BookTranslate-update.sha256']
    prefix=f'https://github.com/{repo}/releases/download/v{new}/'
    for a in (archive,sha):
        if not a['browser_download_url'].startswith(prefix):raise ValueError('Neočekivana adresa ažuriranja.')
    digest=fetcher(sha['browser_download_url']).decode('ascii').strip().split()[0]
    dest=base/'versions'/new
    blob=fetcher(archive['browser_download_url'])
    if dest.exists():
        # Recover an interrupted activation, verifying every file against the release.
        if hashlib.sha256(blob).hexdigest()!=digest:raise ValueError('SHA-256 provera nije uspela.')
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            if set(z.namelist())!=FILES or len(z.namelist())!=len(FILES):raise ValueError('Neispravan paket.')
            if any(not (dest/n).is_file() or (dest/n).read_bytes()!=z.read(n) for n in FILES):raise ValueError('Postojeća verzija se razlikuje od paketa.')
        dependency_check(dest)
    else:install_blob(blob,digest,new,dest,dependency_check)
    atomic(base/'active_version.json',{'version':new,'previous':ver})
    return dest,new

def main():
    BASE.mkdir(parents=True,exist_ok=True)
    try:active,ver=choose_active()
    except Exception as e:print('Ne mogu da pročitam izbor verzije:',e);active=ROOT;ver='bundled'
    try:active,ver=update()
    except Exception as e:print('Ažuriranje nije primenjeno; pokrećem postojeću verziju:',e)
    print('BookTranslate',ver)
    # GUI does not start a second updater or mutate source files in use.
    try:runpy.run_path(str(active/'app.py'),run_name='__main__')
    except Exception:
        if active!=ROOT:
            print('Nova verzija nije pokrenuta. Vraćam uključenu verziju.')
            atomic(BASE/'active_version.json',{})
            atomic(BASE/'updates.json',{**load(BASE/'updates.json',{}),'auto_update':False})
            runpy.run_path(str(ROOT/'app.py'),run_name='__main__')
        else:raise
if __name__=='__main__':main()
