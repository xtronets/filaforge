"""A print process for one filament: the printer's process with its speeds fitted to the filament.

Two things lower a speed: the filament's max volumetric speed (the most plastic per second the hot end melts for
it, which OrcaSlicer also enforces while slicing; here it shows up in the preset) and the material's habits
(PETG and silk PLA look better with slower outer walls, TPU needs everything slow). The factors are rules of
thumb, marked "estimated"; the volumetric limit is arithmetic.
"""

import math

from .text import fold, number, split_material, tokens

# speed key -> (line width key, layer height key)
SPEEDS = {
    "outer_wall_speed": ("outer_wall_line_width", "layer_height"),
    "inner_wall_speed": ("inner_wall_line_width", "layer_height"),
    "sparse_infill_speed": ("sparse_infill_line_width", "layer_height"),
    "internal_solid_infill_speed": ("internal_solid_infill_line_width", "layer_height"),
    "top_surface_speed": ("top_surface_line_width", "layer_height"),
    "gap_infill_speed": ("internal_solid_infill_line_width", "layer_height"),
    "small_perimeter_speed": ("outer_wall_line_width", "layer_height"),
    "scarf_joint_speed": ("outer_wall_line_width", "layer_height"),
    "support_speed": ("support_line_width", "layer_height"),
    "support_interface_speed": ("support_line_width", "layer_height"),
    "bridge_speed": ("bridge", "layer_height"),
    "internal_bridge_speed": ("internal_bridge", "layer_height"),
    "skirt_speed": ("initial_layer_line_width", "initial_layer_print_height"),
    "initial_layer_speed": ("initial_layer_line_width", "initial_layer_print_height"),
    "initial_layer_infill_speed": ("initial_layer_line_width", "initial_layer_print_height"),
}
# a value given in % is a share of another setting (as OrcaSlicer reads it)
RELATIVE = {
    "internal_bridge_speed": "bridge_speed", "small_perimeter_speed": "outer_wall_speed",
    "scarf_joint_speed": "outer_wall_speed",
    "sparse_infill_acceleration": "default_acceleration", "internal_solid_infill_acceleration": "default_acceleration",
    "bridge_acceleration": "outer_wall_acceleration", "top_surface_acceleration": "default_acceleration",
    "inner_wall_acceleration": "default_acceleration", "outer_wall_acceleration": "default_acceleration",
}
ACCELERATIONS = ("default_acceleration", "outer_wall_acceleration", "inner_wall_acceleration",
                 "top_surface_acceleration", "sparse_infill_acceleration", "internal_solid_infill_acceleration",
                 "bridge_acceleration", "initial_layer_acceleration", "travel_acceleration")
# material class -> {speed key: factor}; "*" for every speed key not named
FACTORS = {
    "PLA": {},
    "SILK": {"outer_wall_speed": 0.6, "top_surface_speed": 0.7, "inner_wall_speed": 0.85, "scarf_joint_speed": 0.6},
    "PETG": {"outer_wall_speed": 0.7, "top_surface_speed": 0.75, "inner_wall_speed": 0.85, "sparse_infill_speed": 0.85,
             "gap_infill_speed": 0.8, "scarf_joint_speed": 0.7},
    "ABS": {"outer_wall_speed": 0.85, "top_surface_speed": 0.85},
    "ASA": {"outer_wall_speed": 0.85, "top_surface_speed": 0.85},
    "PC": {"*": 0.7},
    "PA": {"*": 0.7},
    "CF": {"outer_wall_speed": 0.8, "top_surface_speed": 0.8},
    "TPU": {"*": 0.3},
}
# the same for accelerations (a soft filament in a direct drive wants gentle starts)
ACCEL_FACTORS = {"TPU": {"*": 0.5}, "SILK": {"outer_wall_acceleration": 0.7}}
TPU_CAP = 60.0          # mm/s: no TPU speed above this, whatever the process said
BRIDGE_RAMP = 0.025     # s: a bridge reaches its speed this fast at most, so the strand isn't torn at the start
MIN_ACCEL = 500.0
# part sizes: {variant: ({speed key: factor}, {acceleration key: factor})}
VARIANTS = {
    "standard": ({}, {}),
    "small": ({"outer_wall_speed": 0.7, "inner_wall_speed": 0.8, "top_surface_speed": 0.8, "scarf_joint_speed": 0.7,
               "small_perimeter_speed": 0.8},
              {"outer_wall_acceleration": 0.6, "inner_wall_acceleration": 0.6, "top_surface_acceleration": 0.6}),
}
SMALL_PART_MM = 40.0    # a part whose footprint fits in this square counts as small


