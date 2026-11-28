"""Spoolman: the user's filaments, their tested values, and FilaForge's values written back into the filament's
"orca_settings" extra field (the field Spooler turns into OrcaSlicer presets).

The field holds a JSON object of OrcaSlicer filament keys in sections: "*" for every printer, or a part of a
printer preset's name ("Creality K1C"). Spoolman stores an extra value as JSON text, so the object is encoded
twice. FilaForge only adds keys a section doesn't have: a value already there is the user's, and stays.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from .model import TESTED, Observation
from .text import fold, number

FIELD = "orca_settings"
TIMEOUT = 8
# OrcaSlicer key -> canonical key, for the values already in orca_settings (they count as tested)
TESTED_KEYS = {
    "filament_flow_ratio": "flow_ratio", "pressure_advance": "pressure_advance",
    "filament_max_volumetric_speed": "max_volumetric_speed", "nozzle_temperature": "nozzle_temp",
    "hot_plate_temp": "bed_temp", "textured_plate_temp": "bed_temp", "filament_retraction_length": "retraction_length",
    "filament_retraction_speed": "retraction_speed", "fan_min_speed": "fan_min", "fan_max_speed": "fan_max",
}
# Spooler's own calibration fields (extra, number) -> canonical key
CALIBRATION_FIELDS = {"orca_flow_ratio": "flow_ratio", "orca_pressure_advance": "pressure_advance",
                      "orca_max_volumetric_speed": "max_volumetric_speed"}


# Spoolman's own filament fields: a copy in orca_settings would hide a later edit of the field from Spooler's preset
NATIVE_KEYS = {"filament_density": "density", "filament_diameter": "diameter", "filament_cost": "price"}


class SpoolmanError(Exception):
    pass


def base_url(url):
    url = str(url or "").strip()
    if not url:
        raise SpoolmanError("no Spoolman address set")
    if "://" not in url:
        url = "http://" + url
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise SpoolmanError("not a Spoolman address: %s" % url)
    return "%s://%s%s/" % (parts.scheme, parts.netloc, parts.path.rstrip("/"))


class Client:
    def __init__(self, url, opener=None):
        self.url = base_url(url)
        self._open = opener or urllib.request.urlopen

    def call(self, method, path, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.url + "api/v1/" + path, data=data, method=method,
                                         headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with self._open(request, timeout=TIMEOUT) as response:
                text = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:300]
            raise SpoolmanError("Spoolman %s %s: HTTP %s %s" % (method, path, error.code, detail)) from None
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise SpoolmanError("Spoolman at %s: %s" % (self.url, getattr(error, "reason", error))) from None
        return json.loads(text) if text.strip() else None

    def filaments(self):
        out = []
        for f in self.call("GET", "filament") or []:
            vendor = (f.get("vendor") or {}).get("name") or ""
            out.append({"id": f.get("id"), "name": f.get("name") or "", "vendor": vendor,
                        "material": f.get("material") or "", "color_hex": f.get("color_hex") or "",
                        "density": f.get("density"), "variant": extra_text(f, "variant"),
                        "has_settings": bool(settings_of(f))})
        out.sort(key=lambda f: (fold(f["vendor"]), fold(f["material"]), fold(f["name"])))
        return out

    def filament(self, fid):
        return self.call("GET", "filament/%d" % int(fid))

    def spools(self, fid):
        """[{id, remaining_weight, location}] of the filament's spools that are not archived."""
        out = []
        for sp in self.call("GET", "spool?filament.id=%d" % int(fid)) or []:
            if sp.get("archived") or (sp.get("filament") or {}).get("id") not in (None, int(fid)):
                continue
            out.append({"id": sp.get("id"), "remaining_weight": sp.get("remaining_weight"),
                        "location": sp.get("location") or ""})
        return out

    def ensure_field(self):
        fields = {f.get("key") for f in self.call("GET", "field/filament") or []}
        if FIELD not in fields:
            self.call("POST", "field/filament/%s" % FIELD, {"name": "Orca settings", "field_type": "text"})

    def write_settings(self, fid, section, values, overwrite=()):
        """Add values ({OrcaSlicer key: text}) to the filament's section, keeping every key already there
        except those in overwrite whose value differs. Returns merge_settings' result (without "data")."""
        self.ensure_field()
        fresh = self.filament(fid)               # read just before writing: keep what changed meanwhile
        data = settings_of(fresh)
        if data is None:
            raise SpoolmanError("the filament's %s field is not a JSON object; fix it in Spoolman first" % FIELD)
        result = merge_settings(data, section, values, overwrite)
        merged = result.pop("data")
        if result["added"] or result["replaced"]:
            extra = dict(fresh.get("extra") or {})
            extra[FIELD] = json.dumps(json.dumps(merged))
            # the whole extra map: an older Spoolman replaces it, a newer one merges it; both keep every field
            self.call("PATCH", "filament/%d" % int(fid), {"extra": extra})
        return result


