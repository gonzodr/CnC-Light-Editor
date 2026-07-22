# CnC Light Editor

Pygame-alapú, réteges és keyframe-es fényeffekt-szerkesztő a Cheech & Chong flipperhez.

## Jelenlegi hatókör

- A baked-frame firmware-rel közös, 68 LED-es playfield-térkép (`0..67`).
- A `data/led_map.json` pozíciói képfeldolgozással készültek a kapott jelöléses rajzból.
- A LED-ek `firmware_index` értékei egyelőre ideiglenes térbeli sorrendet jelentenek. A fizikai WS2812B-lánccal történő kalibrálás előtt ne kerüljenek végleges firmware-be.
- A firmware további 56 LED-je szándékosan nincs az editorban.

## Már működő funkciók

- Eredeti playfield-grafika és 68 pontos LED-overlay.
- Ellipszis, téglalap, háromszög és vonal alakzat.
- Több, külön ki- és bekapcsolható layer.
- Pozíció-, scale-, több teljes fordulatot megőrző forgatás-, szín-, opacity-, stroke- és visibility-keyframe.
- Linear, Ease In, Ease Out és Ease In/Out interpoláció, kijelölhető keyframe-ek és idővonalas lejátszás.
- Fill/stroke alakzatmód, állítható körvonalvastagság, keyframe-elhető peremlágyítás (Feather) és pozitív/negatív Mask Expansion.
- Solid, tetszőleges számú húzható színstoppal szerkeszthető Linear és Radial gradient fill; a Radial lehet sugárirányú vagy forgásirányú, a Linear iránya és az Angular kezdőfázisa 0–360° között állítható.
- Layer-szintű, shape-független Random LED / Sparkle effekt determinisztikus seeddel, life, born speed, maximális aktív elemszám és szín paraméterekkel. Az enabled állapot keyframe-elhető.
- Edit nézet: a DXF-ből képzett fehér vonalas guide jelenik meg sötét háttéren.
- Stencil mód: nagy, additívan keveredő fényforrások világítanak az alfa-lyukas playfield artwork mögött.
- Kalibrációs nézet húzható, hozzáadható és törölhető LED-helyekkel, beírható firmware-ID-vel, LED-nevekkel és ütközésmentes indexcserével.
- V4 baked-RGB `effect_data.h` import, effektválasztás és loop/outro-hű idővonalas lejátszás; a régi `EffectID...` maszkfájlok olvasása kompatibilitási módként megmaradt.
- A `NULL` nevű LED-slotok előnézetben és exportban kikapcsolva maradnak.
- Az export validálja a `0..67` firmware-sorrendet és frame-enként pontosan 68 × RGB, azaz 204 bájtot generál. A `NULL` slot RGB-je fekete.
- JSON projektmentés és visszatöltés.
- A végleges V4 `EffectDef` protokollal kompatibilis Arduino `PROGMEM` export explicit ID, `frameMs`, `loops`, `loopFrames` és `overlay` metaadatokkal. Az azonos képkockák is megmaradnak, mert a motor fix frame-idővel játszik.
- Külön Effect Bank / Export ablak a meglévő `effect_data.h` feltérképezéséhez, több effekt együttes újraexportálásához és a fix 150 KiB flash-keret vizuális tervezéséhez.

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
- Inspector: az X/Y/W/H/ROT/TURNS/OPACITY mezőket vízszintesen húzva finoman állíthatod, kattintás után pedig közvetlenül beírhatod az értéket. A `ROT` a körön belüli szög, a `TURNS ×N` a teljes fordulatok száma; például a `ROT` mezőbe írt `720` automatikusan `0.0° ×2` lesz, és két teljes fordulatként interpolálódik. A számbevitel támogatja a `Ctrl+A`, `Ctrl+V`, Enter és Esc műveleteket.
- Layer-sorrend: húzd a layer sorát fel vagy le. A timeline layernevére kattintva aktiválhatod; az Inspector `D` gombja vagy a `Ctrl+D` duplikálja, a `−` gomb pedig törli az aktív layert.
- Keyframe-időzítés: kattintással jelöld ki, majd húzd az idővonal gyémántját; a snapping frame-határra igazít.
- Az inaktív layerek és nem kijelölt objektumok keyframe-jei is láthatók a saját sorukban visszafogott kékesszürke gyémántként; az aktív cél keyframe-jei maradnak kiemelve és szerkeszthetők.
- A timeline layer-sorai előtt külön `ON/OFF` kapcsoló vezérli a layer láthatóságát. A `>` gomb lenyitja a layert az animált property-k külön dope sheet sávjaira. Ha a sorok nem férnek ki, a layernév-oszlop fölött görgetve függőlegesen lapozhatók; a jobb oldali időterület fölötti görgő továbbra is timeline-zoom.
- Graph Editor: jelölj ki egy numerikus property-sávot vagy keyframe-et, majd nyomd meg a timeline `GRAPH` gombját. A görbe ugyanazt az interpolációt mutatja, amely az exportba kerül; a pontokat vízszintesen az idő, függőlegesen az érték módosításához húzhatod. Jobb kattintással válaszd a `Custom Bezier` módot a két lila fogantyú megjelenítéséhez; a függőleges fogantyúk túllövéses görbét is engednek. A `DOPE` gombbal válthatsz vissza.
- Több keyframe kijelölése: jobb egérgombbal húzz kijelölőkeretet a timeline-on, akár több property-sávon és shape-en keresztül; `Ctrl` mellett a találatok hozzáadódnak a meglévő kijelöléshez. `Ctrl` + jobb kattintással egyenként is hozzáadhatsz vagy kivehetsz keyframe-eket.
- Keyframe-offset: fogd meg bármelyik kijelölt gyémántot; a teljes kijelölés shape-ek és property-k között is együtt mozog, az egymás közötti időeltolás megtartásával.
- Keyframe másolás: `Ctrl+C` eltárolja az összes kijelölt keyframe-et, a `Ctrl+V` pedig úgy illeszti be őket, hogy a legkorábbi másolat az aktuális playheadhez kerüljön. A shape/property cél, az egymás közötti időeltolás, az easing és a Custom Bezier adatok is megmaradnak; szükség esetén a timeline automatikusan meghosszabbodik legfeljebb 15 másodpercig.
- Keyframe easing/törlés: jobb kattintás a gyémántra.
- Ha egy property legelső keyframe-jét törlöd, az első megmaradó keyframe értéke lesz az alakzat új alapértéke, ezért a keyframe előtti szakasz nem ugrik vissza egy oda nem illő default állapotra.
- Timeline zoom: egérgörgő; vízszintes görgetés: `Shift` + egérgörgő. Frame-skálán a playhead és a húzott keyframe-ek mindig pontos frame-határra illeszkednek.
- Nagy timeline-zoomnál az időskála automatikusan másodpercről `F0`, `F1`… frame-számozásra vált. Importált firmware-effektnél az effekt saját `frameMs` értékét használja.
- Animáció hossza: a felső Length mezőbe beírható, vagy a mellette lévő csúszkával állítható 0,5–15 másodperc között.
- Fill/stroke mód és stroke-vastagság: a Transform inspector Style részében. A stroke mező vízszintesen húzható, kattintás után pedig kézzel is beírható `0,1–5,0` között.
- Feather és Mask Expansion: a Transform mezőkben százalékosan húzhatók vagy beírhatók. A Feather `0–10%` között lágyítja a maszk mindkét szélét, az Expansion `−10–+10%` között összehúzza vagy kitágítja. Mindkettő keyframe-elhető, és a lágyított LED-fényerő kerül a firmware-exportba is.
- Gradient: Fill módban nyisd meg a `Gradient fill…` panelt. Kattints a colorbarra új stophoz, húzd a stopokat, majd adj színt a kijelölt stopnak. A Solid/Linear/Radial mód, a Radial `Radius/Angular` iránya, valamint a Linear angle és az Angular phase ugyanitt állítható.
- Firmware V4: az Inspectorban állítható az explicit effekt-ID, a 20/25/≈30,3 FPS preset (`50/40/33 ms`), a loopok száma, valamint a playheadnél a loop vége. A `FULL/CANVAS` exportkapcsoló adja az `overlay` flaget; a panel élő flash- és lejátszási időbecslést mutat.
- Random LED / Sparkle: az aktív layer `FX` gombjával nyitható. Nem használ shape-maszkot: közvetlenül a firmware LED-slotokat villogtatja. A Toggle az aktuális időnél keyframe-et hoz létre, a `+ Key` megtartja az aktuális enabled állapotot.
- Undo/redo: `Ctrl+Z`, `Ctrl+Shift+Z` vagy `Ctrl+Y`; aktív layer duplikálása: `Ctrl+D`.
- Minden tulajdonság keyframe-je: `K`; láthatóság: `V`; lejátszás: `Space`.
- Snap ki/be: `G`. Bekapcsolva az alakzat pozícióját és méretét 0,01-es normalizált rácsra, a forgatást 15°-ra, a keyframe idejét pedig FPS-képkockahatárra igazítja; kikapcsolva minden folyamatosan mozgatható.
- Projektmentés/betöltés: `Ctrl+S`, `Ctrl+O`; Save As: `Ctrl+Shift+S`. Az első mentés fájlnevet kér, a további mentések ugyanazt a `.cnclight` fájlt frissítik.
- A jobb oldali színminták az aktuális playheadnél hoznak létre szín-keyframe-et. Többszörös keyframe-kijelölésnél a kiválasztott szín minden kijelölt időpontra egyszerre kerül rá.

