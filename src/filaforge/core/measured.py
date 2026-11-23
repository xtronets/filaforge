"""The user's own tested values (calibration results): kept per filament, for every printer or for one.

Stored as JSON in FilaForge's folder: {"version": 1, "filaments": {identity key: {section: {canonical key: number,
"_at": time}}}}, a section being "*" (every printer) or a printer model such as "Creality K1C".
"""

import json
import os
import tempfile
import time

from .text import brand_key, fold, split_material, tokens

FILE = "measured.json"
# what the user can measure, with sane limits
LIMITS = {
    "nozzle_temp": (150, 450), "bed_temp": (0, 150), "flow_ratio": (0.5, 1.5), "pressure_advance": (0, 2),
    "max_volumetric_speed": (0.5, 80), "retraction_length": (0, 15), "retraction_speed": (1, 200),
    "fan_min": (0, 100), "fan_max": (0, 100), "chamber_temp": (0, 120),
}


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
        folder = os.path.dirname(self.path)
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".measured.", suffix=".tmp", dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def get(self, identity, printer_model=""):
        """{key: value} that apply on this printer: the "*" values under the printer's own."""
        sections = self._read()["filaments"].get(identity_key(identity)) or {}
        out = {k: v for k, v in (sections.get("*") or {}).items() if not k.startswith("_")}
        for name, values in sections.items():
            if name != "*" and printer_model and fold(name) in fold(printer_model):
                out.update({k: v for k, v in values.items() if not k.startswith("_")})
        return out

    def sections(self, identity):
        return self._read()["filaments"].get(identity_key(identity)) or {}

    def set(self, identity, section, values):
        """Replace the section's values (an empty dict removes the section)."""
        values = check(values)
        data = self._read()
        entry = data["filaments"].setdefault(identity_key(identity), {})
        if values:
            entry[section or "*"] = dict(values, _at=int(time.time()))
        else:
            entry.pop(section or "*", None)
        if not entry:
            data["filaments"].pop(identity_key(identity), None)
        self._write(data)
        return self.sections(identity)
