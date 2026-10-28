# Plan testowy: Hismith S2 + Buttplug (Linux) + MultiFunPlayer i głos (Windows)

## Cel
Po wgraniu nowych wersji (Buttplug `hismith-s2-depth`, MultiFunPlayer `buttplug-actuator-descriptions`) trzeba na prawdziwym sprzęcie sprawdzić to, czego nie dało się sprawdzić w chmurze. Plan idzie od najbezpieczniejszych testów (bez maszyny) do pełnych (film + funscript + głos). Zasady bezpieczeństwa na cały plan: **wyłącznik zasilania maszyny pod ręką, pierwsze próby na niskiej prędkości (Output range L1 prawy uchwyt ~30), każdy test zaczynaj od maszyny bez obciążenia**.

## Oznaczenia
Wynik przy każdym teście: ✅ działa / ❌ nie działa (zapisz co widać) / ⚠️ częściowo. Przy ❌ zbierz: log serwera (Linux), log MFP, okno `voice_control.py`.

## Faza 0 – Instalacja (bez maszyny)
| # | Krok | Oczekiwane |
|---|---|---|
| 0.1 | Linux: `git pull` na `hismith-s2-depth`, `cargo build --release -p intiface-engine` | build kończy się bez błędów |
| 0.2 | Linux: uruchom `intiface-engine --websocket-port 12345 --websocket-use-all-interfaces --use-bluetooth-le --log info`; firewall: port 12345/tcp | w logu brak błędów, nasłuch na 12345 |
| 0.3 | Windows: `git pull`, publish MFP (`dotnet.exe publish MultiFunPlayer.csproj -c Release -r win-x64 --self-contained false -o ...`) | build bez błędów |
| 0.4 | Windows: `tools\voice-control\install.bat` | pakiety zainstalowane, self-check pokazuje `dry-run [lube_fire ...]` |
| 0.5 | MFP → Buttplug.io: endpoint `<IP-linuxa>:12345`, Connect | status **Connected** |
| 0.6 | `python voice_control.py keys` | wypisuje listę klawiszy (Num 1–8, 9, 0, `.`, +, −, *, /, F8, Pause) |
| 0.7 | `python -m unittest discover -s tests` w `tools\voice-control` | `OK` (65 testów) |

## Faza 1 – Maszyna widoczna w MFP
| # | Krok | Oczekiwane |
|---|---|---|
| 1.1 | Włącz S2, poczekaj na skan | Linux log: `Hismith Device Identifier: 1101`. **Jeśli inny kod → zapisz i podaj mi.** |
| 1.2 | MFP, Device map → Connected devices | „Hismith Servo Sex Machine" |
| 1.3 | Lista **Indices** dla Oscillate / Position | 5 pozycji: Speed, Depth, Stroke width, Run mode, Preset mode |
| 1.4 | W Device map dodaj `L1 → Oscillate/0` i `L0 → Position/1` | dwa wiersze, przycisk **Add** widoczny także w wąskim oknie (~1100 px) |
| 1.5 | Wiersz **HISMITH S2** pod Endpoint | widać panele **MODES** i **BOOST**, każdy przycisk z podpisem klawisza |

## Faza 2 – Sterowanie ręczne (bez filmu, z maszyną; niska prędkość!)
| # | Krok | Oczekiwane |
|---|---|---|
| 2.1 | L1: ikona domku → Auto-home → Enabled, Target **0%**, Delay 0, Duration 0 | maszyna **stoi** po połączeniu (nie chodzi sama) |
| 2.2 | Auto-home L1 Target 20% | maszyna rusza powoli |
| 2.3 | Auto-home L0 Target 0% / 50% / 100% | okno skoku płytko / środek / głęboko (jak w Twoim teście) |
| 2.4 | Panel BOOST: Faster ×3, Slower ×3 | wyraźnie szybciej (każdy krok ~+20 pkt), potem wraca; status pokazuje `speed boost +N` |
| 2.5 | BOOST: Deeper / Shallower | okno przesuwa się o ~15 pkt; Reset zeruje |
| 2.6 | **Stop** (kwadrat w pasku wyjścia) | zapisz co robi maszyna (staje / jedzie dalej) – ważne do wiedzy o zatrzymywaniu |

