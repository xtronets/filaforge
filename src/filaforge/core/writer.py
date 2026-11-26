"""FilaForge's presets as an OrcaSlicer local bundle: <data dir>/user/<account>/_local/filaforge/.

Filament presets are written standalone (the base preset's whole config, then FilaForge's values, "inherits"
empty, a filament_id of their own): OrcaSlicer's sync from AMS loads only such presets. Process presets
inherit the printer's process and carry only the speeds FilaForge changed. OrcaSlicer refuses the whole bundle
when one file is wrong, so every file is checked before any is written, and the previous files are put back
if the reload fails.
"""

import hashlib
import json
import math
import os
import re

from . import files

BUNDLE = "filaforge"
BUNDLE_NAME = "FilaForge"
STATE = ".filaforge-bundle.json"
ID_PREFIX = "FFG"
FLAT_SKIP = frozenset({"inherits", "compatible_printers", "compatible_printers_condition", "filament_settings_id",
                       "filament_id", "name", "version", "from", "instantiation", "type", "setting_id",
                       "description", "url", "is_custom_defined", "renamed_from", "includes", "alias"})
TEXT_KEYS = frozenset({"filament_notes", "filament_vendor", "filament_type", "filament_colour",
                       "filament_settings_id", "compatible_printers", "filament_start_gcode", "filament_end_gcode"})
_NAME_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


MARKER = "FF"


def with_marker(name):
    """The name with FilaForge's "FF" mark before its "@printer" part ("Sunlu PLA+ FF @Creality K1C"), once."""
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    head, at, tail = name.partition("@")
    head = head.strip()
    if MARKER not in head.split():
        head = (head + " " + MARKER).strip()
    return "%s @%s" % (head, tail.strip()) if at else head


def safe_name(name):
    """A preset name that is also a valid file name on every OS."""
    name = _NAME_BAD.sub("-", str(name)).strip(" .")
    return re.sub(r"\s+", " ", name)[:120] or "FilaForge preset"


def filament_id(identity_key, printer):
    return ID_PREFIX + hashlib.sha256(("%s|%s" % (identity_key, printer)).encode("utf-8")).hexdigest()[:10].upper()


# OrcaSlicer reads these as whole numbers or switches; "212.5" in one of them makes it refuse the whole bundle
INT_KEYS = frozenset({"nozzle_temperature", "nozzle_temperature_initial_layer", "nozzle_temperature_range_low",
                      "nozzle_temperature_range_high", "hot_plate_temp", "hot_plate_temp_initial_layer",
                      "textured_plate_temp", "textured_plate_temp_initial_layer", "temperature_vitrification",
                      "chamber_temperature", "overhang_fan_speed", "close_fan_the_first_x_layers"})
BOOL_KEYS = frozenset({"enable_pressure_advance", "activate_chamber_temp_control"})


def config_value(key, value):
    """value (text) as OrcaSlicer reads key: whole numbers rounded, switches as 0/1, the rest as given."""
    text = str(value).strip()
    if text == "nil" or (key not in INT_KEYS and key not in BOOL_KEYS):
        return text
    if key in BOOL_KEYS and text.lower() in ("true", "false"):
        return "1" if text.lower() == "true" else "0"
    try:
        number = float(text.rstrip("%"))
    except ValueError:
        return text
    if not math.isfinite(number):
        return text
    if key in BOOL_KEYS:
        return "1" if number else "0"
    return str(int(math.floor(number + 0.5)))


def filament_preset(name, base_config, settings, printer, identity, identity_key, description=""):
    """The standalone filament preset: base_config ({key: OrcaSlicer text}) under settings ({key: text})."""
    flat = {k: v for k, v in (base_config or {}).items() if k not in FLAT_SKIP and isinstance(v, str)}
    for key, value in settings.items():
        parent = flat.get(key)
        count = len(parent.split(",")) if isinstance(parent, str) and key not in TEXT_KEYS else 1
        flat[key] = [config_value(key, value)] * max(1, count)
    flat.update({
        "type": "filament", "name": name, "from": "User", "instantiation": "true", "inherits": "",
        "version": "1.0.0", "filament_settings_id": [name], "filament_id": filament_id(identity_key, printer),
        "compatible_printers": [printer] if printer else [], "compatible_printers_condition": "",
        "filament_vendor": [identity.brand], "description": description or "FilaForge",
    })
    if identity.colour and re.match(r"^#[0-9A-Fa-f]{6}$", identity.colour):
        flat["filament_colour"] = [identity.colour.upper()]
    return flat


