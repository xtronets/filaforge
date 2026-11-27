"""One look-up: every source for a filament on one printer, merged into OrcaSlicer settings.

Runs on a worker thread; it touches no OrcaSlicer API (the plugin hands it the presets it read on the UI thread).
"""

import base64

from . import habits as habit_table
from . import merge as merging
from .model import ESTIMATED, Identity, Observation
from .sources import ofd, orca_presets, spoolmandb, tds
from .text import fold, split_material


def identity_from(data):
    """An Identity from the page's form or a Spoolman filament ({brand, material, product, colour})."""
    brand = str(data.get("brand") or data.get("vendor") or "").strip()
    raw_material = str(data.get("material") or "").strip()
    product = str(data.get("product") or data.get("variant") or "").strip()
    base, words = split_material(raw_material, product)
    colour = str(data.get("colour") or data.get("color_hex") or "").strip()
    if colour and not colour.startswith("#"):
        colour = "#" + colour
    if not brand:
        raise ValueError("brand_missing")
    return Identity(brand, base, words, colour[:7])


def typical_speed(process_config):
    """The speed the printer's process mostly prints at (inner walls), to pick a data sheet's temperature band."""
    for key in ("inner_wall_speed", "sparse_infill_speed", "outer_wall_speed"):
        value = str((process_config or {}).get(key, "")).split(",")[0]
        try:
            return float(value)
        except ValueError:
            continue
    return None


HABIT_WEIGHT = 0.3


HABIT_KEYS = (("fan_min", "fan_min"), ("fan_max", "fan_max"), ("bridge_fan", "bridge_fan"),
              ("full_fan_layer", "full_fan_layer"), ("layer_time", "layer_time"), ("aux_fan", "aux_fan"),
              ("fan_off_layers", "fan_off_layers"), ("chamber", "chamber_temp"))


def habit_observations(identity, row, cls, chamber_control=False, aux_fan=True):
    """The material table's filament estimates: the part fan, the auxiliary fan (a printer that has one), the
    shortest layer time, fan-free first layers, chamber (a printer that heats it)."""
    label = "FilaForge: %s habits" % cls
    note = "rule of thumb for %s (Settings → Materials)" % cls
    out = []
    for field, key in HABIT_KEYS:
        value = row.get(field)
        if value is None or (key == "chamber_temp" and (not chamber_control or not value)) or \
                (key == "aux_fan" and not aux_fan):
            continue
        out.append(Observation(key, float(value), ESTIMATED, "habits", label, "", HABIT_WEIGHT, note))
    return out


def run(identity, presets, fetcher, process_config=None, tested=(), sheet_urls=(), sheet_files=(), progress=None,
        use_online=True, own=None, table=None, printer=None, enclosed_printers=()):
    """The result the page shows: {identity, reports, picks, settings, base, sources}.

    presets: the filament presets read from OrcaSlicer (see orca_presets); tested: Observations of the user's
    values; sheet_urls / sheet_files ({"name", "data": base64}): data sheets the user added; own: the user's own
    OrcaSlicer preset (same shape as presets) to start from: its values count, and it is the base. table: the
    material habits (habits.table()); printer: {"name", "chamber_control"}; enclosed_printers: the printers the user
    said are closed."""
    printer = printer or {}
    cls = habit_table.material_class(identity.material, identity.product)
    row = (table or habit_table.table()).get(cls, {})
    say = progress or (lambda *a: None)
    observations, reports = [], []
    hint, sheets = "", []
    if use_online:
        say("ofd")
        obs, report, extra = ofd.lookup(fetcher, identity)
        observations += obs
        reports.append(report)
        hint = extra.get("orca_profile", "")
        sheets += [o.value for o in obs if o.key == "data_sheet"]
        say("spoolmandb")
        obs, report = spoolmandb.lookup(fetcher, identity)
        observations += obs
        reports.append(report)
    say("orca")
    obs, report, chosen = orca_presets.lookup(presets, identity, hint)
    observations += obs
    reports.append(report)
    if own:
        obs, report = orca_presets.own_lookup(own)
        observations += obs
        reports.append(report)
    for url in list(dict.fromkeys(list(sheet_urls) + (sheets if use_online else []))):
        say("tds")
        obs, report = tds.lookup(fetcher, url, "Data sheet: %s" % identity.label())
        observations += obs
        reports.append(report)
    for item in sheet_files:
        try:
            data = base64.b64decode(item.get("data") or "", validate=False)
        except (ValueError, TypeError):
            continue
        obs, report = tds.from_bytes(data, "Data sheet: %s" % (item.get("name") or "file"), item.get("name") or "")
        observations += obs
        reports.append(report)
    observations += habit_observations(identity, row, cls, bool(printer.get("chamber_control")),
                                       printer.get("aux_fan", True))
    observations += list(tested)
    if tested:
        reports.append(merging_report(tested))
    picks = merging.merge(observations, typical_speed(process_config))
    base = own or chosen["vendor"] or chosen["generic"]
    base_kind = "own" if own else "vendor" if chosen["vendor"] else "generic"
    settings = merging.orca_settings(picks, (base or {}).get("config"), bool(printer.get("chamber_control")))
    return {
        "identity": {"brand": identity.brand, "material": identity.material, "product": identity.product,
                     "colour": identity.colour, "label": identity.label()},
        "reports": [r.__dict__ for r in reports],
        "picks": {k: p.as_dict() for k, p in picks.items()},
        "settings": settings,
        "base": {"name": base["name"], "kind": base_kind} if base else None,
        "observations": [o.as_dict() for o in observations],
        "material_class": cls,
        "warnings": habit_table.warnings(cls, row, printer, enclosed_printers),
    }


def merging_report(tested):
    from .model import SourceReport
    return SourceReport("tested", True, len(tested), ", ".join(sorted({fold(o.key) for o in tested})))