def material_class(material, product=""):
    base, words = split_material(material, product)
    words = set(tokens(words)) | set(tokens(material))
    if base == "TPU":
        return "TPU"
    if base.endswith("-CF") or "cf" in words or "carbon" in words:
        return "CF" if base.startswith("PLA") else (base.split("-")[0] if base.split("-")[0] in FACTORS else "CF")
    if base == "PLA" and "silk" in words:
        return "SILK"
    if base in ("PETG", "PCTG", "PET"):
        return "PETG"
    return base if base in FACTORS else "PLA"


def width_mm(text, nozzle, fallback=None):
    """A line width in mm from OrcaSlicer's text ("0.45", "110%", "0" for automatic)."""
    raw = str(text or "").split(",")[0].strip()
    if raw.endswith("%"):
        value = number(raw)
        return nozzle * value / 100 if value else fallback
    value = number(raw)
    return value if value else fallback


def area(width, height):
    """OrcaSlicer's cross-section of an extrusion: a rectangle with rounded sides."""
    return height * (width - height * (1 - math.pi / 4))


def _value(config, key):
    raw = str(config.get(key, "")).split(",")[0].strip()
    return raw, number(raw)


def resolved(config, key, depth=0):
    """The absolute value of a speed or acceleration, a % being a share of the setting it refers to."""
    raw, value = _value(config, key)
    if value is None:
        return None
    if raw.endswith("%"):
        ref = RELATIVE.get(key)
        base = resolved(config, ref, depth + 1) if ref and depth < 4 else None
        return base * value / 100 if base else None
    return value


def _area(config, key, width_key, height, nozzle, default_width):
    if width_key in ("bridge", "internal_bridge"):
        thick = str(config.get("thick_bridges" if width_key == "bridge" else "thick_internal_bridges", "0"))
        flow = number(str(config.get("bridge_flow" if width_key == "bridge" else "internal_bridge_flow", "1"))) or 1.0
        if thick.split(",")[0].strip() in ("1", "true"):
            return math.pi * nozzle * nozzle / 4 * flow
        width = width_mm(config.get("outer_wall_line_width"), nozzle, default_width)
        return area(width, height) * flow
    width = width_mm(config.get(width_key), nozzle, default_width)
    return area(width, height)


def fit(config, nozzle, max_volumetric_speed, material, product="", machine=None, variant="standard"):
    """[{key, group, base, new, unit, reasons}] of what changes for this filament (and part size).

    reasons: [{"code": "material"|"flow"|"tpu"|"machine"|"bridge_ramp"|"variant", ...numbers}] for the page to word.
    machine: {"accel": max extruding acceleration, "travel_accel", "speed": max XY speed} of the printer."""
    machine = machine or {}
    cls = material_class(material, product)
    factors = FACTORS.get(cls, {})
    vspeed, vaccel = VARIANTS.get(variant, VARIANTS["standard"])
    default_width = width_mm(config.get("line_width"), nozzle, nozzle * 1.125)
    changes, new_speeds = [], {}
    for key, (width_key, height_key) in SPEEDS.items():
        base = resolved(config, key)
        if not base:
            continue
        height = number(str(config.get(height_key) or config.get("layer_height") or "0.2").split(",")[0]) or 0.2
        cross = _area(config, key, width_key, height, nozzle, default_width)
        limit = max_volumetric_speed / cross if max_volumetric_speed and cross else None
        reasons = []
        new = base
        factor = factors.get(key, factors.get("*", 1.0))
        if factor != 1.0:
            new *= factor
            reasons.append({"code": "material", "cls": cls, "factor": factor})
        if vspeed.get(key, 1.0) != 1.0:
            new *= vspeed[key]
            reasons.append({"code": "variant", "variant": variant, "factor": vspeed[key]})
        if cls == "TPU" and new > TPU_CAP:
            new = TPU_CAP
            reasons.append({"code": "tpu", "cap": TPU_CAP})
        if limit is not None and new > limit:
            new = limit
            reasons.append({"code": "flow", "mvs": max_volumetric_speed, "area": round(cross, 4)})
        if machine.get("speed") and new > machine["speed"]:
            new = machine["speed"]
            reasons.append({"code": "machine", "cap": machine["speed"]})
        new = max(1, int(math.floor(new)))
        new_speeds[key] = new
        if new < int(math.floor(base)):
            changes.append({"key": key, "group": "speed", "unit": "mm/s", "base": round(base, 1), "new": new,
                            "limit": round(limit, 1) if limit else None, "reasons": reasons})
    accel_factors = ACCEL_FACTORS.get(cls, {})
    for key in ACCELERATIONS:
        base = resolved(config, key)
        if not base:
            continue
        new, reasons = base, []
        factor = accel_factors.get(key, accel_factors.get("*", 1.0))
        if factor != 1.0:
            new *= factor
            reasons.append({"code": "material", "cls": cls, "factor": factor})
        if vaccel.get(key, 1.0) != 1.0:
            new *= vaccel[key]
            reasons.append({"code": "variant", "variant": variant, "factor": vaccel[key]})
        if key == "bridge_acceleration":
            bridge = new_speeds.get("bridge_speed") or resolved(config, "bridge_speed")
            if bridge:
                ramp = max(MIN_ACCEL, bridge / BRIDGE_RAMP)
                if new > ramp:
                    new = ramp
                    reasons.append({"code": "bridge_ramp", "speed": bridge, "seconds": BRIDGE_RAMP})
        cap = machine.get("travel_accel" if key == "travel_acceleration" else "accel")
        if cap and new > cap:
            new = cap
            reasons.append({"code": "machine", "cap": cap})
        new = max(MIN_ACCEL if base >= MIN_ACCEL else base, new)
        new = int(math.floor(new / 100) * 100) if new >= 1000 else int(math.floor(new))
        if new < int(math.floor(base)):
            changes.append({"key": key, "group": "accel", "unit": "mm/s²", "base": round(base), "new": new,
                            "limit": None, "reasons": reasons})
    return changes


