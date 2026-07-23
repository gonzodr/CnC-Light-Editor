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
- Layer-zárolás és teljes layer-opacity; a lock védi a shape-eket és keyframe-eket, az opacity pedig a layer teljes baked fényerejét skálázza.
- Pozíció-, scale-, több teljes fordulatot megőrző forgatás-, szín-, opacity-, stroke- és visibility-keyframe.
- Linear, Ease In, Ease Out és Ease In/Out interpoláció, kijelölhető keyframe-ek és idővonalas lejátszás.
- Fill/stroke alakzatmód, állítható körvonalvastagság, keyframe-elhető peremlágyítás (Feather) és pozitív/negatív Mask Expansion.
- Solid, tetszőleges számú húzható színstoppal szerkeszthető Linear és Radial gradient fill vagy gradient stroke; a Radial lehet sugárirányú vagy forgásirányú, a Linear iránya és az Angular kezdőfázisa 0–360° között állítható.
- Layer-szintű, shape-független Random LED / Sparkle effekt determinisztikus seeddel, life, born speed, maximális aktív elemszám, szín és interpolálható opacity paraméterekkel. Az enabled állapot és az opacity keyframe-elhető.
- Edit nézet: a DXF-ből képzett fehér vonalas guide jelenik meg sötét háttéren.
- Stencil mód: nagy, additívan keveredő fényforrások világítanak az alfa-lyukas playfield artwork mögött.
- Kalibrációs nézet húzható, hozzáadható és törölhető LED-helyekkel, beírható firmware-ID-vel, LED-nevekkel és ütközésmentes indexcserével.
- V4 baked-RGB `effect_data.h` import, effektválasztás és loop/outro-hű idővonalas lejátszás; a régi `EffectID...` maszkfájlok olvasása kompatibilitási módként megmaradt.
- A `NULL` nevű LED-slotok előnézetben kikapcsolva maradnak; `OVERLAY` exportban alapból átlátszó sentinelt kapnak, nem fekete pixelt.
- Az export validálja a `0..67` firmware-sorrendet és frame-enként pontosan 68 × RGB, azaz 204 bájtot generál.
- JSON projektmentés és visszatöltés, valamint mentetlen munkára figyelmeztető `New` projektindítás.
- Forgó, három példányos automatikus recovery-mentés és induláskor megjelenő Recover/Discard ablak; a kézi projektmentés után a recovery-pillanatképek automatikusan törlődnek.
- A végleges V4 `EffectDef` protokollal kompatibilis Arduino `PROGMEM` export explicit ID, `frameMs`, `loops`, `loopFrames` és `overlay` metaadatokkal. Az azonos képkockák is megmaradnak, mert a motor fix frame-idővel játszik.
- Külön Effect Bank / Export ablak a meglévő `effect_data.h` feltérképezéséhez, több effekt együttes újraexportálásához és a fix 150 KiB flash-keret vizuális tervezéséhez. Az editor által exportált effektek a szerkeszthető projektjüket is hordozzák tömörített kommentként, és közvetlenül visszatölthetők a bankból.
- Egységes hover/active/destructive gombállapotok, kontextusérzékeny súgóbuborékok, tagolt Inspector-fejlécek és GUIDE/STENCIL viewport-jelvény a gyorsabb vizuális tájékozódáshoz.
- Az Effect Bank listája ID, név, méret vagy időtartam szerint rendezhető, és élő kereséssel szűrhető (ID/név/méret/időtartam token-egyezés). Ha az aktuális projekt firmware-ID-je már szerepel a bankban, az exportálás a régi bejegyzést cseréli le rá ahelyett, hogy duplikált ID-val hibázna.
- Minden effekthez apró, gyorsítótárazott előnézeti kép a Bank Content listában - a legfényesebb (nem-átlátszó) kockát mutatja, a LED-ek valós playfield-pozícióján.
- Exportkor egyetlen gördülő biztonsági mentés készül az előző `effect_data.h`-ról (`effect_data.h.bak`); a szerkesztő fejlécében megjelenő `Restore backup` gomb visszatölti azt a bankba (lemezre csak újbóli exportkor kerül).
- A LED-map szerkesztésének (mozgatás, hozzáadás, törlés, ID/név csere) saját undo/redo előzménye van: amíg a LED map nézet nyitva van, a `Ctrl+Z`/`Ctrl+Shift+Z` ezt, nem a projekt-előzményt kezeli.
- Elkapatlan kivétel esetén a főciklus egy korlátozott méretű naplóba (`projects/crash.log`, legfeljebb 20 bejegyzés, régiek automatikusan lekerülnek) írja a teljes traceback-et, és megpróbál egy utolsó recovery-mentést készíteni, mielőtt a program leáll.
- A Hotkeys/Help ablak lábléce mutatja a pontos futó verziót és Git commit-hash-t (`vX.Y.Z · abcdef1`), hogy egyértelmű legyen, melyik build fut éppen.
- A loop-határ közvetlenül húzható a timeline vonalzóján: a hurokban lévő szakasz és az egyszer lefutó outro eltérő színárnyalattal jelenik meg, a határon lévő fogantyú pedig fogd-és-vidd módon állítja a `loopFrames` értéket - nincs többé rejtett "görgesd a lejátszófejet, majd kattints Set end-re" lépés. A hurokismétlések száma (`loops`) továbbra is az Inspector Loop −/Loop + gombjaival állítható.
- **Intro szakasz**: a loop-fogantyú mellett egy MÁSODIK húzható fogantyú jelöli, hol ÉR VÉGET az egyszer lefutó intro és hol KEZDŐDIK a hurok - így egy effekt felépülhet: egyszeri intro (pl. nagy villanás) → hurok (pl. pásztázó fény) → egyszeri outro (pl. elhalványuló strobe). Alapból intro=0 (a hurok azonnal a 0. kockánál kezdődik, a régi viselkedés). Az intro fogantyú nem húzható a hurok-vég fogantyú mögé. Ez firmware-oldali protokollváltozás is: az `EffectDef` struct egy új `introFrames` mezőt kapott (a struct végén, így a régi, ennél korábban exportált fejlécek is változatlanul lefordulnak, introFrames=0-val) - **a real gépen futó firmware-t is frissíteni kell** (`d_light_effects.ino` `bakedCurrentFrame()` függvénye), különben az újonnan exportált `effect_data.h` intro-t tartalmazó effektjei csak a hurkot fogják lejátszani, az intro-t figyelmen kívül hagyva.
- Négy új, a Random LED-hez hasonlóan shape-független layer-effekt, mind az "FX" gombra kattintva nyíló menüből választható: **Strobe/Flash** (frekvencia + duty cycle szerint villogtatja az összes firmware LED-et; a `Flash` kapcsoló Color/Blackout módra állítható - Blackout esetén a strobe nem egy külön színt villant, hanem FEKETÉRE vált, így megszakítja/kivágja a réteg többi, már animált fényét), **Color Cycle/Rainbow** (forgó szivárvány-árnyalat, állítható sebesség, terjedés, telítettség, fényerő és irány), **Pulse/Breathe** (szinuszos fényerő-lélegzés állítható periódussal és mélységgel) és **Comet/Chase** (elhalványuló fényfoltnak futó fény a firmware LED-sorrend mentén, állítható sebesség, uszályhossz és irány). Mindegyiknek saját enabled/opacity keyframe-je van, és egy layeren egyszerre több effekt-fajta is lehet.
- **Wiggle**: bármely alakzat pozíciója és elforgatása procedurálisan, determinisztikusan remeghet (seedelt, több szinuszhullámból összeadott mozgás - nem véletlenszerű zaj, hanem sima, szerves remegés). A Transform panel üresen maradt rácshelyén kapott helyet, tehát kikapcsolt állapotban egyáltalán nem foglal extra helyet; bekapcsolva az amplitúdó (`Wiggle amount`) és a sebesség (`Wiggle speed`) is húzható/beírható. Az enabled állapot ugyanúgy keyframe-elhető, mint a láthatóság.
- **Noise kitöltés**: a Gradient szerkesztőben a Solid/Linear/Radial mellett negyedik módként választható a **Noise** - minden firmware LED függetlenül, véletlenszerűen ugrál a kiválasztott color stopok között képkockánként, TV-statikus hatást adva. Ugyanazokat a gradient-stopokat használja, mint a Linear/Radial.
- Az Inspector oldalsáv - ha egy alakzat sok effektet/beállítást használ és a tartalom nem férne ki - a timeline-hoz hasonlóan görgethető: egérgörgővel a panel fölött, jobb szélén vékony csúszkajelzővel. A fejléc (LED-szám, firmware-adatok) és a lábléc (állapotsor) mindig rögzített marad, csak a köztes tartalom görgethető.

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

