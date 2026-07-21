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
- Kalibrációs nézet kattintható LED-helyekkel és ütközésmentes firmware-index cserével.
- Az export validálja a `0..58` indextartományt, majd firmware-sorrendben generál.
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

## LED-kalibráció

1. Töltsd fel ideiglenesen a `firmware/playfield_led_calibration.ino` sketch-et az Arduino Megára.
2. Nyisd meg a Serial Monitort `115200` baud sebességgel.
3. Az `n` és `p` parancsokkal léptesd az egyetlen világító LED-et, vagy küldj egy `0..58` indexet.
4. Az editorban válaszd a `Calibrate` módot, kattints a világító grafikai LED-helyre, majd az `Index -/+` gombokkal rendeld hozzá a kijelzett firmware-indexet.
5. Mentsd a térképet. Csak a teljes fizikai ellenőrzés után használd a `Mark hardware verified` gombot.
6. A kalibráció után töltsd vissza a normál flipper firmware-t.

Az indexmódosítás csereként működik: ha egy index már foglalt, a két LED indexe felcserélődik. Emiatt a térkép minden lépés után egyedi és exportálható marad.

## Tesztelés

```powershell
pytest
python -m cnc_light_editor.app --smoke-test --screenshot smoke.png
```

Minden push és pull request ugyanezeket a teszteket, valamint a Pygame headless smoke tesztjét GitHub Actionsben is lefuttatja.

## Következő mérföldkő

Közvetlen soros kapcsolat az editor és az Arduino diagnosztikai módja között, hogy a `Next` gomb egyben a hardveren is a következő LED-re váltson.

Az artwork és a DXF a gép saját gyártási anyaga. A repository jelenleg nem tartalmaz külön nyílt forrású licencet, ezért a GitHub alapértelmezett szerzői jogi szabályai érvényesek.