def describe(reason):
    """English words for a reason (tests, logs); the page words them itself."""
    code = reason["code"]
    if code == "material":
        return "material %s x%g" % (reason["cls"], reason["factor"])
    if code == "flow":
        return "max flow %g mm³/s through %.4f mm²" % (reason["mvs"], reason["area"])
    if code == "tpu":
        return "TPU max %g mm/s" % reason["cap"]
    if code == "machine":
        return "printer limit %g" % reason["cap"]
    if code == "bridge_ramp":
        return "bridge speed %g mm/s reached in %g s" % (reason["speed"], reason["seconds"])
    if code == "variant":
        return "%s parts x%g" % (reason["variant"], reason["factor"])
    return code


def variant_for(sizes):
    """'small' when every part on the plate fits in SMALL_PART_MM x SMALL_PART_MM, else 'standard'."""
    sizes = [s for s in sizes or [] if s and s[0] > 0 and s[1] > 0]
    if sizes and all(max(x, y) <= SMALL_PART_MM for x, y in sizes):
        return "small"
    return "standard"


# not copied from a resolved base: they name the base itself
_OWN_KEYS = frozenset({"inherits", "compatible_printers", "compatible_printers_condition", "print_settings_id",
                       "name", "version", "from", "instantiation", "type", "setting_id", "description", "url",
                       "is_custom_defined", "renamed_from", "includes", "alias", "base_id", "user_id", "updated_time"})


def preset(base_name, name, printer_names, changes, base_config=None):
    """An OrcaSlicer process preset with the changed speeds over base_name.

    A system base is inherited. A user's own base (base_config given: its resolved settings) can't be: OrcaSlicer
    loads plugin bundles before the user's presets, so on the next start the parent would be missing and the
    preset dropped. Then the preset carries all the base's settings and inherits the base's own system parent."""
    out = {"type": "process", "name": name, "from": "User", "instantiation": "true", "inherits": base_name,
           "print_settings_id": name, "version": "1.0.0"}
    if base_config is not None:
        out.update({k: v for k, v in base_config.items() if k not in _OWN_KEYS and isinstance(v, str)})
        out["inherits"] = str(base_config.get("inherits") or "").strip().strip('"')
    if printer_names:
        out["compatible_printers"] = list(printer_names)
        out["compatible_printers_condition"] = ""
    for change in changes:
        out[change["key"]] = str(change["new"])
    return out


def process_name(base_name, filament_label):
    """The process's name: the base's without its "@printer" part, then the filament ("0.20mm Standard Sunlu PLA+")."""
    bare = base_name.split("@")[0].strip() if "@" in base_name else base_name
    return ("%s %s" % (bare, filament_label)).strip()