Az alapértelmezett munkaterület natív `1280×1024`, ezért közvetlenül illeszkedik a flipper Raspberry Pi kijelzőjéhez. Teljes képernyős indítás:

```bash
cnc-light-editor --resolution 1280x1024 --fullscreen
```

Az editor desktop környezet nélkül is futtatható közvetlenül a Pi kijelzőjén. Ott a projekt- és firmware-fájlok megnyitását/mentését saját Pygame fájlböngésző kezeli, ezért nincs `tkinter`, X11 vagy Wayland fájlablak-függőség. A hang alrendszert az editor sosem használja, ezért `pygame.init()` mindig a néma SDL audio-drivert kényszeríti ki - Pi-n ALSA nélkül is elkerüli a hotplug-szál CPU-terhelését. KMS/DRM konzolos indításnál az SDL videodriver a Pi beállításától függően például így választható ki:

```bash
SDL_VIDEODRIVER=kmsdrm cnc-light-editor --resolution 1280x1024 --fullscreen
```

A beépített fájlböngészőben a Home, Project, Projects, Exports és Root gyorshelyekről lehet indulni; az útvonal és mentési fájlnév kézzel vagy `Ctrl+V`-vel is megadható. Az overwrite és a nem mentett projekt megerősítése szintén az SDL/Pygame felületen történik.