def culprits(keys, loads):
    """The fewest of keys to leave out so that loads(left_out) is true, found by halving; [] if nothing needs to
    go, None if leaving all of them out does not help either."""
    dropped = []
    rest = list(keys)
    while not loads(dropped):
        if not rest or not loads(dropped + rest):
            return None
        lo, hi = 0, len(rest)
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if loads(dropped + rest[:mid]):
                hi = mid
            else:
                lo = mid
        dropped.append(rest.pop(hi - 1))
    return dropped


def problem(preset):
    """Why OrcaSlicer would refuse the preset, or ""."""
    for key in ("type", "name", "from", "inherits"):
        if not isinstance(preset.get(key), str):
            return "%s is missing" % key
    if preset["type"] not in ("filament", "process"):
        return "unknown type"
    for key, value in preset.items():
        if isinstance(value, list):
            if not all(isinstance(v, str) for v in value):
                return "%s is not a list of text" % key
            if key not in TEXT_KEYS and key != "compatible_printers" and value and \
                    not all(_numberish(v) for v in value) and preset["type"] == "filament" and key in NUMERIC_KEYS:
                return "%s is not a number" % key
            if preset["type"] == "filament" and key in INT_KEYS | BOOL_KEYS and \
                    any(v != "nil" and v != config_value(key, v) for v in value):
                return "%s is not a whole number" % key
        elif not isinstance(value, str):
            return "%s is not text" % key
    return ""


NUMERIC_KEYS = frozenset({"nozzle_temperature", "nozzle_temperature_initial_layer", "nozzle_temperature_range_low",
                          "nozzle_temperature_range_high", "hot_plate_temp", "hot_plate_temp_initial_layer",
                          "textured_plate_temp", "textured_plate_temp_initial_layer", "filament_density",
                          "filament_max_volumetric_speed", "fan_min_speed", "fan_max_speed",
                          "temperature_vitrification", "chamber_temperature", "filament_flow_ratio",
                          "pressure_advance", "enable_pressure_advance", "filament_retraction_length",
                          "filament_retraction_speed", "overhang_fan_speed", "close_fan_the_first_x_layers",
                          "activate_chamber_temp_control"})


def _numberish(text):
    if text == "nil":
        return True
    try:
        return math.isfinite(float(text.rstrip("%")))
    except ValueError:
        return False


def _parsed(data):
    try:
        return json.loads(data.decode("utf-8"))
    except ValueError:
        return None


def _write_json(path, value):
    files.write_json(path, value, ensure_ascii=False, indent=4)


