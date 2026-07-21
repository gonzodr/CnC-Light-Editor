# CnC Light Editor

Pygame-alapú, réteges és keyframe-es fényeffekt-szerkesztő a Cheech & Chong flipperhez.

## Jelenlegi hatókör

- Alapértelmezetten a playfield alatt azonosított 59 LED; a térkép az editorban legfeljebb 68 firmware-slotig bővíthető.
- A `data/led_map.json` pozíciói képfeldolgozással készültek a kapott jelöléses rajzból.
- A LED-ek `firmware_index` értékei egyelőre ideiglenes térbeli sorrendet jelentenek. A fizikai WS2812B-lánccal történő kalibrálás előtt ne kerüljenek végleges firmware-be.
- A firmware további 56 LED-je szándékosan nincs az editorban.

## Már működő funkciók

- Eredeti playfield-grafika és 59 pontos LED-overlay.
- Ellipszis, téglalap, háromszög és vonal alakzat.
- Több, külön ki- és bekapcsolható layer.
- Pozíció-, scale-, forgatás-, szín-, opacity-, stroke- és visibility-keyframe.
- Linear, Ease In, Ease Out és Ease In/Out interpoláció, kijelölhető keyframe-ek és idővonalas lejátszás.
- Fill/stroke alakzatmód, állítható körvonalvastagság.
- Edit nézet: a DXF-ből képzett fehér vonalas guide jelenik meg sötét háttéren.
- Stencil mód: nagy, additívan keveredő fényforrások világítanak az alfa-lyukas playfield artwork mögött.
- Kalibrációs nézet húzható, hozzáadható és törölhető LED-helyekkel, beírható firmware-ID-vel, LED-nevekkel és ütközésmentes indexcserével.
- Meglévő `effect_data.h` firmware-effektek importja, effektválasztása és idővonalas lejátszása.
- A `NULL` nevű LED-slotok előnézetben és exportban kikapcsolva maradnak.
- Az export validálja a `0..58` playfield-indextartományt, majd 68 LED-es firmware-frame-et generál; az utolsó 9 LED RGB-értéke nulla.
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

- Új alakzat: húzd a bal oldali Circle/Rect/Tri/Line eszközt a playfieldre.
- Mozgatás: fogd meg közvetlenül a kijelölt alakzatot.
- Méretezés: húzd a négy sarokfogópont egyikét.
- Forgatás: húzd a kijelölés fölötti kör alakú fogópontot.
- Zoom: egérgörgő a viewport felett; pásztázás: középső egérgomb vagy `Space` + húzás.
- Inspector: az X/Y/W/H/ROT/OPACITY mezőket vízszintesen húzva finoman állíthatod.
- Layer-sorrend: húzd a layer sorát fel vagy le. A timeline layernevére kattintva aktiválhatod; az Inspector `−` gombja törli az aktív layert.
- Keyframe-időzítés: kattintással jelöld ki, majd húzd az idővonal gyémántját; a snapping frame-határra igazít.
- Több keyframe kijelölése: `Ctrl` + kattintás. A `Delete` csak a kijelölt keyframe-et törli.
- Keyframe easing/törlés: jobb kattintás a gyémántra.
- Timeline zoom: egérgörgő; vízszintes görgetés: `Shift` + egérgörgő.
- Nagy timeline-zoomnál az időskála automatikusan másodpercről `F0`, `F1`… frame-számozásra vált. Importált firmware-effektnél a 60 ms-os firmware-frame-eket mutatja.
- Animáció hossza: a felső Length mezőbe beírható, vagy a mellette lévő csúszkával állítható 0,5–15 másodperc között.
- Fill/stroke mód és stroke-vastagság: a Transform inspector Style részében.
- Undo/redo: `Ctrl+Z`, `Ctrl+Shift+Z` vagy `Ctrl+Y`; duplikálás: `Ctrl+D`.
- Minden tulajdonság keyframe-je: `K`; láthatóság: `V`; lejátszás: `Space`.
- Snap ki/be: `G`. Bekapcsolva az alakzat pozícióját és méretét 0,01-es normalizált rácsra, a forgatást 15°-ra, a keyframe idejét pedig FPS-képkockahatárra igazítja; kikapcsolva minden folyamatosan mozgatható.
- Mentés/betöltés: `Ctrl+S`, `Ctrl+O`.
- A jobb oldali színminták az aktuális playheadnél hoznak létre szín-keyframe-et.

A toolbar `Arduino export` gombja az `exports/cnc_effect.h` fájlt generálja. A projekt alapértelmezetten a `projects/current.cnclight` fájlba ment.

A bal oldali `Import` gombbal válaszd ki a firmware `effect_data.h` fájlját. Az editor automatikusan stencil nézetre vált; a `Next FX` végiglépteti a felismert `EffectID` tömböket, a `Project` pedig visszatér a szerkesztett animációhoz. `Ctrl+I` szintén megnyitja az importot. Parancssorból: `cnc-light-editor --effect-data "F:\...\effect_data.h"`.

Az editor az opacityt az exportált RGB-csatornákba előre belekeveri. A jelenlegi `d_light_effects.ino` motor ezzel szemben 68 elemű maszk-/palettakódos `uint8_t` frame-eket olvas, és nincs külön opacity csatornája; a `fadeToBlackBy` csak globális időbeli utánfényt ad. Az editor RGB-exportjához ezért a firmware-ben később külön RGB-frame lejátszó szükséges.

## LED-kalibráció

1. Töltsd fel ideiglenesen a `firmware/playfield_led_calibration.ino` sketch-et az Arduino Megára.
2. Nyisd meg a Serial Monitort `115200` baud sebességgel.
3. Az `n` és `p` parancsokkal léptesd az egyetlen világító LED-et, vagy küldj egy `0..58` indexet.
4. Az editorban válaszd a `LED map` módot, majd kattintással és húzással állítsd be a világító grafikai pozíciót.
5. Az `Assigned LED ID` mezőbe írd be a fizikai lánc `0..58` indexét. Az ütköző indexek automatikusan felcserélődnek.
6. Adj nevet a LED-nek. A `NULL` név kikapcsolja az adott firmware-slotot az előnézetben és az exportban.
   A névmező támogatja a `Ctrl+V` beillesztést is.
7. A `Position −/+` a grafikai helyek, a `LED ID −/+` a fizikai lánc sorrendjében léptet. Az `Add LED` középre tesz egy új pontot, a `Delete LED` törli a kijelöltet és folytonosra zárja a firmware-indexeket. A `Save LED map` a pozíciókat, ID-ket és neveket is elmenti.
8. Csak a teljes fizikai ellenőrzés után használd a `Mark hardware verified` gombot.
9. A kalibráció után töltsd vissza a normál flipper firmware-t.

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