Windowson, valódi (nem headless) munkamenetben ugyanezek a "Map header…"/"Export bank…"/projekt-megnyitás gombok a natív Windows Intéző-ablakot nyitják meg a Pygame-böngésző helyett - a fájlválasztás így az OS-hez szokott billentyűparancsokkal/kedvencekkel megy. A `Map firmware effect_data.h` útvonala munkamenetek között is megmarad: ha egyszer bemappelsz egy `effect_data.h`-t, a szerkesztő legközelebbi indításkor automatikusan újra betölti ugyanazt a fájlt.

### Automatikus frissítés

Git clone-ból, a tiszta `main` ágon futtatva az editor induláskor háttérben ellenőrzi az `origin/main` ágat. Új commit esetén kizárólag fast-forward frissítést hajt végre, újratelepíti a Python csomagot az aktív interpreterbe, recovery snapshotot készít a nem mentett projektről, majd ugyanabban az SDL/KMS környezetben újraindul. Fejlesztői branch, eltérő history vagy módosított követett fájl esetén nem ír felül semmit, hanem szünetelteti az automatikus frissítést. Sikertelen telepítéskor visszaáll az előző commitra.

Az automatikus ellenőrzés szükség esetén kikapcsolható:

```bash
cnc-light-editor --no-auto-update
# vagy
CNC_LIGHT_EDITOR_AUTO_UPDATE=0 cnc-light-editor
```

### Raspberry Pi 3 teljesítmény

A Pygame 2D Surface-skálázása, alfa-keverése, shape- és glow-rajzolása jellemzően CPU- és memóriasávszélesség-terhelés; a GPU közvetlenül nem rendereli ezeket a műveleteket. Az editor ezért csak a viewportban ténylegesen látható playfield/guide területet skálázza, korlátozza a render-cache méretét, és önálló pixelbufferből skálázza a cropokat. Raspberry Pi-n teljesen elkerüli az ARM-on instabil natív `smoothscale` útvonalat, és a biztonságos `scale` műveletet használja. Emellett újrahasznosítja a stencil glow vásznat és a kvantált glow sprite-okat. Raspberry Pi 3-on a UI/preview automatikusan 20 FPS-re áll, ami nem módosítja az Arduino-export `frameMs`/FPS értékét. A felső FPS-kijelző a mért és a beállított értéket mutatja.

Álló lejátszásnál az editor eseményvezérelt dirty renderinget használ: csak felhasználói interakció, updater-állapot vagy autosave után rajzol új képkockát. Lejátszás közben továbbra is folyamatosan, a beállított preview FPS-sel frissít.