A toolbar `Export` gombja az Effect Bank ablakot nyitja meg. A `Map header…` beolvassa egy meglévő V4 `effect_data.h` összes effektjét; a felső szegmentált memóriasáv és az effektenkénti színes hosszcsíkok a tényleges `frames × 204` flash-foglalást mutatják. A panel kijelzi a 150 KiB keretből felhasznált és szabad helyet, valamint az aktuális projekt FPS-ével becsült hátralévő animációs időt. Egy blokkra kattintva a név és az explicit firmware-ID szerkeszthető, a mappelt effekt eltávolítható az exportbankból. Az aktuális projekt mindig külön `CURRENT` blokként szerepel, és a saját exportnevét/ID-ját ugyanitt lehet beállítani. Az `Export bank…` egy közös, firmware-kész V4 headert ír; duplikált ID, hibás LED-map vagy 150 KiB fölötti adat esetén blokkolja az exportot.

A projektfájl helyét az első Save alkalmával lehet kiválasztani; a címsorban és a Save gombon látható `*` mentetlen módosítást jelez.

A bal oldali `Import` gombbal válaszd ki a firmware `effect_data.h` fájlját. Az editor automatikusan stencil nézetre vált; a `Next FX` végiglépteti a `bakedEffects[]` leírókat, a `Project` pedig visszatér a szerkesztett animációhoz. `Ctrl+I` szintén megnyitja az importot. Parancssorból: `cnc-light-editor --effect-data "F:\...\effect_data.h"`.