## Faza 3 – Tryby wbudowane (ryzyka R1–R3)
| # | Krok | Oczekiwane |
|---|---|---|
| 3.1 | Kliknij **1**, potem 2 … 8 (po ~10 s każdy) | maszyna zmienia wzór ruchu; MODES podświetla/pokazuje aktywny tryb w statusie |
| 3.2 | **R1:** w trybie 3 kliknij Faster | ✅ tryb przyspiesza i zostaje, albo ❌ tryb się kończy (zapisz!) |
| 3.3 | **Script** (powrót) | maszyna wraca do wartości ze skryptu/Auto-home; szerokość skoku z powrotem ~40 |
| 3.4 | **A** (Num 9) | max prędkość, pełna głębokość, **najmniejszy skok**; **R2:** zmierz/oceń szerokość skoku (≈10 czy mniej?) |
| 3.5 | **B** (Num 0) | **R3:** najwolniej; ruszy w ogóle? Jeśli stoi → napisz, podniesiemy `HismithSlowestSpeed` (domyślnie 1) |
| 3.6 | Script po A/B | skok wraca do zwykłej szerokości, prędkość = skrypt (nie zostaje 100/1) |
| 3.7 | Sekwencja (podwójne kliknięcie / przełącznik) | 2 min na tryb, Hold zatrzymuje licznik, koniec wraca do skryptu |

