"""What each kind of material likes, beyond what its data says: one table the process and the filament preset read.

Speed and acceleration factors lower the printer's process (PETG: slower outer walls and bridges). The filament
part gives estimates for the preset: the part fan (lowest and highest speed, on bridges and overhangs, from which
layer it runs fully), the auxiliary fan, the shortest layer time, the chamber temperature, and how many first layers
print without the part fan. Every number here is a rule of thumb, never the maker's data: the
filament values are "estimated" and lose to a preset made for the filament, a data sheet or the user's own value.

The user can change any of them (settings.json "habits": {class: {field: value or None}}); a field they left out
keeps the default, None clears it (no factor, or no estimate).
"""

from .text import split_material, tokens

CLASSES = ("PLA", "SILK", "PETG", "ABS", "ASA", "PC", "PA", "CF", "TPU")

# field -> (kind, low, high). Factors only lower: a process is never sped up past what it says.
FIELDS = {
    "all": ("factor", 0.1, 1.0),            # every speed no group below names
    "outer": ("factor", 0.1, 1.0),          # outer wall, scarf joint
    "inner": ("factor", 0.1, 1.0),          # inner walls
    "top": ("factor", 0.1, 1.0),            # top surface
    "infill": ("factor", 0.1, 1.0),         # sparse infill, gap infill
    "bridge": ("factor", 0.1, 1.0),         # bridges, internal bridges
    "accel": ("factor", 0.1, 1.0),          # every acceleration
    "outer_accel": ("factor", 0.1, 1.0),    # outer wall acceleration
    "fan_min": ("int", 0, 100),             # % part fan at its lowest (long layers)
    "fan_max": ("int", 0, 100),             # % part fan at its highest (short layers)
    "bridge_fan": ("int", 0, 100),          # % part fan on bridges and overhangs
    "full_fan_layer": ("int", 1, 10),       # the layer from which the part fan runs at full speed
    "layer_time": ("int", 0, 60),           # s, a shorter layer slows down and gets the highest fan
    "aux_fan": ("int", 0, 100),             # % auxiliary (side) fan, on a printer that has one
    "fan_off_layers": ("int", 0, 10),       # first layers without the part fan
    "chamber": ("int", 0, 90),              # °C, for a printer that heats its chamber
    "enclosure": ("bool", 0, 1),            # warps in open air: wants a closed printer
}
SPEED_GROUPS = {"outer_wall_speed": "outer", "scarf_joint_speed": "outer", "inner_wall_speed": "inner",
                "top_surface_speed": "top", "sparse_infill_speed": "infill", "gap_infill_speed": "infill",
                "bridge_speed": "bridge", "internal_bridge_speed": "bridge"}
ACCEL_GROUPS = {"outer_wall_acceleration": "outer_accel"}

_PLA_FANS = {"fan_min": 100, "fan_max": 100, "full_fan_layer": 3, "layer_time": 8, "aux_fan": 70}
DEFAULTS = {
    "PLA": {"bridge_fan": 100, **_PLA_FANS},
    "SILK": {"outer": 0.6, "inner": 0.85, "top": 0.7, "outer_accel": 0.7, "bridge_fan": 100, **_PLA_FANS},
    # PETG sags less and strings less with slower bridges and a gentler fan; PLA wants the opposite
    "PETG": {"outer": 0.7, "inner": 0.85, "top": 0.75, "infill": 0.85, "bridge": 0.8, "bridge_fan": 60,
             "fan_min": 30, "fan_max": 60, "full_fan_layer": 3, "layer_time": 10, "aux_fan": 20},
    # warping materials: little fan, none from the side, so the layers stay warm enough to bond
    "ABS": {"outer": 0.85, "top": 0.85, "bridge_fan": 50, "fan_off_layers": 3, "chamber": 45, "enclosure": True,
            "fan_min": 10, "fan_max": 30, "full_fan_layer": 4, "layer_time": 12, "aux_fan": 0},
    "ASA": {"outer": 0.85, "top": 0.85, "bridge_fan": 50, "fan_off_layers": 3, "chamber": 45, "enclosure": True,
            "fan_min": 10, "fan_max": 35, "full_fan_layer": 4, "layer_time": 12, "aux_fan": 0},
    "PC": {"all": 0.7, "bridge_fan": 40, "fan_off_layers": 4, "chamber": 55, "enclosure": True,
           "fan_min": 0, "fan_max": 30, "full_fan_layer": 5, "layer_time": 15, "aux_fan": 0},
    "PA": {"all": 0.7, "bridge_fan": 40, "fan_off_layers": 3, "chamber": 45, "enclosure": True,
           "fan_min": 0, "fan_max": 40, "full_fan_layer": 4, "layer_time": 12, "aux_fan": 0},
    "CF": {"outer": 0.8, "top": 0.8},
    "TPU": {"all": 0.3, "accel": 0.5, "fan_min": 100, "fan_max": 100, "full_fan_layer": 3, "layer_time": 8},
}


