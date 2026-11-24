"""The user's own tested values (calibration results): kept per filament, for every printer or for one.

Stored as JSON in FilaForge's folder: {"version": 1, "filaments": {identity key: {section: {canonical key: number,
"_at": time, "_nozzle": {"0.4": {canonical key: number}}}}}}, a section being "*" (every printer) or a printer model
such as "Creality K1C". Values that depend on the nozzle (pressure advance, max flow) are kept per nozzle diameter
under "_nozzle"; such a value saved before 0.3.1 at the top of a section counts for a 0.4 mm nozzle.
"""

import json
import os
import time

from . import files
from .text import brand_key, fold, split_material, tokens

FILE = "measured.json"
# what the user can measure, with sane limits
LIMITS = {
    "nozzle_temp": (150, 450), "bed_temp": (0, 150), "flow_ratio": (0.5, 1.5), "pressure_advance": (0, 2),
    "max_volumetric_speed": (0.5, 80), "retraction_length": (0, 15), "retraction_speed": (1, 200),
    "fan_min": (0, 100), "fan_max": (0, 100), "chamber_temp": (0, 120),
}


# measured on one nozzle, wrong on another: a 0.6 mm nozzle melts more per second, and pressure advance changes
NOZZLE_KEYS = ("pressure_advance", "max_volumetric_speed")
LEGACY_NOZZLE = "0.4"


def nozzle_tag(nozzle):
    """"0.4" for 0.4, 0.40 or "0,4"; "" when there is no usable diameter."""
    try:
        value = float(str(nozzle).replace(",", "."))
    except (TypeError, ValueError):
        return ""
    return ("%g" % round(value, 2)) if 0.05 <= value <= 3 else ""


def section_values(section, nozzle=None):
    """{key: value} a stored section gives for this nozzle (None: the values for every nozzle only)."""
    section = section or {}
    tag = nozzle_tag(nozzle) if nozzle is not None else ""
    out = {}
    for key, value in section.items():
        if key.startswith("_"):
            continue
        if key in NOZZLE_KEYS and tag and tag != LEGACY_NOZZLE:
            continue        # measured (before 0.3.1) on a 0.4 mm nozzle
        out[key] = value
    if tag:
        out.update((section.get("_nozzle") or {}).get(tag) or {})
    return out


def identity_key(identity):
    base, words = split_material(identity.material, identity.product)
    product = " ".join(sorted(set(tokens(words)) - set(tokens(base)))) if words else ""
    return "%s|%s|%s" % (brand_key(identity.brand), base.lower(), fold(product))


def check(values):
    """{key: float} of the values that are known keys within their limits; raises ValueError otherwise."""
    out = {}
    for key, value in (values or {}).items():
        if value in (None, ""):
            continue
        if key not in LIMITS:
            raise ValueError("unknown value: %s" % key)
        try:
            number = float(str(value).replace(",", "."))
        except ValueError:
            raise ValueError("%s: not a number" % key) from None
        lo, hi = LIMITS[key]
        if not lo <= number <= hi:
            raise ValueError("%s: %g is outside %g-%g" % (key, number, lo, hi))
        out[key] = number
    return out


class Store:
    def __init__(self, folder):
        self.path = os.path.join(folder, FILE)

    def _read(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {"version": 1, "filaments": {}}
        if not isinstance(data, dict) or not isinstance(data.get("filaments"), dict):
            return {"version": 1, "filaments": {}}
        return data

    def _write(self, data):
        files.write_json(self.path, data, ensure_ascii=False, indent=1, sort_keys=True)

    def get(self, identity, printer_model="", nozzle=None):
        """{key: value} that apply on this printer and nozzle: the "*" values under the printer's own."""
        sections = self._read()["filaments"].get(identity_key(identity)) or {}
        out = section_values(sections.get("*"), nozzle)
        for name, values in sections.items():
            if name != "*" and printer_model and fold(name) in fold(printer_model):
                out.update(section_values(values, nozzle))
        return out

    def sections(self, identity):
        return self._read()["filaments"].get(identity_key(identity)) or {}

    def set(self, identity, section, values, nozzle=None):
        """Replace the section's values for this nozzle: the values for every nozzle, and the nozzle's own
        (pressure advance, max flow) under its diameter, other nozzles' kept. Nothing left removes the section."""
        values = check(values)
        tag = nozzle_tag(nozzle) if nozzle is not None else ""
        data = self._read()
        entry = data["filaments"].setdefault(identity_key(identity), {})
        old = entry.get(section or "*") or {}
        per_nozzle = {k: dict(v) for k, v in (old.get("_nozzle") or {}).items() if isinstance(v, dict)}
        new = {k: v for k, v in values.items() if not (tag and k in NOZZLE_KEYS)}
        if tag:
            if tag != LEGACY_NOZZLE:     # the old 0.4 mm values stay at the top until a 0.4 mm save
                new.update({k: v for k, v in old.items() if k in NOZZLE_KEYS and k not in new})
            mine = {k: v for k, v in values.items() if k in NOZZLE_KEYS}
            if mine:
                per_nozzle[tag] = mine
            else:
                per_nozzle.pop(tag, None)
        if new or per_nozzle:
            entry[section or "*"] = dict(new, _at=int(time.time()), **({"_nozzle": per_nozzle} if per_nozzle else {}))
        else:
            entry.pop(section or "*", None)
        if not entry:
            data["filaments"].pop(identity_key(identity), None)
        self._write(data)
        return self.sections(identity)
