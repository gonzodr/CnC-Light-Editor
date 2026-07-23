# CnC Light Editor roadmap

## Következő fejlesztések

1. **Valódi térbeli Particle effekt**
   - [x] Shape-független, közvetlen Random LED / Sparkle generátor seeddel és keyframe-elhető enabled állapottal.
   - [x] Négy további shape-független generátor-effekt, közös architektúrán (`KeyframeMixin`, `GENERATOR_KINDS` registry): Strobe/Flash, Color Cycle/Rainbow, Pulse/Breathe, Comet/Chase - mind saját FX-menüpontból nyitható, saját enabled/opacity keyframe-jével.
   - [x] Strobe Blackout mód: kapcsolóval választható, hogy a strobe egy saját színt villantson, vagy feketével vágja ki/szakítsa meg a réteg alatta futó animációt.
   - [x] Wiggle: alakzat-szintű (nem LED-szintű) procedurális remegés pozícióra és forgatásra, seedelt/determinisztikus, meglévő keyframe-ekre rárakódva. Amplitúdó és sebesség állítható; a Transform panel egyébként üres rácshelyén kap helyet, hogy ne foglaljon extra magasságot kikapcsolt állapotban.
   - [x] Noise kitöltési mód a Gradient szerkesztőben (Solid/Linear/Radial mellett negyedikként): minden LED függetlenül, véletlenszerűen vált a color stopok között képkockánként - villódzó TV-statikus hatás, nem térbeli gradiens.
   - Térben mozgó részecskék emitterrel, sebességgel, iránnyal, szórással és gravitációval.
   - A LED csak akkor kapjon fényt, ha a részecske ténylegesen metszi a LED-ablakot.
   - Particle pozíciók megjelenítése az edit nézetben és determinisztikus baked export.
   - [ ] Automatikus loopolható-szakasz-felismerés a timeline loop-fogantyújához (a húzható sáv már kész, l. lentebb az 5. pontnál).

2. **LED-fényprofilok**
   - LED-enként állítható fényfoltméret, intenzitás és forma.
   - Kör, ellipszis vagy egyedi fényablakmaszk.
   - Numerikusan szerkeszthető pozíció és sugár.

3. **LED-map interakció javítása**
   - [x] Az első kattintás csak kijelöli a LED-et, nem változtatja meg a pozícióját.
   - [x] A már kijelölt LED-re történő második kattintás indítja a mozgatási módot.
   - [x] Újabb kattintás rögzíti a pozíciót; az `Esc` visszaállítja az eredeti helyet.
   - [x] LED-map módosítások saját undo/redo előzménnyel (`Ctrl+Z`/`Ctrl+Shift+Z` a kalibráció módban a projekt-előzmény helyett a LED-map saját stackjét használja).

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
   - [x] A loop-határ közvetlenül húzható sáv a timeline vonalzóján (a korábbi "scrub majd Set end" helyett) - a hurok- és outro-szakasz eltérő színárnyalattal jelenik meg. Automatikus loopolható-szakasz-felismerés még nincs.
   - [x] Intro szakasz: MÁSODIK húzható fogantyú jelöli a hurok KEZDETÉT (az addig tartó rész egyszer fut le, mielőtt a hurok elindulna) - intro→hurok→outro háromszakaszos felépítés. Firmware protokollváltozás: `EffectDef` új `introFrames` mezője (struct végén, visszafelé kompatibilis - régi fejlécek introFrames=0-val fordulnak). Firmware oldal (`F:\Projects\cheech and chong\firmware\CnC_firmware4\d_light_effects.ino`, `bakedCurrentFrame()`) frissítve, de a bench/gépi flashelés/harness-ellenőrzés a felhasználó feladata (a harness explicit NEM fedi a LED-rutinokat). A firmware C++ logika és az editor Python logikája 860 000+ mintán kereszt-ellenőrizve, 0 eltéréssel.

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
   - [x] Skálázott artworkök, guide és stencil glow erőforrások cache-elése.
   - [x] Desktop-független, Pygame-es fájlböngésző és megerősítő dialógus headless Pi futtatáshoz.
   - [x] Biztonságos `origin/main` auto-update, csomagtelepítés, recovery és újraindítás.
   - [x] Automatikus Pi 3 preview FPS-limit és élő FPS-kijelzés.
   - [x] Egyetlen kivétel sem tűnhet el nyomtalanul: a főciklus köré kerülő hibakezelő egy korlátozott méretű (`projects/crash.log`, legfeljebb 20 bejegyzés) naplóba írja a teljes traceback-et, és megpróbál menteni egy recovery-pillanatképet, mielőtt a program leáll.
   - [x] Az Inspector oldalsáv görgethető (a timeline mintájára), ha egy alakzat annyi effektet/beállítást halmoz fel, hogy nem férne ki a fix panelmagasságban.
   - Windows- és Raspberry Pi-specifikus CI/smoke ellenőrzések.

9. **Professional Effect Bank workflow**
   - [x] Azonos firmware-ID esetén Replace, ne vak duplikáció (`resolve_effects`, exportkor a bank egyező ID-jű bejegyzését cseréli, nem hibázik duplikációval).
   - [x] Statikus thumbnail minden effekthez (a legfényesebb, nem-átlátszó kockát mutatja, a LED-ek valós playfield-pozícióján, gyorsítótárazva). Animált előnézet még nincs - a folyamatos újrarajzolás rontaná a Pi-s idle CPU-t.
   - [x] Rendezés ID, név, flash-méret vagy lejátszási hossz szerint (`Sort:` gomb a Bank Content lista fölött).
   - [x] Keresés (élő, ID/név/méret/időtartam token-egyezés) - a projekttel együtt menthető címkék még nincsenek.
   - [ ] Export előtti változáslista **dialógusként** (jelenleg a `describe_changes` eredménye az export utáni státuszsorban jelenik meg, nem egy megerősítő ablakban).
   - [x] Exportkor egyetlen gördülő biztonsági mentés (`effect_data.h.bak`) az előző verzióról, "Restore backup" gombbal visszatölthető (nem ír a lemezre, csak a bank-listát tölti vissza - az újraexportálás a felhasználó döntése).
   - [ ] Egyetlen effekt külön importja és exportja.
   - [ ] `Optimize bank` elemzés pazarló FPS-re, túl hosszú effektre és loopolható szakaszokra.
   - [x] Összesített flash-foglalás mellett a bank teljes becsült lejátszási ideje (`total_playback_ms`, a kapacitás-sorban).
