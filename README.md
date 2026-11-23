<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/logo-dark.jpg">
    <img src="docs/logo-light.jpg" alt="FilaForge" width="600">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/xtronets/filaforge/releases/latest"><img src="https://img.shields.io/github/v/release/xtronets/filaforge?label=wersja" alt="Wersja"></a>
  <a href="https://github.com/xtronets/filaforge/actions/workflows/build.yml"><img src="https://github.com/xtronets/filaforge/actions/workflows/build.yml/badge.svg" alt="Build"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/licencja-MIT-blue" alt="Licencja MIT"></a>
</p>

<p align="center"><b>Polski</b> · <a href="README.en.md">English</a></p>

# FilaForge

Wtyczka do OrcaSlicera, która robi profile filamentów z otwartych baz danych, z karty technicznej producenta
i z Twoich własnych pomiarów. Przy każdej wartości widać, skąd pochodzi i na ile jest pewna.

<p align="center"><img src="docs/filaforge.gif" alt="FilaForge" width="480"></p>

## Spis treści

- [Co potrafi](#co-potrafi)
- [Skąd bierze dane](#skąd-bierze-dane)
- [Jak wybiera wartość](#jak-wybiera-wartość)
- [Proces druku dla filamentu](#proces-druku-dla-filamentu)
- [Wymagania](#wymagania)
- [Instalacja, aktualizacja, odinstalowanie](#instalacja-aktualizacja-odinstalowanie)
- [Jak używać](#jak-używać)
- [Ustawienia](#ustawienia)
- [Gdzie co zapisuje](#gdzie-co-zapisuje)
- [Prywatność i sieć](#prywatność-i-sieć)
- [Rozwiązywanie problemów](#rozwiązywanie-problemów)
- [Dla programistów](#dla-programistów)
- [Licencja](#licencja)

## Co potrafi

- **Szuka danych o filamencie** wybranym ze Spoolmana, z Twoich presetów w Orce albo wpisanym ręcznie
  (producent, materiał, odmiana), w kilku źródłach naraz, i pokazuje je w jednej tabeli.
- **Ulepsza presety, które już masz**: na karcie *Z Orki* wybierasz swój preset filamentu, a FilaForge
  uzupełnia go danymi producenta i zapisuje jako nowy preset z dopiskiem FF. Twój preset zostaje bez zmian,
  a jego wartości (np. flow ratio, który ustawiłeś ręcznie) mają pierwszeństwo przed presetami producenta.
- **Czyta karty techniczne (TDS)** producentów: z pliku PDF albo z adresu PDF lub strony produktu. Wyciąga
  temperatury dyszy (także zakresy zależne od prędkości druku), stołu, retrakcję, gęstość, Vicat/HDT i suszenie.
- **Tworzy preset filamentu w Orce** jednym kliknięciem. To samodzielny preset z własnym `filament_id`, więc
  *Synchronizacja z AMS* i agenci drukarek (np. U1 Control) potrafią go dopasować. Nazwa dostaje znacznik
  **FF** (np. `Sunlu PLA+ FF`), a preset jest przypisany do wybranej drukarki.
- **Zapisuje ustawienia w Spoolmanie**, w polu `orca_settings` filamentu, dla wszystkich drukarek albo tylko
  dla tego modelu. Dopisuje wyłącznie brakujące ustawienia, a przed zapisem pokazuje, co doda i co zostawi.
  Z tych ustawień [Spooler](https://github.com/bloodf/spooler) robi potem presety.
- **Pamięta Twoje pomiary**: temperaturę dyszy i stołu, flow ratio, pressure advance, maks. przepływ
  i retrakcję. Pomiary są per filament, dla wszystkich drukarek albo dla jednej, i zawsze mają pierwszeństwo.
- **Liczy proces druku dopasowany do filamentu**: prędkości obniżone do maks. przepływu, zwyczaje materiału,
  mosty, przyspieszenia, limity drukarki i warianty dla małych detali.
- **Ma interfejs po polsku i po angielsku**, w jasnym i ciemnym motywie Orki.
- **Działa też bez internetu**: korzysta wtedy z presetów Orki, Twoich pomiarów i zapisanych wcześniej odpowiedzi.

## Skąd bierze dane

| Źródło | Co daje | Rodzaj |
|---|---|---|
| [Open Filament Database](https://openfilamentdatabase.org) (MIT) | zakresy temperatur dyszy i stołu, gęstość, maks. przepływ, wentylator, suszenie, adres karty TDS, pasujący preset Orki | producent |
| Karta techniczna producenta (PDF lub strona produktu) | zakres temperatur dyszy dla prędkości druku, stół, retrakcja, Vicat/HDT, gęstość, suszenie | producent |
| [SpoolmanDB](https://github.com/Donkie/SpoolmanDB) (MIT) | temperatury, gęstość | producent |
| Presety OrcaSlicera | preset producenta tego filamentu dla Twojej drukarki oraz ogólny preset materiału | preset / ogólny |
| Twój preset wybrany na karcie *Z Orki* | wartości, z którymi drukujesz teraz; jest bazą nowego presetu | preset (przed presetem producenta) |
| Twoje pomiary i `orca_settings`, które już są w Spoolmanie | wszystko, co skalibrowałeś | zmierzona |

## Jak wybiera wartość

Każde ustawienie w tabeli ma wartość, pochodzenie (kolor), pewność (kreski) i listę wartości z pozostałych
źródeł. Pewność rośnie, gdy źródła się zgadzają.

Kolejność pierwszeństwa:

1. **Zmierzona**: Twój pomiar zawsze wygrywa.
2. **Producent**: wartości deklarowane w bazach i w karcie TDS. Temperatura dyszy i stołu jest wybierana
   w zakresie producenta, według wiarygodności źródeł. Jeśli karta podaje temperatury dla różnych prędkości,
   brany jest zakres pasujący do prędkości procesu Twojej drukarki.
3. **Preset**: wartość z presetu Orki zostaje tylko wtedy, gdy mieści się w zakresie producenta.
4. **Szacunek**: np. maks. przepływ wyliczony z prędkości druku podanej przez producenta.
5. **Ogólny**: wartość z ogólnego presetu materiału, gdy nic lepszego nie ma.

Zaznaczasz, które ustawienia trafią do presetu i do Spoolmana. Ustawienia takie same jak w presecie bazowym
są domyślnie ukryte; pokażesz je linkiem pod tabelą.

## Proces druku dla filamentu

Karta **Proces druku dla tego filamentu** bierze wybrany proces drukarki i robi z niego kopię z dziedziczeniem
(`inherits`). Zmienia w niej tylko to, co trzeba:

- **Prędkości** są ograniczone do maks. przepływu filamentu. Wzór: v = przepływ / (h · (w − h·(1 − π/4))),
  gdzie h to wysokość warstwy, a w to szerokość linii. Dotyczy to ścian, wypełnienia, górnej powierzchni,
  szczelin, małych obwodów, szwu scarf, podpór i ich interfejsu oraz mostów. Dla mostów przekrój liczony jest
  jak dla okrągłej nitki.
- **Zwyczaje materiału**: PETG i PLA Silk mają wolniejsze ściany zewnętrzne i górę. ABS, ASA i materiały z
  włóknem węglowym są trochę wolniejsze, PC i PA ok. 70%. TPU ma ok. 30% prędkości, nie więcej niż 60 mm/s,
  oraz łagodniejsze przyspieszenia.
- **Mosty**: przyspieszenie mostu jest takie, żeby most rozpędzał się do swojej prędkości w ok. 25 ms i nie
  zrywał nitki na starcie (minimum 500 mm/s²).
- **Limity drukarki**: żadna prędkość ani przyspieszenie nie przekracza limitów maszyny z profilu drukarki.
- **Warianty**: *Zwykłe modele*, *Małe detale (do 40 mm)* z wolniejszymi ścianami i przyspieszeniami, albo
  *Według modeli na płycie*. Ten ostatni sam wybiera wariant na podstawie wymiarów modeli na płycie.

Przy każdej zmianie widać wartość „teraz”, „nowa” i powód. Proces zapisuje się w Orce jako
np. `0.20mm Standard Sunlu PLA+ FF`.

## Wymagania

- OrcaSlicer **2.5** lub nowszy, z obsługą wtyczek.
- Opcjonalnie [Spoolman](https://github.com/Donkie/Spoolman), żeby wybierać szpule i zapisywać ustawienia. Bez niego wszystko inne działa.
- Opcjonalnie [Spooler](https://github.com/bloodf/spooler). Jeśli go masz, FilaForge sam weźmie z niego adres Spoolmana.

## Instalacja, aktualizacja, odinstalowanie

**Instalacja**

1. Pobierz `filaforge_any.whl` z [Releases](https://github.com/xtronets/filaforge/releases/latest).
2. W Orce otwórz menu **Wtyczki**. W Windows i Linux jest pod ▾ obok menu Plik.
3. Kliknij **Zainstaluj lokalną wtyczkę** i wskaż pobrany plik.
4. Zaznacz **Aktywuj** i zezwól na to, o co wtyczka prosi: wątek w tle, własny folder i adresy sieciowe
   opisane w [Prywatność i sieć](#prywatność-i-sieć).
5. Obok zakładek Przygotuj, Podgląd i Urządzenie pojawi się zakładka **FilaForge**.

**Aktualizacja**: zainstaluj nowy plik `.whl` tak samo, a potem uruchom Orkę ponownie. Twoje ustawienia,
pomiary i presety zostają.

**Odinstalowanie**: usuń wtyczkę w menu **Wtyczki**. Presety zrobione przez FilaForge usuniesz wcześniej
w oknie **Zapisane** albo w Orce. Folder `<dane Orki>/filaforge/` możesz potem skasować ręcznie.

## Jak używać

1. **Wybierz filament.** Na karcie *Ze Spoolmana* wybierz szpulę z listy (jest wyszukiwarka). Na karcie
   *Z Orki* wybierz swój preset filamentu dla tej drukarki; producenta, materiał, odmianę i kolor FilaForge
   odczyta z presetu. Na karcie *Ręcznie* wpisz producenta, materiał i odmianę; podpowiedzi odmian pochodzą
   z Open Filament Database.
2. **Opcjonalnie dodaj kartę TDS**: wklej adres PDF lub strony produktu albo wybierz plik PDF z dysku.
3. Kliknij **Szukaj danych**. Pasek źródeł pokazuje, co znalazło się w każdym z nich.
4. **Przejrzyj tabelę** i odznacz to, czego nie chcesz.
5. Kliknij **Utwórz preset w Orce**. Preset pojawi się w Filament → Presety użytkownika.
6. Opcjonalnie kliknij **Zapisz w Spoolmanie…**: wybierz sekcję (wszystkie drukarki albo tylko ten model),
   sprawdź plan i zatwierdź.
7. Po kalibracji wpisz wyniki w **Twoje pomiary** i kliknij **Zapisz pomiary i przelicz**.
8. W **Proces druku dla tego filamentu** wybierz proces i wariant, kliknij **Przelicz**, a potem
   **Zapisz proces w Orce**.

**Bez Spoolmana** wtyczka działa normalnie: od razu otwiera się karta *Z Orki* z Twoimi presetami (albo
*Ręcznie*, jeśli własnych presetów nie masz), a przycisk zapisu do Spoolmana jest ukryty. Wyszukiwanie danych, presety, Twoje pomiary i proces druku działają tak samo.

Przycisk **Zapisane** w nagłówku pokazuje wszystkie presety i procesy zrobione przez FilaForge. Tam też je usuniesz.

**Nazwy presetów**: FilaForge dodaje znacznik **FF** i nie dopisuje `@drukarka`. Jeśli preset o tej samej
nazwie istnieje już dla innej drukarki, do nazwy dochodzi model w nawiasie, np. `Sunlu PLA+ FF (Snapmaker U1)`.

## Ustawienia

| Ustawienie | Opis |
|---|---|
| Adres Spoolmana | np. `http://192.168.1.2:7912`. Gdy pole jest puste, a masz Spoolera, użyty jest adres ze Spoolera. |
| Język | Automatycznie (według języka systemu), polski albo angielski. |
| Szukaj w internecie | Wyłączone = tylko presety Orki, Twoje pomiary i zapisane wcześniej odpowiedzi. |

## Gdzie co zapisuje

| Co | Gdzie |
|---|---|
| Presety filamentów i procesów | osobny pakiet **FilaForge** w Twoich presetach użytkownika: `<dane Orki>/user/<konto>/_local/filaforge/` |
| Ustawienia | `<dane Orki>/filaforge/settings.json` |
| Twoje pomiary | `<dane Orki>/filaforge/measured.json` |
| Pamięć podręczna odpowiedzi z internetu | `<dane Orki>/filaforge/cache/`. Odpowiedzi baz są ważne tydzień, pozostałe dzień. Gdy sieci nie ma, użyta jest starsza kopia. |
| Dziennik | `<dane Orki>/filaforge/filaforge.log` |
| Ustawienia w Spoolmanie | pole dodatkowe `orca_settings` filamentu. Gęstość, średnica i cena zostają w zwykłych polach Spoolmana. |

## Prywatność i sieć

FilaForge łączy się tylko z:

- `api.openfilamentdatabase.org` (Open Filament Database),
- `api.github.com` i `raw.githubusercontent.com` (SpoolmanDB i presety OrcaSlicera),
- Twoim Spoolmanem,
- stronami kart TDS, które sam podasz.

Nie wysyła żadnych danych o Tobie ani o Twoich wydrukach. Do Spoolmana zapisuje tylko to, co zatwierdzisz.

Orca pyta o zgodę na adresy przy starcie wtyczki. Kiedy podasz kartę TDS z nowej strony, FilaForge zapamięta
ten adres i poprosi o niego przy następnym uruchomieniu Orki. Do tego czasu możesz wybrać plik PDF z dysku.

## Rozwiązywanie problemów

- **Ikona zakładki się nie zmieniła po aktualizacji.** Zamknij Orkę całkowicie i uruchom ją ponownie.
  Orka przy starcie rozpakowuje wtyczkę ze swojej kopii pliku `.whl`, więc aktualizuj ją przez
  *Zainstaluj lokalną wtyczkę*, a nie przez podmianę plików.
- **„Ustaw adres Spoolmana w Ustawieniach.”** Wpisz adres Spoolmana w **Ustawienia** albo skonfiguruj go w Spoolerze.
- **Karta TDS z nowej strony nie chce się pobrać.** Uruchom Orkę ponownie i zezwól na nowy adres albo wybierz plik PDF.
- **Brak presetu producenta.** FilaForge użyje ogólnego presetu materiału i powie o tym w pasku źródeł.
- **Coś poszło nie tak.** Szczegóły są w `<dane Orki>/filaforge/filaforge.log`. Zgłoś błąd w
  [Issues](https://github.com/xtronets/filaforge/issues) i dołącz ten plik.

## Dla programistów

```
python3 -m unittest discover -s tests          # rdzeń i wtyczka, z udawaną Orką
python3 scripts/build.py                       # dist/filaforge_any.whl
python3 scripts/vendor_pypdf.py pypdf-*.whl    # odświeżenie dołączonego czytnika PDF
```

- `src/filaforge/core/`: rdzeń bez zależności od Orki (źródła, scalanie, pomiary, procesy, zapis presetów, Spoolman).
- `src/filaforge/plugin.py` i `__init__.py`: zakładka w Orce i obsługa wiadomości ze strony.
- `src/filaforge/ui/page.html`: interfejs (PL/EN).
- `src/filaforge/_vendor/pypdf/`: dołączony [pypdf](https://github.com/py-pdf/pypdf) 5.4.0 (BSD).

**Wydanie nowej wersji**: podnieś `VERSION` w `src/filaforge/plugin.py`, wypchnij zmiany i opublikuj na
GitHubie wydanie z tagiem `v<VERSION>`. Workflow *Build* uruchomi testy, zbuduje `filaforge_any.whl`
i dołączy go do wydania.

## Licencja

[MIT](LICENSE) © xtronets. Dane pochodzą z Open Filament Database i SpoolmanDB (obie MIT) oraz z presetów
OrcaSlicera. Dołączony pypdf jest na licencji BSD.
