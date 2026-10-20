# BookTranslate / Prevodilac

Windows aplikacija za lokalni prevod EPUB knjiga preko Ollama / TranslateGemma.

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

Osam testova pokriva EPUB strukturu, nastavak, red, oporavak i provere ažuriranja.
Stvarni GPU prevod i Windows interfejs zahtevaju proveru na Windows računaru.
