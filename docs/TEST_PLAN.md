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
| 6.21 | Sceny: „kochanie, pieprz mnie" / „rozepchaj mnie" / „drażnij mnie" / „użyj mnie" (dry-run, potem live) | odpowiednio: szybciej+głębiej / głębiej ×2 / wolniej+płycej / szybciej+głębiej; zmiana wyraźna, ale nie skokowa |
| 6.22 | Improwizacja (potrzebny klucz): „kochanie, jestem twoją małą dziwką, zrób ze mną co chcesz", „kochanie, błagam, weź mnie" | `executed [improvise llm 0.8x]`, w oknie i w `voice_log.jsonl` widać `plan`; kilka razy pod rząd plany się różnią |
| 6.23 | Granice improwizacji: czy kiedykolwiek strzeli lube, włączy tryb A/B albo zrobi więcej niż 3 kroki „szybciej" na jedno zdanie? | **nigdy**; jeśli tak → zgłoś, zaostrzę |

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