## Faza 4 – Film + funscript (HereSphere + Stash)
| # | Krok | Oczekiwane |
|---|---|---|
| 4.1 | MFP: źródło mediów HereSphere → Connect (HereSphere uruchomiony) | status Connected, widać nazwę filmu |
| 4.2 | Stash jako repozytorium skryptów (adres, ApiKey) | MFP wczytuje `.funscript` do osi (lub przeciągnij plik na pole nazwy skryptu osi) |
| 4.3 | Odtwórz film | L0 i L1 w Axis values się ruszają; maszyna podąża za skryptem |
| 4.4 | Pauza / wznowienie w HereSphere | maszyna zatrzymuje / wznawia się zgodnie z filmem |
| 4.5 | **Boost podczas skryptu:** Num + / Num * | ruch **dalej idzie wg skryptu**, ale jest szybszy/głębszy (nie zastępuje go); Num . kasuje |
| 4.6 | Tryb 4 w środku filmu → **Script** | tryb zastępuje skrypt, po powrocie maszyna znów jest w miejscu filmu |
| 4.7 | Pętla (`Script::Loop`, klawisz / głos „zapętl to") | ostatnie ~5 s skryptu się powtarza, „koniec pętli" puszcza dalej |
| 4.8 | Output range / limit prędkości osi | przycinają wartości zgodnie z ustawieniem |

## Faza 5 – Klawisze globalne (HereSphere z fokusem!)
| # | Krok | Oczekiwane |
|---|---|---|
| 5.1 | `python voice_control.py run --dry-run`, NumLock on, kliknij okno HereSphere, naciśnij Num +, Num 3, Num 9, F8 | w oknie narzędzia pojawiają się `[key] faster` itd. (dry-run) |
| 5.2 | Jak wyżej, ale `run` na żywo (`run.bat live`), maszyna stoi na 20% | Num + zwiększa prędkość, Num 3 włącza tryb 3 |
| 5.3 | **Pause** | okno: `listening OFF`, głos ignorowany, klawisze nadal działają; ponownie Pause → `ON` |
| 5.4 | Czy HereSphere reaguje na te same klawisze (np. Num +)? | jeśli tak – zmień klawisze w `commands.json` (`keys`) |

## Faza 6 – Głos (mikrofon headsetu Pimax)
| # | Krok | Oczekiwane |
|---|---|---|
| 6.1 | `python voice_control.py mic`, mów i milcz 15 s | pasek poziomu rośnie przy mowie, próg stoi nad szumem; `speech detected` przy mowie |
| 6.2 | `run.bat` (dry run), powiedz: „kochanie, szybciej", „kochanie, głębiej", „kochanie, tryb trzy" | w oknie `dry-run [faster …]` itd. |
| 6.3 | Bez słowa: „szybciej" | `ignored (no wake word)` |
| 6.4 | Zdania nie-komendy ze słowem i bez: „kochanie, jak ci minął dzień", „dziękuję" | wszystkie `ignored` |
| 6.5 | **Film w głośnikach** z dialogami (maszyna nieaktywna, dry-run), 5 min | **0 komend**. Jeśli są → podnieś `listen.threshold_db` lub użyj słuchawek |
| 6.6 | Synonimy: prędzej, przyspiesz, mocniej, zwolnij, spokojniej, płycej, nie tak głęboko, na maksa, najwolniej, jedynka, tryb pierwszy | każdy trafia we właściwą intencję; zapisz, które **nie** działają |
| 6.7 | `run.bat live` + MFP **Remote commands ON**: „kochanie szybciej" przy 20% | maszyna wyraźnie szybciej |
| 6.8 | Tryby głosem: „kochanie tryb dwa" → „kochanie szybciej" → „kochanie wróć do filmu" | zachowanie jak w testach 3.1–3.3 |
| 6.9 | „kochanie na maksa", „kochanie najwolniej", „kochanie normalnie" | A / B / powrót + zdjęcie boostu |
| 6.10 | Pauza / odtwarzanie: „kochanie stop", „kochanie graj" (HereSphere połączony) | film się zatrzymuje / rusza |
| 6.11 | Lube Launcher (jeśli podłączony): „kochanie spuść się na mnie" → strzał; „… kilka razy" → 3 strzały co ~1 s; powtórzenie < 10 s → `cooldown` | zgodnie z opisem |
| 6.12 | Z maszyną chodzącą: czy hałas maszyny psuje rozpoznawanie? Powiedz 10 komend | zlicz trafione/nietrafione; > 2 pomyłek → bliżej ust mikrofon, zmień `margin_db` |
| 6.13 | Koszt/ruch: po 30 min zobacz zużycie na platform.openai.com | rozsądny; jeśli za duży → częściej Pause |
| 6.14 | `python voice_control.py eval` (z kluczem OpenAI) | **FALSE TRIGGERS = 0**, trafność ≥ 90 %; zapisz listę pomyłek. `eval --no-llm` daje bazę (ok. 70 %) |
| 6.15 | Przekręcone słowo: „trwanie, spuścić się", „rochanie, szybciej" (live, dry-run) | `dry-run [... llm 0.9x]` z właściwą komendą |
| 6.16 | Niejasne: „kochanie, mógłbyś przyspieszyć", „kochanie, idź głębiej", „kochanie, daj mi tryb szósty" | trafia w faster / deeper / mode_6; „zdecydowanie szybciej" = 2–3 kroki |
| 6.17 | Przeczenia i dialog: „kochanie, nie spuszczaj się", „a nie spuść się", „kochanie, kup mleko" | `ignored`, nic nie jest wysłane |
| 6.18 | Odłącz internet i powiedz „trwanie, spuścić się"; potem „kochanie szybciej" | pierwsze: `ignored (llm unavailable)`; drugie dalej działa (ścisła ścieżka offline) |
| 6.19 | Film z dialogami w głośnikach 5 min, `voice_log.jsonl` | w logu brak wpisów `via: llm` wykonanych jako komenda; nieliczne wywołania modelu kończą się `ignored` |
| 6.20 | „kochanie, dość" / „wystarczy" / klawisz **Home** / przycisk **Calm** (maszyna chodzi szybciej) | **nie staje**: najwolniejsza prędkość, średnia głębokość, status „CALM"; po „kochanie szybciej" przyspiesza dalej |
| 6.20b | „kochanie, koniec" / „wyłącz maszynę" / klawisz **End** / przycisk **Stop** | prędkość 0 od razu, status „STOPPED"; skrypt nie rusza maszyny, dopóki nie wybierzesz trybu lub **Script** |
| 6.20c | 'kochanie, zaskocz mnie' 12 razy (maszyna + Lube Launcher podłączone), klawisz **Insert**, przycisk **Surprise** | za każdym razem coś się dzieje: nowy tryb (nigdy ten sam dwa razy z rzędu) i/lub strzał (0 / 1 / 3, losowo, niezależnie od trybu); czasem sam tryb, czasem sam strzał, czasem oba; bez Lube Launchera tylko tryb |
| 6.20d | Po 'zaskocz mnie': 'kochanie, nieee, coś innego' / 'zmień' / klawisz **Del** / przycisk **Again** | stan z niespodzianki trwa dalej, w statusie MFP odlicza się 'next surprise in 2:00'; po 120 s nowe losowanie (tryb + strzały) wchodzi samo; strzały z poprzedniego zestawu, które jeszcze nie padły, są odwołane od razu |
| 6.20e | Ponowne 'coś innego' w trakcie odliczania; wybór trybu ręką / Script / Calm / Stop w trakcie; 'coś innego' bez wcześniejszej niespodzianki | odliczanie startuje od nowa; wybór trybu je kasuje (nic się nie losuje); bez niespodzianki nic się nie dzieje; czas zmienisz w polu obok przycisku Again |
| 6.20f | Hold: w trybie 3 powiedz 'kochanie, utrzymaj to', potem 'szybciej', 'tryb pięć', 'zaskocz mnie', 'dość', klawisze Num + i Num 5, przyciski MODES | wszystko ignorowane (narzędzie: `locked`, MFP: log 'Held for another N s'); status pokazuje 'HELD for 4:5x'; maszyna zostaje w trybie 3 |
| 6.20g | W trakcie holdu: 'spuść się na mnie', 'kilka razy', F8; potem 'kochanie, koniec' | lube strzela normalnie; 'koniec' zatrzymuje maszynę i zdejmuje hold (potem 'szybciej' znów działa) |
| 6.20h | Hold i czas: odczekaj 5 min (albo skróć pole Minutes obok przycisku Hold); w trakcie: 'odblokuj', 'dość', 'wróć do filmu', PgDn | po czasie sterowanie wraca samo; w trakcie NIC poza lube i 'koniec' nie działa (safe word); ponowne 'utrzymaj to' liczy 5 min od nowa |
| 6.20i | Film + funscript gra (bez trybu wbudowanego), powiedz 'kochanie, utrzymaj to' / 'rób tak dalej' / 'jeszcze' | ostatnie ~20 s skryptu zapętla się (status HELD); po 5 min albo 'odblokuj' pętla znika i film idzie dalej od bieżącego miejsca; w trybie wbudowanym samo wstrzymanie, bez pętli |
| 6.20j | Tryb A (np. po 'na maksa' albo losowaniu) i 'kochanie, nieee, coś innego' | losowanie następuje od razu (bez 120 s); w innych trybach nadal po 120 s |
| 6.21 | Sceny: „kochanie, pieprz mnie" / „rozepchaj mnie" / „drażnij mnie" / „użyj mnie" (dry-run, potem live) | odpowiednio: szybciej+głębiej / głębiej ×2 / wolniej+płycej / szybciej+głębiej; zmiana wyraźna, ale nie skokowa |
| 6.22 | Improwizacja (potrzebny klucz): „kochanie, jestem twoją małą dziwką, zrób ze mną co chcesz", „kochanie, błagam, weź mnie" | `executed [improvise llm 0.8x]`, w oknie i w `voice_log.jsonl` widać `plan`; kilka razy pod rząd plany się różnią |
| 6.23 | Granice improwizacji: czy kiedykolwiek zatrzyma / uspokoi maszynę, strzeli więcej niż raz na jedno zdanie albo zrobi więcej niż 3 kroki 'szybciej'? Czy przy 'zniszcz mnie i skończ we mnie' potrafi dać tryb A + jeden strzał? | nigdy stop/calm, max 1 strzał, max 3 'szybciej'; A/B i jeden strzał dozwolone; jeśli inaczej -> zgłoś |
| 6.24 | Pani (klucz, głośniki/słuchawki): 'kochanie, proszę, spuść się na mnie' i dalej wg jej żądań ('proszę, Pani', 'jestem Twoją dziwką', 'dziękuję') | słychać jej głos (stanowczy, PL), w oknie `denied/executed`, `p` i `roll`; zwykle 2-4 prośby do strzału; odpowiedzi bez 'kochanie' działają przez 60 s po jej słowach |
| 6.25 | Jej głos z głośników: po każdej jej linii obserwuj okno narzędzia | żadna jej linia nie jest rozpoznana jako Twoja komenda (mikrofon głuchy, gdy mówi); jeśli jest -> zwiększ `persona.echo_tail` |
| 6.26 | 'kochanie, koniec' gdy Pani mówi / w trakcie jej odmowy; klawisz End | maszyna staje natychmiast, hold zdjęty, Pani milknie na następną linię |
| 6.27 | Hold ('utrzymaj to') + prośby do Pani: 'szybciej' / 'spuść się na mnie' | 'szybciej': ona mówi, nic nie zmienia (`locked`); lube przechodzi przez jej kości |
| 6.30 | 'kochanie, dość' 5 razy z rzędu (maszyna chodzi), potem 'koniec' | 'dość' bywa odmówione (Pani: 'Nie. Jeszcze nie skończyłam.'), przy kolejnych prośbach zgoda (50 % pierwsza, rośnie); 'koniec' ZAWSZE stawia maszynę od razu |
| 6.31 | 'kochanie, pauza' / 'wróć do filmu' / 'tryb trzy' / 'wolniej' | każde przez Panią (słychać jej odpowiedź), czasem odmowa; 'utrzymaj to' i 'koniec' bez niej |
| 6.32 | Wyłącz internet / zły klucz: 'dość', 'wolniej', 'pauza', potem 'spuść się na mnie', 'szybciej' | dość/wolniej/pauza działają zwyczajnie (komunikat 'ran plain'); lube/szybciej ignorowane (persona unavailable) |
| 6.33 | `ELEVENLABS_API_KEY`, `python voice_control.py voices`, wpisz `voice_id`, `run.bat` i rozmowa | głos kobiecy ElevenLabs po polsku; pierwszy dźwięk < ~1 s po tym, jak linia pojawi się w oknie; łączny czas od końca Twojego zdania do głosu < ~2,5 s |
| 6.34 | 10 minut rozmowy (prośby, wyznania, odmowy) | ton rośnie etapami: chłodno -> stanowczo -> ostro -> wulgarnie; w logu `stage` rośnie; po 10 min ciszy wraca do łagodnego |
| 6.35 | Treści etapów 3-4: czy ElevenLabs / OpenAI nie łagodzą ani nie odrzucają wulgarnych kwestii | jeśli odrzuca: okno pokazuje `text-to-speech (...) failed`, mówi zapasowy dostawca; zanotuj, który i przy jakich słowach (zmienimy `stages` / `character`) |
| 6.36 | Cisza 25 s w trakcie sesji | Pani odzywa się sama (max 3 razy z rzędu), Twoja odpowiedź (okno 60 s) wraca do niej; `--no-nudge` wyłącza |
| 6.37 | Słuchawki: zacznij mówić, gdy Pani mówi; potem 'kochanie, koniec' w trakcie jej zdania | jej głos urywa się od razu; 'koniec' zatrzymuje maszynę |
| 6.38 | Głośniki (`persona.output: speakers`): jej głos nie uruchamia komend; 'koniec' w trakcie jej mowy | brak fałszywych komend; w tym trybie 'koniec' w trakcie mowy działa tylko klawiszem End (mikrofon głuchy) |
| 6.39 | Kwestia ostra (etap 3-4), którą ElevenLabs może odrzucić; sprawdź okno | `text-to-speech (elevenlabs) failed`, a ta sama kwestia mówiona głosem OpenAI (zapas); brak powtórzenia i brak ciszy |
| 6.40 | Zwroty: 'Pani, szybciej', 'kochanie, szybciej'; Pani zwraca się różnie (dziewczynko, mała, suko...) i przyjmuje 'Pani'/'kochanie' | oba słowa zaczynają komendę; 'proszę pani...' w dialogu filmu nie uruchamia niczego |
| 6.41 | 20 min rozmowy; zapisuj kwestie Pani | zwroty się nie powtarzają (bank ~100 + własne modelu); pojawiają się rozkazy / własność / upokorzenia / drwiny / pochwały / wyznania; na etapie 0-1 łagodnie, od 2 ostro |
| 6.42 | Powiedz 'należę do Ciebie' / 'zrób ze mną, co chcesz' / 'dziękuję, Pani' po jej żądaniu | w logu `p` rośnie o 0.3 przy wyznaniu; wykonane żądanie ('dziękuję') daje zgodę (`decision: owed`), nawet gdy model nie oznaczył wyznania |
| 6.43 | 20-30 min rozmowy; zapisz ton każdej kwestii (okno pokazuje `[etap]`, log `tone`/`harsh`) | czułe i ostre na przemian; na początku prawie same czułe ('Odpuść', 'Jesteś moja', 'Nie kontrolujesz tego'); ostre rośnie z etapem, ale bywa nieprzewidywalne; nigdy > 2 ostre z rzędu |
| 6.44 | Szczyt sesji (etap 4) | czasem bardzo ostre słowa (`harsh: 3`), rzadko (ok. 30 % ostrych); zaraz potem często czułość |
| 6.45 | Głos: kwestia czuła vs ostra | czuła: miękko, wolniej; ostra: chłodno, szybciej (różne `voice_settings`/instrukcje OpenAI) |
| 6.46 | Trudność: policz prośby do pierwszego strzału lube w 5 sesjach | średnio ok. 5 (3-8); 'dość' / 'wolniej' zwykle w 1-3; jeśli za trudno/łatwo -> `persona.difficulty` |
| 6.47 | Niezamówione ruchy: obserwuj log `unsolicited` | zdarzają się nagrody/dręczenie bez Twojej prośby (rzadko na niskim etapie); lube niezamówione tylko od etapu 3, rzadko, nie częściej niż co 5 min; w holdzie tylko lube |
| 6.48 | 'koniec' / End w trakcie jej ostrej i czułej kwestii | zawsze natychmiast; nigdy 'zaskoczenie' nie zatrzymuje ani nie uspokaja maszyny |
| 6.49 | Poproś bez 'proszę' kilka razy, potem z 'proszę, Pani' | grzeczne prośby dostają zgodę wyraźnie częściej (log: `polite`, `p`) |
| 6.50 | Nie rób tego, czego żąda (milcz/zmień temat), potem poproś | niższe `p`; po wykonaniu żądania szansa wraca |
| 6.51 | Odmów: 'nie chcę' / 'nie będę prosić' | Pani karci, przez 90 s same odmowy (`punished`), nawet po wielu prośbach; 'przepraszam, Pani' kończy karę |
| 6.52 | W trakcie kary: 'koniec' / 'utrzymaj to' | działają natychmiast, bez kary |
| 6.28 | Klawisze przy włączonej Pani: F8, Num +, Num 9 | działają od razu, bez proszenia |
| 6.29 | `--no-tts` i `--no-persona` | linie tylko w oknie / zwykłe komendy jak dawniej |

## Faza 7 – Testy awaryjne
| # | Krok | Oczekiwane |
|---|---|---|
| 7.1 | Rozłącz Buttplug w MFP w trakcie trybu 3, połącz ponownie | maszyna w znanym stanie (zapisz), MFP się nie wiesza |
| 7.2 | Restart serwera Buttplug na Linuxie w trakcie filmu | MFP się łączy z powrotem; Device map zostaje |
| 7.3 | Wyłącz Remote commands, powiedz komendę (live) | brak reakcji, brak błędu w MFP |
| 7.4 | Zamknij `run.bat` | klawisze i głos przestają działać, MFP działa dalej |
| 7.5 | **Awaryjne zatrzymanie:** prędkość 0 (Auto-home L1 = 0% albo BOOST: Slower do zera) i wyłącznik maszyny | wiesz, co wciskasz, gdy „zaniemówisz" |

## Co mi odesłać po testach
Tabelka wyników (✅/❌/⚠️) z numerami testów + dla każdego ❌/⚠️: opis, ostatnie ~30 linii logu serwera (Linux) i okna `voice_control.py`. Priorytet: 1.1 (kod urządzenia), 3.2 (R1), 3.4 (R2), 3.5 (R3), 2.6 (co robi Stop), 5.4 i 6.5 (kolizje klawiszy i dźwięk z filmu).