class Bundle:
    """The files of FilaForge's bundle in one account folder. history (history.History): where a replaced or
    removed preset is kept first."""

    def __init__(self, folder, history=None):
        self.folder = folder
        self.history = history

    def path(self, kind, name):
        return os.path.join(self.folder, "filament" if kind == "filament" else "process", safe_name(name) + ".json")

    def state(self):
        try:
            with open(os.path.join(self.folder, STATE), encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) and isinstance(data.get("presets"), dict) else {"presets": {}}
        except (OSError, ValueError):
            return {"presets": {}}

    def listing(self):
        """[{kind, name, file, at}] of the presets FilaForge wrote."""
        out = []
        for name, info in sorted(self.state()["presets"].items()):
            out.append(dict(info, name=name, exists=os.path.exists(info.get("file", ""))))
        return out

    def info(self, name):
        return self.state()["presets"].get(name)

    def update_meta(self, name, changes):
        """Merge changes into a preset's entry (no file is written but the bundle's state)."""
        state = self.state()
        if name not in state["presets"]:
            return False
        state["presets"][name].update(changes)
        _write_json(os.path.join(self.folder, STATE), state)
        return True

    def forget(self, name):
        """Drop a preset from the state only (its file is gone)."""
        state = self.state()
        if state["presets"].pop(name, None) is None:
            return False
        _write_json(os.path.join(self.folder, STATE), state)
        return True

    def save(self, preset, reload_fn, meta=None, reason="replaced"):
        """Write one preset (replacing one of the same name) and reload the bundle; on a refused reload the
        previous file is put back and the error raised. The replaced file goes into the history first."""
        issue = problem(preset)
        if issue:
            raise ValueError("preset %s: %s" % (preset.get("name"), issue))
        kind = preset["type"]
        path = self.path(kind, preset["name"])
        old = None
        if os.path.exists(path):
            with open(path, "rb") as f:
                old = f.read()
        old_meta = self.state()["presets"].get(preset["name"])
        _write_json(path, preset)
        _write_json(os.path.join(self.folder, "bundle_metadata.json"),
                    {"id": BUNDLE, "name": BUNDLE_NAME, "version": "1.0.0"})
        try:
            reload_fn(BUNDLE)
        except Exception:
            if old is None:
                os.unlink(path)
            else:
                with open(path, "wb") as f:
                    f.write(old)
            try:
                reload_fn(BUNDLE)
            except Exception:
                pass
            raise
        if old is not None and self.history is not None and _parsed(old) != preset:
            self.history.add(kind, preset["name"], old, {k: v for k, v in (old_meta or {}).items() if k != "file"},
                             reason)
        state = self.state()
        state["presets"][preset["name"]] = dict(meta or {}, kind=kind, file=path)
        _write_json(os.path.join(self.folder, STATE), state)
        return path

    def attempt(self, preset, reload_fn):
        """Whether OrcaSlicer takes the bundle with preset in it. The bundle's files are as before afterwards
        (not reloaded: the caller saves or reloads next)."""
        path = self.path(preset["type"], preset["name"])
        old = None
        if os.path.exists(path):
            with open(path, "rb") as f:
                old = f.read()
        _write_json(path, preset)
        try:
            reload_fn(BUNDLE)
            ok = True
        except Exception:
            ok = False
        if old is None:
            os.unlink(path)
        else:
            with open(path, "wb") as f:
                f.write(old)
        return ok

    def files(self):
        """[(kind, path)] of the preset files in the bundle's folders, whether FilaForge lists them or not."""
        out = []
        for kind, sub in (("filament", "filament"), ("process", "process")):
            folder = os.path.join(self.folder, sub)
            if os.path.isdir(folder):
                out += [(kind, os.path.join(folder, f)) for f in sorted(os.listdir(folder)) if f.endswith(".json")]
        return out

    def set_aside(self, reload_fn, standalone=None):
        """Take out the presets that make OrcaSlicer refuse the bundle. standalone(data) -> a fixed copy to try
        first (a process whose parent is gone, say) or None. Every one taken out goes into the history, so it can
        be restored. Returns [(name, "fixed"|"removed")], or None if the bundle is refused without any of them."""
        items = self.files()
        held = []
        for kind, path in items:
            with open(path, "rb") as f:
                held.append((kind, path, f.read()))
            os.unlink(path)
        try:
            reload_fn(BUNDLE)
        except Exception:
            for _, path, data in held:
                with open(path, "wb") as f:
                    f.write(data)
            try:
                reload_fn(BUNDLE)
            except Exception:
                pass
            return None
        state = self.state()
        out = []
        for kind, path, data in held:
            with open(path, "wb") as f:
                f.write(data)
            try:
                reload_fn(BUNDLE)
                continue
            except Exception:
                pass
            parsed = _parsed(data) if isinstance(_parsed(data), dict) else {}
            name = str(parsed.get("name") or os.path.basename(path)[:-5])
            fixed = standalone(parsed) if standalone and parsed else None
            if fixed is not None:
                _write_json(path, fixed)
                try:
                    reload_fn(BUNDLE)
                    if self.history is not None:
                        self.history.add(kind, name, data, {}, "repaired")
                    out.append((name, "fixed"))
                    continue
                except Exception:
                    pass
            os.unlink(path)
            if self.history is not None:
                info = state["presets"].get(name) or {}
                self.history.add(kind, name, data, {k: v for k, v in info.items() if k != "file"}, "invalid")
            state["presets"].pop(name, None)
            out.append((name, "removed"))
        _write_json(os.path.join(self.folder, STATE), state)
        reload_fn(BUNDLE)
        return out

    def remove(self, name, reload_fn, reason="removed"):
        state = self.state()
        info = state["presets"].pop(name, None)
        if info and os.path.exists(info.get("file", "")):
            if self.history is not None:
                with open(info["file"], "rb") as f:
                    self.history.add(info.get("kind", "filament"), name, f.read(),
                                     {k: v for k, v in info.items() if k != "file"}, reason)
            os.unlink(info["file"])
        _write_json(os.path.join(self.folder, STATE), state)
        reload_fn(BUNDLE)
        return info is not None


def account_folders(data_dir, preset_files):
    """Account folders under <data dir>/user to try: the ones user presets come from, then the others."""
    found = []
    for path in preset_files:
        parts = os.path.normpath(path or "").replace("\\", "/").split("/")
        if "user" in parts:
            i = parts.index("user")
            if i + 1 < len(parts) - 1 and parts[i + 1] not in found:
                found.append(parts[i + 1])
    try:
        found += sorted(n for n in os.listdir(os.path.join(data_dir, "user"))
                        if os.path.isdir(os.path.join(data_dir, "user", n)) and n not in found and n != "default")
    except OSError:
        pass
    return found + (["default"] if "default" not in found else [])
