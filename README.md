# CnC Light Editor

Pygame-alapú, réteges és keyframe-es fényeffekt-szerkesztő a Cheech & Chong flipperhez.

## Jelenlegi hatókör

- Kizárólag a playfield alatt azonosított 59 LED.
- A `data/led_map.json` pozíciói képfeldolgozással készültek a kapott jelöléses rajzból.
- A LED-ek `firmware_index` értékei egyelőre ideiglenes térbeli sorrendet jelentenek. A fizikai WS2812B-lánccal történő kalibrálás előtt ne kerüljenek végleges firmware-be.
- A firmware további 56 LED-je szándékosan nincs az editorban.

## Már működő funkciók

- Eredeti playfield-grafika és 59 pontos LED-overlay.
- Ellipszis, téglalap, háromszög és vonal alakzat.
- Több, külön ki- és bekapcsolható layer.
- Pozíció-, scale-, forgatás-, szín-, opacity- és visibility-keyframe.
- Lineáris interpoláció és idővonalas lejátszás.
- Stencil mód: kizárólag a LED-nyílásokban látszik a kompozitált animáció.
- JSON projektmentés és visszatöltés.
- Arduino `PROGMEM` header export; az egymást követő azonos frame-ek automatikusan összevonódnak.

## Telepítés Windows alatt

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
cnc-light-editor
```

## Telepítés Raspberry Pi OS alatt

```bash
sudo apt install python3-venv
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cnc-light-editor
```

## Kezelés

- Alakzat kijelölése és mozgatása: bal egérgomb + húzás.
- Méretezés: egérgörgő.
- Forgatás: `[` és `]`.
- Minden tulajdonság keyframe-je: `K`.
- Láthatóság váltása/keyframe-je: `V`.
- Lejátszás: `Space`.
- Mentés/betöltés: `Ctrl+S`, `Ctrl+O`.
- A jobb oldali színminták az aktuális playheadnél hoznak létre szín-keyframe-et.

A toolbar `Arduino export` gombja az `exports/cnc_effect.h` fájlt generálja. A projekt alapértelmezetten a `projects/current.cnclight` fájlba ment.

## Tesztelés

```powershell
pytest
python -m cnc_light_editor.app --smoke-test --screenshot smoke.png
```

## Következő mérföldkő

A firmware-be kerülő diagnosztikai mód egyenként felvillantja a playfield LED-jeit. Az editor kalibrációs nézetében minden felvillanó LED-hez hozzá lehet majd rendelni a megfelelő grafikai pozíciót. Ezzel a most ideiglenes indexsorrend véglegessé tehető anélkül, hogy a geometriára épülő animációkat újra kellene rajzolni.

Az artwork és a DXF a gép saját gyártási anyaga; a repository publikussá tétele előtt a felhasználási jogokat külön ellenőrizni kell.