Kézi preview limit:

```bash
cnc-light-editor --fps 24 --fullscreen
# vagy
CNC_LIGHT_EDITOR_FPS=24 cnc-light-editor --fullscreen
```

Más kijelzőméret a `--resolution WIDTHxHEIGHT` kapcsolóval vagy tartósan a `CNC_LIGHT_EDITOR_RESOLUTION` környezeti változóval állítható. Ablakos módban a felület átméretezhető; a viewport, timeline és Inspector automatikusan újratördelődik, 1280×1024-en pedig a magasabb Inspector több layer-sort jelenít meg.

## Kezelés

- Új alakzat: húzd a bal oldali Circle/Rect/Tri/Line eszközt a playfieldre.
- Mozgatás: fogd meg közvetlenül a kijelölt alakzatot.
- Méretezés: húzd a négy sarokfogópont egyikét.
- Forgatás: húzd a kijelölés fölötti kör alakú fogópontot.
- Zoom: egérgörgő a viewport felett; pásztázás: középső egérgomb vagy `Space` + húzás.
- Inspector: az egységes numerikus property-mezőket vízszintesen húzva finoman állíthatod, rövid kattintás után pedig közvetlenül beírhatod az értéket. Ez az X/Y/W/H/ROT/TURNS/OPACITY/FEATHER/EXPAND/STROKE mezőkre és a Random LED összes paraméterére ugyanúgy érvényes. A `ROT` a körön belüli szög, a `TURNS ×N` a teljes fordulatok száma; például a `ROT` mezőbe írt `720` automatikusan `0.0° ×2` lesz, és két teljes fordulatként interpolálódik. A számbevitel támogatja a `Ctrl+A`, `Ctrl+V`, Enter és Esc műveleteket.
- Layer-sorrend: húzd a layer sorát fel vagy le. Átnevezéshez nyomd meg a layer sorának `R` gombját, az `F2`-t, vagy kattints duplán a layer nevére az Inspectorban vagy a timeline-on; a névmező támogatja a `Ctrl+A` és `Ctrl+V` műveleteket. A timeline layernevére kattintva valóban kijelölöd a layert; ilyenkor a `Delete`/`Backspace` a teljes kijelölt layert törli. Az Inspector `D` gombja vagy a `Ctrl+D` duplikálja, a `−` gomb pedig szintén törli az aktív layert.
- Layer lock/opacity: az Inspector és a timeline layer-sorának `L` gombja zárolja a layert. A zárolt layer továbbra is látható és exportálódik, de a shape-jei, effektjei és keyframe-jei nem módosíthatók vagy törölhetők. Az aktív layer alatt lévő `Layer opacity` mező vízszintesen húzható vagy százalékosan beírható; a teljes layer shape- és generatív effektkimenetére hat.
- Keyframe-időzítés: kattintással jelöld ki, majd húzd az idővonal gyémántját; a snapping frame-határra igazít.
- Lejátszás: a `Space`, a felső Play/Stop gomb és a timeline fejlécének középső `▶`/`■` gombja ugyanazt a playback állapotot vezérli. A mellette lévő `|<` és `>|` gomb pontosan egy, az effekt saját `frameMs` értéke szerinti képkockát léptet hátra vagy előre, és automatikusan megállítja a lejátszást.
- Az inaktív layerek és nem kijelölt objektumok keyframe-jei is láthatók a saját sorukban visszafogott kékesszürke gyémántként; az aktív cél keyframe-jei maradnak kiemelve és szerkeszthetők.
- A timeline layer-sorai előtt külön `ON/OFF` kapcsoló vezérli a layer láthatóságát. A `>` gomb lenyitja a layert az animált property-k külön dope sheet sávjaira. Ha a sorok nem férnek ki, a layernév-oszlop fölött görgetve függőlegesen lapozhatók; a jobb oldali időterület fölötti görgő továbbra is timeline-zoom.
- Graph Editor: jelölj ki egy numerikus property-sávot vagy keyframe-et, majd nyomd meg a timeline `GRAPH` gombját. A görbe ugyanazt az interpolációt mutatja, amely az exportba kerül; a pontokat vízszintesen az idő, függőlegesen az érték módosításához húzhatod. Jobb kattintással válaszd a `Custom Bezier` módot a két lila fogantyú megjelenítéséhez; a függőleges fogantyúk túllövéses görbét is engednek. A `DOPE` gombbal válthatsz vissza.
- Több keyframe kijelölése: jobb egérgombbal húzz kijelölőkeretet a timeline-on, akár több property-sávon és shape-en keresztül; `Ctrl` mellett a találatok hozzáadódnak a meglévő kijelöléshez. `Ctrl` + jobb kattintással egyenként is hozzáadhatsz vagy kivehetsz keyframe-eket.
- Keyframe-offset: fogd meg bármelyik kijelölt gyémántot; a teljes kijelölés shape-ek és property-k között is együtt mozog, az egymás közötti időeltolás megtartásával.
- Keyframe másolás: `Ctrl+C` eltárolja az összes kijelölt keyframe-et, a `Ctrl+V` pedig úgy illeszti be őket, hogy a legkorábbi másolat az aktuális playheadhez kerüljön. A shape/property cél, az egymás közötti időeltolás, az easing és a Custom Bezier adatok is megmaradnak; szükség esetén a timeline automatikusan meghosszabbodik legfeljebb 15 másodpercig.
- Keyframe easing/törlés: jobb kattintás a gyémántra.
- Ha egy property első keyframe-je nem a timeline elején áll, az előtte lévő teljes üres szakasz automatikusan ennek az első keyframe-nek az értékét tartja. Ez minden animálható shape-, Random LED- és Canvas-tulajdonságra érvényes. Ha a legelső keyframe-et törlöd, az első megmaradó keyframe tölti ki visszafelé a timeline elejéig tartó szakaszt.
- Timeline zoom: egérgörgő; vízszintes görgetés: `Shift` + egérgörgő. Frame-skálán a playhead és a húzott keyframe-ek mindig pontos frame-határra illeszkednek.
- Timeline-magasság: húzd a timeline felső peremének közepén látható dupla fogantyút. A nagyobb panel több layer/property-sort mutat, a kisebb több helyet hagy a playfieldnek. Dupla kattintás a fogantyún visszaállítja az alapméretet; az editor a beállítást következő indításra is megjegyzi.
- Nagy timeline-zoomnál az időskála automatikusan másodpercről `F0`, `F1`… frame-számozásra vált. Importált firmware-effektnél az effekt saját `frameMs` értékét használja.
- Az Inspector (jobb oldali panel) a timeline-hoz hasonlóan görgethető, ha egy alakzat annyi beállítást/effektet halmoz fel (pl. Stroke + Wiggle egyszerre), hogy nem férne ki a panelban: egérgörgő a panel fölött görget, jobb szélén vékony csúszkajelző mutatja a pozíciót. A fejléc és az állapotsor rögzített marad.
- Animáció hossza: a felső Length mezőbe beírható, vagy a mellette lévő csúszkával állítható 0,5–15 másodperc között.
- Fill/stroke mód és stroke-vastagság: a Transform inspector Style részében. A stroke mező vízszintesen húzható, kattintás után pedig kézzel is beírható `0,1–5,0` között.
- Feather és Mask Expansion: a Transform mezőkben százalékosan húzhatók vagy beírhatók. A Feather `0–10%` között lágyítja a maszk mindkét szélét, az Expansion `−10–+10%` között összehúzza vagy kitágítja. Mindkettő keyframe-elhető, és a lágyított LED-fényerő kerül a firmware-exportba is.
- Gradient: Fill módban a `Gradient fill…`, Stroke módban a `Gradient stroke…` panelt nyisd meg. Kattints a colorbarra új stophoz, húzd a stopokat, majd adj színt a kijelölt stopnak. A Solid/Linear/Radial/**Noise** mód, a Radial `Radius/Angular` iránya, valamint a Linear angle és az Angular phase ugyanitt állítható; Stroke módban ugyanez a színmező csak a körvonal maszkját festi. Noise módban a Radial irány és az Angle/Phase csúszka nem elérhető, mivel a Noise minden LED-et függetlenül, véletlenszerűen vált a stopok színei között - nincs térbeli iránya.
- Wiggle: a Transform panel jobb alsó, egyébként üresen maradó rácshelyén található `Wiggle: ON/OFF` gombbal kapcsolható be alakzatonként. Bekapcsolva megjelenik a `Wiggle amount` (remegés amplitúdója, 0–20%) és a `Wiggle speed` (0,1–20 Hz) mező, mindkettő húzható/beírható. A remegés a meglévő keyframe-elt pozícióra/forgatásra rakódik rá, seedelt, tehát determinisztikus (ugyanaz a projekt mindig ugyanúgy remeg).
- Firmware V4: az Inspectorban állítható az explicit effekt-ID, a 20/25/≈30,3 FPS preset (`50/40/33 ms`) és a loopok száma (`Loop −`/`Loop +`); a `Full (no outro)` gomb törli a külön outro-szakaszt. Maga a loop-vég és az intro-vég is a timeline vonalzóján lévő két húzható fogantyúval állítható - lásd fentebb. A stats sor `intro▸loopvég/összes` alakban mutatja mindkettőt (pl. `10▸50/80`). A `FULL/CANVAS` exportkapcsoló adja az `overlay` flaget; a panel élő flash- és lejátszási időbecslést mutat. A Layers fejléc `C+` gombja egyetlen, speciális Canvas réteget ad a projekthez. A Canvas kijelölésekor az Inspector `Enable key` és `Disable key` gombjai az aktuális playheadnél explicit állapot-keyframe-et írnak: Enable esetén az érintetlen LED-ek átlátszóak, Disable esetén fekete blackout értéket kapnak. A timeline ON/OFF gombja és a `V` gyorsbillentyű a két állapot között vált.
- Layer-effektek: az aktív layer `FX` gombja egy választómenüt nyit: Random LED / Sparkle, Strobe / Flash, Color Cycle / Rainbow, Pulse / Breathe, Comet / Chase. Egyik sem használ shape-maszkot: mind közvetlenül a firmware LED-slotokat vezérli. Minden numerikus mező vízszintesen húzható, rövid kattintás után pedig közvetlenül beírható; a számbevitel támogatja a `Ctrl+A` és `Ctrl+V` műveleteket. A Toggle az aktuális időnél állapotot vált, a `State key` megtartja az aktuális enabled állapotot. Az opacity mező `+K` gombja explicit opacity-keyframe-et ír, míg az opacity későbbi playheadnél történő húzása vagy beírása automatikusan keyframe-et készít. A Color Cycle és a Comet effektnek Direction (Forward/Reverse) kapcsolója is van.
- Undo/redo: `Ctrl+Z`, `Ctrl+Shift+Z` vagy `Ctrl+Y`; aktív layer duplikálása: `Ctrl+D`.
- Hotkey súgó: a felső `?` gomb vagy `F1`; bezárás: `Esc`, `F1` vagy az ablak `×` gombja.
- Minden tulajdonság keyframe-je: `K`; láthatóság: `V`; lejátszás: `Space` - a teljes szekvenciát (0-tól a végéig) hurkolja végtelenítve. A Play gomb mellett a `|▶|` ikonú **Play Loop** gomb (vagy `Shift+Space`) csak a kijelölt loop-szakaszt (az intro- és loop-vég fogantyú közötti részt) hurkolja - így pontosan azt látod előnézetben, amit a firmware a `loops` alkalommal ismételt középső részként lejátszana. Lejátszás közben átválthatsz Space/Shift+Space között anélkül, hogy a lejátszófej visszaugrana - csak az, hogy MIT hurkol, változik.
- Snap ki/be: `G`. Bekapcsolva az alakzat pozícióját és méretét 0,01-es normalizált rácsra, a forgatást 15°-ra, a keyframe idejét pedig FPS-képkockahatárra igazítja; kikapcsolva minden folyamatosan mozgatható.
- Új projekt: felső `New` gomb vagy `Ctrl+N`. Mentetlen módosítás esetén az editor megerősítést kér, majd tiszta, mentetlen projektet nyit.
- Projektmentés/betöltés: `Ctrl+S`, `Ctrl+O`; Save As: `Ctrl+Shift+S`. Az első mentés fájlnevet kér, a további mentések ugyanazt a `.cnclight` fájlt frissítik.
- A jobb oldali színminták (16 beépített + saját egyéni színek) az aktuális playheadnél hoznak létre szín-keyframe-et. Többszörös keyframe-kijelölésnél a kiválasztott szín minden kijelölt időpontra egyszerre kerül rá. A `+` gomb egy Color Mixer ablakot nyit (R/G/B csúszkák, élő előnézet); az `Apply & save to custom colors` a projektek között megmaradó saját palettához adja a színt (legfeljebb 7, a legrégebbi automatikusan lekerül).

A toolbar `Export` gombja az Effect Bank ablakot nyitja meg. A `Map header…` beolvassa egy meglévő V4 `effect_data.h` összes effektjét; a felső szegmentált memóriasáv és az effektenkénti színes hosszcsíkok a tényleges `frames × 204` flash-foglalást mutatják. A panel kijelzi a 150 KiB keretből felhasznált és szabad helyet, valamint az aktuális projekt FPS-ével becsült hátralévő animációs időt. Egy blokkra kattintva a név és az explicit firmware-ID szerkeszthető, a mappelt effekt eltávolítható az exportbankból. A `BANK+PROJECT` jelölésű effektnél a `Load editable project` visszaállítja a teljes layereket, shape-eket és keyframe-eket; a bankban átírt név és ID az így betöltött projektbe is átkerül. Az aktuális projekt mindig külön `CURRENT` blokként szerepel, és a saját exportnevét/ID-ját ugyanitt lehet beállítani. Az `Export bank…` egy közös, firmware-kész V4 headert ír; duplikált ID, hibás LED-map vagy 150 KiB fölötti adat esetén blokkolja az exportot.

Az exportált szerkesztőprojekt verziózott, zlibbel tömörített Base64 adatként, kizárólag `//` kommentekben kerül az adott RGB-tömb mellé. Az Arduino fordító figyelmen kívül hagyja, ezért nem fogyaszt a 150 KiB effektbankból és nem kerül a mikrokontroller flashébe. Régebbi vagy más eszközzel készült header továbbra is betölthető, de annál az Effect Bank csak a baked képkockákat tudja lejátszani; szerkeszthető projekt nélkül nem jelenik meg a visszatöltő gomb.

A projektfájl helyét az első Save alkalmával lehet kiválasztani; a címsorban és a Save gombon látható `*` mentetlen módosítást jelez.

Mentetlen módosítás közben az editor 30 másodpercenként forgó recovery-pillanatképet készít a `projects/.autosave` mappába. Váratlan leállás után a következő induláskor külön ablakban választható a legutóbbi érvényes állapot visszaállítása vagy elvetése. A recovery nem helyettesíti a névvel ellátott `.cnclight` projektmentést.

A `Ctrl+I` gyorsbillentyűvel vagy az Effect Bank `Map header…` gombjával válaszd ki a firmware `effect_data.h` fájlját. Az editor automatikusan stencil nézetre vált; a bal oldali `Next FX` végiglépteti a `bakedEffects[]` leírókat, a `Project` pedig visszatér a szerkesztett animációhoz. Parancssorból: `cnc-light-editor --effect-data "F:\...\effect_data.h"`.

Az editor minden opacityt, easinget, gradientet és generatív effektet előre belesüt a frame-ek RGB-értékeibe. A V4 motor kizárólag ezeket a kész képkockákat játssza le. A loop első `loopFrames` képkockája `loops` alkalommal ismétlődik, a maradék outro egyszer fut le; az ID explicit és nem függ a táblasorrendtől. `FULL` módban minden LED-re kész RGB kerül. `CANVAS/OVERLAY` módban a formák által nem festett cella `(255,0,255)` sentinel, amit a firmware átugrik; a `(0,0,0)` továbbra is valódi, fedő fekete. Ha egy festett szín pontosan `(255,0,255)` lenne, az export `(254,0,255)` értékre módosítja, így nem ütközik a transzparens sentinel értékével. A `NULL` LED-map helyek `OVERLAY` módban ugyanezt az átlátszó sentinelt kapják. `FULL` módban feketének kell maradniuk, mert a jelenlegi firmware ebben a módban minden RGB-cellát ténylegesen kiír, tehát nincs átlátszóság-fogalma.

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
