"""One look-up: every source for a filament on one printer, merged into OrcaSlicer settings.

Runs on a worker thread; it touches no OrcaSlicer API (the plugin hands it the presets it read on the UI thread).
"""

import base64

from . import merge as merging
from .model import Identity
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


def run(identity, presets, fetcher, process_config=None, tested=(), sheet_urls=(), sheet_files=(), progress=None,
        use_online=True):
    """The result the page shows: {identity, reports, picks, settings, base, sources}.

    presets: the filament presets read from OrcaSlicer (see orca_presets); tested: Observations of the user's
    values; sheet_urls / sheet_files ({"name", "data": base64}): data sheets the user added."""
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
    observations += list(tested)
    if tested:
        reports.append(merging_report(tested))
    picks = merging.merge(observations, typical_speed(process_config))
    base = chosen["vendor"] or chosen["generic"]
    settings = merging.orca_settings(picks, (base or {}).get("config"))
    return {
        "identity": {"brand": identity.brand, "material": identity.material, "product": identity.product,
                     "colour": identity.colour, "label": identity.label()},
        "reports": [r.__dict__ for r in reports],
        "picks": {k: p.as_dict() for k, p in picks.items()},
        "settings": settings,
        "base": {"name": base["name"], "kind": "vendor" if chosen["vendor"] else "generic"} if base else None,
        "observations": [o.as_dict() for o in observations],
    }


def merging_report(tested):
    from .model import SourceReport
    return SourceReport("tested", True, len(tested), ", ".join(sorted({fold(o.key) for o in tested})))
