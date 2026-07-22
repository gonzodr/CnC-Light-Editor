# CnC Light Editor roadmap

## Következő fejlesztések

1. **Valódi térbeli Particle effekt**
   - [x] Shape-független, közvetlen Random LED / Sparkle generátor seeddel és keyframe-elhető enabled állapottal.
   - Térben mozgó részecskék emitterrel, sebességgel, iránnyal, szórással és gravitációval.
   - A LED csak akkor kapjon fényt, ha a részecske ténylegesen metszi a LED-ablakot.
   - Particle pozíciók megjelenítése az edit nézetben és determinisztikus baked export.

2. **LED-fényprofilok**
   - LED-enként állítható fényfoltméret, intenzitás és forma.
   - Kör, ellipszis vagy egyedi fényablakmaszk.
   - Numerikusan szerkeszthető pozíció és sugár.

3. **LED-map interakció javítása**
   - [x] Az első kattintás csak kijelöli a LED-et, nem változtatja meg a pozícióját.
   - [x] A már kijelölt LED-re történő második kattintás indítja a mozgatási módot.
   - [x] Újabb kattintás rögzíti a pozíciót; az `Esc` visszaállítja az eredeti helyet.
   - [ ] LED-map módosítások saját undo/redo előzménnyel.

4. **Részletes property timeline és Graph Editor**
   - [x] Külön Position, Scale, Rotation, Color, Opacity és Visibility sávok.
   - [x] Dobozos kijelölés, keyframe másolás/beillesztés és tömeges mozgatás.
   - [x] Húzható easing görbék és loop-tartomány.
   - [ ] Timeline-markerek és elnevezett eseménypontok.

5. **Firmware-hű V4 round-trip export**
   - [x] 68 × RGB baked frame import/export, fix `frameMs` időzítéssel.
   - [x] Explicit ID, `loops`, `loopFrames`, egyszer lefutó outro és FULL/CANVAS overlay flag támogatása.
   - [x] Duplikált ID és `frames × 204` adathossz validációja.
   - [x] Több projekt-effekt egyetlen `effect_data.h` könyvtárba fűzése.

6. **Élő Arduino kapcsolat**
   - Soros port kiválasztása és fizikai LED-léptetés.
   - A kijelölt LED automatikus felvillantása.
   - Animáció streamelése közvetlen hardveres teszthez.

7. **Projektkezelés és szerkesztési eszközök**
   - [x] New, Open, Save As, automatikus mentés és helyreállítás.
   - Több alakzat kijelölése, igazítása, elosztása és csoportosítása.
   - [x] Layer átnevezés, zárolás és opacity.

8. **Architektúra és Raspberry Pi teljesítmény**
   - A canvas, timeline, inspector, LED-map és firmware-import külön modulokra bontása.
   - Skálázott artworkök és fényrétegek cache-elése.
   - [x] Desktop-független, Pygame-es fájlböngésző és megerősítő dialógus headless Pi futtatáshoz.
   - Windows- és Raspberry Pi-specifikus CI/smoke ellenőrzések.

9. **Professional Effect Bank workflow**
   - [ ] Azonos firmware-ID esetén választható Replace, ne vak duplikáció.
   - [ ] Thumbnail vagy animált mini előnézet minden effekthez.
   - [ ] Rendezés ID, név, flash-méret vagy lejátszási hossz szerint.
   - [ ] Keresés és projekttel együtt menthető címkék.
   - [ ] Export előtti változáslista: hozzáadás, csere, átnevezés, ID-váltás, törlés.
   - [ ] Egyetlen effekt külön importja és exportja.
   - [ ] `Optimize bank` elemzés pazarló FPS-re, túl hosszú effektre és loopolható szakaszokra.
   - [ ] Összesített flash-foglalás mellett a bank teljes becsült lejátszási ideje.
