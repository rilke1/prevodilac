"""Build the small release asset consumed by updater.py."""
from pathlib import Path
import hashlib, zipfile
root=Path(__file__).resolve().parent
out=root/'dist';out.mkdir(exist_ok=True)
archive=out/'BookTranslate-update.zip'
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
    for name in ('app.py','requirements.txt','VERSION'):z.write(root/name,name)
(out/'BookTranslate-update.sha256').write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'\n',encoding='ascii')
print(archive)