def material_class(material, product=""):
    """Which row of the table a filament reads: PLA, SILK, PETG, ABS, ASA, PC, PA, CF or TPU."""
    base, words = split_material(material, product)
    words = set(tokens(words)) | set(tokens(material))
    if base == "TPU":
        return "TPU"
    if base.endswith("-CF") or "cf" in words or "carbon" in words:
        head = base.split("-")[0]
        return "CF" if base.startswith("PLA") else (head if head in CLASSES else "CF")
    if base == "PLA" and "silk" in words:
        return "SILK"
    if base in ("PETG", "PCTG", "PET"):
        return "PETG"
    return base if base in CLASSES else "PLA"


def _clean_value(field, value):
    """The value within its field's limits, None to clear, or KeyError/ValueError when it isn't one."""
    kind, low, high = FIELDS[field]
    if value is None or value == "":
        return None
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if str(value).lower() in ("1", "true", "yes"):
            return True
        if str(value).lower() in ("0", "false", "no"):
            return False
        raise ValueError(value)
    number = float(str(value).replace(",", "."))
    if number != number:                     # NaN
        raise ValueError(value)
    number = min(max(number, low), high)
    return int(round(number)) if kind == "int" else round(number, 3)


def clean_overrides(data):
    """The user's changes as {class: {field: value or None}}, only known classes and fields, values in limits;
    a value equal to the default is dropped (it is the default)."""
    out = {}
    if not isinstance(data, dict):
        return out
    for cls, fields in data.items():
        if cls not in CLASSES or not isinstance(fields, dict):
            continue
        row = {}
        for field, value in fields.items():
            if field not in FIELDS:
                continue
            try:
                value = _clean_value(field, value)
            except (ValueError, TypeError):
                continue
            if value != DEFAULTS[cls].get(field):
                row[field] = value
        if row:
            out[cls] = row
    return out


def table(overrides=None):
    """{class: {field: value}} with the user's changes over the defaults (a cleared field is left out)."""
    overrides = clean_overrides(overrides)
    out = {}
    for cls in CLASSES:
        row = dict(DEFAULTS[cls])
        row.update(overrides.get(cls, {}))
        out[cls] = {k: v for k, v in row.items() if v is not None}
    return out


def speed_factor(row, key):
    group = SPEED_GROUPS.get(key)
    value = row.get(group) if group else None
    return float(value if value is not None else row.get("all", 1.0))


def accel_factor(row, key):
    group = ACCEL_GROUPS.get(key)
    value = row.get(group) if group else None
    return float(value if value is not None else row.get("accel", 1.0))


def needs_enclosure(row):
    return bool(row.get("enclosure"))


def warnings(cls, row, printer, enclosed_printers=()):
    """[{code, cls, ...}] the page shows next to the filament and the process.

    enclosure: the material warps in open air and nothing says the printer is closed. chamber_passive: the printer
    is closed but can't heat its chamber, so the chamber temperature isn't set (warm it up before printing)."""
    if not needs_enclosure(row):
        return []
    if printer.get("chamber_control"):
        return []
    if printer.get("name") and printer["name"] in (enclosed_printers or ()):
        return [{"code": "chamber_passive", "cls": cls, "chamber": row.get("chamber")}]
    return [{"code": "enclosure", "cls": cls, "printer": printer.get("name", "")}]
