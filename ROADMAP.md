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
   - Külön Position, Scale, Rotation, Color, Opacity és Visibility sávok.
   - Dobozos kijelölés, keyframe másolás/beillesztés és tömeges mozgatás.
   - Húzható easing görbék, loop-tartomány és markerek.

5. **Firmware-hű V4 round-trip export**
   - [x] 68 × RGB baked frame import/export, fix `frameMs` időzítéssel.
   - [x] Explicit ID, `loops`, `loopFrames` és egyszer lefutó outro támogatása.
   - [x] Duplikált ID és `frames × 204` adathossz validációja.
   - Több projekt-effekt egyetlen rendezhető `effect_data.h` könyvtárba fűzése.

6. **Élő Arduino kapcsolat**
   - Soros port kiválasztása és fizikai LED-léptetés.
   - A kijelölt LED automatikus felvillantása.
   - Animáció streamelése közvetlen hardveres teszthez.

7. **Projektkezelés és szerkesztési eszközök**
   - New, Open, Save As, automatikus mentés és helyreállítás.
   - Több alakzat kijelölése, igazítása, elosztása és csoportosítása.
   - Layer átnevezés, zárolás és opacity.

8. **Architektúra és Raspberry Pi teljesítmény**
   - A canvas, timeline, inspector, LED-map és firmware-import külön modulokra bontása.
   - Skálázott artworkök és fényrétegek cache-elése.
   - Windows- és Raspberry Pi-specifikus CI/smoke ellenőrzések.
