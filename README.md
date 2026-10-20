# BookTranslate / Prevodilac

Windows aplikacija za lokalni prevod knjiga i dokumenata preko Ollama / TranslateGemma.

- Red za više knjiga; obrada jedna po jedna, uz dodavanje tokom rada.
- Čuvanje reda i završenih prevoda; pauza i nastavak posle zatvaranja.
- Srpska latinica, TranslateGemma 12B kao podrazumevani model.
- Čuvanje EPUB slika, veza i strukture; tekst uz inline formatiranje se prevodi
  odvojeno, što može da oslabi kontekst rečenice.
- Automatska ažuriranja iz GitHub Releases pri pokretanju kroz POKRENI.bat.

## Pokretanje

1. Instaliraj Ollama i pokreni `ollama pull translategemma:12b`.
2. Instaliraj Python 3.10+ sa Python Launcher-om i Tcl/Tk.
3. Preuzmi izvorni kod (Code → Download ZIP), raspakuj i pokreni POKRENI.bat.
4. Izaberi jezik i izlazni folder, dodaj EPUB knjige i pokreni red.

Knjige i sačuvani napredak su lokalni: `%LOCALAPPDATA%\BookTranslate`.
Ollama prima tekst isključivo preko `127.0.0.1`.

## Ažuriranja

Repozitorijum: `rilke1/prevodilac`. Za ažuriranja bez tokena mora biti javan.
Prilikom svakog push-a na main, GitHub Actions pokreće testove i objavljuje
release ako verzija iz VERSION još nije objavljena. Za novu verziju promeni
VERSION fajl i VERSION u app.py. Postojeće objave se ne prepisuju.

POKRENI.bat proverava poslednji stabilni release. Paket se proverava SHA-256
kontrolnim zbirom, instalira u zaseban direktorijum i aktivira pre pokretanja GUI-ja.
Pri grešci preuzimanja ostaje postojeća verzija. Za isključivanje ili promenu
repozitorijuma koristi dugme Ažuriranja. Knjige se nikad ne dodaju u repo.
Stabilni launcher se menja ručnim preuzimanjem novog punog paketa.

## Provera

`python -m unittest discover -v`

Dvanaest testova pokriva EPUB strukturu, nastavak, red, oporavak i provere ažuriranja.
Stvarni GPU prevod i Windows interfejs zahtevaju proveru na Windows računaru.

## Ulazni formati (1.2.0)

EPUB direktno; MOBI, AZW/AZW3/AZW4, PRC, FB2, DOCX, ODT, RTF, HTML,
HTMLZ, LIT, LRF, PDB, PML/PMLZ, RB, SNB, TCR i CHM preko Calibre-a.
TXT i Markdown kao običan tekst. PDF/XPS i PNG/JPEG/TIFF/BMP/WebP kroz
izdvajanje teksta i Tesseract OCR. DjVu/DJV (i ekstenzija DJWU) zahteva
DjVuLibre ddjvu koji prvo pravi PDF. Stari DOC zahteva LibreOffice + Calibre.

Instaliraj [Calibre](https://calibre-ebook.com/download_windows). Za DjVu
instaliraj [DjVuLibre](https://djvu.sourceforge.net/) sa ddjvu u PATH-u.
Za OCR instaliraj [Tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html)
sa odgovarajućim jezičkim paketima: eng, lat, fra, deu ili ara.
Standardni Windows folderi za ove alate se automatski pretražuju.

Izlaz je uvek EPUB. Zaštićene knjige i proizvoljni binarni formati nisu podržani.
PDF, DjVu i slike daju tekst po stranama: originalni raspored, slike i fusnote
nisu rekonstruisani. OCR treba pregledati; višekolonski i mešoviti sken/tekst
dokumenti mogu imati greške u redosledu ili izostavljen tekst na slikama.
Windows konverteri i Tesseract su spoljne instalacije; testirane su njihove
integracione putanje pomoću zamena, a stvarno izdvajanje običnog PDF teksta
i stvaranje EPUB-a provereni su lokalno.

## Intenzitet rada (1.3.0)

Klizač 10–100% kontroliše odnos vremena računanja i pauze između novih delova.
Na 50% aplikacija posle dela čeka približno koliko je taj deo trajao, a na 25%
čeka približno tri puta toliko. Vreme učitavanja i ostalih aplikacija može da
promeni stvarno prosečno opterećenje. Ovo nije hardverski limit GPU zauzeća:
tokom računanja GPU može dostići 100%, a VRAM ostaje zauzet i tokom pauza.
Promene se čuvaju i važe za sve knjige, od narednog dela.

Dugme Igranje: CPU postavlja CPU režim sa dve niti i intenzitetom 25%.
Ollami se šalje num_gpu=0, a postojeći model se uklanja pre promene uređaja.
Provera stvarnog GPU/VRAM ponašanja i FPS-a zahteva probu na korisnikovom računaru.
Ako je red pauziran, CPU režim se primenjuje tek kada ga nastaviš. Prevod je
znatno sporiji; priprema/OCR dokumenata nije obuhvaćena ovim klizačem.
Za potpuno mirovanje koristi Pauziraj red. Za ručno oslobađanje modela iz
memorije posle pauze: ollama stop translategemma:12b.