Az editor minden opacityt, easinget, gradientet és generatív effektet előre belesüt a frame-ek RGB-értékeibe. A V4 motor kizárólag ezeket a kész képkockákat játssza le. A loop első `loopFrames` képkockája `loops` alkalommal ismétlődik, a maradék outro egyszer fut le; az ID explicit és nem függ a táblasorrendtől. `FULL` módban a fekete cella leoltja a LED-et; `CANVAS/OVERLAY` módban a `(0,0,0)` átlátszó, ezért a normál játékfény látható marad alatta.

## LED-kalibráció

1. Töltsd fel ideiglenesen a `firmware/playfield_led_calibration.ino` sketch-et az Arduino Megára.
2. Nyisd meg a Serial Monitort `115200` baud sebességgel.
3. Az `n` és `p` parancsokkal léptesd az egyetlen világító LED-et, vagy küldj egy `0..67` indexet.
4. Az editorban válaszd a `LED map` módot. Az első kattintás kijelöli a LED-et, a kijelölt LED-re adott második kattintás elindítja a mozgatást. Mozgasd az egeret gombnyomás nélkül, majd kattints a rögzítéshez; az `Esc` visszaállítja az eredeti pozíciót.
5. Az `Assigned LED ID` mezőbe írd be a fizikai lánc `0..67` indexét. Az ütköző indexek automatikusan felcserélődnek.
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

A priorizált fejlesztési lista a [ROADMAP.md](ROADMAP.md) fájlban található.

Az artwork és a DXF a gép saját gyártási anyaga. A repository jelenleg nem tartalmaz külön nyílt forrású licencet, ezért a GitHub alapértelmezett szerzői jogi szabályai érvényesek.
