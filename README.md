<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/logo-dark.jpg">
    <img src="docs/logo-light.jpg" alt="FilaForge" width="600">
  </picture>
</p>

# FilaForge

Filament profiles for OrcaSlicer, built from open data, the maker's data sheet and your own measurements, with
the origin of every value in plain sight.

*Polski opis niżej.*

<p align="center"><img src="docs/filaforge.gif" alt="FilaForge" width="480"></p>

## What it does

Pick a spool from Spoolman (or type a brand and material) and press **Find data**. FilaForge reads:

| Source | What it gives | Kind |
|---|---|---|
| [Open Filament Database](https://openfilamentdatabase.org) (MIT) | nozzle/bed ranges, density, max flow, fans, drying, the data sheet's address, the matching OrcaSlicer preset | maker |
| The maker's data sheet (PDF or product page) | nozzle range per print speed, bed, retraction, Vicat/HDT, density, drying | maker |
| [SpoolmanDB](https://github.com/Donkie/SpoolmanDB) (MIT) | temperatures, density | maker |
| OrcaSlicer's own presets | the maker's preset for this filament on your printer, and the generic one of the material | preset / generic |
| Your measurements, and the `orca_settings` already in Spoolman | anything you calibrated | tested |

Each setting shows its value, where it came from, how sure it is (sources that agree raise it) and the other
values the sources gave. Rules: your tested value wins; then the maker's declared values; a preset value is kept
only inside the maker's range; for a data sheet that gives temperatures per print speed, the band of your
printer's process speed is used.

Then:

- **Create preset in OrcaSlicer**: a standalone filament preset (its own `filament_id`, so *Sync from AMS* and
  printer agents can pick it) in a local bundle named FilaForge, limited to the selected printer.
- **Save to Spoolman**: the ticked settings go into the filament's `orca_settings` field, for every printer or
  only this printer model, and only where the field doesn't already have the setting. Spooler turns them into
  presets. You see what will be added and what is kept before anything is written.
- **Your measurements**: flow ratio, pressure advance, max flow, temperatures, retraction; per filament, for
  every printer or one.
- **Print process**: the printer's process with speeds lowered to the filament's max flow and to the
  material's habits (slower outer walls for PETG and silk, slow TPU), saved as a process that inherits it.

## Install

Download `filaforge_any.whl` from [Releases](https://github.com/xtronets/filaforge/releases/latest), then in OrcaSlicer: **Plugins** (the ▾ next to File on Windows/Linux) →
**Install local plugin** → tick **Activate** and allow what it asks for (background thread, its folder, the
addresses above). The **FilaForge** tab appears next to Prepare, Preview and Device.

Needs OrcaSlicer 2.5 or newer. Works offline with OrcaSlicer's presets and your measurements; answers from the
internet are cached for a day (a week for the databases) in `<OrcaSlicer data>/filaforge/`.

## Development

    python3 -m unittest discover -s tests      # core and plugin, with a fake OrcaSlicer
    python3 scripts/build.py                   # dist/filaforge_any.whl
    python3 scripts/vendor_pypdf.py pypdf-*.whl  # refresh the vendored PDF reader

---

## Po polsku

Wybierz szpulę ze Spoolmana (albo wpisz producenta i materiał) i kliknij **Szukaj danych**. FilaForge zbiera
wartości z Open Filament Database, z karty technicznej producenta (PDF), ze SpoolmanDB, z presetów Orki
i z Twoich pomiarów. Przy każdym ustawieniu widać, skąd jest, jak pewna jest wartość i co podały inne źródła.
Potem jednym kliknięciem tworzy preset filamentu w Orce, dopisuje brakujące ustawienia do Spoolmana
(Spooler zrobi z nich presety) i liczy proces druku dopasowany do przepływu filamentu.

Instalacja: pobierz `filaforge_any.whl` z [Releases](https://github.com/xtronets/filaforge/releases/latest), potem Orca → **Wtyczki** → **Zainstaluj lokalną wtyczkę** → wskaż ten plik.