def extra_text(filament, key):
    raw = (filament.get("extra") or {}).get(key)
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        value = raw
    return value if isinstance(value, str) else ""


def settings_of(filament):
    """The orca_settings object as {section: {key: value}} ({} when empty, None when it isn't an object)."""
    raw = (filament.get("extra") or {}).get(FIELD)
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        data = json.loads(data) if isinstance(data, str) and data.strip() else data
    except ValueError:
        return None
    if data is None or (isinstance(data, str) and not data.strip()):
        return {}
    if not isinstance(data, dict):
        return None
    return data if any(isinstance(v, dict) for v in data.values()) else {"*": data}


def sections_for(data, printer_name):
    """The sections that apply to a printer preset, "*" first (later ones win)."""
    low = fold(printer_name)
    return [("*", data.get("*") or {})] + [(k, v) for k, v in data.items()
                                            if k != "*" and isinstance(v, dict) and fold(k) in low]


def tested(filament, printer_name):
    """Observations of the tested values a Spoolman filament already holds for this printer."""
    out = {}
    data = settings_of(filament) or {}
    for name, section in sections_for(data, printer_name):
        for key, canonical in TESTED_KEYS.items():
            value = section.get(key)
            value = value[0] if isinstance(value, list) and value else value
            n = number(value)
            if n is not None:
                out[canonical] = (n, "Spoolman orca_settings [%s]" % name)
    for field, canonical in CALIBRATION_FIELDS.items():
        raw = (filament.get("extra") or {}).get(field)
        n = number(raw)
        if n is not None and canonical not in out:
            out[canonical] = (n, "Spoolman %s" % field)
    return [Observation(k, v, TESTED, "spoolman", "%s: %s" % (filament.get("name") or "Spoolman", where), "", 1.0)
            for k, (v, where) in out.items()]


def _text(value):
    return str(value[0] if isinstance(value, list) and value else value if value is not None else "").strip()


def same_value(a, b):
    """True when two OrcaSlicer values say the same ("0.95" and ["0.950"])."""
    x, y = _text(a), _text(b)
    nx, ny = number(x), number(y)
    if nx is not None and ny is not None:
        return abs(nx - ny) < 1e-9
    return x == y


def merge_settings(data, section, values, overwrite=()):
    """{"data": new object, "added": [], "replaced": [], "same": [], "differs": [], "existing": {key: value}}.
    A key no applying section has is added to section. A key already there is left alone, unless it says
    something else and is in overwrite: then section gets the new value (a model section wins over "*")."""
    data = json.loads(json.dumps(data or {}))
    values = {k: v for k, v in values.items() if k not in NATIVE_KEYS}
    overwrite = set(overwrite or ())
    target = data.setdefault(section or "*", {})
    applies = [data.get("*") or {}] + ([target] if section and section != "*" else [])
    out = {"added": [], "replaced": [], "same": [], "differs": [], "existing": {}}
    for key, value in values.items():
        now = None
        for s in applies:            # the one that wins on these printers
            if key in s:
                now = s[key]
        if now is None:
            target[key] = value
            out["added"].append(key)
            continue
        out["existing"][key] = _text(now)
        if same_value(now, value):
            out["same"].append(key)
        elif key in overwrite:
            target[key] = value
            out["replaced"].append(key)
        else:
            out["differs"].append(key)
    if not target:
        data.pop(section or "*", None)
    out["data"] = data
    return out


def add_missing(data, section, values):
    """(new object, added keys, kept keys): values added to section where no section that applies to the
    same printers already has the key."""
    r = merge_settings(data, section, values)
    return r["data"], r["added"], r["same"] + r["differs"]
