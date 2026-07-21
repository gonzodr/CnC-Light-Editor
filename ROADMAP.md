# CnC Light Editor roadmap

## Következő fejlesztések

1. **LED-fényprofilok**
   - LED-enként állítható fényfoltméret, intenzitás és forma.
   - Kör, ellipszis vagy egyedi fényablakmaszk.
   - Numerikusan szerkeszthető pozíció és sugár.

2. **LED-map interakció javítása**
   - [x] Az első kattintás csak kijelöli a LED-et, nem változtatja meg a pozícióját.
   - [x] A már kijelölt LED-re történő második kattintás indítja a mozgatási módot.
   - [x] Újabb kattintás rögzíti a pozíciót; az `Esc` visszaállítja az eredeti helyet.
   - [ ] LED-map módosítások saját undo/redo előzménnyel.

3. **Részletes property timeline és Graph Editor**
   - Külön Position, Scale, Rotation, Color, Opacity és Visibility sávok.
   - Dobozos kijelölés, keyframe másolás/beillesztés és tömeges mozgatás.
   - Húzható easing görbék, loop-tartomány és markerek.

4. **Firmware-hű előnézet és round-trip export**
   - `fadeToBlackBy()` utánfény szimulációja.
   - Programból generált EffectID4 és EffectID6 előnézete.
   - Importált maszk-/palettaeffekt módosítása és visszaexportálása.

5. **Élő Arduino kapcsolat**
   - Soros port kiválasztása és fizikai LED-léptetés.
   - A kijelölt LED automatikus felvillantása.
   - Animáció streamelése közvetlen hardveres teszthez.

6. **Projektkezelés és szerkesztési eszközök**
   - New, Open, Save As, automatikus mentés és helyreállítás.
   - Több alakzat kijelölése, igazítása, elosztása és csoportosítása.
   - Layer átnevezés, zárolás és opacity.

7. **Architektúra és Raspberry Pi teljesítmény**
   - A canvas, timeline, inspector, LED-map és firmware-import külön modulokra bontása.
   - Skálázott artworkök és fényrétegek cache-elése.
   - Windows- és Raspberry Pi-specifikus CI/smoke ellenőrzések.
